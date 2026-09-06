"""Durable, resumable run coordinator and external wait.

The coordinator owns the append-only session (EventStore events + content-
addressed checkpoints) for one attempt.  Every logical side effect is an
ACTIVITY identified by an operation id ``<run_id>/<activity>``.  Before
executing, replay inspects the VALIDATED event chain: a committed activity is
skipped (exactly-once); an uncommitted activity executes and commits with an
appended ``activity_committed`` event plus a checkpoint.  A crash at any
boundary therefore never double-commits a logical effect.

External scheduler wait is Coordinator-owned: it polls through the trusted
waiter WITHOUT an LLM call, charges ``scheduler_wait_ms`` (never a model turn),
and resumes the Agent only on terminal/progress/deadline events.  Formal
profiles prohibit model-driven ``sleep`` polling.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Protocol

from ccbench.config.profiles import canonical_json, digest_bytes
from ccbench.core.budgets import BudgetLedger
from ccbench.core.event_store import EventStore
from ccbench.executors.base import ExecutionContext, Executor


class SimulatedCrash(RuntimeError):
    """Test seam: a controlled crash at a named activity boundary."""


class LockMismatchError(ValueError):
    """Resume requires the identical resolved lock digest."""


# Logical effects that may each commit exactly once per attempt.
ACTIVITY_ORDER = (
    "model_response",
    "tool_result",
    "job_submit",
    "artifact_fetch",
    "submission_seal",
    "verifier_result",
)

_COMMITTED = "activity_committed"


class ActivityExecutor(Protocol):
    """Executes one logical effect; must be idempotent per operation id."""

    async def execute(self, operation_id: str, activity: str) -> Any: ...


class HpcWaiter(Protocol):
    """Trusted external waiter. Never calls an LLM."""

    async def wait(
        self,
        job_ids: tuple[str, ...],
        deadline: float,
        ledger: BudgetLedger | None,
    ) -> dict[str, str]: ...


@dataclass(frozen=True)
class ExternalWait:
    """A Coordinator-owned external wait: poll without an LLM call."""

    job_ids: tuple[str, ...]
    deadline: float
    states: dict[str, str] | None = None


class FormalToolPolicyError(ValueError):
    """Raised when a Formal tool call violates the frozen tool policy."""


def validate_formal_tool_call(tool_name: str) -> None:
    """Reject model-driven sleep polling in Formal profiles.

    Queue latency must not be measured as Agent verbosity; external waits are
    Coordinator-owned.  ``run_command sleep`` is still permitted as a compute
    action; only the dedicated ``sleep`` tool is blocked.
    """
    if tool_name == "sleep":
        raise FormalToolPolicyError(
            "model-driven sleep polling is prohibited in Formal profiles; "
            "external waits are Coordinator-owned"
        )


class RunCoordinator:
    """Durable session machine for one attempt, resumable after any crash."""

    def __init__(
        self,
        *,
        run_id: str,
        lock_digest: str,
        executor: ActivityExecutor,
        events: EventStore,
        ledger: BudgetLedger | None = None,
        hpc_waiter: HpcWaiter | None = None,
        crash_after: str | None = None,
        activity_order: tuple[str, ...] = ACTIVITY_ORDER,
        case_executor: Executor | None = None,
        execution_context: ExecutionContext | None = None,
    ):
        if crash_after is not None and crash_after not in activity_order:
            raise ValueError(f"unknown crash boundary: {crash_after!r}")
        self.run_id = run_id
        self.lock_digest = lock_digest
        self.executor = executor
        self.events = events
        self.ledger = ledger
        self.hpc_waiter = hpc_waiter
        self.crash_after = crash_after
        self.activity_order = activity_order
        # Optional case-executor lifecycle (Task 11): the coordinator owns
        # prepare/settle around the drive and close at teardown, so any driver
        # gets the case's execution semantics (e.g. hpc_controller gateway)
        # without touching this machine.  Default None keeps the coordinator
        # purely a durable activity machine.
        self.case_executor = case_executor
        self.execution_context = execution_context

    # -- public ---------------------------------------------------------------

    async def start(self) -> Any:
        """Drive all uncommitted activities, checkpointing after each."""
        await self._case_lifecycle("prepare")
        try:
            return await self._drive()
        finally:
            await self._case_lifecycle("settle")

    async def resume(self, run_id: str) -> Any:
        """Resume a crashed run. Requires the identical resolved lock.

        The validated chain and checkpoint are inspected first: committed
        activities are skipped; the run continues from the first uncommitted
        activity.  A checkpoint pinned to a different lock digest raises
        ``CheckpointError`` (EventStore) — never silently reuses stale state.
        """
        if run_id != self.run_id:
            raise LockMismatchError(
                f"resume run_id {run_id!r} != coordinator run_id {self.run_id!r}"
            )
        self._verify_resumable()
        await self._case_lifecycle("prepare")
        try:
            return await self._drive()
        finally:
            await self._case_lifecycle("settle")

    async def close(self) -> None:
        """Tear down the case executor (e.g. gateway shutdown); idempotent."""
        await self._case_lifecycle("close")

    async def yield_external(
        self,
        job_ids: tuple[str, ...],
        deadline: float,
    ) -> ExternalWait:
        """Coordinator-owned external wait: trusted poll, never an LLM call.

        Charges ``scheduler_wait_ms`` (via the waiter) — never a model turn —
        and checkpoints on completion so a crash mid-wait resumes cleanly.
        Replay-aware (I6): a wait with identical job_ids and deadline already
        completed in the durable event chain returns its recorded states
        instead of re-waiting and re-charging.
        """
        if self.hpc_waiter is None:
            raise ValueError("no hpc_waiter configured")
        op_id = f"{self.run_id}/external_wait"
        replayed = self._replay_external_wait(op_id, job_ids, deadline)
        if replayed is not None:
            return replayed
        self.events.append(
            op_id, "external_wait_started", {"job_ids": list(job_ids),
                                             "deadline": deadline}
        )
        states = await self.hpc_waiter.wait(tuple(job_ids), deadline, self.ledger)
        self.events.append(
            op_id, "external_wait_completed", {"states": states}
        )
        self._checkpoint(phase="external_wait")
        return ExternalWait(job_ids=tuple(job_ids), deadline=deadline, states=states)

    def _replay_external_wait(
        self,
        op_id: str,
        job_ids: tuple[str, ...],
        deadline: float,
    ) -> ExternalWait | None:
        matched_started = False
        for event in self.events.load_events():
            if event.operation_id != op_id:
                continue
            if event.kind == "external_wait_started":
                matched_started = (
                    list(event.payload.get("job_ids") or []) == list(job_ids)
                    and float(event.payload.get("deadline")) == float(deadline)
                )
            elif event.kind == "external_wait_completed" and matched_started:
                return ExternalWait(
                    job_ids=tuple(job_ids),
                    deadline=float(deadline),
                    states=dict(event.payload.get("states") or {}),
                )
        return None

    # -- internals ------------------------------------------------------------

    async def _case_lifecycle(self, phase: str) -> None:
        """Call ``phase`` on the optional case executor, if it has the hook.

        Sync and async hooks are both accepted; a missing hook (e.g. a minimal
        executor without ``settle``) is a no-op.  Never dispatches on a case id.
        """
        if self.case_executor is None:
            return
        hook = getattr(self.case_executor, phase, None)
        if hook is None:
            return
        result = hook(self.execution_context)
        if inspect.isawaitable(result):
            await result

    def _verify_resumable(self) -> None:
        checkpoint = self.events.load_checkpoint(self.lock_digest)
        if checkpoint is not None:
            # load_checkpoint already raised on digest mismatch; the state
            # cursor is what we resume from (skipped via committed events).
            state = checkpoint.state
            if state.get("run_id") != self.run_id:
                raise LockMismatchError(
                    f"checkpoint run_id {state.get('run_id')!r} != {self.run_id!r}"
                )

    async def _drive(self) -> Any:
        result: Any = None
        for activity in self.activity_order:
            op_id = f"{self.run_id}/{activity}"
            if self._activity_committed(op_id):
                committed = self._committed_outcome(op_id)
                if committed is not None:
                    result = committed
                continue
            self.events.append(
                op_id, "activity_started", {"activity": activity}
            )
            outcome = await self.executor.execute(op_id, activity)
            self._commit(op_id, activity, outcome)
            result = outcome
            if self.crash_after == activity:
                raise SimulatedCrash(f"crash after {activity}")
        return result

    def _committed_outcome(self, op_id: str) -> Any | None:
        for event in self.events.load_events():
            if event.operation_id == op_id and event.kind == _COMMITTED:
                return event.payload.get("outcome")
        return None

    def _activity_committed(self, op_id: str) -> bool:
        for event in self.events.load_events():
            if event.operation_id == op_id and event.kind == _COMMITTED:
                return True
        return False

    def _commit(self, op_id: str, activity: str, outcome: Any) -> None:
        payload: dict[str, Any] = {"activity": activity}
        if outcome is not None:
            payload["outcome_digest"] = digest_bytes(
                canonical_json(_outcome_dict(outcome))
            )
            payload["outcome"] = _outcome_dict(outcome)
        self.events.append(op_id, _COMMITTED, payload)
        self._checkpoint(phase=activity)

    def _checkpoint(self, *, phase: str) -> None:
        cursor = {
            f"{self.run_id}/{activity}": activity
            for activity in self.activity_order
            if self._activity_committed(f"{self.run_id}/{activity}")
        }
        state = {
            "lock_digest": self.lock_digest,
            "run_id": self.run_id,
            "phase": phase,
            "cursor": cursor,
            "budgets": self.ledger.snapshot() if self.ledger is not None else {},
        }
        self.events.write_checkpoint(state)


def _outcome_dict(outcome: Any) -> dict[str, Any]:
    if isinstance(outcome, dict):
        return outcome
    if hasattr(outcome, "to_dict"):
        return outcome.to_dict()
    if hasattr(outcome, "__dict__"):
        return {
            k: v for k, v in outcome.__dict__.items()
            if not k.startswith("_")
        }
    return {"value": str(outcome)}
