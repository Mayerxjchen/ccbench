"""C4b account-scoped manager integration contracts.

All provider interactions in this module use fake CLI runners.  The
cross-process fixture stores its tiny provider inventory in a file so the
test exercises the same durable account lock and ledger boundaries as two
independent managers.
"""

from __future__ import annotations

import fcntl
import json
import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from ccbench.hpc.drivers.compshare import (
    CliResult,
    CompShareCli,
    CompShareCreateUncertainError,
    CompShareDriver,
    CompShareDriverError,
    CompShareManagerError,
    FakeCompShareCliRunner,
    RunScopedInstanceManager,
    make_ownership_marker,
)


def _manager(cli: CompShareCli, root: Path, *, timeout: float | None = 1.0):
    return RunScopedInstanceManager(
        cli,
        state_root=root,
        lock_timeout_sec=timeout,
        ledger_path=root / "ledger.jsonl",
        orphan_ledger_path=root / "orphans.jsonl",
    )


def test_stale_managers_reload_ledger_and_serialize_operation_reservation(tmp_path: Path):
    runner = FakeCompShareCliRunner(initial_stock=2)
    first = _manager(CompShareCli(runner=runner), tmp_path)
    second = _manager(CompShareCli(runner=runner), tmp_path)

    instance_id = first.get_or_create_instance(
        "run-one", "img-one", operation_id="op-one"
    )
    with pytest.raises(CompShareManagerError, match="max_instances"):
        second.get_or_create_instance("run-two", "img-two", operation_id="op-two")
    with pytest.raises(CompShareManagerError, match="active GPU operation"):
        second.get_or_create_instance(
            "run-one", "img-one", operation_id="op-retry"
        )

    first.release_operation("run-one", "op-one")
    assert (
        second.get_or_create_instance("run-one", "img-one", operation_id="op-retry")
        == instance_id
    )
    second.release_operation("run-one", "op-retry")
    first.terminate_run("run-one")


def _file_state_read(path: Path) -> dict:
    if not path.exists():
        return {"create_count": 0, "seq": 0, "instances": {}}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _file_state_update(path: Path, update):
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            state = _file_state_read(path)
            update(state)
            path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


class _FileCountingRunner:
    """Small file-backed fake provider used by forked manager workers."""

    def __init__(self, root: str):
        self.path = Path(root) / "provider.json"

    def __call__(self, argv, _env=None):
        cmd = tuple(argv[2:4])

        def response(data):
            return CliResult(
                0,
                json.dumps({"ok": True, "schema_version": "1", "data": data}),
                "",
            )

        def error(code, message):
            return CliResult(
                1,
                json.dumps(
                    {
                        "ok": False,
                        "schema_version": "1",
                        "error": {"code": code, "message": message},
                    }
                ),
                message,
            )

        if cmd == ("instance", "search"):
            return response({"instances": [{"available_count": 2}]})
        if cmd == ("instance", "list"):
            state = _file_state_read(self.path)
            return response({"items": list(state["instances"].values())})
        if cmd == ("instance", "create"):
            if "--dry-run" in argv:
                return response({"dry_run": True})
            name = argv[argv.index("--name") + 1]
            remark = argv[argv.index("--remark") + 1]
            result = {}

            def create(state):
                state["seq"] += 1
                state["create_count"] += 1
                instance_id = f"file-inst-{state['seq']:04d}"
                state["instances"][instance_id] = {
                    "id": instance_id,
                    "instance_id": instance_id,
                    "name": name,
                    "remark": remark,
                    "status": "Running",
                }
                result["instance_id"] = instance_id

            _file_state_update(self.path, create)
            return response(result)
        instance_id = argv[4] if len(argv) > 4 else ""
        if cmd == ("instance", "show"):
            state = _file_state_read(self.path)
            item = state["instances"].get(instance_id)
            return response({"instance": item}) if item else error(
                "NOT_FOUND", f"Instance {instance_id} not found"
            )
        if cmd == ("instance", "stop"):
            def stop(state):
                if instance_id in state["instances"]:
                    state["instances"][instance_id]["status"] = "Stopped"

            _file_state_update(self.path, stop)
            return response({"instance_id": instance_id, "status": "Stopped"})
        if cmd == ("instance", "delete"):
            removed = {}

            def delete(state):
                removed["item"] = state["instances"].pop(instance_id, None)

            _file_state_update(self.path, delete)
            if removed["item"] is None:
                return error("NOT_FOUND", f"Instance {instance_id} not found")
            return response({"instance_id": instance_id, "deleted": True})
        return error("UNKNOWN_COMMAND", f"unsupported fake command: {argv}")


def _cross_process_create(root: str, run_id: str, results) -> None:
    manager = _manager(
        CompShareCli(runner=_FileCountingRunner(root)), Path(root), timeout=5.0
    )
    try:
        value = manager.get_or_create_instance(
            run_id, f"img-{run_id}", operation_id=f"op-{run_id}"
        )
        results.put(("ok", value))
    except Exception as exc:  # pragma: no cover - asserted by parent process
        results.put(("error", type(exc).__name__, str(exc)))


def test_two_processes_different_runs_create_at_most_once(tmp_path: Path):
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    first = context.Process(
        target=_cross_process_create, args=(str(tmp_path), "run-a", results)
    )
    second = context.Process(
        target=_cross_process_create, args=(str(tmp_path), "run-b", results)
    )
    first.start()
    second.start()
    outcomes = [results.get(timeout=5), results.get(timeout=5)]
    first.join(timeout=5)
    second.join(timeout=5)
    assert first.exitcode == 0
    assert second.exitcode == 0
    assert sum(outcome[0] == "ok" for outcome in outcomes) == 1
    assert sum(outcome[0] == "error" and "capacity" in outcome[-1] for outcome in outcomes) == 1
    assert _file_state_read(tmp_path / "provider.json")["create_count"] == 1


class _AckLossRunner:
    def __init__(self):
        self.base = FakeCompShareCliRunner(initial_stock=2)
        self.real_create_calls = 0

    def __call__(self, argv, env=None):
        if tuple(argv[2:4]) == ("instance", "create") and "--dry-run" not in argv:
            self.real_create_calls += 1
            result = self.base(argv, env)
            if self.real_create_calls == 1:
                raise RuntimeError("lost create acknowledgement")
            return result
        return self.base(argv, env)


def test_ack_loss_is_durable_and_never_recreated(tmp_path: Path):
    runner = _AckLossRunner()
    cli = CompShareCli(runner=runner)
    manager = _manager(cli, tmp_path)
    with pytest.raises(CompShareCreateUncertainError):
        manager.get_or_create_instance("run-uncertain", "img-uncertain", operation_id="op")
    with pytest.raises(CompShareManagerError, match="unresolved"):
        manager.get_or_create_instance("run-uncertain", "img-uncertain", operation_id="op-2")
    assert runner.real_create_calls == 1
    assert any(
        record.get("status") == "CREATE_UNCERTAIN"
        for record in manager._state_records.values()
    )
    report = manager.reconcile_and_recover()
    assert report.clean is True
    assert runner.base.instances == {}


def test_capacity_and_query_fail_closed_for_unknown_or_malformed_provider_state(
    tmp_path: Path,
):
    runner = FakeCompShareCliRunner(initial_stock=2)
    name, remark = make_ownership_marker("run-unknown")
    runner.instances["unknown"] = {
        "id": "unknown",
        "instance_id": "unknown",
        "name": name,
        "remark": remark,
        "status": "Unknown",
    }
    manager = _manager(CompShareCli(runner=runner), tmp_path)
    with pytest.raises(CompShareManagerError, match="capacity"):
        manager.get_or_create_instance("run-new", "img-new", operation_id="op")

    class BrokenCli(CompShareCli):
        def instance_list(self, **_kwargs):
            raise RuntimeError("pagination timeout")

    broken = _manager(BrokenCli(), tmp_path / "broken")
    with pytest.raises(CompShareManagerError, match="capacity preflight"):
        broken.get_or_create_instance("run-query", "img-query", operation_id="op")


def test_lock_timeout_is_reported_without_provider_calls(tmp_path: Path):
    runner = FakeCompShareCliRunner(initial_stock=1)
    first = _manager(CompShareCli(runner=runner), tmp_path, timeout=1.0)
    second = _manager(CompShareCli(runner=runner), tmp_path, timeout=0.0)
    with first._account_lock:
        with pytest.raises(CompShareManagerError, match="timed out"):
            second.get_or_create_instance("run-timeout", "img-timeout", operation_id="op")


def test_reconcile_and_create_are_serialized_on_same_scope_lock(tmp_path: Path):
    runner = FakeCompShareCliRunner(initial_stock=2)
    first = _manager(CompShareCli(runner=runner), tmp_path)
    second = _manager(CompShareCli(runner=runner), tmp_path)
    first_id = first.get_or_create_instance("run-reconcile", "img-one", operation_id="op")
    first.release_operation("run-reconcile", "op")

    entered = threading.Event()
    release = threading.Event()
    original_method = first.cli.instance_show

    def blocked_show(instance_id):
        entered.set()
        assert release.wait(2.0)
        return original_method(instance_id)

    first.cli.instance_show = blocked_show  # type: ignore[method-assign]
    holder: dict[str, object] = {}

    def reconcile():
        holder["report"] = first.reconcile_and_recover()

    thread = threading.Thread(target=reconcile)
    thread.start()
    assert entered.wait(2.0)
    creator_done = threading.Event()

    def create_after_reconcile():
        try:
            holder["new_id"] = second.get_or_create_instance(
                "run-new", "img-two", operation_id="op-two"
            )
        finally:
            creator_done.set()

    creator = threading.Thread(target=create_after_reconcile)
    creator.start()
    time.sleep(0.05)
    assert not creator_done.is_set()
    release.set()
    thread.join(timeout=3.0)
    creator.join(timeout=3.0)
    assert holder["report"].clean is True
    assert holder["report"].recovered == [first_id]
    assert holder["new_id"] in runner.instances


def _formal_profile(*, state_root: Path | None = None):
    from ccbench.hpc.site_profile import HpcSiteProfile

    runtime_policy = {
        "requires_apptainer": False,
        "provider": "compshare",
        "default_gpu_type": "4090",
        "budget_policy": {
            "max_budget_cny": 100.0,
            "max_instance_hours": 1.0,
            "max_instances": 1,
        },
    }
    if state_root is not None:
        runtime_policy["compshare_state_root"] = str(state_root)
    return HpcSiteProfile.from_dict(
        {
            "schema_version": 1,
            "site_id": "c4b-compshare",
            "scheduler": "compshare",
            "connection": {
                "credential_profile_id": "fake",
                "target_binding": "fake://compshare",
                "remote_user": "offline",
                "remote_root_policy": "/workspace/{run_id}",
            },
            "account": "fake-account",
            "queues": {
                "gpu": {
                    "partition": "fake",
                    "qos": "default",
                    "max_cpus": 1,
                    "max_memory_gb": 1,
                    "max_gpus": 1,
                    "max_walltime_minutes": 1,
                }
            },
            "runtime_policy": runtime_policy,
        }
    )


def test_formal_compshare_requires_explicit_persistent_state_root(tmp_path: Path):
    with pytest.raises(CompShareDriverError, match="compshare_state_root"):
        CompShareDriver(
            CompShareCli(runner=FakeCompShareCliRunner()),
            workspace_root=str(tmp_path / "workspace"),
            site_profile=_formal_profile(),
        )

    driver = CompShareDriver(
        CompShareCli(runner=FakeCompShareCliRunner()),
        workspace_root=str(tmp_path / "workspace"),
        site_profile=_formal_profile(state_root=tmp_path / "state"),
    )
    assert driver.manager.state_root == tmp_path / "state"
