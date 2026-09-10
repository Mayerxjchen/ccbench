"""Durable run coordinator: crash/resume, exactly-once effects, external wait."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bench.core.budgets import BudgetLedger, BudgetPolicy
from bench.core.coordinator import (
    ACTIVITY_ORDER,
    ExternalWait,
    FormalToolPolicyError,
    LockMismatchError,
    RunCoordinator,
    SimulatedCrash,
    validate_formal_tool_call,
)
from bench.core.event_store import CheckpointError, EventStore


class ScriptedExecutor:
    """Records every execution; each operation id executes once."""

    def __init__(self):
        self.executed: list[tuple[str, str]] = []

    async def execute(self, operation_id: str, activity: str):
        self.executed.append((operation_id, activity))
        return {"activity": activity, "ok": True}


class FakeWaiter:
    """Trusted waiter: terminal states, charges scheduler wait, no LLM."""

    def __init__(self):
        self.calls = 0

    async def wait(self, job_ids, deadline, ledger):
        self.calls += 1
        if ledger is not None:
            ledger.charge("scheduler_wait_ms", 3_600_000, "WAIT-1")
        return {job_id: "COMPLETED" for job_id in job_ids}


def _policy() -> BudgetPolicy:
    return BudgetPolicy(
        limits={
            "model_turns": 64,
            "logical_requests": 128,
            "api_attempts": 512,
            "tokens": 10_000_000,
            "usd_microcost": 5_000_000,
            "agent_active_walltime_ms": 86_400_000,
            "run_total_walltime_ms": 172_800_000,
            "local_tool_walltime_ms": 7_200_000,
            "api_retry_walltime_ms": 3_600_000,
            "scheduler_wait_ms": 3_600_000,
            "jobs": 16,
            "cpu_hours": 500,
            "gpu_hours": 24,
            "storage_byte_hours": 10_000_000_000,
        }
    )


def _store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events.jsonl")


@pytest.mark.parametrize(
    "crash_after",
    [
        "model_response",
        "tool_result",
        "job_submit",
        "artifact_fetch",
        "submission_seal",
        "verifier_result",
    ],
)
def test_resume_commits_each_logical_effect_once(crash_after, tmp_path):
    store = _store(tmp_path)
    executor = ScriptedExecutor()
    coordinator = RunCoordinator(
        run_id="RUN-1",
        lock_digest="sha256:lock",
        executor=executor,
        events=store,
        crash_after=crash_after,
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(coordinator.start())
    first_run = list(executor.executed)

    fresh = ScriptedExecutor()
    resumed = RunCoordinator(
        run_id="RUN-1",
        lock_digest="sha256:lock",
        executor=fresh,
        events=store,
    )
    result = asyncio.run(resumed.resume("RUN-1"))
    assert result is not None

    executed = first_run + fresh.executed
    ops = [op_id for op_id, _ in executed]
    assert len(ops) == len(set(ops)) == len(ACTIVITY_ORDER)
    assert {op for op, _ in executed} == {f"RUN-1/{a}" for a in ACTIVITY_ORDER}

    # Each operation id committed exactly once in the validated chain.
    events = store.load_events()
    committed = [e for e in events if e.kind == "activity_committed"]
    assert len(committed) == len(ACTIVITY_ORDER)
    for activity in ACTIVITY_ORDER:
        op_id = f"RUN-1/{activity}"
        assert sum(1 for e in committed if e.operation_id == op_id) == 1


def test_start_is_idempotent_when_replayed(tmp_path):
    """Calling start() again on a fully-committed session must not re-execute."""
    store = _store(tmp_path)
    executor = ScriptedExecutor()
    coordinator = RunCoordinator(
        run_id="RUN-1", lock_digest="sha256:lock", executor=executor, events=store
    )
    asyncio.run(coordinator.start())
    second = ScriptedExecutor()
    again = RunCoordinator(
        run_id="RUN-1", lock_digest="sha256:lock", executor=second, events=store
    )
    result = asyncio.run(again.start())
    assert result is not None
    assert second.executed == []
    assert len(executor.executed) == len(ACTIVITY_ORDER)


def test_checkpoint_written_after_each_activity(tmp_path):
    store = _store(tmp_path)
    coordinator = RunCoordinator(
        run_id="RUN-1",
        lock_digest="sha256:lock",
        executor=ScriptedExecutor(),
        events=store,
        crash_after="job_submit",
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(coordinator.start())
    checkpoint = store.load_checkpoint("sha256:lock")
    assert checkpoint is not None
    assert checkpoint.state["run_id"] == "RUN-1"
    assert checkpoint.state["phase"] == "job_submit"
    assert set(checkpoint.state["cursor"]) == {
        f"RUN-1/{a}" for a in ("model_response", "tool_result", "job_submit")
    }


def test_resume_wrong_run_id_rejected(tmp_path):
    store = _store(tmp_path)
    coordinator = RunCoordinator(
        run_id="RUN-1", lock_digest="sha256:lock", executor=ScriptedExecutor(),
        events=store,
    )
    with pytest.raises(LockMismatchError, match="resume run_id"):
        asyncio.run(coordinator.resume("OTHER-RUN"))


def test_resume_rejects_changed_lock(tmp_path):
    """Resume requires the identical resolved lock digest."""
    store = _store(tmp_path)
    coordinator = RunCoordinator(
        run_id="RUN-1",
        lock_digest="sha256:lock",
        executor=ScriptedExecutor(),
        events=store,
        crash_after="model_response",
    )
    with pytest.raises(SimulatedCrash):
        asyncio.run(coordinator.start())

    drifted = RunCoordinator(
        run_id="RUN-1",
        lock_digest="sha256:DIFFERENT",
        executor=ScriptedExecutor(),
        events=store,
    )
    with pytest.raises(CheckpointError, match="lock digest"):
        asyncio.run(drifted.resume("RUN-1"))


def test_unknown_crash_boundary_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown crash boundary"):
        RunCoordinator(
            run_id="RUN-1",
            lock_digest="sha256:lock",
            executor=ScriptedExecutor(),
            events=_store(tmp_path),
            crash_after="bogus",
        )


def test_yield_external_polls_without_llm(tmp_path):
    ledger = BudgetLedger(_policy())
    store = _store(tmp_path)
    waiter = FakeWaiter()
    coordinator = RunCoordinator(
        run_id="RUN-1",
        lock_digest="sha256:lock",
        executor=ScriptedExecutor(),
        events=store,
        ledger=ledger,
        hpc_waiter=waiter,
    )
    wait = asyncio.run(coordinator.yield_external(("JOB-1", "JOB-2"), deadline=600.0))

    assert isinstance(wait, ExternalWait)
    assert wait.states == {"JOB-1": "COMPLETED", "JOB-2": "COMPLETED"}
    # scheduler wait charges walltime only — never an accepted model turn
    assert ledger.used("scheduler_wait_ms") == 3_600_000
    assert ledger.used("model_turns") == 0
    kinds = [e.kind for e in store.load_events()]
    assert "external_wait_started" in kinds
    assert "external_wait_completed" in kinds
    assert store.load_checkpoint("sha256:lock") is not None


def test_yield_external_requires_waiter(tmp_path):
    coordinator = RunCoordinator(
        run_id="RUN-1",
        lock_digest="sha256:lock",
        executor=ScriptedExecutor(),
        events=_store(tmp_path),
    )
    with pytest.raises(ValueError, match="no hpc_waiter"):
        asyncio.run(coordinator.yield_external(("JOB-1",), 60.0))


def test_formal_tool_policy_rejects_sleep_polling():
    with pytest.raises(FormalToolPolicyError, match="sleep polling"):
        validate_formal_tool_call("sleep")
    # compute actions remain permitted; only the dedicated sleep tool is blocked
    validate_formal_tool_call("run_command")
    validate_formal_tool_call("read_file")


def test_external_wait_snapshot_lands_in_checkpoint(tmp_path):
    store = _store(tmp_path)
    coordinator = RunCoordinator(
        run_id="RUN-1",
        lock_digest="sha256:lock",
        executor=ScriptedExecutor(),
        events=store,
        hpc_waiter=FakeWaiter(),
    )
    asyncio.run(coordinator.yield_external(("JOB-1",), 60.0))
    checkpoint = store.load_checkpoint("sha256:lock")
    assert checkpoint is not None
    assert checkpoint.state["phase"] == "external_wait"


class RecordingCaseExecutor:
    """Records lifecycle calls for the Task 11 wiring test."""

    def __init__(self):
        self.calls: list[str] = []

    async def prepare(self, context):
        self.calls.append("prepare")
        context.local_gpus = 0

    async def execute(self, context):
        self.calls.append("execute")

    async def settle(self, context):
        self.calls.append("settle")

    async def close(self, context):
        self.calls.append("close")


def test_case_executor_lifecycle_wraps_drive(tmp_path):
    """Task 11: the coordinator owns prepare/settle around the drive and close
    at teardown — execution semantics come from the case executor (resolved by
    execution class), never from a case name inside this machine."""
    from bench.executors import ExecutionContext

    store = _store(tmp_path)
    case_exec = RecordingCaseExecutor()
    context = ExecutionContext(
        task=object(), threads_root=tmp_path, model="m", max_turns=8
    )
    coordinator = RunCoordinator(
        run_id="RUN-1",
        lock_digest="sha256:lock",
        executor=ScriptedExecutor(),
        events=store,
        case_executor=case_exec,
        execution_context=context,
    )
    asyncio.run(coordinator.start())
    assert case_exec.calls == ["prepare", "settle"]
    assert context.local_gpus == 0
    asyncio.run(coordinator.close())
    assert case_exec.calls == ["prepare", "settle", "close"]


def test_case_executor_optional_is_noop(tmp_path):
    """No case executor -> the coordinator is purely a durable activity
    machine (existing behavior unchanged)."""
    store = _store(tmp_path)
    activities = ScriptedExecutor()
    coordinator = RunCoordinator(
        run_id="RUN-1",
        lock_digest="sha256:lock",
        executor=activities,
        events=store,
    )
    result = asyncio.run(coordinator.start())
    assert result is not None
    assert len(activities.executed) == len(ACTIVITY_ORDER)
