"""C4a contracts for account-scope locking and managed capacity checks."""

from __future__ import annotations

import multiprocessing
import queue
import time
from pathlib import Path

import pytest

from dftworld_bench.hpc.drivers.compshare import (
    AccountScopeLock,
    AccountScopeLockTimeout,
    CompShareCli,
    CompSharePolicyError,
    FakeCompShareCliRunner,
    assert_account_capacity,
    make_ownership_marker,
)
from dftworld_bench.hpc.drivers.compshare.cli import CliResult


def _scope_lock(root: Path, *, timeout_sec: float | None = 1.0) -> AccountScopeLock:
    return AccountScopeLock(
        root,
        provider="compshare",
        target_binding="offline://compshare",
        account="offline-account",
        timeout_sec=timeout_sec,
    )


def test_account_scope_hash_is_stable_and_separates_policy_identity(tmp_path: Path):
    first = _scope_lock(tmp_path)
    again = _scope_lock(tmp_path)
    assert first.scope_hash == again.scope_hash
    assert first.lock_path == again.lock_path
    assert "offline-account" not in str(first.lock_path)

    other_target = AccountScopeLock(
        tmp_path,
        provider="compshare",
        target_binding="offline://other",
        account="offline-account",
    )
    other_account_scope = AccountScopeLock(
        tmp_path,
        provider="compshare",
        target_binding="offline://compshare",
        account="offline-account",
        managed_account_scope_id="managed-scope-b",
    )
    assert first.scope_hash != other_target.scope_hash
    assert first.scope_hash != other_account_scope.scope_hash


def test_account_scope_lock_is_reentrant_and_times_out(tmp_path: Path):
    first = _scope_lock(tmp_path, timeout_sec=1.0)
    second = _scope_lock(tmp_path, timeout_sec=0.02)
    with first:
        assert first.locked
        assert first.depth == 1
        with first:
            assert first.depth == 2
        assert first.depth == 1
        with pytest.raises(AccountScopeLockTimeout):
            second.acquire()
    assert not first.locked
    with second:
        assert second.locked


def _hold_account_lock(root: str, entered, release, result) -> None:
    lock = AccountScopeLock(
        root,
        provider="compshare",
        target_binding="offline://compshare",
        account="offline-account",
        timeout_sec=2.0,
    )
    with lock:
        entered.set()
        release.wait(2.0)
    result.put("released")


def _acquire_account_lock(root: str, result) -> None:
    lock = AccountScopeLock(
        root,
        provider="compshare",
        target_binding="offline://compshare",
        account="offline-account",
        timeout_sec=2.0,
    )
    started = time.monotonic()
    with lock:
        result.put(("acquired", time.monotonic() - started))


def test_account_scope_flock_serializes_independent_processes(tmp_path: Path):
    # ``fork`` is available on the supported maintainer hosts and avoids
    # importing the full test module through a subprocess command line.
    context = multiprocessing.get_context("fork")
    entered = context.Event()
    release = context.Event()
    first_result = context.Queue()
    second_result = context.Queue()
    first = context.Process(
        target=_hold_account_lock,
        args=(str(tmp_path), entered, release, first_result),
    )
    second = context.Process(
        target=_acquire_account_lock,
        args=(str(tmp_path), second_result),
    )
    first.start()
    assert entered.wait(1.0)
    second.start()
    time.sleep(0.10)
    with pytest.raises(queue.Empty):
        second_result.get_nowait()
    release.set()
    assert first_result.get(timeout=2.0) == "released"
    acquired, waited = second_result.get(timeout=2.0)
    assert acquired == "acquired"
    assert waited >= 0.05
    first.join(timeout=2.0)
    second.join(timeout=2.0)
    assert first.exitcode == 0
    assert second.exitcode == 0


def test_assert_account_capacity_uses_complete_managed_inventory():
    runner = FakeCompShareCliRunner(initial_stock=2)
    cli = CompShareCli(runner=runner)
    assert assert_account_capacity(cli) == []
    name, remark = make_ownership_marker("capacity-run")
    cli.instance_create(image="img-capacity", name=name, remark=remark)
    with pytest.raises(CompSharePolicyError, match="capacity exhausted"):
        assert_account_capacity(cli)


@pytest.mark.parametrize(
    "item",
    [
        {"name": "mlffbench-aaaaaaaaaaaaaaaa", "remark": "wrong", "id": "i-1", "status": "Running"},
        {"name": "mlffbench-aaaaaaaaaaaaaaaa", "remark": "mlffbench:run:aaaaaaaaaaaaaaaa"},
        {"name": "mlffbench-aaaaaaaaaaaaaaaa", "remark": "mlffbench:run:aaaaaaaaaaaaaaaa", "id": "i-1"},
        {"name": "mlffbench-aaaaaaaaaaaaaaaa", "remark": "mlffbench:run:aaaaaaaaaaaaaaaa", "id": "i-1", "status": 1},
    ],
)
def test_assert_account_capacity_rejects_ambiguous_managed_records(item: dict):
    class Provider:
        def instance_list(self, *, all: bool):
            assert all is True
            return [item]

    with pytest.raises(CompSharePolicyError):
        assert_account_capacity(Provider())


def test_assert_account_capacity_ignores_only_explicit_terminal_states():
    name, remark = make_ownership_marker("terminal-run")

    class Provider:
        def instance_list(self, *, all: bool):
            return [
                {"name": name, "remark": remark, "id": "i-deleted", "status": "DELETED"},
                {"name": "external", "id": "i-external", "status": "Running"},
            ]

    assert assert_account_capacity(Provider()) == []


def test_assert_account_capacity_wraps_query_failures():
    class Provider:
        def instance_list(self, *, all: bool):
            raise RuntimeError("offline provider unavailable")

    with pytest.raises(CompSharePolicyError, match="instance_list"):
        assert_account_capacity(Provider())


def test_assert_account_capacity_propagates_malformed_list_shape():
    class Provider:
        def instance_list(self, *, all: bool):
            return {"items": []}

    with pytest.raises(CompSharePolicyError, match="non-list"):
        assert_account_capacity(Provider())
