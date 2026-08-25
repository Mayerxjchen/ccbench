"""RunRecordV2: monotonic one-terminal lifecycle, full run identity, and the
v1 reader stays intact (v1 records remain readable historical artifacts)."""
from __future__ import annotations

import json

import pytest

from dftworld_bench.contracts.events import next_event
from dftworld_bench.contracts.result import BenchmarkResult
from dftworld_bench.contracts.run_record import RunRecord, RunRecordError, RunRecordV2
from dftworld_bench.core.run_store import RunStore


def _event_chain() -> list[dict]:
    first = next_event(
        None,
        "MODEL-1",
        "model_attempt",
        {
            "attempt": 1,
            "state": "success",
            "provider_request_id": "req-1",
            "usage": {"tokens": 5},
            "metadata_digest": "sha256:" + "a" * 64,
        },
        "2026-08-20T01:00:03Z",
    )
    second = next_event(
        first,
        "MODEL-1",
        "model_response_committed",
        {"accepted_attempt": 1, "provider_request_id": "req-1"},
        "2026-08-20T01:00:04Z",
    )
    return [first.to_dict(), second.to_dict()]


_BUDGETS = {
    "model_turns": 1,
    "logical_requests": 1,
    "api_attempts": 1,
    "tokens": 5,
    "usd_microcost": 0,
    "agent_active_walltime_ms": 1000,
    "run_total_walltime_ms": 2000,
    "local_tool_walltime_ms": 0,
    "api_retry_walltime_ms": 0,
    "scheduler_wait_ms": 0,
    "jobs": 0,
    "cpu_hours": 0,
    "gpu_hours": 0,
    "storage_byte_hours": 0,
}


_RUNTIME_IDENTITIES = {
    role: {
        "role": role,
        "profile": f"local-{role}",
        "image": (
            "dftworld-base:sha256:def"
            if role == "verifier"
            else "dftworld-base:sha256:abc"
        ),
        "digest": "sha256:" + char * 64,
    }
    for role, char in zip(
        ("candidate", "control", "compute", "verifier"), "cdef", strict=True
    )
}
_RUNTIME_IDENTITIES["control"] = None


@pytest.fixture
def record_payload() -> dict:
    """A complete v2-shaped payload with a legal one-terminal lifecycle."""
    event_chain = _event_chain()
    return {
        "schema_version": 2,
        "run_id": "2026-08-20__01-00-00",
        "case_id": "001-hello",
        "execution_class": "local_sandbox",
        "agent_model": "deepseek/deepseek-chat",
        "condition_id": "no-skill",
        "skills_source": "none",
        "skills_sha": None,
        "image": "dftworld-base:sha256:abc",
        "benchmark_commit": "8f1921f",
        "profile": "paper",
        "submission_root": ".",
        "verifier": "dftworld-base:sha256:def",
        "platform": "local_docker",
        "job_id": None,
        "site_config_digest": "sha256:" + "8" * 64,
        "usage": {"tool_calls": 3, "tokens": 1200, "elapsed_sec": 12.4},
        "lifecycle_events": [
            {"phase": "CREATED", "at": "2026-08-20T01:00:00Z"},
            {"phase": "PACKAGED", "at": "2026-08-20T01:00:01Z"},
            {"phase": "CANDIDATE_STARTING", "at": "2026-08-20T01:00:02Z"},
            {"phase": "CANDIDATE_RUNNING", "at": "2026-08-20T01:00:03Z"},
            {"phase": "CANDIDATE_STOPPING", "at": "2026-08-20T01:00:30Z"},
            {"phase": "CANDIDATE_FROZEN", "at": "2026-08-20T01:00:31Z"},
            {"phase": "SUBMISSION_COLLECTED", "at": "2026-08-20T01:00:32Z"},
            {"phase": "CANDIDATE_DESTROYED", "at": "2026-08-20T01:00:33Z"},
            {"phase": "STRUCTURALLY_VALIDATED", "at": "2026-08-20T01:00:34Z"},
            {"phase": "QUARANTINED", "at": "2026-08-20T01:00:35Z"},
            {"phase": "SEALED", "at": "2026-08-20T01:00:36Z"},
            {"phase": "VERIFYING", "at": "2026-08-20T01:00:37Z"},
            {"phase": "COMPLETED", "at": "2026-08-20T01:01:00Z"},
        ],
        "result": {
            "run_id": "2026-08-20__01-00-00",
            "result_class": "VALID_RESULT",
            "failure_code": "PASS",
            "reason": "hello.txt exists",
            "retryable": False,
            "is_counted_scientifically": True,
        },
        "experiment_id": "skill-ablation-v1",
        "replicate": 1,
        "attempt": 1,
        "thread_dir": None,
        "legacy_normalized": False,
        # v2-only identity
        "run_mode": "formal",
        "lock_digest": "sha256:" + "1" * 64,
        "event_root_digest": event_chain[-1]["event_digest"],
        "event_chain": event_chain,
        "attempts": [
            {
                "operation_id": "MODEL-1",
                "attempt": 1,
                "state": "success",
                "provider_request_id": "req-1",
                "usage": {"tokens": 5},
                "metadata_digest": "sha256:" + "a" * 64,
                "accepted": True,
            }
        ],
        "budgets": dict(_BUDGETS),
        "runtime_identities": dict(_RUNTIME_IDENTITIES),
        "remote_jobs": [],
        "seal": {
            "manifest_digest": "sha256:" + "9" * 64,
            "file_count": 1,
            "total_bytes": 5,
            "sealed_at": "2026-08-20T01:00:36Z",
            "legacy_layout": False,
            "exclusions": [],
        },
    }


def test_record_has_exactly_one_terminal(record_payload):
    record_payload["lifecycle_events"] += [
        {"phase": "COMPLETED", "at": "t"},
        {"phase": "INVALID_INFRA", "at": "t2"},
    ]
    with pytest.raises(RunRecordError, match="one terminal"):
        RunRecordV2.from_dict(record_payload).validate()


def test_terminal_must_be_last_event(record_payload):
    events = record_payload["lifecycle_events"]
    # swap: COMPLETED is no longer the last event
    record_payload["lifecycle_events"] = [events[-1]] + events[:-1]
    with pytest.raises(RunRecordError, match="last"):
        RunRecordV2.from_dict(record_payload).validate()


def test_terminal_must_match_result_class(record_payload):
    record_payload["lifecycle_events"][-1] = {
        "phase": "FAILED_AGENT",
        "at": "2026-08-20T01:01:00Z",
    }
    with pytest.raises(RunRecordError, match="result"):
        RunRecordV2.from_dict(record_payload).validate()


def test_lifecycle_must_replay_through_state_machine(record_payload):
    # direct CANDIDATE_RUNNING -> VERIFYING jump skips STOPPING/FROZEN/...
    events = [
        e for e in record_payload["lifecycle_events"]
        if e["phase"] in {
            "CREATED", "PACKAGED", "CANDIDATE_STARTING", "CANDIDATE_RUNNING",
        }
    ]
    record_payload["lifecycle_events"] = events + [
        {"phase": "VERIFYING", "at": "2026-08-20T01:00:50Z"},
        {"phase": "COMPLETED", "at": "2026-08-20T01:01:00Z"},
    ]
    with pytest.raises(RunRecordError, match="state machine|transition"):
        RunRecordV2.from_dict(record_payload).validate()


def test_valid_v2_record_passes_validate(record_payload):
    record = RunRecordV2.from_dict(record_payload)
    record.validate()  # no exception
    assert record.schema_version == 2
    assert record.lock_digest == "sha256:" + "1" * 64


def test_v2_requires_schema_version_2(record_payload):
    record_payload["schema_version"] = 1
    with pytest.raises(RunRecordError, match="schema_version"):
        RunRecordV2.from_dict(record_payload)


def test_v2_requires_full_identity(record_payload):
    del record_payload["runtime_identities"]
    with pytest.raises(RunRecordError, match="runtime_identities"):
        RunRecordV2.from_dict(record_payload)


@pytest.mark.parametrize("field", ["lock_digest", "event_root_digest"])
def test_v2_rejects_null_digest_provenance(record_payload, field):
    record_payload[field] = None
    with pytest.raises(RunRecordError, match=field):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_requires_complete_runtime_roles(record_payload):
    del record_payload["runtime_identities"]["verifier"]
    with pytest.raises(RunRecordError, match="runtime identities|verifier"):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_local_runtime_keeps_logical_null_control(record_payload):
    assert record_payload["runtime_identities"]["control"] is None
    RunRecordV2.from_dict(record_payload).validate()


def test_v2_local_runtime_rejects_fabricated_control(record_payload):
    record_payload["runtime_identities"]["control"] = {
        "role": "control",
        "profile": "fabricated-local-control",
        "image": record_payload["image"],
        "digest": "sha256:" + "d" * 64,
    }
    with pytest.raises(RunRecordError, match="local_sandbox.*control.*null"):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_requires_complete_budget_snapshot(record_payload):
    del record_payload["budgets"]["api_attempts"]
    with pytest.raises(RunRecordError, match="budget|api_attempts"):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_requires_real_site_identity(record_payload):
    record_payload["platform"] = ""
    with pytest.raises(RunRecordError, match="site identity|platform"):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_rejects_mismatched_event_chain_root(record_payload):
    record_payload["event_root_digest"] = "sha256:" + "0" * 64
    with pytest.raises(RunRecordError, match="event.*root"):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_rejects_mutated_event_chain(record_payload):
    record_payload["event_chain"][0]["payload"]["state"] = "known_failure"
    with pytest.raises(RunRecordError, match="event chain"):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_attempts_must_match_validated_event_chain(record_payload):
    record_payload["attempts"][0]["state"] = "known_failure"
    with pytest.raises(RunRecordError, match="attempts.*event chain"):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_hpc_record_requires_job_audit(record_payload):
    record_payload["execution_class"] = "hpc_controller"
    # hpc_controller also requires a real control identity; give it one so the
    # remote_jobs gate is the check under test.
    record_payload["runtime_identities"]["control"] = {
        "role": "control",
        "profile": "hpc-control",
        "image": "dftworld-base:sha256:abc",
        "digest": "sha256:" + "5" * 64,
    }
    with pytest.raises(RunRecordError, match="remote_jobs"):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_hpc_record_requires_real_control_identity(record_payload):
    record_payload["execution_class"] = "hpc_controller"
    record_payload["remote_jobs"] = [
        {
            "job_id": "123",
            "state": "COMPLETED",
            "resources": {"gpus": 1},
            "artifact_hashes": {},
        }
    ]
    with pytest.raises(RunRecordError, match="hpc_controller.*control"):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_valid_result_requires_complete_seal(record_payload):
    del record_payload["seal"]["total_bytes"]
    with pytest.raises(RunRecordError, match="seal.*total_bytes"):
        RunRecordV2.from_dict(record_payload).validate()


def test_v2_round_trip_through_store(tmp_path, record_payload):
    store = RunStore(tmp_path)
    record = RunRecordV2.from_dict(record_payload)
    store.create(record)
    loaded = store.load(record.run_id)
    assert isinstance(loaded, RunRecordV2), type(loaded)
    assert loaded.lock_digest == record.lock_digest
    assert loaded.lifecycle_events[-1]["phase"] == "COMPLETED"
    assert len(loaded.lifecycle_events) == len(record.lifecycle_events)


def test_v1_records_stay_readable(tmp_path, record_payload):
    """The v1 reader loads a historical fixture without using the v2 writer."""
    v1_payload = {k: v for k, v in record_payload.items() if k not in {
        "lock_digest", "event_root_digest", "event_chain", "attempts", "budgets",
        "runtime_identities", "remote_jobs", "seal",
    }}
    v1_payload["schema_version"] = 1
    store = RunStore(tmp_path)
    target = tmp_path / v1_payload["run_id"] / "run-record.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(v1_payload), encoding="utf-8")
    loaded = store.load(v1_payload["run_id"])
    assert type(loaded) is RunRecord
    assert loaded.schema_version == 1
    assert isinstance(loaded.result, BenchmarkResult)


def test_run_store_rejects_v1_writes(tmp_path, record_payload):
    v1_payload = {k: v for k, v in record_payload.items() if k not in {
        "lock_digest", "event_root_digest", "event_chain", "attempts", "budgets",
        "runtime_identities", "remote_jobs", "seal",
    }}
    v1_payload["schema_version"] = 1
    with pytest.raises(RunRecordError, match="writes only v2"):
        RunStore(tmp_path).create(RunRecord.from_dict(v1_payload))


def _hpc_payload(record_payload):
    """An otherwise-valid hpc_controller payload carrying one remote job."""
    record_payload["execution_class"] = "hpc_controller"
    record_payload["runtime_identities"]["control"] = {
        "role": "control",
        "profile": "hpc-control",
        "image": "dftworld-base:sha256:abc",
        "digest": "sha256:" + "5" * 64,
    }
    record_payload["remote_jobs"] = [
        {
            "job_id": "123",
            "state": "COMPLETED",
            "resources": {"gpus": 1},
            "artifact_hashes": {},
        }
    ]
    return record_payload


def test_remote_jobs_operation_attempt_lineage_pairwise(record_payload):
    """operation_id and attempt are validated together: both or neither."""
    def _with(job_extra):
        payload = _hpc_payload(record_payload)
        payload["remote_jobs"][0].update(job_extra)
        return RunRecordV2.from_dict(payload)

    _with({}).validate()  # lineage optional in stored v1-shaped entries
    _with({"operation_id": "scf-round-01", "attempt": 2}).validate()
    with pytest.raises(RunRecordError, match="together"):
        _with({"operation_id": "scf-round-01"}).validate()
    with pytest.raises(RunRecordError, match="positive integer"):
        _with({"operation_id": "scf-round-01", "attempt": 0}).validate()
