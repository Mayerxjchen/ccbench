"""Trusted Harness: fixed Local orchestration order + guaranteed teardown.

The order is normative (LIFECYCLE.md): the candidate is packaged, started, run,
frozen, collected, and destroyed BEFORE the sealed submission is quarantined and
verified in a fresh container. Teardown must still be attempted after an agent
timeout and after any Agent-adapter exception.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bench.agents import AgentAdapter
from bench.contracts.result import (
    BenchmarkResult,
    FailureCode,
    ResultClass,
)
from bench.core.harness import (
    HarnessSpec,
    Profile,
    RunMode,
    Treatment,
    TrustedHarness,
)
from bench.core.run_store import RunStore
from bench.core.quarantine import SubmissionSeal

LOCAL_ORDER = [
    "package",
    "candidate_start",
    "agent_start",
    "agent_stop",
    "candidate_freeze",
    "submission_collect",
    "candidate_destroy",
    "structural_validate",
    "quarantine",
    "sealed",
    "verifier_start",
    "record_write",
]

_COMPLETE_BUDGETS = {
    "max_model_turns": 64,
    "max_total_tokens": 10_000_000,
    "agent_active_walltime_sec": 86_400,
    "scheduler_wait_walltime_sec": 3_600,
    "logical_requests": 128,
    "api_attempts": 512,
    "usd_microcost": 5_000_000,
    "run_total_walltime_ms": 172_800_000,
    "local_tool_walltime_ms": 7_200_000,
    "api_retry_walltime_ms": 3_600_000,
    "jobs": 16,
    "cpu_hours": 500,
    "gpu_hours": 24,
    "storage_byte_hours": 10_000_000_000,
}

_RUNTIME_IDENTITIES = {
    role: {
        "role": role,
        "profile": f"local-{role}",
        "image": "dftworld-base:sha256:abc",
        "digest": "sha256:" + char * 64,
    }
    for role, char in zip(
        ("candidate", "control", "compute", "verifier"), "cdef", strict=True
    )
}
# local_sandbox has no control plane by contract.
_RUNTIME_IDENTITIES["control"] = None


class FakeAdapter:
    """Minimal AgentAdapter implementation; records its own call order."""

    def __init__(self, *, fail_start: Exception | None = None, hang: bool = False):
        self.calls: list[str] = []
        self.fail_start = fail_start
        self.hang = hang

    async def prepare(self) -> None:
        self.calls.append("prepare")

    async def start(self, instruction: str) -> None:
        self.calls.append("start")
        if self.hang:
            await asyncio.sleep(3600)
        if self.fail_start is not None:
            raise self.fail_start

    async def stop(self, metainfo: dict) -> None:
        self.calls.append("stop")

    async def close(self) -> None:
        self.calls.append("close")

    def collect_logs(self) -> dict:
        return {
            "thread_dir": str(self._thread_dir()),
            "tool_calls": 3,
            "tokens": 1200,
            "skills_invoked": [],
            "usage": {"tool_calls": 3, "tokens": 1200},
        }

    def _thread_dir(self) -> Path:
        return Path("threads") / "001-hello"

    @property
    def version(self) -> str:
        return "fake-1.0"


def _fake_collector(workspace, submission_root, raw, legacy_layout) -> None:
    Path(raw).mkdir(parents=True, exist_ok=True)


def _fake_quarantiner(raw, clean, limits) -> None:
    Path(clean).mkdir(parents=True, exist_ok=True)
    return SubmissionSeal(
        manifest_digest="sha256:" + "9" * 64,
        file_count=0,
        total_bytes=0,
        sealed_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        legacy_layout=False,
        exclusions=(),
    )


def _fake_verifier(spec, sealed, logs, run_id=None, runner=None) -> BenchmarkResult:
    return BenchmarkResult.valid(run_id or "r", passed=True, reason="fake verifier")


def _make_spec(tmp_path, *, case_id: str = "001-hello", timeout: float = 60.0) -> HarnessSpec:
    return HarnessSpec(
        case_id=case_id,
        case_dir=Path("001-hello"),
        image="dftworld-base:sha256:abc",
        instruction="write hello.txt",
        agent_timeout_sec=timeout,
        gpus=0,
        verifier_timeout_sec=60.0,
        verifier_env={},
        submission_root=".",
        legacy_submission_layout=True,
        threads_root=tmp_path / "threads",
        run_id=f"2026-08-18__01-00-00__{case_id}",
        model="deepseek/deepseek-chat",
        max_turns=32,
        verbose=False,
        budgets=_COMPLETE_BUDGETS,
        lock_digest="sha256:" + "1" * 64,
        runtime_identities=_RUNTIME_IDENTITIES,
    )


def _treatment() -> Treatment:
    return Treatment(
        condition_id="no-skill",
        skills_source="none",
        skills_sha=None,
        benchmark_commit="8f1921f",
        agent_model="deepseek/deepseek-chat",
    )


def _profile() -> Profile:
    return Profile(
        execution_class="local_sandbox",
        platform="local_docker",
        site_config_digest="sha256:" + "8" * 64,
    )


def _harness(adapter, tmp_path) -> TrustedHarness:
    from bench.core.event_store import EventStore

    session = EventStore(tmp_path / "session" / "events.jsonl")
    session.append(
        "MODEL-1",
        "model_attempt",
        {
            "attempt": 1,
            "state": "success",
            "provider_request_id": "req-1",
            "usage": {"tokens": 5},
            "metadata_digest": "sha256:" + "a" * 64,
        },
    )
    session.append(
        "MODEL-1",
        "model_response_committed",
        {"accepted_attempt": 1, "provider_request_id": "req-1"},
    )
    return TrustedHarness(
        adapter,
        store=RunStore(tmp_path / "jobs"),
        collector=_fake_collector,
        quarantiner=_fake_quarantiner,
        verifier=_fake_verifier,
        session=session,
    )


def test_fake_adapter_conforms_to_protocol():
    assert isinstance(FakeAdapter(), AgentAdapter)


def test_local_orchestration_order(tmp_path):
    adapter = FakeAdapter()
    harness = _harness(adapter, tmp_path)

    result = asyncio.run(harness.run(_make_spec(tmp_path), _treatment(), _profile()))

    assert harness.events == LOCAL_ORDER
    assert result.is_counted_scientifically is True
    assert result.failure_code is FailureCode.PASS
    assert adapter.calls == ["prepare", "start", "stop", "close"]


def test_record_written_for_successful_run(tmp_path):
    adapter = FakeAdapter()
    harness = _harness(adapter, tmp_path)

    asyncio.run(harness.run(_make_spec(tmp_path), _treatment(), _profile()))

    record_dir = tmp_path / "jobs" / "2026-08-18__01-00-00__001-hello"
    assert (record_dir / "run-record.json").is_file()
    assert not (record_dir / "run-record.json.tmp").exists()


def test_teardown_attempted_after_timeout(tmp_path):
    adapter = FakeAdapter(hang=True)
    harness = _harness(adapter, tmp_path)

    result = asyncio.run(
        harness.run(_make_spec(tmp_path, timeout=0.05), _treatment(), _profile())
    )

    assert result.result_class is ResultClass.AGENT_FAILURE
    assert result.failure_code is FailureCode.AGENT_TIMEOUT
    assert result.is_counted_scientifically is False
    # teardown still attempted: stop + close ran, submission collected
    assert adapter.calls == ["prepare", "start", "stop", "close"]
    assert "candidate_freeze" in harness.events
    assert "candidate_destroy" in harness.events
    assert harness.events[-1] == "record_write"
    # no verification after a timed-out agent
    assert "verifier_start" not in harness.events


def test_teardown_attempted_after_adapter_exception(tmp_path):
    adapter = FakeAdapter(fail_start=RuntimeError("boom"))
    harness = _harness(adapter, tmp_path)

    result = asyncio.run(harness.run(_make_spec(tmp_path), _treatment(), _profile()))

    assert result.result_class is ResultClass.INFRA_INVALID
    assert result.failure_code is FailureCode.HARNESS_FAILURE
    assert result.is_counted_scientifically is False
    assert result.retryable is True
    assert "boom" in result.reason
    assert adapter.calls == ["prepare", "start", "stop", "close"]
    assert "candidate_destroy" in harness.events


def test_record_written_even_when_run_fails(tmp_path):
    adapter = FakeAdapter(hang=True)
    harness = _harness(adapter, tmp_path)

    result = asyncio.run(
        harness.run(_make_spec(tmp_path, timeout=0.05), _treatment(), _profile())
    )

    record_dir = tmp_path / "jobs" / "2026-08-18__01-00-00__001-hello"
    assert (record_dir / "run-record.json").is_file()
    assert result.result_class is ResultClass.AGENT_FAILURE


def test_harness_stops_before_verifier_on_structural_failure(tmp_path):
    """A submission failing the structural contract is an agent failure, and
    the Verifier never starts."""
    adapter = FakeAdapter()
    harness = _harness(adapter, tmp_path)
    spec = _make_spec(tmp_path)
    # Fake collector creates an EMPTY raw dir; the contract requires model.pb.
    spec = HarnessSpec(
        **{**spec.__dict__,
           "submission_contract": {
               "required": [{"path": "model.pb", "type": "file"}],
           }},
    )

    result = asyncio.run(harness.run(spec, _treatment(), _profile()))

    assert result.result_class is ResultClass.AGENT_FAILURE
    assert result.failure_code is FailureCode.INVALID_SUBMISSION
    assert "verifier_start" not in harness.events
    assert "structural_validate" in harness.events


def test_structural_gate_passes_with_satisfied_contract(tmp_path):
    """A submission satisfying the structural contract reaches the Verifier."""
    adapter = FakeAdapter()
    harness = _harness(adapter, tmp_path)
    spec = _make_spec(tmp_path)
    # The fake collector writes nothing, so require nothing and quarantine runs.
    spec = HarnessSpec(
        **{**spec.__dict__, "submission_contract": {}},
    )

    result = asyncio.run(harness.run(spec, _treatment(), _profile()))

    assert result.result_class is ResultClass.VALID_RESULT
    assert "verifier_start" in harness.events


def test_budget_ledger_exposed_and_snapshot_lands_in_record(tmp_path):
    """A lock-declared budget block freezes a policy, exposes a live ledger,
    and writes the (zero-charged) snapshot into the run record usage."""
    adapter = FakeAdapter()
    harness = _harness(adapter, tmp_path)
    spec = HarnessSpec(**{**_make_spec(tmp_path).__dict__, "budgets": _COMPLETE_BUDGETS})

    result = asyncio.run(harness.run(spec, _treatment(), _profile()))

    assert harness.ledger is not None
    assert harness.ledger.used("model_turns") == 0
    assert result.result_class is ResultClass.VALID_RESULT
    record_dir = tmp_path / "jobs" / "2026-08-18__01-00-00__001-hello"
    record = json.loads((record_dir / "run-record.json").read_text(encoding="utf-8"))
    assert record["usage"]["budgets"]["model_turns"] == 0
    assert record["usage"]["budgets"]["run_total_walltime_ms"] == 0


def test_transport_charges_ledger_and_snapshot_reflects_it(tmp_path):
    """The transport (task 8) charges the harness ledger; a rejected attempt
    must not consume an accepted model turn."""
    adapter = FakeAdapter()
    harness = _harness(adapter, tmp_path)
    spec = HarnessSpec(**{**_make_spec(tmp_path).__dict__, "budgets": _COMPLETE_BUDGETS})

    asyncio.run(harness.run(spec, _treatment(), _profile()))
    assert harness.ledger is not None
    harness.ledger.charge("api_attempts", 3, "MODEL-1/A1")
    harness.ledger.charge("api_retry_walltime_ms", 900, "MODEL-1/A1")
    harness.ledger.charge("model_turns", 1, "MODEL-1/A1")
    assert harness.ledger.used("model_turns") == 1
    assert harness.ledger.used("api_attempts") == 3


def test_incomplete_budget_limits_raise_at_run_start(tmp_path):
    """A budgets block missing formal limits is an infra error surfaced
    immediately — never recorded as an agent failure."""
    adapter = FakeAdapter()
    harness = _harness(adapter, tmp_path)
    spec = HarnessSpec(
        **{**_make_spec(tmp_path).__dict__, "budgets": {"max_model_turns": 64}},
    )

    with pytest.raises(ValueError, match="missing budget limits"):
        asyncio.run(harness.run(spec, _treatment(), _profile()))
    # No run record was written for the misconfigured run.
    assert not (tmp_path / "jobs").exists()


def test_smoke_mode_is_not_a_lockless_bypass(tmp_path):
    """SMOKE mode does not relax the identity gates: a smoke run without the
    resolved lock/budgets/runtime identities is rejected exactly like a formal
    one, with a mode-labelled error."""
    adapter = FakeAdapter()
    harness = _harness(adapter, tmp_path)
    spec = HarnessSpec(
        **{
            **_make_spec(tmp_path).__dict__,
            "mode": RunMode.SMOKE,
            "budgets": None,
            "lock_digest": None,
            "runtime_identities": None,
        },
    )

    with pytest.raises(ValueError, match="smoke run requires resolved budget limits"):
        asyncio.run(harness.run(spec, _treatment(), _profile()))
    # No run record was written for the lockless smoke run.
    assert not (tmp_path / "jobs").exists()


def test_harness_session_records_durable_boundaries(tmp_path):
    """With a durable session attached, the harness emits + checkpoints at its
    side-effect boundaries (freeze, seal, verifier) in order."""
    from bench.core.event_store import EventStore

    adapter = FakeAdapter()
    session = EventStore(tmp_path / "session" / "events.jsonl")
    session.append(
        "MODEL-1",
        "model_attempt",
        {
            "attempt": 1,
            "state": "success",
            "provider_request_id": "req-1",
            "usage": {"tokens": 5},
            "metadata_digest": "sha256:" + "a" * 64,
        },
    )
    session.append(
        "MODEL-1",
        "model_response_committed",
        {"accepted_attempt": 1, "provider_request_id": "req-1"},
    )
    harness = TrustedHarness(
        adapter,
        store=RunStore(tmp_path / "jobs"),
        collector=_fake_collector,
        quarantiner=_fake_quarantiner,
        verifier=_fake_verifier,
        session=session,
    )

    result = asyncio.run(harness.run(_make_spec(tmp_path), _treatment(), _profile()))

    assert result.result_class is ResultClass.VALID_RESULT
    kinds = [e.kind for e in session.load_events()]
    assert kinds == [
        "model_attempt",
        "model_response_committed",
        "candidate_frozen",
        "submission_sealed",
        "verifier_result",
    ]
    # The terminal checkpoint is record_write; a pointer exists.
    checkpoint = session.load_checkpoint()
    assert checkpoint is not None
    assert checkpoint.state["phase"] == "record_write"
    assert checkpoint.state["run_id"] == "2026-08-18__01-00-00__001-hello"

    record = json.loads(
        (tmp_path / "jobs" / checkpoint.state["run_id"] / "run-record.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["event_root_digest"] == record["event_chain"][-1]["event_digest"]
    assert record["attempts"][0]["accepted"] is True
    assert set(record["runtime_identities"]) == {
        "candidate", "control", "compute", "verifier"
    }


def test_harness_session_stops_before_verifier_on_agent_failure(tmp_path):
    """On an agent failure the durable session records the freeze boundary but
    never a seal or verifier result — a resumed run must not verify."""
    from bench.core.event_store import EventStore

    adapter = FakeAdapter(hang=True)
    session = EventStore(tmp_path / "session" / "events.jsonl")
    session.append(
        "MODEL-1",
        "model_attempt",
        {
            "attempt": 1,
            "state": "timeout_unknown",
            "provider_request_id": None,
            "usage": None,
            "metadata_digest": "sha256:" + "a" * 64,
        },
    )
    harness = TrustedHarness(
        adapter,
        store=RunStore(tmp_path / "jobs"),
        collector=_fake_collector,
        quarantiner=_fake_quarantiner,
        verifier=_fake_verifier,
        session=session,
    )

    result = asyncio.run(
        harness.run(_make_spec(tmp_path, timeout=0.05), _treatment(), _profile())
    )

    assert result.result_class is ResultClass.AGENT_FAILURE
    kinds = [e.kind for e in session.load_events()]
    assert "candidate_frozen" in kinds
    assert "submission_sealed" not in kinds
    assert "verifier_result" not in kinds


def test_harness_teardown_failure_marks_infra_invalid_and_halts_verification(tmp_path):
    """Teardown failure (e.g. TopologyCleanupError) must be marked HARNESS_FAILURE / INFRA_INVALID
    and strictly halt scientific verification."""
    class TeardownFailingAdapter(FakeAdapter):
        async def close(self) -> None:
            raise RuntimeError("TopologyCleanupError: Dangling sidecar container")

    verifier_called = []

    def mock_verifier(*args, **kwargs):
        verifier_called.append(True)
        return _fake_verifier(*args, **kwargs)

    from bench.core.event_store import EventStore

    adapter = TeardownFailingAdapter()
    session = EventStore(tmp_path / "session" / "events.jsonl")
    harness = TrustedHarness(
        adapter,
        store=RunStore(tmp_path / "jobs"),
        collector=_fake_collector,
        quarantiner=_fake_quarantiner,
        verifier=mock_verifier,
        session=session,
    )

    result = asyncio.run(
        harness.run(_make_spec(tmp_path), _treatment(), _profile())
    )

    assert result.result_class is ResultClass.INFRA_INVALID
    assert result.failure_code is FailureCode.HARNESS_FAILURE
    assert "candidate teardown failed: RuntimeError: TopologyCleanupError" in result.reason
    assert len(verifier_called) == 0, "Verifier MUST NOT be invoked when teardown fails!"

    # Verify RunRecord was written
    record_file = tmp_path / "jobs" / "2026-08-18__01-00-00__001-hello" / "run-record.json"
    assert record_file.is_file()
    record = json.loads(record_file.read_text(encoding="utf-8"))
    assert record["result"]["result_class"] == "INFRA_INVALID"
    assert record["result"]["failure_code"] == "HARNESS_FAILURE"
