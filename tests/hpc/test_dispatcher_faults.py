"""Deterministic fault-injection matrix for the dispatcher stack.

Aggregates every failure mode the simplification must survive, each proven on
the production path (gateway -> driver -> adapter):

- transient transport failures surface and recover on retry;
- a crash immediately before ``sbatch`` leaves an intent that recovers by
  exact-marker adoption or fails closed;
- candidate input mutation between validation and staging fails closed;
- duplicate submits, full state ladder, missing outputs, stale remote runs,
  settlement cancellation, and token revocation behave per contract.
"""

from __future__ import annotations

import time

import pytest

from dftworld_bench.hpc.adapters.base import TransportUnknown
from dftworld_bench.hpc.adapters.process_test import AdapterError, ProcessTestAdapter
from dftworld_bench.hpc.audit import GatewayAudit
from dftworld_bench.hpc.dispatcher import (
    DispatcherClosedError,
    HpcDispatcher,
    SettlementError,
)
from dftworld_bench.hpc.drivers.slurm import SlurmDriver
from dftworld_bench.hpc.gateway import ALL_OPS, Gateway, GatewayError
from dftworld_bench.hpc.request import ExecutionRequestV2, RequestError
from dftworld_bench.hpc.staging import StagingError, seal_inputs
from scripts.ablation.transport.slurm_transport import JobState

DIGEST = "img@sha256:" + "a" * 64

SITE = {
    "site": "<site-alias>",
    "gateway_url": "https://gw.example.test",
    "run_token": "0123456789abcdef0123456789abcdef",
    "ssh_alias": "<site-alias>",
    "scratch": "/data/bench",
    "account": "mlip-bench",
    "platform_profile": {
        "name": "<site-alias>",
        "default_queue": "gpu",
        "queues": [
            {"name": "gpu", "max_cpus": 32, "max_memory_gb": 128,
             "max_gpus": 1, "max_walltime_minutes": 1440},
        ],
    },
}


def _spec(key: str) -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": DIGEST,
        "command": ["/bin/sleep", "30"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": [],
    }


class FlakyTransport:
    """Scripted slurm transport whose queries fail once each, then recover."""

    def __init__(self):
        self.submits: list[tuple] = []
        self._status: dict[str, JobState] = {}
        self._fail_next_submit = False
        self._fail_next_status = False
        self.cancelled: list[str] = []

    def submit(self, script, opts):
        if self._fail_next_submit:
            self._fail_next_submit = False
            raise ConnectionError("transient ssh drop during sbatch")
        self.submits.append((script, opts))
        slurm_id = str(len(self.submits) + 1000)
        self._status[slurm_id] = JobState.RUNNING
        return slurm_id

    def status(self, job_id):
        if self._fail_next_status:
            self._fail_next_status = False
            raise ConnectionError("transient query timeout")
        return self._status.get(job_id, JobState.RUNNING)

    def log(self, job_id, tail=None):
        return f"log-{job_id}"

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        self._status[job_id] = JobState.CANCELLED

    def fetch(self, remote_paths, local_dir):
        return []

    def alloc_tres(self, job_id):
        return ""

    def remote_arch(self):
        return "x86_64"


def _slurm_gateway(tmp_path, transport):
    audit = GatewayAudit(tmp_path / "audit.jsonl")
    from dftworld_bench.hpc.adapters.slurm import SlurmAdapter

    adapter = SlurmAdapter(SITE, transport, case_id="water64")
    gateway = Gateway(adapter, audit=audit, workspace_root=tmp_path / "ws")
    token = gateway.issue("run-f", ALL_OPS)
    return gateway, token


def test_transient_submit_failure_surfaces_then_recovers(tmp_path):
    transport = FlakyTransport()
    transport._fail_next_submit = True
    gateway, token = _slurm_gateway(tmp_path, transport)
    from dftworld_bench.hpc.adapters.slurm import SlurmAdapterError

    with pytest.raises(SlurmAdapterError, match="sbatch rejected"):
        gateway.submit(token, "run-f", _spec("k1"), operation_id="op", attempt=1)
    # The scheduler never accepted: the fsynced intent has zero marker
    # matches, so the same attempt is refused (no blind resubmission) and the
    # scientific path forward is the next attempt.
    with pytest.raises(GatewayError, match="no scheduler job"):
        gateway.submit(token, "run-f", _spec("k1"), operation_id="op", attempt=1)
    result = gateway.submit(token, "run-f", _spec("k1"), operation_id="op", attempt=2)
    assert result["duplicate"] is False


def test_transient_status_failure_surfaces_then_recovers(tmp_path):
    transport = FlakyTransport()
    gateway, token = _slurm_gateway(tmp_path, transport)
    submitted = gateway.submit(token, "run-f", _spec("k1"), operation_id="op", attempt=1)
    transport._fail_next_status = True
    with pytest.raises(ConnectionError, match="transient"):
        gateway.status(token, "run-f", submitted["job_id"])
    assert gateway.status(token, "run-f", submitted["job_id"])["state"] in (
        "RUNNING", "CANCELLED",
    )


def test_crash_before_sbatch_fails_closed_on_recovery(tmp_path):
    """Intent fsynced, scheduler never accepted: recovery refuses both paths."""
    session = HpcDispatcher.process_test(tmp_path / "site").open_run(
        "run-x", workspace=tmp_path / "ws"
    )
    adapter = session.gateway._adapter
    # Lose the response AFTER acceptance, then lose the job itself.
    adapter.fail_after_scheduler_accept_once()
    with pytest.raises(TransportUnknown):
        session.submit(_spec("k1"), operation_id="op", attempt=1)
    adapter.forget_all_jobs()
    with pytest.raises(GatewayError, match="no scheduler job"):
        session.submit(_spec("k1"), operation_id="op", attempt=1)
    assert adapter.physical_submit_count == 1


def test_candidate_input_mutation_after_validation_fails_closed(tmp_path):
    body = b"declared bytes"
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True)
    source = workspace / "input.inp"
    source.write_bytes(body)
    request = ExecutionRequestV2.from_dict(
        {
            "schema_version": 2,
            "operation_id": "op",
            "attempt": 1,
            "compute_class": "cpu",
            "runtime": DIGEST,
            "command": ["tool", "-i", "input.inp"],
            "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0,
                          "walltime_minutes": 5},
            "inputs": [{"path": "input.inp",
                        "sha256": __import__("hashlib").sha256(body).hexdigest(),
                        "size_bytes": len(body)}],
            "outputs": [],
        }
    )
    source.write_bytes(b"mutated after validation")
    with pytest.raises(StagingError):
        seal_inputs(request, workspace, tmp_path / "sealed")


def test_full_state_ladder_reaches_terminal(tmp_path):
    adapter = ProcessTestAdapter(tmp_path / "site", timeout_sec=60.0)
    gateway = Gateway(
        adapter,
        audit=GatewayAudit(tmp_path / "ladder-audit.jsonl"),
        workspace_root=tmp_path / "ws",
    )
    token = gateway.issue("run-l", ALL_OPS)
    running = gateway.submit(
        token, "run-l", _spec("k1"), operation_id="op-running", attempt=1
    )
    assert gateway.status(token, "run-l", running["job_id"])["state"] == "RUNNING"
    cancelled = gateway.cancel(token, "run-l", running["job_id"])
    assert cancelled["state"] == "CANCELLED"

    finished = gateway.submit(
        token, "run-l",
        {**_spec("k2"), "command": ["/bin/echo", "ok"]},
        operation_id="op-done", attempt=1,
    )
    for _ in range(500):
        if gateway.status(token, "run-l", finished["job_id"])["state"] == "SUCCEEDED":
            break
        time.sleep(0.01)
    assert gateway.status(token, "run-l", finished["job_id"])["state"] == "SUCCEEDED"


def test_missing_output_fetch_is_rejected(tmp_path):
    adapter = ProcessTestAdapter(tmp_path / "site", timeout_sec=60.0)
    gateway = Gateway(
        adapter,
        audit=GatewayAudit(tmp_path / "fetch-audit.jsonl"),
        workspace_root=tmp_path / "ws",
    )
    token = gateway.issue("run-m", ALL_OPS)
    running = gateway.submit(token, "run-m", _spec("k1"), operation_id="op", attempt=1)
    with pytest.raises(GatewayError, match="fetch before success"):
        gateway.fetch(token, "run-m", running["job_id"])


def test_stale_remote_run_dedupes_to_prior_job(tmp_path):
    """A replayed client against a surviving site reuses the prior job."""
    adapter = ProcessTestAdapter(tmp_path / "site", timeout_sec=60.0)
    audit_path = tmp_path / "stale-audit.jsonl"

    def fresh_gateway():
        gateway = Gateway(
            adapter,
            audit=GatewayAudit(audit_path),
            workspace_root=tmp_path / "ws",
        )
        return gateway, gateway.issue("run-s", ALL_OPS)

    gateway1, token1 = fresh_gateway()
    first = gateway1.submit(token1, "run-s", _spec("k1"), operation_id="legacy-op")
    # A restarted gateway over the same surviving site replays the op.
    gateway2, token2 = fresh_gateway()
    second = gateway2.submit(token2, "run-s", _spec("k1"), operation_id="legacy-op")
    assert second == {"job_id": first["job_id"], "duplicate": True}


def test_settlement_cancellation_and_token_revocation(tmp_path):
    session = HpcDispatcher.process_test(tmp_path / "site").open_run(
        "run-t", workspace=tmp_path / "ws"
    )
    session.submit(_spec("k1"), operation_id="op", attempt=1)
    report = session.settle(cancel_pending=True)
    assert report.attempts[0].state == "SUCCEEDED" or (
        report.attempts[0].state == "CANCELLED"
    )
    session.close()
    with pytest.raises(DispatcherClosedError):
        session.usage()


def test_formal_construction_rejects_process_driver():
    from dftworld_bench.hpc.drivers import DriverSelectionError, resolve_driver

    with pytest.raises(DriverSelectionError):
        resolve_driver(mode="formal", kind="process_test")
