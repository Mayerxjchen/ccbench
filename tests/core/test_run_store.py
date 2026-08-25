"""Append-only immutable Run Records: RunStore refuses overwrite and the
treatment identity (condition + skill source + content digest) is frozen.
RunStore writes only v2 records (v1 is read-only history), so the fixtures
build complete v2 identities: run_mode, lock digest, rooted event chain,
complete budgets, resolved runtime identities, and a seal for valid results.
"""
from __future__ import annotations

import json

import pytest

from dftworld_bench.contracts.events import next_event
from dftworld_bench.contracts.result import (
    BenchmarkResult,
    FailureCode,
    ResultClass,
)
from dftworld_bench.contracts.run_record import RunRecord, RunRecordError, RunRecordV2
from dftworld_bench.core.run_store import RunAlreadyExists, RunStore

def _DIGEST(suffix: str) -> str:
    return "sha256:" + suffix * 64


def _event_chain(operation_id: str = "MODEL-1") -> list[dict]:
    first = next_event(
        None,
        operation_id,
        "model_attempt",
        {
            "attempt": 1,
            "state": "success",
            "provider_request_id": "req-1",
            "usage": {"tokens": 5},
            "metadata_digest": _DIGEST("a"),
        },
        "2026-08-18T01:00:00Z",
    )
    second = next_event(
        first,
        operation_id,
        "model_response_committed",
        {"accepted_attempt": 1, "provider_request_id": "req-1"},
        "2026-08-18T01:00:01Z",
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


def _runtime_identities(
    execution_class: str, verifier_image: str = "dftworld-base:sha256:def"
) -> dict:
    identities = {
        role: {
            "role": role,
            "profile": f"local-{role}",
            "image": (
                verifier_image
                if role == "verifier"
                else "dftworld-base:sha256:abc"
            ),
            "digest": _DIGEST(char),
        }
        for role, char in zip(
            ("candidate", "control", "compute", "verifier"), "cdef", strict=True
        )
    }
    if execution_class == "local_sandbox":
        identities["control"] = None
    return identities


def _v2_record(
    run_id: str = "2026-08-18__01-00-00",
    *,
    execution_class: str = "local_sandbox",
    result: BenchmarkResult | None = None,
    remote_jobs: list[dict] | None = None,
    seal: dict | None = None,
) -> RunRecordV2:
    """A complete, valid v2 record; the v2 identity is never optional."""
    if result is None:
        result = BenchmarkResult.valid(
            run_id, passed=True, reason="hello.txt exists"
        )
    event_chain = _event_chain()
    is_valid = result.result_class is ResultClass.VALID_RESULT
    lifecycle = [
        {"phase": "PACKAGED", "at": "2026-08-18T01:00:00Z"},
        {"phase": "CANDIDATE_STARTING", "at": "2026-08-18T01:00:01Z"},
        {"phase": "CANDIDATE_RUNNING", "at": "2026-08-18T01:00:02Z"},
        {"phase": "CANDIDATE_STOPPING", "at": "2026-08-18T01:00:30Z"},
        {"phase": "CANDIDATE_FROZEN", "at": "2026-08-18T01:00:31Z"},
        {"phase": "SUBMISSION_COLLECTED", "at": "2026-08-18T01:00:32Z"},
        {"phase": "CANDIDATE_DESTROYED", "at": "2026-08-18T01:00:33Z"},
        {"phase": "STRUCTURALLY_VALIDATED", "at": "2026-08-18T01:00:34Z"},
        {"phase": "QUARANTINED", "at": "2026-08-18T01:00:35Z"},
        {"phase": "SEALED", "at": "2026-08-18T01:00:36Z"},
        {"phase": "VERIFYING", "at": "2026-08-18T01:00:37Z"},
    ]
    if is_valid:
        lifecycle.append({"phase": "COMPLETED", "at": "2026-08-18T01:01:00Z"})
    else:
        lifecycle.append({"phase": "INVALID_INFRA", "at": "2026-08-18T01:01:00Z"})
    return RunRecordV2(
        run_id=run_id,
        case_id=(
            "031-matclaw-cips-active-distillation"
            if execution_class == "hpc_controller"
            else "001-hello"
        ),
        execution_class=execution_class,
        agent_model="deepseek/deepseek-chat",
        condition_id="no-skill",
        skills_source="none",
        skills_sha=None,
        image="dftworld-base:sha256:abc",
        benchmark_commit="8f1921f",
        profile="paper",
        submission_root=".",
        verifier="dftworld-base:sha256:def",
        platform=(
            "hpc-site-a" if execution_class == "hpc_controller" else "local_docker"
        ),
        job_id="12345" if execution_class == "hpc_controller" else None,
        site_config_digest=_DIGEST("8"),
        usage={"tool_calls": 3, "tokens": 1200, "elapsed_sec": 12.4},
        lifecycle_events=lifecycle,
        result=result,
        run_mode="formal",
        lock_digest=_DIGEST("1"),
        event_root_digest=event_chain[-1]["event_digest"],
        event_chain=event_chain,
        attempts=[
            {
                "operation_id": "MODEL-1",
                "attempt": 1,
                "state": "success",
                "provider_request_id": "req-1",
                "usage": {"tokens": 5},
                "metadata_digest": _DIGEST("a"),
                "accepted": True,
            }
        ],
        budgets=dict(_BUDGETS),
        runtime_identities=_runtime_identities(execution_class),
        remote_jobs=remote_jobs or (
            [{"job_id": "12345", "state": "COMPLETED", "resources": {"gpus": 1},
              "artifact_hashes": {}}]
            if execution_class == "hpc_controller"
            else []
        ),
        seal=seal
        if seal is not None
        else {
            "manifest_digest": _DIGEST("9"),
            "file_count": 1,
            "total_bytes": 5,
            "sealed_at": "2026-08-18T01:00:36Z",
            "legacy_layout": False,
            "exclusions": [],
        }
        if is_valid
        else None,
    )


@pytest.fixture
def valid_record() -> RunRecordV2:
    return _v2_record()


def test_run_store_never_overwrites(tmp_path, valid_record):
    store = RunStore(tmp_path)
    store.create(valid_record)
    with pytest.raises(RunAlreadyExists):
        store.create(valid_record)


def test_run_record_requires_frozen_treatment_identity(valid_record):
    valid_record.condition_id = "with-skill"
    valid_record.skills_source = "image"
    valid_record.skills_sha = None
    with pytest.raises(RunRecordError, match="skills_sha"):
        valid_record.validate()


def test_no_skill_treatment_must_not_carry_a_digest(valid_record):
    valid_record.skills_sha = "sha256:abcd"
    with pytest.raises(RunRecordError, match="skills_sha"):
        valid_record.validate()


def test_store_writes_canonical_path_and_schema_valid(tmp_path, valid_record):
    store = RunStore(tmp_path)
    path = store.create(valid_record)
    assert path.name == "run-record.json"
    assert path.parent.name == valid_record.run_id
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["case_id"] == "001-hello"
    assert payload["result"]["result_class"] == "VALID_RESULT"
    # no leftover temp file after atomic rename
    assert not path.with_name("run-record.json.tmp").exists()


def test_store_load_round_trip(tmp_path, valid_record):
    store = RunStore(tmp_path)
    store.create(valid_record)
    loaded = store.load(valid_record.run_id)
    assert loaded.condition_id == "no-skill"
    assert loaded.result.failure_code is FailureCode.PASS
    assert loaded.lifecycle_events[-1]["phase"] == "COMPLETED"


def test_infra_invalid_record_is_retryable_and_not_scientific(tmp_path):
    record = _v2_record(
        run_id="2026-08-18__02-00-00",
        execution_class="hpc_controller",
        result=BenchmarkResult.infra_invalid(
            "2026-08-18__02-00-00",
            FailureCode.HPC_FAILURE,
            "scheduler unreachable",
        ),
    )
    store = RunStore(tmp_path)
    store.create(record)
    loaded = store.load(record.run_id)
    assert loaded.result.result_class is ResultClass.INFRA_INVALID
    assert loaded.result.is_counted_scientifically is False
    assert loaded.result.retryable is True
