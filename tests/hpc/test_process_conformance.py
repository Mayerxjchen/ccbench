"""ProcessTestAdapter conformance.

The process-test adapter runs jobs as real local subprocesses — no SSH, no
scheduler, no Docker. It exists so the conformance suite can exercise the full
submit → status → logs → fetch → cancel → settlement contract in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.hpc.adapters.process_test import AdapterError, ProcessTestAdapter
from bench.hpc.conformance import run_conformance

DIGEST = "img@sha256:" + "a" * 64


def _spec(key: str, command: list[str], *, outputs: list[str] | None = None) -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": DIGEST,
        "command": command,
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": ["out.txt"] if outputs is None else outputs,
    }


def _submit(adapter: ProcessTestAdapter, key: str, command: list[str], **kw) -> dict:
    spec = _spec(key, command, **kw)
    return adapter.submit(spec, run_id=key, operation_id=f"{key}-op")


def _wait_terminal(adapter: ProcessTestAdapter, job_id: str, deadline: float = 5.0) -> str:
    import time
    end = time.monotonic() + deadline
    state = adapter.status(job_id)["state"]
    while state not in ("SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED") and time.monotonic() < end:
        time.sleep(0.02)
        state = adapter.status(job_id)["state"]
    return state


def test_process_adapter_runs_a_real_job(tmp_path: Path) -> None:
    adapter = ProcessTestAdapter(tmp_path / "runs")
    result = adapter.submit(
        _spec("run-1", ["/bin/sh", "-c", "echo hi > out.txt"]),
        run_id="run-1", operation_id="OP-real-job",
    )
    job_id = result["job_id"]
    state = adapter.status(job_id)["state"]
    assert state in ("QUEUED", "RUNNING", "SUCCEEDED")
    assert adapter.logs(job_id)["stdout"] in ("hi\n", "")
    assert _wait_terminal(adapter, job_id) == "SUCCEEDED"
    assert adapter.fetch(job_id)["files"] == ["out.txt"]


def test_fetch_before_success_raises(tmp_path: Path) -> None:
    adapter = ProcessTestAdapter(tmp_path / "runs")
    result = _submit(adapter, "run-1", ["/bin/sh", "-c", "sleep 5"])
    with pytest.raises(AdapterError):
        adapter.fetch(result["job_id"])
    adapter.cancel(result["job_id"])


def test_cancel_after_terminal_raises(tmp_path: Path) -> None:
    adapter = ProcessTestAdapter(tmp_path / "runs")
    result = _submit(adapter, "run-1", ["/bin/sh", "-c", "sleep 5"])
    adapter.cancel(result["job_id"])
    with pytest.raises(AdapterError):
        adapter.cancel(result["job_id"])


def test_duplicate_submit_returns_original_and_launches_once(tmp_path: Path) -> None:
    adapter = ProcessTestAdapter(tmp_path / "runs")
    first = _submit(adapter, "key-1", ["/bin/sh", "-c", "echo hi > out.txt"])
    second = _submit(adapter, "key-1", ["/bin/sh", "-c", "echo hi > out.txt"])
    assert second["job_id"] == first["job_id"]
    assert second["duplicate"] is True
    assert adapter.usage()["submitted"] == 1


def test_run_isolation_separates_job_directories(tmp_path: Path) -> None:
    adapter = ProcessTestAdapter(tmp_path / "runs")
    a = _submit(adapter, "key-a", ["/bin/sh", "-c", "echo a > out.txt"])
    b = _submit(adapter, "key-b", ["/bin/sh", "-c", "echo b > out.txt"])
    assert a["job_id"] != b["job_id"]
    assert _wait_terminal(adapter, a["job_id"]) == "SUCCEEDED"
    assert _wait_terminal(adapter, b["job_id"]) == "SUCCEEDED"
    assert adapter.fetch(a["job_id"])["outputs"]["out.txt"] == "a\n"
    assert adapter.fetch(b["job_id"])["outputs"]["out.txt"] == "b\n"


def test_timeout_transitions_to_timeout_state(tmp_path: Path) -> None:
    adapter = ProcessTestAdapter(tmp_path / "runs", timeout_sec=0.2)
    result = _submit(adapter, "run-1", ["/bin/sh", "-c", "sleep 5"])
    state = adapter.status(result["job_id"])["state"]
    while state not in ("SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED"):
        state = adapter.status(result["job_id"])["state"]
    assert state == "TIMEOUT"


def test_process_adapter_passes_full_conformance(tmp_path: Path) -> None:
    adapter = ProcessTestAdapter(tmp_path / "runs")
    report = run_conformance(adapter, deadline_sec=5.0)
    assert report.conformant is True
    data = report.to_dict()
    assert data["conformant"] is True
    names = {check["name"] for check in data["checks"]}
    for mandatory in (
        "capabilities", "submit", "status-progress", "logs", "fetch-after-success",
        "idempotency", "run-isolation", "cancel", "fetch-before-success-raises",
        "cancel-after-terminal-raises", "resource-accounting", "settlement",
    ):
        assert mandatory in names, mandatory
    for check in data["checks"]:
        assert check["passed"] is True, check


def test_conformance_fails_when_adapter_breaks_idempotency(tmp_path: Path) -> None:
    class _NonIdempotent(ProcessTestAdapter):
        def __init__(self, root):
            super().__init__(root)
            self._n = 0

        def submit(self, spec, *, run_id: str, operation_id: str):
            spec = dict(spec)
            self._n += 1
            spec["idempotency_key"] = f"{spec['idempotency_key']}-{self._n}"
            return super().submit(spec, run_id=run_id, operation_id=operation_id)

    adapter = _NonIdempotent(tmp_path / "runs")
    report = run_conformance(adapter, deadline_sec=5.0)
    assert report.conformant is False
    by_name = {check.name: check for check in report.checks}
    assert by_name["idempotency"].passed is False


def test_conformance_report_is_machine_readable(tmp_path: Path) -> None:
    adapter = ProcessTestAdapter(tmp_path / "runs")
    report = run_conformance(adapter)
    data = report.to_dict()
    assert data["adapter"] == "process_test"
    assert isinstance(data["checks"], list)
    assert all(set(check) == {"name", "passed", "detail"} for check in data["checks"])
