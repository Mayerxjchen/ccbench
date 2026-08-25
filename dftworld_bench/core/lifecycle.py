"""Trusted attempt lifecycle.

Explicit legal edges only: no backward transitions, no direct jumps. The
Verifier (``VERIFYING``) is reachable only after ``CANDIDATE_DESTROYED``.
Every transition is an immutable event ``{phase, utc_timestamp, reason?}``;
the history is never rewritten. Terminal failure classes (``FAILED_AGENT``,
``INVALID_INFRA``) are enterable from any non-terminal phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RunPhase(Enum):
    CREATED = "CREATED"
    PACKAGED = "PACKAGED"
    CANDIDATE_STARTING = "CANDIDATE_STARTING"
    CANDIDATE_RUNNING = "CANDIDATE_RUNNING"
    CAPABILITY_ISSUED = "CAPABILITY_ISSUED"  # HPC
    CANDIDATE_STOPPING = "CANDIDATE_STOPPING"
    SUBMISSIONS_DISABLED = "SUBMISSIONS_DISABLED"  # HPC
    JOBS_SETTLED = "JOBS_SETTLED"  # HPC
    CAPABILITY_REVOKED = "CAPABILITY_REVOKED"  # HPC
    CANDIDATE_FROZEN = "CANDIDATE_FROZEN"
    SUBMISSION_COLLECTED = "SUBMISSION_COLLECTED"
    CANDIDATE_DESTROYED = "CANDIDATE_DESTROYED"
    STRUCTURALLY_VALIDATED = "STRUCTURALLY_VALIDATED"
    QUARANTINED = "QUARANTINED"
    SEALED = "SEALED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED_AGENT = "FAILED_AGENT"
    INVALID_INFRA = "INVALID_INFRA"


TERMINAL_PHASES = frozenset(
    {RunPhase.COMPLETED, RunPhase.FAILED_AGENT, RunPhase.INVALID_INFRA}
)

_LEGAL_EDGES: dict[RunPhase, frozenset[RunPhase]] = {
    RunPhase.CREATED: frozenset({RunPhase.PACKAGED}),
    RunPhase.PACKAGED: frozenset({RunPhase.CANDIDATE_STARTING}),
    RunPhase.CANDIDATE_STARTING: frozenset({RunPhase.CANDIDATE_RUNNING}),
    RunPhase.CANDIDATE_RUNNING: frozenset(
        {RunPhase.CANDIDATE_STOPPING, RunPhase.CAPABILITY_ISSUED}
    ),
    RunPhase.CAPABILITY_ISSUED: frozenset(
        {RunPhase.CANDIDATE_RUNNING, RunPhase.CANDIDATE_STOPPING}
    ),
    RunPhase.CANDIDATE_STOPPING: frozenset(
        {RunPhase.SUBMISSIONS_DISABLED, RunPhase.CANDIDATE_FROZEN}
    ),
    RunPhase.SUBMISSIONS_DISABLED: frozenset(
        {RunPhase.JOBS_SETTLED, RunPhase.CANDIDATE_FROZEN}
    ),
    RunPhase.JOBS_SETTLED: frozenset(
        {RunPhase.CAPABILITY_REVOKED, RunPhase.CANDIDATE_FROZEN}
    ),
    RunPhase.CAPABILITY_REVOKED: frozenset({RunPhase.CANDIDATE_FROZEN}),
    RunPhase.CANDIDATE_FROZEN: frozenset({RunPhase.SUBMISSION_COLLECTED}),
    RunPhase.SUBMISSION_COLLECTED: frozenset({RunPhase.CANDIDATE_DESTROYED}),
    # The structural submission gate sits between collection and quarantine:
    # a candidate can only be quarantined after it passed the shape checks.
    RunPhase.CANDIDATE_DESTROYED: frozenset({RunPhase.STRUCTURALLY_VALIDATED}),
    RunPhase.STRUCTURALLY_VALIDATED: frozenset({RunPhase.QUARANTINED}),
    RunPhase.QUARANTINED: frozenset({RunPhase.SEALED}),
    RunPhase.SEALED: frozenset({RunPhase.VERIFYING}),
    RunPhase.VERIFYING: frozenset({RunPhase.COMPLETED}),
    # Terminal failure classes are enterable from any non-terminal phase.
    RunPhase.FAILED_AGENT: frozenset(),
    RunPhase.INVALID_INFRA: frozenset(),
}
for _from in _LEGAL_EDGES.keys() - TERMINAL_PHASES:
    _LEGAL_EDGES[_from] = _LEGAL_EDGES[_from] | frozenset(
        {RunPhase.FAILED_AGENT, RunPhase.INVALID_INFRA}
    )


class InvalidTransition(RuntimeError):
    def __init__(self, current: RunPhase, attempted: RunPhase) -> None:
        super().__init__(
            f"invalid lifecycle transition {current.value} -> {attempted.value}"
        )
        self.current = current
        self.attempted = attempted


@dataclass(frozen=True)
class TransitionEvent:
    phase: RunPhase
    at: datetime
    reason: str | None = None


class Lifecycle:
    """Immutable-history state machine for a single attempt."""

    def __init__(self, run_id: str, at: datetime | None = None) -> None:
        self.run_id = run_id
        self._events: list[TransitionEvent] = []
        born = at if at is not None else datetime.now(timezone.utc)
        self._events.append(TransitionEvent(RunPhase.CREATED, born))
        self._current = RunPhase.CREATED

    @property
    def phase(self) -> RunPhase:
        return self._current

    @property
    def events(self) -> tuple[TransitionEvent, ...]:
        return tuple(self._events)

    def transition(self, next_phase: RunPhase, at: datetime, reason: str | None = None) -> None:
        if next_phase not in _LEGAL_EDGES.get(self._current, frozenset()):
            raise InvalidTransition(self._current, next_phase)
        self._events.append(TransitionEvent(next_phase, at, reason))
        self._current = next_phase

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "current": self._current.value,
            "events": [
                {
                    "phase": event.phase.value,
                    "utc_timestamp": event.at.isoformat(),
                    "reason": event.reason,
                }
                for event in self._events
            ],
        }
