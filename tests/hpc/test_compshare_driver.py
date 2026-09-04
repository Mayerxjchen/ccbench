"""Unit and conformance tests for CompShareDriver and CompShareCli."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

import pytest

from dftworld_bench.hpc.drivers.base import HpcDriver, HpcDriverError
from dftworld_bench.hpc.drivers.compshare import (
    BudgetConfig,
    CliResult,
    CompShareBudgetExceededError,
    CompShareCli,
    CompShareCliCapacityError,
    CompShareCliError,
    CompShareCliJsonError,
    CompShareDriver,
    CompShareDriverError,
    CompShareManagerError,
    CompShareOrphanError,
    FakeCompShareCliRunner,
    RunScopedInstanceManager,
    make_ownership_marker,
)


def _cli(stock: int = 4) -> CompShareCli:
    runner = FakeCompShareCliRunner(initial_stock=stock)
    return CompShareCli(runner=runner)


def _mock_deepmd_runtime():
    from dftworld_bench.hpc.runtime_resolution import ResolvedRuntime, RuntimeStatus
    return ResolvedRuntime(
        capability="deepmd",
        artifact_kind="compshare_image",
        artifact_path_or_id="img-deepmd-gpu-v1",
        digest="sha256:" + "b" * 64,
        status=RuntimeStatus.QUALIFIED,
        qualification_verified=True,
        provider="compshare",
    )


def test_compshare_driver_satisfies_hpc_driver_protocol(tmp_path):
    driver = CompShareDriver(_cli(), workspace_root=str(tmp_path))
    assert isinstance(driver, HpcDriver)


def test_cli_version_and_doctor():
    cli = _cli()
    ver = cli.version()
    assert "compshare-cli" in ver

    doc = cli.doctor()
    assert doc["auth_ok"] is True
    assert doc["profile"] == "default"


def test_cli_stock_and_instance_lifecycle():
    cli = _cli(stock=2)
    stocks = cli.instance_search(gpu="4090")
    assert len(stocks) == 1
    assert stocks[0]["available_count"] == 2

    # Insufficient stock test
    cli_empty = _cli(stock=0)
    assert cli_empty.instance_search(gpu="4090") == []

    name, remark = make_ownership_marker("cli-lifecycle-run")
    inst = cli.instance_create(
        image="img-deepmd-gpu-v1", gpu="4090", name=name, remark=remark
    )
    inst_id = inst["instance_id"]
    assert inst["status"] == "Running"

    ready = cli.wait_instance_ready(inst_id)
    assert ready["status"] == "Running"

    stat = cli.instance_show(inst_id)
    assert stat["status"] == "Running"

    stop_res = cli.instance_stop(inst_id)
    assert stop_res["status"] == "Stopped"

    del_res = cli.instance_delete(inst_id)
    assert del_res["deleted"] is True


def test_cli_json_error_handling():
    def bad_json_runner(argv, env):
        return CliResult(0, "Not a JSON document", "")

    cli = CompShareCli(runner=bad_json_runner)
    with pytest.raises(CompShareCliJsonError):
        cli.doctor()


def test_cli_exit_code_error_handling():
    def error_runner(argv, env):
        return CliResult(2, "", "Network connection failed\n")

    cli = CompShareCli(runner=error_runner)
    with pytest.raises(CompShareCliError, match="Network connection failed"):
        cli.doctor()


def test_run_scoped_manager_single_instance_and_reuse(tmp_path: Path):
    cli = _cli()
    manager = RunScopedInstanceManager(cli, ledger_path=tmp_path / "ledger.jsonl")

    # Op 1 creates instance
    inst1 = manager.get_or_create_instance("run-100", "img-deepmd-gpu-v1", operation_id="op-1")
    assert inst1.startswith("inst-")

    # Concurrent op on same run fails closed
    with pytest.raises(CompShareManagerError, match="already has an active GPU operation"):
        manager.get_or_create_instance("run-100", "img-deepmd-gpu-v1", operation_id="op-2")

    # Release Op 1
    manager.release_operation("run-100", "op-1", gpu_seconds=60.0)

    # Op 2 reuses the same instance
    inst2 = manager.get_or_create_instance("run-100", "img-deepmd-gpu-v1", operation_id="op-2")
    assert inst2 == inst1

    # Terminate run cleanly
    assert manager.terminate_run("run-100") is True

    # After termination, instance is inactive
    usage = manager.get_usage("run-100")
    assert usage["has_active_instance"] is False


def test_run_scoped_manager_budget_limits(tmp_path: Path):
    cli = _cli()
    tight_budget = BudgetConfig(max_gpu_hours=0.01, max_cost_cny=0.05, hourly_rate_cny=10.0)
    manager = RunScopedInstanceManager(cli, budget=tight_budget, ledger_path=tmp_path / "ledger.jsonl")

    manager.get_or_create_instance("run-budget", "img-deepmd-gpu-v1", operation_id="op-1")
    manager.release_operation("run-budget", "op-1", gpu_seconds=3600.0)

    with pytest.raises(CompShareBudgetExceededError):
        manager.get_or_create_instance("run-budget", "img-deepmd-gpu-v1", operation_id="op-2")

    manager.terminate_run("run-budget")


def test_orphan_instance_logging(tmp_path: Path):
    base_runner = FakeCompShareCliRunner()

    def failing_runner(argv, env):
        if "delete" in argv:
            return CliResult(1, '{"ok": false, "schema_version": "1", "error": {"code": "TIMEOUT", "message": "deletion failed"}}', "deletion failed")
        return base_runner(argv, env)

    cli = CompShareCli(runner=failing_runner)
    orphan_ledger = tmp_path / "orphan-ledger.jsonl"
    manager = RunScopedInstanceManager(
        cli, ledger_path=tmp_path / "ledger.jsonl", orphan_ledger_path=orphan_ledger
    )

    inst_id = manager.get_or_create_instance("run-1", "img-deepmd-gpu-v1", operation_id="op-1")
    manager.release_operation("run-1", "op-1")

    with pytest.raises(CompShareOrphanError, match="Failed to terminate"):
        manager.terminate_run("run-1")

    assert orphan_ledger.is_file()
    entries = [json.loads(line) for line in orphan_ledger.read_text().strip().split("\n")]
    assert len(entries) == 1
    assert entries[0]["instance_id"] == inst_id
    assert entries[0]["run_id"] == "run-1"


def test_driver_submit_status_fetch_settle(tmp_path: Path):
    cli = _cli()
    driver = CompShareDriver(cli, workspace_root=str(tmp_path / "workspace"))

    caps = driver.capabilities()
    assert caps["scheduler"] == "compshare"
    assert caps["compute_classes"] == ["gpu"]

    # Input file
    inp_file = tmp_path / "data.raw"
    inp_content = b"dataset content"
    inp_file.write_bytes(inp_content)
    inp_sha = hashlib.sha256(inp_content).hexdigest()

    spec = {
        "runtime": "deepmd",
        "_resolved_runtime": _mock_deepmd_runtime(),
        "command": ["dp", "train", "input.json"],
        "resources": {"cpus": 4, "memory_gb": 16, "gpus": 1, "walltime_minutes": 30},
        "inputs": [{"path": "data.raw", "source_path": str(inp_file), "sha256": inp_sha, "size_bytes": len(inp_content)}],
        "outputs": [{"path": "model.pb"}],
    }

    # Submit
    res = driver.submit(spec, run_id="run-alpha", operation_id="train-01", attempt=1)
    job_id = res["job_id"]
    assert res["duplicate"] is False

    # Duplicate submit returns duplicate
    res_dup = driver.submit(spec, run_id="run-alpha", operation_id="train-01", attempt=1)
    assert res_dup["job_id"] == job_id
    assert res_dup["duplicate"] is True

    # Check status
    stat = driver.status("run-alpha", job_id)
    assert stat["state"] == "SUCCEEDED"

    # Check logs
    logs = driver.logs("run-alpha", job_id)
    assert "Executed:" in logs["logs"]

    # Fetch output
    fetched = driver.fetch("run-alpha", job_id)
    assert "model.pb" in fetched["outputs"]

    # Settle
    assert driver.settle("run-alpha") is True
    usage = driver.usage()
    assert usage["active_instances"] == 0


def test_driver_rejects_cpu_runtime_fail_closed(tmp_path: Path):
    cli = _cli()
    driver = CompShareDriver(cli, workspace_root=str(tmp_path / "workspace"))

    spec = {
        "runtime": "cp2k",  # CPU runtime
        "command": ["cp2k", "-i", "in.inp"],
        "resources": {"cpus": 4, "memory_gb": 16, "gpus": 0, "walltime_minutes": 30},
        "inputs": [],
        "outputs": [],
    }
    with pytest.raises(HpcDriverError, match="CompShareDriver only accepts GPU runtimes"):
        driver.submit(spec, run_id="run-beta", operation_id="cp2k-01", attempt=1)


def test_driver_fetch_schema_string_outputs_and_security(tmp_path: Path):
    runner = FakeCompShareCliRunner(initial_stock=1)
    cli = CompShareCli(runner=runner)
    driver = CompShareDriver(cli, workspace_root=str(tmp_path / "workspace"))

    spec = {
        "runtime": "deepmd",
        "_resolved_runtime": _mock_deepmd_runtime(),
        "command": ["python", "train.py"],
        "resources": {"cpus": 4, "memory_gb": 16, "gpus": 1, "walltime_minutes": 30},
        "inputs": [],
        "outputs": ["model.pb"],  # ExecutionRequest schema: array of strings!
    }
    res = driver.submit(spec, run_id="run-fetch", operation_id="op-1")
    job_id = res["job_id"]
    job = driver._jobs[job_id]

    # Pre-populate remote file
    runner.files[job.instance_id][f"{job.remote_workdir}/model.pb"] = b"binary model bytes\x00\x01"

    fetched = driver.fetch("run-fetch", job_id)
    assert "model.pb" in fetched["outputs"]
    assert fetched["outputs"]["model.pb"] == b"binary model bytes\x00\x01"
    meta = fetched["artifacts"]["model.pb"]
    assert meta["size_bytes"] == 20
    assert meta["sha256"].startswith("sha256:")
    # Verify local file binary bytes intact
    local_path = Path(meta["path"])
    assert local_path.read_bytes() == b"binary model bytes\x00\x01"

    # Verify security: traversal attack rejected
    job.declared_outputs = ["../../etc/passwd"]
    with pytest.raises(HpcDriverError, match="Forbidden traversal"):
        driver.fetch("run-fetch", job_id)

    # Verify security: absolute path rejected
    job.declared_outputs = ["/etc/shadow"]
    with pytest.raises(HpcDriverError, match="Forbidden traversal"):
        driver.fetch("run-fetch", job_id)


def test_driver_cancel_fails_closed_when_remote_fails(tmp_path: Path, monkeypatch):
    runner = FakeCompShareCliRunner(initial_stock=1)
    cli = CompShareCli(runner=runner)
    driver = CompShareDriver(cli, workspace_root=str(tmp_path / "workspace"))

    spec = {
        "runtime": "deepmd",
        "_resolved_runtime": _mock_deepmd_runtime(),
        "command": ["python", "train.py"],
        "resources": {"cpus": 4, "memory_gb": 16, "gpus": 1, "walltime_minutes": 30},
        "inputs": [],
        "outputs": [],
    }
    res = driver.submit(spec, run_id="run-cancel", operation_id="op-1")
    job_id = res["job_id"]

    # Mock remote cancel to fail
    def fail_cancel(*a, **kw):
        raise RuntimeError("Cloud network failure")

    monkeypatch.setattr(cli, "instance_job_cancel", fail_cancel)

    with pytest.raises(HpcDriverError, match="Remote job cancellation failed"):
        driver.cancel(job_id)

    # Ensure job state was NOT set to CANCELLED locally
    assert driver._jobs[job_id].state != "CANCELLED"


def test_driver_status_fails_closed_on_cloud_query_error(tmp_path: Path, monkeypatch):
    runner = FakeCompShareCliRunner(initial_stock=1)
    cli = CompShareCli(runner=runner)
    driver = CompShareDriver(cli, workspace_root=str(tmp_path / "workspace"))

    spec = {
        "runtime": "deepmd",
        "_resolved_runtime": _mock_deepmd_runtime(),
        "command": ["python", "train.py"],
        "resources": {"cpus": 4, "memory_gb": 16, "gpus": 1, "walltime_minutes": 30},
        "inputs": [],
        "outputs": [],
    }
    res = driver.submit(spec, run_id="run-status", operation_id="op-1")
    job_id = res["job_id"]

    def fail_show(*a, **kw):
        raise RuntimeError("Cloud status query failed")

    monkeypatch.setattr(cli, "instance_job_show", fail_show)

    with pytest.raises(HpcDriverError, match="Error querying cloud status"):
        driver.status("run-status", job_id)


def test_instance_manager_corrupt_ledger_fails_closed(tmp_path: Path):
    from dftworld_bench.hpc.drivers.compshare.instance_manager import (
        CompShareManagerError,
        RunScopedInstanceManager,
    )
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("CORRUPTED_NON_JSON_LINE\n")

    with pytest.raises(CompShareManagerError, match="is corrupt; refusing to start"):
        RunScopedInstanceManager(cli=_cli(), ledger_path=ledger)


def test_gateway_freeze_settle_failure_raises(tmp_path: Path):
    from dftworld_bench.hpc.gateway import Gateway, GatewayError

    class FailingAdapter:
        def settle(self, run_id: str) -> bool:
            return False

    gw = Gateway(adapter=FailingAdapter())
    tok = gw.issue("run-fail", ["usage"], ttl_sec=60.0)
    gw.freeze(tok, "run-fail")  # freeze submissions only
    with pytest.raises(GatewayError, match="Adapter settlement reported failure"):
        gw.teardown_resources(tok, "run-fail")


def test_gateway_freeze_settle_failure_allows_retry(tmp_path: Path):
    from dftworld_bench.hpc.gateway import Gateway, GatewayError

    calls = 0
    succeed_on_retry = False

    class FlakyAdapter:
        def settle(self, run_id: str) -> bool:
            nonlocal calls
            calls += 1
            return succeed_on_retry

    gw = Gateway(adapter=FlakyAdapter())
    tok = gw.issue("run-retry", ["usage"], ttl_sec=60.0)

    # Freeze submissions (always succeeds)
    gw.freeze(tok, "run-retry")

    # First teardown fails
    with pytest.raises(GatewayError, match="Adapter settlement reported failure"):
        gw.teardown_resources(tok, "run-retry")
    assert calls == 1
    assert gw._settlement_states["run-retry"] in ("SETTLEMENT_FAILED", "TEARDOWN_FAILED")

    # Second teardown retries!
    succeed_on_retry = True
    gw.teardown_resources(tok, "run-retry")
    assert calls == 2
    assert gw._settlement_states["run-retry"] == "SETTLED"

    # Third teardown does not call settle again (already SETTLED)
    gw.teardown_resources(tok, "run-retry")
    assert calls == 2


def test_instance_create_double_failure_records_to_orphan_ledger(tmp_path: Path, monkeypatch):
    from dftworld_bench.hpc.drivers.compshare.instance_manager import RunScopedInstanceManager
    runner = FakeCompShareCliRunner(initial_stock=1)
    cli = CompShareCli(runner=runner)
    orphan_log = tmp_path / "orphan-ledger.jsonl"
    mgr = RunScopedInstanceManager(
        cli,
        orphan_ledger_path=orphan_log,
        ledger_path=tmp_path / "ledger.jsonl",
    )

    # Force wait_instance_ready to fail
    def fail_wait(inst_id, **kw):
        raise RuntimeError("Ready timeout")

    # Force instance_delete to also fail
    def fail_delete(inst_id, **kw):
        raise RuntimeError("Cloud delete network error")

    monkeypatch.setattr(cli, "wait_instance_ready", fail_wait)
    monkeypatch.setattr(cli, "instance_delete", fail_delete)

    with pytest.raises(RuntimeError, match="Ready timeout"):
        mgr.get_or_create_instance("run-orphan", "img-test", operation_id="op-1")

    # Verify orphan ledger was created and contains the orphan record without TypeError
    assert orphan_log.is_file()
    lines = orphan_log.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["run_id"] == "run-orphan"
    assert rec["instance_id"].startswith("inst-")
    assert "rollback deletion failed" in rec["reason"]


def test_instance_create_executes_dry_run(tmp_path: Path, monkeypatch):
    from dftworld_bench.hpc.drivers.compshare.instance_manager import RunScopedInstanceManager
    runner = FakeCompShareCliRunner(initial_stock=1)
    cli = CompShareCli(runner=runner)
    calls: list[bool] = []
    orig_create = cli.instance_create

    def spy_create(**kw):
        calls.append(kw.get("provider_dry_run", False))
        return orig_create(**kw)

    monkeypatch.setattr(cli, "instance_create", spy_create)
    mgr = RunScopedInstanceManager(
        cli,
        orphan_ledger_path=tmp_path / "orphan.jsonl",
        ledger_path=tmp_path / "ledger.jsonl",
    )
    mgr.get_or_create_instance("run-dry", "img-test", operation_id="op-1")
    assert calls == [True, False]


def test_job_journal_corrupt_fails_closed(tmp_path: Path):
    runner = FakeCompShareCliRunner(initial_stock=1)
    cli = CompShareCli(runner=runner)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "compshare_jobs.jsonl").write_text("CORRUPTED\n")
    with pytest.raises(HpcDriverError, match="is corrupt; refusing to start"):
        CompShareDriver(cli, workspace_root=str(ws))


def test_routed_driver_requires_compute_class(tmp_path: Path):
    from unittest.mock import MagicMock
    from dftworld_bench.hpc.compute_profile import ComputeProfile, ComputeRouter
    from dftworld_bench.hpc.drivers.routed import RoutedDriver

    prof = ComputeProfile.from_dict({
        "schema_version": 1,
        "profile_id": "p",
        "routes": {"cpu": {"site_profile": "s-cpu"}, "gpu": {"site_profile": "s-gpu"}},
    })
    site_cpu = MagicMock(site_id="s-cpu", scheduler="slurm")
    site_gpu = MagicMock(site_id="s-gpu", scheduler="compshare")
    router = ComputeRouter(prof, site_profiles={"s-cpu": site_cpu, "s-gpu": site_gpu})
    routed = RoutedDriver(
        router=router,
        drivers={"slurm": MagicMock(), "compshare": MagicMock()},
    )
    with pytest.raises(HpcDriverError, match="missing mandatory 'compute_class'"):
        routed.submit({}, run_id="r", operation_id="op")


def test_gateway_expired_token_trusted_teardown_succeeds(tmp_path: Path):
    """Gate A1: Teardown must succeed even if client token expired."""
    from dftworld_bench.hpc.gateway import Gateway, GatewayError

    class SuccessfulAdapter:
        def __init__(self):
            self.settled_runs = []

        def settle(self, run_id: str) -> bool:
            self.settled_runs.append(run_id)
            return True

    adapter = SuccessfulAdapter()
    now_time = 1000.0
    gw = Gateway(adapter=adapter, now=lambda: now_time)
    tok = gw.issue("run-exp", ["usage"], ttl_sec=10.0)

    # Fast-forward time past token expiration
    now_time = 1020.0

    # Submit or normal usage with expired token fails
    with pytest.raises(GatewayError, match="token expired"):
        gw.status(tok, "run-exp", "job-1")

    # But freeze and teardown_resources succeed!
    gw.freeze(tok, "run-exp")
    gw.teardown_resources(tok, "run-exp")
    assert "run-exp" in adapter.settled_runs
    assert gw._settlement_states["run-exp"] == "SETTLED"


def test_instance_manager_injects_mlffbench_ownership_marker(tmp_path: Path):
    """Gate A1: CompShare instances must be tagged with mlffbench ownership marker."""
    runner = FakeCompShareCliRunner(initial_stock=2)
    cli = CompShareCli(runner=runner)
    mgr = RunScopedInstanceManager(
        cli,
        ledger_path=tmp_path / "ledger.jsonl",
        orphan_ledger_path=tmp_path / "orphans.jsonl",
    )
    inst_id = mgr.get_or_create_instance("run-marker-1", "img-1", operation_id="op-1")
    rec = runner.instances[inst_id]
    expected_name, expected_remark = make_ownership_marker("run-marker-1")
    assert rec["name"] == expected_name
    assert rec["remark"] == expected_remark


def test_instance_manager_durable_teardown_recovery(tmp_path: Path):
    """Gate A1: Durable teardown recovery terminates dangling instances from prior crashes."""
    runner = FakeCompShareCliRunner(initial_stock=2)
    cli = CompShareCli(runner=runner)
    ledger_path = tmp_path / "ledger.jsonl"

    # Simulate prior run that created an instance but crashed before settlement
    name, remark = make_ownership_marker("run-crashed")
    inst_doc = cli.instance_create(image="img-1", name=name, remark=remark)
    dangling_id = inst_doc["instance_id"]
    ledger_entry = {
        "run_id": "run-crashed",
        "instance_id": dangling_id,
        "image_id": "img-1",
        "gpu_type": "4090",
        "gpu_count": 1,
        "created_at": time.time(),
        "terminated_at": None,
        "active_operation_id": "op-crashed",
        "accumulated_gpu_seconds": 100.0,
    }
    ledger_path.write_text(json.dumps(ledger_entry) + "\n")

    mgr = RunScopedInstanceManager(
        cli,
        ledger_path=ledger_path,
        orphan_ledger_path=tmp_path / "orphans.jsonl",
    )
    assert mgr._instances["run-crashed"].terminated_at is None

    # Perform durable teardown recovery
    recovered = mgr.recover_dangling_instances()
    assert dangling_id in recovered
    assert mgr._instances["run-crashed"].terminated_at is not None
    assert dangling_id not in runner.instances  # deleted on cloud!


def test_instance_manager_reconcile_and_recover_full(tmp_path: Path):
    """P3: Explicit reconcile_and_recover returns clean report and cleans dangling instances."""
    runner = FakeCompShareCliRunner(initial_stock=2)
    cli = CompShareCli(runner=runner)
    ledger_path = tmp_path / "ledger.jsonl"

    name, remark = make_ownership_marker("run-c1")
    inst_doc = cli.instance_create(image="img-1", name=name, remark=remark)
    dangling_id = inst_doc["instance_id"]
    ledger_entry = {
        "run_id": "run-c1",
        "instance_id": dangling_id,
        "image_id": "img-1",
        "gpu_type": "4090",
        "gpu_count": 1,
        "created_at": time.time(),
        "terminated_at": None,
    }
    ledger_path.write_text(json.dumps(ledger_entry) + "\n")

    mgr = RunScopedInstanceManager(
        cli,
        ledger_path=ledger_path,
        orphan_ledger_path=tmp_path / "orphans.jsonl",
    )
    # Pure local constructor: instance remains non-terminated initially
    assert mgr._instances["run-c1"].terminated_at is None

    report = mgr.reconcile_and_recover()
    assert report.clean is True
    assert report.still_active == []
    assert dangling_id in report.recovered_instances
    assert len(report.failed_instances) == 0
    assert dangling_id not in runner.instances


def test_instance_manager_reconcile_and_recover_fail_closed_on_error(tmp_path: Path, monkeypatch):
    """P3: Deletion failure during recovery fails closed and records to orphan ledger."""
    runner = FakeCompShareCliRunner(initial_stock=2)
    cli = CompShareCli(runner=runner)
    ledger_path = tmp_path / "ledger.jsonl"
    orphan_path = tmp_path / "orphans.jsonl"

    name, remark = make_ownership_marker("run-fail")
    inst_doc = cli.instance_create(image="img-1", name=name, remark=remark)
    dangling_id = inst_doc["instance_id"]
    ledger_entry = {
        "run_id": "run-fail",
        "instance_id": dangling_id,
        "image_id": "img-1",
        "gpu_type": "4090",
        "gpu_count": 1,
        "created_at": time.time(),
        "terminated_at": None,
    }
    ledger_path.write_text(json.dumps(ledger_entry) + "\n")

    mgr = RunScopedInstanceManager(
        cli,
        ledger_path=ledger_path,
        orphan_ledger_path=orphan_path,
    )

    # Force delete to fail
    def fail_delete(inst_id, **kw):
        raise RuntimeError("Cloud delete failed")

    monkeypatch.setattr(cli, "instance_delete", fail_delete)

    report = mgr.reconcile_and_recover()
    assert report.clean is False
    assert dangling_id in report.failed_instances
    assert orphan_path.is_file()
    assert dangling_id in orphan_path.read_text()
