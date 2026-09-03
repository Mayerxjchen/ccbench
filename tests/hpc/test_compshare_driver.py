"""Unit and conformance tests for CompShareDriver and CompShareCli."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dftworld_bench.hpc.drivers.base import HpcDriver
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
)


def _cli(stock: int = 4) -> CompShareCli:
    runner = FakeCompShareCliRunner(initial_stock=stock)
    return CompShareCli(runner=runner)


def test_compshare_driver_satisfies_hpc_driver_protocol():
    driver = CompShareDriver(_cli())
    assert isinstance(driver, HpcDriver)


def test_cli_version_and_auth():
    cli = _cli()
    ver = cli.version()
    assert "compshare-cli" in ver

    auth = cli.check_auth()
    assert auth["authenticated"] is True
    assert auth["account"] == "maintainer"


def test_cli_stock_and_instance_lifecycle():
    cli = _cli(stock=2)
    assert cli.check_stock("rtx4090", 1) is True
    assert cli.check_stock("rtx4090", 5) is False

    inst = cli.create_instance(name="test-inst", image_id="img-deepmd-gpu-v1")
    inst_id = inst["instance_id"]
    assert inst["status"] == "RUNNING"

    ready = cli.wait_instance_ready(inst_id)
    assert ready["status"] == "RUNNING"

    stat = cli.get_instance(inst_id)
    assert stat["status"] == "RUNNING"

    assert cli.stop_instance(inst_id) is True
    assert cli.terminate_instance(inst_id) is True


def test_cli_json_error_handling():
    def bad_json_runner(argv, env):
        return CliResult(0, "Not a JSON document", "")

    cli = CompShareCli(runner=bad_json_runner)
    with pytest.raises(CompShareCliJsonError):
        cli.check_auth()


def test_cli_exit_code_error_handling():
    def error_runner(argv, env):
        return CliResult(2, "", "Network connection failed\n")

    cli = CompShareCli(runner=error_runner)
    with pytest.raises(CompShareCliError, match="Network connection failed"):
        cli.check_auth()


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

    # Different run gets a different instance
    inst_other = manager.get_or_create_instance("run-200", "img-deepmd-gpu-v1", operation_id="op-a")
    assert inst_other != inst1


def test_run_scoped_manager_enforces_budget_limits():
    cli = _cli()
    budget = BudgetConfig(max_gpu_hours=0.01, max_cost_usd=0.02)
    manager = RunScopedInstanceManager(cli, budget=budget)

    inst = manager.get_or_create_instance("run-1", "img-deepmd-gpu-v1", operation_id="op-1")
    manager.release_operation("run-1", "op-1", gpu_seconds=7200.0)  # 2 hours

    with pytest.raises(CompShareBudgetExceededError):
        manager.get_or_create_instance("run-1", "img-deepmd-gpu-v1", operation_id="op-2")


def test_run_scoped_manager_orphan_ledger_on_termination_failure(tmp_path: Path):
    cli = _cli()
    orphan_ledger = tmp_path / "orphan-ledger.jsonl"
    manager = RunScopedInstanceManager(cli, orphan_ledger_path=orphan_ledger)

    inst_id = manager.get_or_create_instance("run-1", "img-deepmd-gpu-v1", operation_id="op-1")
    manager.release_operation("run-1", "op-1")

    # Break CLI terminate to simulate failure
    def broken_terminate(iid):
        raise RuntimeError("Cloud provider CLI 500 error on delete")

    cli.terminate_instance = broken_terminate

    with pytest.raises(CompShareOrphanError, match="Failed to terminate instance"):
        manager.terminate_run("run-1")

    assert orphan_ledger.is_file()
    entries = [json.loads(line) for line in orphan_ledger.read_text().splitlines() if line]
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
    inp_file.write_text("dataset content")

    spec = {
        "runtime": "deepmd",
        "command": ["dp", "train", "input.json"],
        "resources": {"cpus": 4, "memory_gb": 16, "gpus": 1, "walltime_minutes": 30},
        "inputs": [{"path": "data.raw", "source_path": str(inp_file), "sha256": "abc", "size_bytes": 15}],
        "outputs": ["model.pb"],
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
    stat = driver.status(job_id)
    assert stat["state"] == "COMPLETED"

    # Check logs
    logs = driver.logs(job_id)
    assert "Executed:" in logs["logs"]

    # Fetch output
    fetched = driver.fetch(job_id)
    assert len(fetched["artifacts"]) == 1
    assert fetched["artifacts"][0]["path"] == "model.pb"
    out_file = tmp_path / "workspace" / "run-alpha" / "train-01" / "att-1" / "model.pb"
    assert out_file.is_file()

    # Settle
    settlement = driver.settle()
    assert "run-alpha" in settlement["settled_runs"]
    assert settlement["active_instances"] == 0
