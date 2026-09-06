"""Focused provider-readback and zero-orphan contracts (C5/C6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ccbench.hpc.drivers.compshare.cli import (
    CliResult,
    CompShareCli,
    CompShareCliJsonError,
    FakeCompShareCliRunner,
)
from ccbench.hpc.drivers.compshare.instance_manager import (
    CompShareOrphanError,
    RunScopedInstanceManager,
)
from ccbench.hpc.drivers.compshare.policy import (
    make_ownership_marker,
    matches_ownership_marker,
)


def test_new_marker_is_fixed_hash_and_legacy_is_cleanup_only():
    run_id = "run/with:unsafe chars"
    name, remark = make_ownership_marker(run_id)
    assert name == "mlffbench-" + __import__("hashlib").sha256(
        run_id.encode()
    ).hexdigest()[:16]
    assert remark == "mlffbench:run:" + name.removeprefix("mlffbench-")
    assert matches_ownership_marker({"name": name, "remark": remark}, run_id)
    assert not matches_ownership_marker(
        {"name": "mlffbench-run-legacy-worker", "remark": ""}, run_id
    )
    # Global recovery may identify the old shape for cleanup.
    assert matches_ownership_marker(
        {"name": "mlffbench-run-legacy-worker", "remark": ""}
    )
    # Prefix lookalikes are not owned.
    assert not matches_ownership_marker(
        {"name": "mlffbench-aaaaaaaaaaaaaaaa-extra", "remark": ""}
    )


def test_delete_ack_without_provider_readback_is_orphan(tmp_path: Path):
    runner = FakeCompShareCliRunner()
    cli = CompShareCli(runner=runner)
    manager = RunScopedInstanceManager(
        cli,
        ledger_path=tmp_path / "ledger.jsonl",
        orphan_ledger_path=tmp_path / "orphans.jsonl",
    )
    instance_id = manager.get_or_create_instance(
        "run-delete-readback", "img-1", operation_id="op-1"
    )

    def ack_only(_instance_id: str, **_kwargs):
        return {"instance_id": _instance_id, "deleted": True}

    cli.instance_delete = ack_only  # type: ignore[method-assign]
    with pytest.raises(CompShareOrphanError, match="terminate"):
        manager.terminate_run("run-delete-readback")
    assert manager._instances["run-delete-readback"].terminated_at is None
    assert instance_id in runner.instances


def test_zero_orphan_query_is_real_and_unknown_status_is_active(tmp_path: Path):
    runner = FakeCompShareCliRunner()
    cli = CompShareCli(runner=runner)
    name, remark = make_ownership_marker("run-zero")
    runner.instances["inst-zero"] = {
        "id": "inst-zero",
        "instance_id": "inst-zero",
        "name": name,
        "remark": remark,
        "status": "Unknown",
    }
    manager = RunScopedInstanceManager(
        cli,
        ledger_path=tmp_path / "ledger.jsonl",
        orphan_ledger_path=tmp_path / "orphans.jsonl",
    )
    evidence = manager.zero_orphan_query("run-zero")
    assert evidence["method"] == "instance_list(all=True)"
    assert evidence["observed_ids"] == ["inst-zero"]
    assert evidence["active_ids"] == ["inst-zero"]
    assert manager.query_zero_orphans("run-zero") == ["inst-zero"]


def test_cli_instance_list_consumes_pages_and_rejects_malformed():
    calls: list[list[str]] = []
    pages = [
        {"items": [{"id": "i-1"}], "next_page_token": "next-1"},
        {"items": [{"id": "i-2"}]},
    ]

    def runner(argv, _env):
        calls.append(list(argv))
        return CliResult(
            0,
            json.dumps({"ok": True, "schema_version": "1", "data": pages.pop(0)}),
            "",
        )

    cli = CompShareCli(runner=runner)
    assert cli.instance_list(all=True) == [{"id": "i-1"}, {"id": "i-2"}]
    assert "--page-token" in calls[1]
    assert "next-1" in calls[1]

    bad = CompShareCli(
        runner=lambda argv, env: CliResult(
            0,
            json.dumps({"ok": True, "schema_version": "1", "data": {}}),
            "",
        )
    )
    with pytest.raises(CompShareCliJsonError, match="items/instances"):
        bad.instance_list(all=True)
