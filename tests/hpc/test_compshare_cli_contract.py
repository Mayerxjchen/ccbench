"""Official CompShare CLI command contract and response envelope tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dftworld_bench.hpc.drivers.compshare.cli import (
    PINNED_COMPSHARE_CLI_VERSION,
    CliResult,
    CompShareCli,
    CompShareCliCapacityError,
    CompShareCliError,
    CompShareCliJsonError,
    FakeCompShareCliRunner,
)
from dftworld_bench.hpc.drivers.compshare.policy import make_ownership_marker


def test_cli_version_pinned():
    cli = CompShareCli(runner=FakeCompShareCliRunner())
    assert PINNED_COMPSHARE_CLI_VERSION in cli.version()


def test_cli_global_json_flag_enforced():
    """Verify that commands place --json globally and runners enforce it."""
    runner = FakeCompShareCliRunner()
    cli = CompShareCli(runner=runner)

    doc = cli.doctor()
    assert doc.get("auth_ok") is True

    # Bad runner without global --json fails
    bad_runner = lambda argv, env: CliResult(1, '{"ok": false, "error": {"code": "BAD_FLAGS"}}', "error")
    bad_cli = CompShareCli(runner=bad_runner)
    with pytest.raises(CompShareCliError, match="BAD_FLAGS"):
        bad_cli.doctor()


def test_cli_envelope_success_and_error():
    # Success envelope unwraps data
    s_runner = lambda argv, env: CliResult(0, '{"ok": true, "schema_version": "1", "data": {"val": 42}}', "")
    s_cli = CompShareCli(runner=s_runner)
    assert s_cli._exec(["dummy"]) == {"val": 42}

    # Error envelope raises CompShareCliError with code and message
    e_runner = lambda argv, env: CliResult(1, '{"ok": false, "schema_version": "1", "error": {"code": "PERM_DENIED", "message": "unauthorized"}}', "")
    e_cli = CompShareCli(runner=e_runner)
    with pytest.raises(CompShareCliError) as exc_info:
        e_cli._exec(["dummy"])
    assert exc_info.value.code == "PERM_DENIED"

    # Capacity error raises CompShareCliCapacityError
    c_runner = lambda argv, env: CliResult(1, '{"ok": false, "schema_version": "1", "error": {"code": "OUT_OF_STOCK", "message": "no 4090"}}', "")
    c_cli = CompShareCli(runner=c_runner)
    with pytest.raises(CompShareCliCapacityError):
        c_cli._exec(["dummy"])


def test_cli_invalid_json_envelope_fails_closed():
    bad_json_runner = lambda argv, env: CliResult(0, "not a json string", "")
    cli = CompShareCli(runner=bad_json_runner)
    with pytest.raises(CompShareCliJsonError):
        cli._exec(["dummy"])


def test_official_commands_end_to_end_lifecycle(tmp_path: Path):
    runner = FakeCompShareCliRunner(initial_stock=2)
    cli = CompShareCli(runner=runner)

    # 1. Doctor
    doc = cli.doctor()
    assert doc["auth_ok"] is True

    # 2. Instance Search
    stocks = cli.instance_search(gpu="4090")
    assert len(stocks) == 1
    assert stocks[0]["available_count"] == 2

    # 3. Instance Create
    name, remark = make_ownership_marker("cli-contract-run")
    create_res = cli.instance_create(
        image="img-deepmd-gpu-v1", gpu="4090", name=name, remark=remark
    )
    inst_id = create_res["instance_id"]
    assert inst_id == "inst-0001"

    # 4. Instance Show
    info = cli.instance_show(inst_id)
    assert info["status"] == "Running"

    # 5. Instance CP (upload)
    local_file = tmp_path / "in.txt"
    local_file.write_text("input data")
    cli.instance_cp(inst_id, str(local_file), ":/remote/in.txt")
    assert runner.files[inst_id]["/remote/in.txt"] == b"input data"

    # 6. Job Submit, Show, Logs
    job_res = cli.instance_job_submit(inst_id, ["python", "-c", "print('hello')"])
    job_id = job_res["job_id"]
    job_info = cli.instance_job_show(inst_id, job_id)
    assert job_info["status"] == "COMPLETED"
    logs = cli.instance_job_logs(inst_id, job_id)
    assert "Executed" in logs

    # 7. Job Cancel
    cancel_res = cli.instance_job_cancel(inst_id, job_id)
    assert cancel_res["status"] == "CANCELLED"

    # 8. Instance CP (download)
    out_file = tmp_path / "out.txt"
    runner.files[inst_id]["/remote/out.txt"] = b"output content"
    cli.instance_cp(inst_id, ":/remote/out.txt", str(out_file))
    assert out_file.read_text() == "output content"

    # 9. Stop & Delete
    stop_res = cli.instance_stop(inst_id)
    assert stop_res["status"] == "Stopped"

    del_res = cli.instance_delete(inst_id)
    assert del_res["deleted"] is True
    assert runner.stock == 2
