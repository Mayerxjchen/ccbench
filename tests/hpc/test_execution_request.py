"""ExecutionRequestV2: operation-attempt identity and hardened validation.

The request is the Agent-facing unit of work. Its identity is the triple
``(run_id, operation_id, attempt)``; attempts are monotonic without gaps, one
in flight per operation, and every field is either typed or rejected.
"""

from __future__ import annotations

import hashlib

import pytest

from dftworld_bench.hpc.request import (
    AttemptLedger,
    ExecutionRequestV2,
    RequestError,
)

DIGEST = "img@sha256:" + "a" * 64

INPUT_SHA = hashlib.sha256(b"&GLOBAL\n  RUN_TYPE ENERGY\n&END GLOBAL\n").hexdigest()


def valid_payload() -> dict:
    return {
        "schema_version": 2,
        "operation_id": "cp2k-round-01",
        "attempt": 1,
        "runtime": DIGEST,
        "command": ["/usr/local/bin/cp2k", "-i", "input.inp"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [
            {"path": "input.inp", "sha256": INPUT_SHA, "size_bytes": 36},
        ],
        "outputs": ["out/", "*.log"],
        "environment": {"CP2K_DATA_DIR": "/data"},
    }


@pytest.fixture()
def payload() -> dict:
    return valid_payload()


@pytest.fixture()
def req(payload) -> ExecutionRequestV2:
    return ExecutionRequestV2.from_dict(valid_payload())


def test_request_requires_operation_and_positive_attempt(payload):
    payload.pop("operation_id")
    with pytest.raises(RequestError):
        ExecutionRequestV2.from_dict(payload)
    fresh = valid_payload()
    fresh["attempt"] = 0
    with pytest.raises(RequestError):
        ExecutionRequestV2.from_dict(fresh)


def test_request_rejects_raw_scheduler_flags(payload):
    payload["scheduler_flags"] = ["#SBATCH --partition=secret"]
    with pytest.raises(RequestError):
        ExecutionRequestV2.from_dict(payload)


def test_request_rejects_unknown_fields_and_bad_paths(payload):
    payload["extra"] = 1
    with pytest.raises(RequestError):
        ExecutionRequestV2.from_dict(payload)
    fresh = valid_payload()
    fresh["inputs"][0]["path"] = "/etc/passwd"
    with pytest.raises(RequestError):
        ExecutionRequestV2.from_dict(fresh)
    fresh = valid_payload()
    fresh["outputs"] = ["../escape"]
    with pytest.raises(RequestError):
        ExecutionRequestV2.from_dict(fresh)


def test_request_rejects_mutable_runtime_tag(payload):
    payload["runtime"] = "docker.io/library/python:latest"
    with pytest.raises(RequestError):
        ExecutionRequestV2.from_dict(payload)


def test_transport_identity_is_run_operation_attempt(req):
    assert req.idempotency_key("run-a") == "run-a:cp2k-round-01:1"


def test_with_attempt_and_with_input_are_immutable_copies(req):
    bumped = req.with_attempt(2)
    assert bumped.attempt == 2
    assert req.attempt == 1
    extended = req.with_input("data.xyz", sha256="b" * 64, size_bytes=7)
    assert len(extended.input_entries) == 2
    assert len(req.input_entries) == 1


def test_attempt_must_be_monotonic_without_gaps():
    ledger = AttemptLedger()
    req1 = ExecutionRequestV2.from_dict(valid_payload())
    # Gap: the first admission for an operation must be attempt 1.
    with pytest.raises(RequestError, match="attempt 1"):
        ledger.admit(req1.with_attempt(2))
    ledger.admit(req1.with_attempt(1))
    # The predecessor must be terminal before the next attempt is admitted.
    with pytest.raises(RequestError, match="terminal"):
        ledger.admit(req1.with_attempt(2))
    ledger.mark_terminal("cp2k-round-01", 1)
    ledger.admit(req1.with_attempt(2))
    # Attempts never repeat.
    with pytest.raises(RequestError, match="already"):
        ledger.admit(req1.with_attempt(1))


def test_concurrent_attempts_for_one_operation_rejected():
    ledger = AttemptLedger()
    req = ExecutionRequestV2.from_dict(valid_payload())
    ledger.admit(req.with_attempt(1))
    # Same attempt resubmitted while in flight is a concurrent attempt.
    with pytest.raises(RequestError, match="in flight"):
        ledger.admit(req.with_attempt(1))


def test_attempts_of_different_operations_are_independent():
    ledger = AttemptLedger()
    first = ExecutionRequestV2.from_dict(valid_payload())
    other_payload = valid_payload()
    other_payload["operation_id"] = "lammps-min-01"
    second = ExecutionRequestV2.from_dict(other_payload)
    assert first.operation_id != second.operation_id
    ledger.admit(first.with_attempt(1))
    ledger.admit(second.with_attempt(1))
