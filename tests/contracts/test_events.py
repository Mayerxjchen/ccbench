"""Hash-chained run event contract tests."""

from __future__ import annotations

from ccbench.contracts.events import (
    EventChainError,
    RunEvent,
    next_event,
    validate_event_chain,
)


def test_first_event_has_null_previous_and_seq_1():
    event = next_event(None, "MODEL-1", "attempt_started", {}, at="t1")
    assert event.event_seq == 1
    assert event.event_id == "evt-00000001"
    assert event.previous_event_digest is None
    assert event.operation_id == "MODEL-1"


def test_events_chain_by_digest():
    first = next_event(None, "OP-1", "started", {"step": 1}, at="t1")
    second = next_event(first, "OP-1", "succeeded", {"step": 1}, at="t2")
    assert second.event_seq == 2
    assert second.previous_event_digest == first.event_digest


def test_event_digest_is_deterministic():
    a = next_event(None, "OP-1", "kind", {"k": "v"}, at="t1")
    b = next_event(None, "OP-1", "kind", {"k": "v"}, at="t1")
    assert a.event_digest == b.event_digest


def test_digest_ignores_key_order():
    a = next_event(None, "OP-1", "kind", {"a": 1, "b": 2}, at="t1")
    b = next_event(None, "OP-1", "kind", {"b": 2, "a": 1}, at="t1")
    assert a.event_digest == b.event_digest


def _build_chain(n: int, operation_id: str = "OP-1") -> list[RunEvent]:
    chain: list[RunEvent] = []
    prev = None
    for i in range(n):
        chain.append(next_event(prev, operation_id, f"kind-{i}", {"i": i}, at=f"t{i}"))
        prev = chain[-1]
    return chain


def test_valid_chain_passes():
    validate_event_chain(_build_chain(3))


def test_reordered_chain_is_detected():
    chain = _build_chain(3)
    # Swap the link of event 3 to point at event 1 instead of event 2.
    swapped = RunEvent(
        **{
            **chain[2].to_dict(),
            "previous_event_digest": chain[0].event_digest,
        }
    )
    try:
        validate_event_chain([chain[0], swapped])
    except EventChainError:
        pass
    else:
        raise AssertionError("reordered chain was not detected")


def test_mutated_payload_is_detected():
    chain = [next_event(None, "OP-1", "kind", {"value": 1}, at="t1")]
    forged = RunEvent(
        **{**chain[0].to_dict(), "payload": {"value": 2}},
    )
    try:
        validate_event_chain([forged])
    except EventChainError:
        pass
    else:
        raise AssertionError("mutated event was not detected")


def test_truncated_chain_is_detected():
    chain = _build_chain(3)
    # Truncation that drops the middle event leaves a non-contiguous seq.
    dropped_middle = [chain[0], chain[2]]
    try:
        validate_event_chain(dropped_middle)
    except EventChainError:
        pass
    else:
        raise AssertionError("truncated chain was not detected")


def test_event_round_trips_dict():
    event = next_event(None, "OP-1", "kind", {"k": "v"}, at="t1")
    restored = RunEvent.from_dict(event.to_dict())
    assert restored == event
