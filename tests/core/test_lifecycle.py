"""Lifecycle state machine: explicit legal edges, immutable events."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ccbench.core.lifecycle import InvalidTransition, Lifecycle, RunPhase

NOW = datetime(2026, 8, 18, tzinfo=timezone.utc)


def _full_local_sequence() -> Lifecycle:
    lifecycle = Lifecycle(run_id="r1", at=NOW)
    for phase in (
        RunPhase.PACKAGED,
        RunPhase.CANDIDATE_STARTING,
        RunPhase.CANDIDATE_RUNNING,
        RunPhase.CANDIDATE_STOPPING,
        RunPhase.CANDIDATE_FROZEN,
        RunPhase.SUBMISSION_COLLECTED,
        RunPhase.CANDIDATE_DESTROYED,
        RunPhase.STRUCTURALLY_VALIDATED,
        RunPhase.QUARANTINED,
        RunPhase.SEALED,
        RunPhase.VERIFYING,
        RunPhase.COMPLETED,
    ):
        lifecycle.transition(phase, NOW)
    return lifecycle


def test_verifier_cannot_start_before_candidate_is_destroyed():
    lifecycle = Lifecycle(run_id="r1")
    lifecycle.transition(RunPhase.PACKAGED, NOW)
    with pytest.raises(InvalidTransition):
        lifecycle.transition(RunPhase.VERIFYING, NOW)


def test_full_local_sequence_reaches_completed():
    lifecycle = _full_local_sequence()
    assert lifecycle.phase is RunPhase.COMPLETED


def test_structural_validation_gate_precedes_quarantine():
    """A candidate cannot be quarantined before STRUCTURALLY_VALIDATED."""
    lifecycle = Lifecycle(run_id="r1", at=NOW)
    for phase in (
        RunPhase.PACKAGED,
        RunPhase.CANDIDATE_STARTING,
        RunPhase.CANDIDATE_RUNNING,
        RunPhase.CANDIDATE_STOPPING,
        RunPhase.CANDIDATE_FROZEN,
        RunPhase.SUBMISSION_COLLECTED,
        RunPhase.CANDIDATE_DESTROYED,
    ):
        lifecycle.transition(phase, NOW)
    with pytest.raises(InvalidTransition):
        lifecycle.transition(RunPhase.QUARANTINED, NOW)
    lifecycle.transition(RunPhase.STRUCTURALLY_VALIDATED, NOW)
    lifecycle.transition(RunPhase.QUARANTINED, NOW)
    assert lifecycle.phase is RunPhase.QUARANTINED


def test_structural_validation_rejects_skipping_candidate_destroyed():
    """STRUCTURALLY_VALIDATED is only reachable after CANDIDATE_DESTROYED."""
    lifecycle = Lifecycle(run_id="r1", at=NOW)
    lifecycle.transition(RunPhase.PACKAGED, NOW)
    with pytest.raises(InvalidTransition):
        lifecycle.transition(RunPhase.STRUCTURALLY_VALIDATED, NOW)


def test_no_direct_jump_packaged_to_frozen():
    lifecycle = Lifecycle(run_id="r1")
    lifecycle.transition(RunPhase.PACKAGED, NOW)
    with pytest.raises(InvalidTransition):
        lifecycle.transition(RunPhase.CANDIDATE_FROZEN, NOW)


def test_no_backward_transition():
    lifecycle = Lifecycle(run_id="r1")
    lifecycle.transition(RunPhase.PACKAGED, NOW)
    with pytest.raises(InvalidTransition):
        lifecycle.transition(RunPhase.CREATED, NOW)


def test_terminal_failure_enterable_early():
    lifecycle = Lifecycle(run_id="r1")
    lifecycle.transition(RunPhase.PACKAGED, NOW)
    lifecycle.transition(RunPhase.INVALID_INFRA, NOW, reason="docker daemon down")
    assert lifecycle.phase is RunPhase.INVALID_INFRA


@pytest.mark.parametrize("terminal", tuple(RunPhase)[-3:])
def test_terminal_phase_has_no_outgoing_transition(terminal):
    lifecycle = Lifecycle(run_id="r1", at=NOW)
    if terminal is RunPhase.COMPLETED:
        for phase in (
            RunPhase.PACKAGED,
            RunPhase.CANDIDATE_STARTING,
            RunPhase.CANDIDATE_RUNNING,
            RunPhase.CANDIDATE_STOPPING,
            RunPhase.CANDIDATE_FROZEN,
            RunPhase.SUBMISSION_COLLECTED,
            RunPhase.CANDIDATE_DESTROYED,
            RunPhase.STRUCTURALLY_VALIDATED,
            RunPhase.QUARANTINED,
            RunPhase.SEALED,
            RunPhase.VERIFYING,
            RunPhase.COMPLETED,
        ):
            lifecycle.transition(phase, NOW)
    else:
        lifecycle.transition(terminal, NOW)

    for attempted in RunPhase:
        with pytest.raises(InvalidTransition):
            lifecycle.transition(attempted, NOW)


def test_events_are_immutable_and_ordered():
    lifecycle = _full_local_sequence()
    events = lifecycle.events
    assert len(events) == 13  # CREATED + 12 transitions
    assert events[0].phase is RunPhase.CREATED
    assert events[-1].phase is RunPhase.COMPLETED
    with pytest.raises(AttributeError):
        events[0].phase = RunPhase.SEALED  # frozen tuple of frozen events


def test_hpc_capability_edges_are_legal():
    lifecycle = Lifecycle(run_id="hpc-run", at=NOW)
    for phase in (
        RunPhase.PACKAGED,
        RunPhase.CANDIDATE_STARTING,
        RunPhase.CANDIDATE_RUNNING,
        RunPhase.CAPABILITY_ISSUED,
        RunPhase.CANDIDATE_RUNNING,
        RunPhase.CANDIDATE_STOPPING,
        RunPhase.SUBMISSIONS_DISABLED,
        RunPhase.JOBS_SETTLED,
        RunPhase.CAPABILITY_REVOKED,
        RunPhase.CANDIDATE_FROZEN,
    ):
        lifecycle.transition(phase, NOW)
    assert lifecycle.phase is RunPhase.CANDIDATE_FROZEN


def test_serialized_history_is_json_serializable():
    lifecycle = Lifecycle(run_id="r1", at=NOW)
    lifecycle.transition(RunPhase.PACKAGED, NOW)
    payload = lifecycle.to_dict()
    assert payload["run_id"] == "r1"
    assert payload["current"] == "PACKAGED"
    assert payload["events"][0]["phase"] == "CREATED"
    assert payload["events"][1]["phase"] == "PACKAGED"
