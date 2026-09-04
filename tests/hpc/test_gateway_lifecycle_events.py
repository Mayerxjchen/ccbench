"""C7: lifecycle evidence comes from real Gateway operations.

The tests use the public Dispatcher/Gateway entry with the local
ProcessTestAdapter.  No event is planted directly into the audit ledger.
"""

from __future__ import annotations

import time
from pathlib import Path

from dftworld_bench.hpc.adapters.process_test import ProcessTestAdapter
from dftworld_bench.hpc.audit import GatewayAudit
from dftworld_bench.hpc.gateway import ALL_OPS, Gateway


DIGEST = "img@sha256:" + "a" * 64


def _spec(key: str, command: list[str]) -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": DIGEST,
        "command": command,
        "resources": {
            "cpus": 1,
            "memory_gb": 1,
            "gpus": 0,
            "walltime_minutes": 5,
        },
        "inputs": [],
        "outputs": [],
    }


def _wait_terminal(gateway: Gateway, token: str, run_id: str, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = gateway.status(token, run_id, job_id)
        if state["state"] in ("SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT", "LOST"):
            return state
        time.sleep(0.01)
    raise AssertionError("ProcessTestAdapter job did not become terminal")


def _events(audit: GatewayAudit) -> list[dict]:
    return [entry["event"] for entry in audit.entries()]


def test_success_lifecycle_events_are_real_durable_and_idempotent(tmp_path: Path):
    run_id = "run-c7-success"
    audit_path = tmp_path / "audit.jsonl"
    adapter = ProcessTestAdapter(tmp_path / "site")
    audit = GatewayAudit(audit_path)
    gateway = Gateway(adapter, audit=audit, workspace_root=tmp_path / "workspace")
    token = gateway.issue(run_id, ALL_OPS)
    spec = _spec("c7-success", ["/bin/sh", "-c", "printf result > result.txt"])

    submitted = gateway.submit(
        token, run_id, spec, operation_id="operation-c7", attempt=1
    )
    job_id = submitted["job_id"]
    _wait_terminal(gateway, token, run_id, job_id)
    first_fetch = gateway.fetch(token, run_id, job_id)
    assert first_fetch["outputs"] == {"result.txt": "result"}

    # Replayed reads must not append a second terminal/fetch assertion.
    gateway.status(token, run_id, job_id)
    gateway.fetch(token, run_id, job_id)
    gateway.trusted_freeze(run_id)
    gateway.trusted_freeze(run_id)
    gateway.trusted_teardown(run_id)
    gateway.trusted_teardown(run_id)

    events = _events(GatewayAudit(audit_path))
    kinds = [event["kind"] for event in events]
    required = [
        "SUBMIT_INTENT",
        "SUBMIT_ACCEPTED",
        "JOB_TERMINAL",
        "ARTIFACT_FETCHED",
        "SETTLEMENT_BEGIN",
        "SETTLEMENT_COMPLETE",
    ]
    assert all(kinds.count(kind) == 1 for kind in required)
    indexes = [kinds.index(kind) for kind in required]
    assert indexes == sorted(indexes)

    accepted = next(event for event in events if event["kind"] == "SUBMIT_ACCEPTED")
    terminal = next(event for event in events if event["kind"] == "JOB_TERMINAL")
    fetched = next(event for event in events if event["kind"] == "ARTIFACT_FETCHED")
    for event in (accepted, terminal, fetched):
        assert (event["run_id"], event["operation_id"], event["attempt"], event["job_id"]) == (
            run_id,
            "operation-c7",
            1,
            job_id,
        )
        assert event["image_id"] == "img"

    # A new Gateway over the same audit adopts the accepted lineage instead of
    # scheduling or appending another SUBMIT_ACCEPTED event.
    restarted_audit = GatewayAudit(audit_path)
    restarted = Gateway(
        adapter,
        audit=restarted_audit,
        workspace_root=tmp_path / "workspace",
    )
    restarted_token = restarted.issue(run_id, ALL_OPS)
    replay = restarted.submit(
        restarted_token,
        run_id,
        spec,
        operation_id="operation-c7",
        attempt=1,
    )
    assert replay == {"job_id": job_id, "duplicate": True}
    assert adapter.physical_submit_count == 1
    assert [event["kind"] for event in _events(GatewayAudit(audit_path))].count(
        "SUBMIT_ACCEPTED"
    ) == 1


def test_cancel_emits_terminal_event_only_after_real_cancel_readback(tmp_path: Path):
    run_id = "run-c7-cancel"
    audit_path = tmp_path / "audit.jsonl"
    adapter = ProcessTestAdapter(tmp_path / "site", timeout_sec=60)
    audit = GatewayAudit(audit_path)
    gateway = Gateway(adapter, audit=audit, workspace_root=tmp_path / "workspace")
    token = gateway.issue(run_id, ALL_OPS)
    spec = _spec("c7-cancel", ["/bin/sleep", "30"])

    submitted = gateway.submit(
        token, run_id, spec, operation_id="operation-cancel", attempt=1
    )
    job_id = submitted["job_id"]
    assert gateway.status(token, run_id, job_id)["state"] == "RUNNING"
    assert gateway.cancel(token, run_id, job_id)["state"] == "CANCELLED"
    assert gateway.status(token, run_id, job_id)["state"] == "CANCELLED"
    gateway.trusted_freeze(run_id)
    gateway.trusted_teardown(run_id)

    events = _events(GatewayAudit(audit_path))
    terminal = [event for event in events if event["kind"] == "JOB_TERMINAL"]
    assert len(terminal) == 1
    assert terminal[0]["job_id"] == job_id
    assert terminal[0]["state"] == "CANCELLED"
    assert not [event for event in events if event["kind"] == "ARTIFACT_FETCHED"]
