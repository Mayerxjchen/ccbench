"""Formal INFRA_INVALID runs are excluded from the formal pair and retained in
a separate invalid-run ledger.  The ledger is appended atomically at
RunStore.create time; aggregation refuses an unledgered record, a missing
record, or a duplicate conflicting entry."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from dftworld_bench.contracts.events import next_event
from dftworld_bench.contracts.result import BenchmarkResult, FailureCode
from dftworld_bench.contracts.run_record import RunRecord, RunRecordV2
from dftworld_bench.core.run_store import RunAlreadyExists, RunStore
from dftworld_bench.experiments.ablation import FORMAL_EXPERIMENT_ID
from dftworld_bench.experiments.invalid_ledger import (
    InvalidRunLedger,
    LedgerError,
)


@pytest.fixture
def store(tmp_path) -> RunStore:
    return RunStore(tmp_path)


def _invalid_record(run_id: str, experiment_id: str) -> RunRecordV2:
    event = next_event(
        None,
        "HARNESS",
        "candidate_frozen",
        {"run_id": run_id},
        "2026-08-20T01:00:05Z",
    )
    return RunRecordV2(
        run_id=run_id,
        case_id="001-hello",
        execution_class="local_sandbox",
        agent_model="deepseek/deepseek-chat",
        condition_id="no-skill",
        skills_source="none",
        skills_sha=None,
        image="dftworld-base:sha256:abc",
        benchmark_commit="8f1921f",
        profile="paper",
        submission_root=".",
        verifier="dftworld-base:sha256:def",
        platform="local_docker",
        job_id=None,
        site_config_digest="sha256:" + "8" * 64,
        usage={"tool_calls": 0, "tokens": 0, "elapsed_sec": 1.0},
        lifecycle_events=[
            {"phase": "CREATED", "at": "2026-08-20T01:00:00Z"},
            {"phase": "PACKAGED", "at": "2026-08-20T01:00:01Z"},
            {"phase": "CANDIDATE_STARTING", "at": "2026-08-20T01:00:02Z"},
            {"phase": "CANDIDATE_RUNNING", "at": "2026-08-20T01:00:03Z"},
            {"phase": "CANDIDATE_STOPPING", "at": "2026-08-20T01:00:04Z"},
            {"phase": "CANDIDATE_FROZEN", "at": "2026-08-20T01:00:05Z"},
            {"phase": "SUBMISSION_COLLECTED", "at": "2026-08-20T01:00:06Z"},
            {"phase": "CANDIDATE_DESTROYED", "at": "2026-08-20T01:00:07Z"},
            {"phase": "STRUCTURALLY_VALIDATED", "at": "2026-08-20T01:00:08Z"},
            {"phase": "INVALID_INFRA", "at": "2026-08-20T01:00:09Z"},
        ],
        result=BenchmarkResult.infra_invalid(
            run_id, FailureCode.HARNESS_FAILURE, "collector os error"
        ),
        experiment_id=experiment_id,
        run_mode="formal",
        lock_digest="sha256:" + "1" * 64,
        event_root_digest=event.event_digest,
        event_chain=[event.to_dict()],
        attempts=[],
        budgets={
            "model_turns": 0,
            "logical_requests": 0,
            "api_attempts": 0,
            "tokens": 0,
            "usd_microcost": 0,
            "agent_active_walltime_ms": 0,
            "run_total_walltime_ms": 0,
            "local_tool_walltime_ms": 0,
            "api_retry_walltime_ms": 0,
            "scheduler_wait_ms": 0,
            "jobs": 0,
            "cpu_hours": 0,
            "gpu_hours": 0,
            "storage_byte_hours": 0,
        },
        runtime_identities={
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
                ("candidate", "control", "compute", "verifier"),
                "cdef",
                strict=True,
            )
        }
        | {"control": None},  # local_sandbox has no control plane
        remote_jobs=[],
        seal=None,
    )


@pytest.fixture
def formal_record() -> RunRecordV2:
    return _invalid_record("2026-08-20__02-00-00", FORMAL_EXPERIMENT_ID)


def test_formal_infra_invalid_is_auto_ledgered(store, formal_record):
    store.create(formal_record)
    assert formal_record.run_id in InvalidRunLedger.load(store.ledger_path).run_ids


def test_non_formal_infra_invalid_not_ledgered(store):
    record = _invalid_record("2026-08-20__02-00-01", "some-other-experiment")
    store.create(record)
    assert not store.ledger_path.is_file()


def test_formal_valid_result_not_ledgered(store, formal_record):
    formal_record.result = BenchmarkResult.valid(
        formal_record.run_id, passed=True, reason="ok"
    )
    formal_record.lifecycle_events = [
        {"phase": "CREATED", "at": "2026-08-20T01:00:00Z"},
        {"phase": "PACKAGED", "at": "2026-08-20T01:00:01Z"},
        {"phase": "CANDIDATE_STARTING", "at": "2026-08-20T01:00:02Z"},
        {"phase": "CANDIDATE_RUNNING", "at": "2026-08-20T01:00:03Z"},
        {"phase": "CANDIDATE_STOPPING", "at": "2026-08-20T01:00:04Z"},
        {"phase": "CANDIDATE_FROZEN", "at": "2026-08-20T01:00:05Z"},
        {"phase": "SUBMISSION_COLLECTED", "at": "2026-08-20T01:00:06Z"},
        {"phase": "CANDIDATE_DESTROYED", "at": "2026-08-20T01:00:07Z"},
        {"phase": "STRUCTURALLY_VALIDATED", "at": "2026-08-20T01:00:08Z"},
        {"phase": "QUARANTINED", "at": "2026-08-20T01:00:09Z"},
        {"phase": "SEALED", "at": "2026-08-20T01:00:10Z"},
        {"phase": "VERIFYING", "at": "2026-08-20T01:00:11Z"},
        {"phase": "COMPLETED", "at": "2026-08-20T01:01:00Z"},
    ]
    formal_record.seal = {
        "manifest_digest": "sha256:" + "9" * 64,
        "file_count": 0,
        "total_bytes": 0,
        "sealed_at": "2026-08-20T01:00:10Z",
        "legacy_layout": False,
        "exclusions": [],
    }
    store.create(formal_record)
    assert not store.ledger_path.is_file()


def test_ledger_rejects_duplicate_entry(store, formal_record):
    store.create(formal_record)
    with pytest.raises(LedgerError, match="duplicate"):
        InvalidRunLedger.append(formal_record, store.ledger_path)


def test_ledger_entry_is_immutable(store, formal_record):
    store.create(formal_record)
    ledger = InvalidRunLedger.load(store.ledger_path)
    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    assert entry["run_id"] == formal_record.run_id
    assert entry["experiment_id"] == FORMAL_EXPERIMENT_ID
    assert entry["result_class"] == "INFRA_INVALID"
    assert entry["failure_code"] == "HARNESS_FAILURE"
    assert "record_digest" in entry and entry["record_digest"]


def test_aggregate_fails_on_unledgered_record(store, formal_record):
    store.create(formal_record)
    # A second formal INFRA_INVALID record never written to the ledger.
    other = _invalid_record("2026-08-20__02-00-09", FORMAL_EXPERIMENT_ID)
    with pytest.raises(LedgerError, match="not in the invalid ledger"):
        InvalidRunLedger.aggregate([formal_record, other], store.ledger_path)


def test_aggregate_fails_on_missing_record(store, formal_record):
    store.create(formal_record)
    # Ledger has an entry for formal_record but the record set omits it.
    with pytest.raises(LedgerError, match="no matching record|missing"):
        InvalidRunLedger.aggregate([], store.ledger_path)


def test_aggregate_partitions_candidates_and_invalids(store, formal_record):
    store.create(formal_record)
    valid = RunRecord(
        run_id="2026-08-20__02-00-30",
        case_id="001-hello",
        execution_class="local_sandbox",
        agent_model="deepseek/deepseek-chat",
        condition_id="no-skill",
        skills_source="none",
        skills_sha=None,
        image="dftworld-base:sha256:abc",
        benchmark_commit="8f1921f",
        profile="paper",
        submission_root=".",
        verifier="dftworld-base:sha256:def",
        platform="local_docker",
        job_id=None,
        site_config_digest="sha256:9f86d081",
        usage={"tool_calls": 1, "tokens": 10, "elapsed_sec": 1.0},
        lifecycle_events=[
            {"phase": "CANDIDATE_STARTED", "at": "t0"},
            {"phase": "COMPLETED", "at": "t1"},
        ],
        result=BenchmarkResult.valid(
            "2026-08-20__02-00-30", passed=True, reason="ok"
        ),
        experiment_id=FORMAL_EXPERIMENT_ID,
    )
    candidates, invalids = InvalidRunLedger.aggregate(
        [valid, formal_record], store.ledger_path
    )
    assert [r.run_id for r in candidates] == [valid.run_id]
    assert [r.run_id for r in invalids] == [formal_record.run_id]


def test_failed_ledger_append_is_reconciled_by_create_retry(
    store, formal_record, monkeypatch
):
    original = InvalidRunLedger.append.__func__
    calls = 0

    def fail_once(cls, record, path, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected ledger fsync failure")
        return original(cls, record, path, *args, **kwargs)

    monkeypatch.setattr(InvalidRunLedger, "append", classmethod(fail_once))

    with pytest.raises(OSError, match="injected"):
        store.create(formal_record)

    target = store.create(formal_record)
    assert target.is_file()
    assert formal_record.run_id in InvalidRunLedger.load(store.ledger_path).run_ids
    assert not list(target.parent.glob("invalid-ledger.pending*"))


def test_concurrent_formal_creates_write_one_record_and_one_ledger_entry(
    store, formal_record
):
    workers = 16
    start = Barrier(workers)

    def create_once():
        start.wait()
        try:
            store.create(formal_record)
            return "created"
        except RunAlreadyExists:
            return "exists"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(lambda _: create_once(), range(workers)))

    assert outcomes.count("created") == 1
    assert outcomes.count("exists") == workers - 1
    ledger = InvalidRunLedger.load(store.ledger_path)
    assert [entry["run_id"] for entry in ledger.entries] == [formal_record.run_id]
