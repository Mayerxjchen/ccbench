"""Operation-attempt lifecycle at the trust boundary.

The Agent-facing identity is ``(run_id, operation_id, attempt)``. These tests
pin the lineage rules the gateway enforces regardless of what a client sends:

- two scientific attempts of one operation are distinct scheduler jobs;
- a duplicate transport submit of the same attempt never reschedules;
- a crash after scheduler acceptance adopts the exact intended job via its
  fsynced SUBMIT_INTENT marker — never a blind second sbatch;
- attempts are monotonic without gaps and need a terminal predecessor;
- zero/multiple marker matches fail closed.
"""

from __future__ import annotations

import pytest

from dftworld_bench.hpc.adapters.process_test import ProcessTestAdapter
from dftworld_bench.hpc.audit import GatewayAudit
from dftworld_bench.hpc.gateway import ALL_OPS, Gateway, GatewayError
from dftworld_bench.hpc.adapters.base import TransportUnknown

DIGEST = "img@sha256:" + "a" * 64


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


class Fixture:
    """One adapter (the surviving 'scheduler') plus a renewable gateway."""

    def __init__(self, tmp_path):
        self.adapter = ProcessTestAdapter(
            tmp_path / "site", timeout_sec=60.0
        )
        self.audit_path = tmp_path / "audit.jsonl"
        self.tmp_path = tmp_path

    def gateway(self):
        """A fresh trusted gateway over the same audit log (restart = new GW)."""
        audit = GatewayAudit(self.audit_path)
        gateway = Gateway(self.adapter, audit=audit, workspace_root=self.tmp_path / "ws")
        token = gateway.issue("run-a", ALL_OPS)
        return gateway, token


@pytest.fixture()
def fx(tmp_path):
    return Fixture(tmp_path)


def test_two_scientific_attempts_have_distinct_jobs(fx):
    gateway, token = fx.gateway()
    first = gateway.submit(token, "run-a", _spec("k1"), operation_id="cp2k-round-01", attempt=1)
    fx.adapter.finish(first["job_id"], state="FAILED")
    second = gateway.submit(token, "run-a", _spec("k2"), operation_id="cp2k-round-01", attempt=2)
    assert second["job_id"] != first["job_id"]
    report = gateway.operation_status(token, "run-a", "cp2k-round-01")
    assert report["attempts"] == {
        "1": "FAILED",
        "2": "RUNNING",
    }


def test_duplicate_transport_submit_reuses_same_attempt(fx):
    gateway, token = fx.gateway()
    one = gateway.submit(token, "run-a", _spec("k1"), operation_id="op", attempt=1)
    # Even a payload whose idempotency key differs cannot double-schedule the
    # same logical attempt: identity is (run, operation, attempt).
    duplicate = gateway.submit(token, "run-a", _spec("k-other"), operation_id="op", attempt=1)
    assert duplicate == {"job_id": one["job_id"], "duplicate": True}


def test_crash_after_sbatch_adopts_exact_marker_without_resubmit(fx):
    gateway, token = fx.gateway()
    fx.adapter.fail_after_scheduler_accept_once()
    with pytest.raises(TransportUnknown):
        gateway.submit(token, "run-a", _spec("k1"), operation_id="op", attempt=1)

    # Gateway crash + restart; the scheduler-side adapter survived.
    restarted, rtoken = fx.gateway()
    recovered = restarted.submit(rtoken, "run-a", _spec("k1"), operation_id="op", attempt=1)
    assert recovered["duplicate"] is True
    assert fx.adapter.physical_submit_count == 1


def test_recovery_fails_closed_on_zero_marker_matches(fx):
    gateway, token = fx.gateway()
    fx.adapter.fail_after_scheduler_accept_once()
    with pytest.raises(TransportUnknown):
        gateway.submit(token, "run-a", _spec("k1"), operation_id="op", attempt=1)
    # The scheduler lost the job entirely (e.g. node rebooted clean).
    fx.adapter.forget_all_jobs()
    restarted, rtoken = fx.gateway()
    with pytest.raises(GatewayError, match="marker"):
        restarted.submit(rtoken, "run-a", _spec("k1"), operation_id="op", attempt=1)
    # The single physical submit was the original lost-response one; recovery
    # added neither an adoption nor a resubmission.
    assert fx.adapter.physical_submit_count == 1


def test_recovery_fails_closed_on_ambiguous_marker_matches(fx):
    gateway, token = fx.gateway()
    fx.adapter.fail_after_scheduler_accept_once()
    with pytest.raises(TransportUnknown):
        gateway.submit(token, "run-a", _spec("k1"), operation_id="op", attempt=1)
    # A pathological scheduler reports two jobs carrying the same marker.
    fx.adapter.plant_duplicate_markers()
    restarted, rtoken = fx.gateway()
    with pytest.raises(GatewayError, match="ambiguous"):
        restarted.submit(rtoken, "run-a", _spec("k1"), operation_id="op", attempt=1)
    assert fx.adapter.physical_submit_count == 1


def test_attempt_gaps_and_nonterminal_predecessors_rejected(fx):
    gateway, token = fx.gateway()
    with pytest.raises(GatewayError, match="attempt 1"):
        gateway.submit(token, "run-a", _spec("k2"), operation_id="op", attempt=2)
    first = gateway.submit(token, "run-a", _spec("k1"), operation_id="op", attempt=1)
    # Predecessor still RUNNING: attempt 2 must wait.
    with pytest.raises(GatewayError, match="terminal"):
        gateway.submit(token, "run-a", _spec("k2"), operation_id="op", attempt=2)
    fx.adapter.finish(first["job_id"], state="SUCCEEDED")
    second = gateway.submit(token, "run-a", _spec("k2"), operation_id="op", attempt=2)
    assert second["job_id"] != first["job_id"]


def test_v1_submit_without_attempt_still_works(fx):
    """Frozen v1 semantics: no attempt kwarg, legacy ownership map."""
    gateway, token = fx.gateway()
    result = gateway.submit(token, "run-a", _spec("k1"), operation_id="legacy-op")
    assert result["duplicate"] is False
    again = gateway.submit(token, "run-a", _spec("k1"), operation_id="legacy-op")
    assert again == {"job_id": result["job_id"], "duplicate": True}
