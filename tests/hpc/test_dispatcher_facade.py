"""HpcDispatcher is the one trusted Agent-facing entry for HPC operations.

Task 1 pins the façade contract: pure composition over GatewayRuntime /
GatewayLease / Gateway, zero new retry, path, quota, or state logic, and a
session that dies cleanly when its lease is closed.
"""

from __future__ import annotations

import pytest

from bench.hpc import (
    DispatcherClosedError,
    DispatcherSession,
    HpcDispatcher,
)

DIGEST = "img@sha256:" + "a" * 64


def _spec(key: str, *, cpus: int = 1) -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": DIGEST,
        "command": ["/bin/echo", "ok"],
        "resources": {"cpus": cpus, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": [],
    }


def test_dispatcher_exposes_one_trusted_entry(tmp_path):
    dispatcher = HpcDispatcher.process_test(tmp_path / "site")
    session = dispatcher.open_run("run-1", workspace=tmp_path / "workspace")
    assert session.run_id == "run-1"
    assert set(session.capabilities()) >= {"adapter", "states"}


def test_dispatcher_close_revokes_lease(tmp_path):
    session = HpcDispatcher.process_test(tmp_path / "site").open_run(
        "run-1", workspace=tmp_path / "workspace"
    )
    session.close()
    with pytest.raises(DispatcherClosedError):
        session.usage()


def test_close_is_idempotent(tmp_path):
    session = HpcDispatcher.process_test(tmp_path / "site").open_run(
        "run-1", workspace=tmp_path / "workspace"
    )
    session.close()
    session.close()


def test_session_type_and_operations_bound_to_run(tmp_path):
    session = HpcDispatcher.process_test(tmp_path / "site").open_run(
        "run-1", workspace=tmp_path / "workspace"
    )
    assert isinstance(session, DispatcherSession)
    # Use a long-running command so cancel always finds a non-terminal job.
    # ``/bin/echo`` can finish before the cancel lands, and cancelling a job
    # that already reached a terminal state is a contract error
    # (test_cancel_after_terminal_raises); this test would otherwise be racy.
    spec = _spec("op-1")
    spec["command"] = ["/bin/sh", "-c", "sleep 5"]
    submitted = session.submit(spec, operation_id="op-1")
    job_id = submitted["job_id"]
    assert session.status(job_id)["state"] in ("PENDING", "RUNNING", "SUCCEEDED")
    assert session.logs(job_id) is not None
    session.cancel(job_id)
    assert "runs" in session.usage() or isinstance(session.usage(), dict)


def test_closed_session_rejects_every_operation(tmp_path):
    session = HpcDispatcher.process_test(tmp_path / "site").open_run(
        "run-1", workspace=tmp_path / "workspace"
    )
    session.close()
    with pytest.raises(DispatcherClosedError):
        session.capabilities()
    with pytest.raises(DispatcherClosedError):
        session.submit(_spec("op-1"), operation_id="op-1")
    with pytest.raises(DispatcherClosedError):
        session.status("job-1")
    with pytest.raises(DispatcherClosedError):
        session.logs("job-1")
    with pytest.raises(DispatcherClosedError):
        session.fetch("job-1")
    with pytest.raises(DispatcherClosedError):
        session.cancel("job-1")
    with pytest.raises(DispatcherClosedError):
        session.usage()
    with pytest.raises(DispatcherClosedError):
        session.settle()


def test_new_run_session_replaces_old_lease(tmp_path):
    dispatcher = HpcDispatcher.process_test(tmp_path / "site")
    first = dispatcher.open_run("run-1", workspace=tmp_path / "workspace")
    second = dispatcher.open_run("run-1", workspace=tmp_path / "workspace")
    first.close()  # replacing the lease already revoked the old token
    with pytest.raises(DispatcherClosedError):
        first.usage()
    second.capabilities()
