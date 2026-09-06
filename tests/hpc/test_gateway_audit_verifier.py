"""C8: qualification verification replays the real Gateway event protocol."""

from __future__ import annotations

import copy
import time
from pathlib import Path

from ccbench.experiments import qualification_receipt as qr
from ccbench.hpc.adapters.process_test import ProcessTestAdapter
from ccbench.hpc.audit import GatewayAudit
from ccbench.hpc.gateway import ALL_OPS, Gateway


DIGEST = "img@sha256:" + "a" * 64


def _spec(key: str, command: list[str]) -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": DIGEST,
        "command": command,
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": [],
    }


def _run_success(tmp_path: Path):
    run_id = "run-c8-success"
    audit = GatewayAudit(tmp_path / "audit.jsonl")
    adapter = ProcessTestAdapter(tmp_path / "site")
    gateway = Gateway(adapter, audit=audit, workspace_root=tmp_path / "workspace")
    token = gateway.issue(run_id, ALL_OPS)
    submitted = gateway.submit(
        token,
        run_id,
        _spec("c8-success", ["/bin/sh", "-c", "printf ok > output.txt"]),
        operation_id="operation-c8",
        attempt=1,
    )
    job_id = submitted["job_id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if gateway.status(token, run_id, job_id)["state"] == "SUCCEEDED":
            break
        time.sleep(0.01)
    gateway.fetch(token, run_id, job_id)
    gateway.trusted_freeze(run_id)
    gateway.trusted_teardown(run_id)
    return audit, {
        "run_id": run_id,
        "operation_id": "operation-c8",
        "attempt": 1,
        "job_id": job_id,
        "runtime_decl": DIGEST,
        "state": "SUCCEEDED",
        "exit_code": 0,
    }


def test_verifier_accepts_real_gateway_protocol_and_partial_order(tmp_path: Path):
    audit, job = _run_success(tmp_path)
    problems: list[tuple[str, str]] = []
    qr._check_audit(
        audit.entries(), job, lambda gate, message: problems.append((gate, message))
    )
    assert problems == []


def test_verifier_rejects_tampered_operation_association(tmp_path: Path):
    audit, job = _run_success(tmp_path)
    entries = copy.deepcopy(audit.entries())
    accepted = next(
        entry for entry in entries if entry["event"].get("kind") == "SUBMIT_ACCEPTED"
    )
    accepted["event"]["operation_id"] = "other-operation"
    problems: list[tuple[str, str]] = []
    qr._check_audit(
        entries, job, lambda gate, message: problems.append((gate, message))
    )
    assert any("SUBMIT_ACCEPTED" in message for _, message in problems)
    assert any("orphan SUBMIT_*" in message for _, message in problems)


def test_verifier_rejects_terminal_before_acceptance(tmp_path: Path):
    audit, job = _run_success(tmp_path)
    entries = copy.deepcopy(audit.entries())
    accepted_index = next(
        index
        for index, entry in enumerate(entries)
        if entry["event"].get("kind") == "SUBMIT_ACCEPTED"
    )
    terminal_index = next(
        index
        for index, entry in enumerate(entries)
        if entry["event"].get("kind") == "JOB_TERMINAL"
    )
    entries[accepted_index], entries[terminal_index] = (
        entries[terminal_index],
        entries[accepted_index],
    )
    problems: list[tuple[str, str]] = []
    qr._check_audit(
        entries, job, lambda gate, message: problems.append((gate, message))
    )
    assert any("precedes SUBMIT_ACCEPTED" in message for _, message in problems)


def test_verifier_cancel_path_uses_real_terminal_event_without_fake_fetch(tmp_path: Path):
    run_id = "run-c8-cancel"
    audit = GatewayAudit(tmp_path / "audit.jsonl")
    adapter = ProcessTestAdapter(tmp_path / "site", timeout_sec=60)
    gateway = Gateway(adapter, audit=audit, workspace_root=tmp_path / "workspace")
    token = gateway.issue(run_id, ALL_OPS)
    submitted = gateway.submit(
        token,
        run_id,
        _spec("c8-cancel", ["/bin/sleep", "30"]),
        operation_id="operation-cancel",
        attempt=1,
    )
    job_id = submitted["job_id"]
    gateway.status(token, run_id, job_id)
    gateway.cancel(token, run_id, job_id)
    gateway.trusted_freeze(run_id)
    gateway.trusted_teardown(run_id)
    job = {
        "run_id": run_id,
        "operation_id": "operation-cancel",
        "attempt": 1,
        "job_id": job_id,
        "runtime_decl": DIGEST,
        "state": "CANCELLED",
        "exit_code": None,
    }
    problems: list[tuple[str, str]] = []
    qr._check_audit(
        audit.entries(), job, lambda gate, message: problems.append((gate, message))
    )
    assert problems == []
