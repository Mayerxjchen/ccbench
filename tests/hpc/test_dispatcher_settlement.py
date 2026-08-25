"""Settlement and explicit-fetch evidence semantics.

The run's evidence comes only from the owned lineage: exact ledger job IDs,
explicitly fetched artifacts, and one immutable settlement report. Malicious
remote fixtures (escaping names, destination symlinks, pre-existing files)
must fail closed without corrupting the submission.
"""

from __future__ import annotations

import os
import hashlib
import time
from pathlib import Path

import pytest

from dftworld_bench.hpc.adapters.process_test import ProcessTestAdapter
from dftworld_bench.hpc.dispatcher import (
    DispatcherClosedError,
    HpcDispatcher,
    SettlementError,
    SettlementReport,
)
from dftworld_bench.hpc.audit import GatewayAudit
from dftworld_bench.hpc.gateway import ALL_OPS, Gateway, GatewayError

DIGEST = "img@sha256:" + "a" * 64


def _spec(key: str) -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": DIGEST,
        "command": ["/bin/echo", "ok"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": ["out/*.txt"],
    }


def _session(tmp_path):
    dispatcher = HpcDispatcher.process_test(tmp_path / "site")
    return dispatcher.open_run("run-s", workspace=tmp_path / "ws")


def _await_terminal(adapter, job_id: str) -> str:
    for _ in range(500):
        state = adapter.status(job_id)["state"]
        if state != "RUNNING":
            return state
        time.sleep(0.01)
    return "RUNNING"


class OutputAdapter(ProcessTestAdapter):
    """ProcessTestAdapter whose succeeded jobs report declared outputs."""

    outputs: dict[str, str] = {}

    def fetch(self, job_id):
        base = super().fetch(job_id)
        merged = dict(self.outputs)
        merged.update(base.get("outputs") or {})
        return {**base, "outputs": merged}


def _session_with_outputs(tmp_path, outputs: dict[str, str]):
    adapter = OutputAdapter(tmp_path / "site", timeout_sec=60.0)
    adapter.outputs = outputs
    audit = GatewayAudit(tmp_path / "audit.jsonl")
    gateway = Gateway(adapter, audit=audit, workspace_root=tmp_path / "ws")
    token = gateway.issue("run-s", ALL_OPS)
    from dftworld_bench.hpc.dispatcher import DispatcherSession

    session = DispatcherSession.__new__(DispatcherSession)
    session._lease = type(
        "Lease", (), {
            "run_id": "run-s", "token": token, "gateway": gateway,
            "closed": False,
            "close": lambda self: None,
        },
    )()
    session._settlement = None
    return session, adapter


def test_settlement_report_is_immutable_and_repeatable(tmp_path):
    session = _session(tmp_path)
    first = session.submit(_spec("k1"), operation_id="op-a", attempt=1)
    assert first["duplicate"] is False
    report = session.settle()
    assert isinstance(report, SettlementReport)
    assert [a.operation_id for a in report.attempts] == ["op-a"]
    again = session.settle()
    assert again.digest == report.digest
    assert again.to_dict() == report.to_dict()


def test_settlement_rejects_new_submissions(tmp_path):
    session = _session(tmp_path)
    session.settle()
    with pytest.raises(GatewayError, match="settled"):
        session.submit(_spec("k2"), operation_id="op-b", attempt=1)


def test_settlement_cancels_only_exact_ledger_jobs(tmp_path):
    """A same-user decoy on another backend is never touched by prefix/user."""
    dispatcher = HpcDispatcher.process_test(tmp_path / "site")
    session = dispatcher.open_run("run-c", workspace=tmp_path / "ws")
    decoy_adapter = ProcessTestAdapter(tmp_path / "decoy-site", timeout_sec=60.0)
    decoy_gateway = Gateway(decoy_adapter, workspace_root=tmp_path / "dws")
    decoy_token = decoy_gateway.issue("other-run", ("submit",), ttl_sec=300.0)
    decoy = decoy_gateway.submit(
        decoy_token,
        "other-run",
        {**_spec("decoy"), "command": ["/bin/sleep", "30"]},
        operation_id="op-prefix-match",
    )

    running = session.submit(
        {**_spec("k1"), "command": ["/bin/sleep", "30"]},
        operation_id="long-op",
        attempt=1,
    )
    report = session.settle(cancel_pending=True)
    assert report.cancelled_jobs == (running["job_id"],)
    assert decoy_adapter.status(decoy["job_id"])["state"] == "RUNNING"
    decoy_adapter.cancel(decoy["job_id"])


def test_fetch_publishes_verified_artifacts(tmp_path):
    outputs = {"out/result.txt": "scientific bytes"}
    session, adapter = _session_with_outputs(tmp_path, outputs)
    submitted = session.submit(_spec("k1"), operation_id="op-a", attempt=1)
    assert _await_terminal(adapter, submitted["job_id"]) == "SUCCEEDED"

    manifest = session.fetch_outputs(submitted["job_id"], tmp_path / "fetched")
    assert [(e.path, e.sha256, e.size_bytes) for e in manifest.entries] == [
        ("out/result.txt", hashlib.sha256(b"scientific bytes").hexdigest(),
         len(b"scientific bytes"))
    ]
    published = tmp_path / "fetched" / "out" / "result.txt"
    assert published.read_text() == "scientific bytes"


def test_fetch_refuses_pre_existing_destination_entry(tmp_path):
    outputs = {"out/result.txt": "new bytes"}
    session, adapter = _session_with_outputs(tmp_path, outputs)
    submitted = session.submit(_spec("k1"), operation_id="op-a", attempt=1)
    _await_terminal(adapter, submitted["job_id"])

    target_dir = tmp_path / "fetched"
    (target_dir / "out").mkdir(parents=True)
    keep = target_dir / "out" / "result.txt"
    keep.write_text("original")

    with pytest.raises(SettlementError, match="overwrite"):
        session.fetch_outputs(submitted["job_id"], target_dir)
    assert keep.read_text() == "original"


def test_fetch_refuses_symlinked_destination_directory(tmp_path):
    outputs = {"out/result.txt": "bytes"}
    session, adapter = _session_with_outputs(tmp_path, outputs)
    submitted = session.submit(_spec("k1"), operation_id="op-a", attempt=1)
    _await_terminal(adapter, submitted["job_id"])

    target_dir = tmp_path / "fetched"
    target_dir.mkdir()
    secret = tmp_path / "secret-dir"
    secret.mkdir()
    os_symlink = Path(target_dir / "out")
    os_symlink.symlink_to(secret)

    with pytest.raises(SettlementError, match="symlink"):
        session.fetch_outputs(submitted["job_id"], target_dir)
    assert not (secret / "result.txt").exists()


def test_fetch_rejects_escaping_artifact_names(tmp_path):
    outputs = {
        "../escape.txt": "evil",
        "/etc/passwd-copy": "evil2",
    }
    session, adapter = _session_with_outputs(tmp_path, outputs)
    submitted = session.submit(_spec("k1"), operation_id="op-a", attempt=1)
    _await_terminal(adapter, submitted["job_id"])

    with pytest.raises(SettlementError, match="unsafe artifact"):
        session.fetch_outputs(submitted["job_id"], tmp_path / "fetched2")
    assert not (tmp_path / "escape.txt").exists()
    assert not Path("/etc/passwd-copy").exists()


def test_closed_session_rejects_settlement(tmp_path):
    session = _session(tmp_path)
    session.close()
    with pytest.raises(DispatcherClosedError):
        session.settle()
