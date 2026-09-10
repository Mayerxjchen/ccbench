"""Hash-chained run event contract.

Each event references its predecessor by digest, forming an append-only,
tamper-evident chain. Events are canonicalized (sorted keys, no whitespace)
before hashing so the digest is deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bench.config.profiles import canonical_json, digest_bytes


class EventChainError(ValueError):
    """Raised when the event chain is mutated, reordered, or truncated."""


@dataclass(frozen=True)
class RunEvent:
    """One immutable, hash-chained event."""

    event_seq: int
    event_id: str
    operation_id: str
    kind: str
    previous_event_digest: str | None
    timestamp: str
    payload: dict[str, Any]
    event_digest: str

    @classmethod
    def create(
        cls,
        *,
        event_seq: int,
        operation_id: str,
        kind: str,
        timestamp: str,
        payload: dict[str, Any],
        previous_event_digest: str | None,
    ) -> "RunEvent":
        body = {
            "event_seq": event_seq,
            "event_id": f"evt-{event_seq:08d}",
            "operation_id": operation_id,
            "kind": kind,
            "previous_event_digest": previous_event_digest,
            "timestamp": timestamp,
            "payload": payload,
        }
        digest = digest_bytes(canonical_json(body))
        return cls(
            event_seq=event_seq,
            event_id=body["event_id"],
            operation_id=operation_id,
            kind=kind,
            previous_event_digest=previous_event_digest,
            timestamp=timestamp,
            payload=payload,
            event_digest=digest,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunEvent":
        return cls(
            event_seq=int(data["event_seq"]),
            event_id=str(data["event_id"]),
            operation_id=str(data["operation_id"]),
            kind=str(data["kind"]),
            previous_event_digest=data["previous_event_digest"],
            timestamp=str(data["timestamp"]),
            payload=data["payload"],
            event_digest=str(data["event_digest"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_seq": self.event_seq,
            "event_id": self.event_id,
            "operation_id": self.operation_id,
            "kind": self.kind,
            "previous_event_digest": self.previous_event_digest,
            "timestamp": self.timestamp,
            "payload": self.payload,
            "event_digest": self.event_digest,
        }

    @property
    def body_digest(self) -> str:
        """Digest of the canonical body (without the self event_digest)."""
        body = {
            "event_seq": self.event_seq,
            "event_id": self.event_id,
            "operation_id": self.operation_id,
            "kind": self.kind,
            "previous_event_digest": self.previous_event_digest,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }
        return digest_bytes(canonical_json(body))


def next_event(
    previous: RunEvent | None,
    operation_id: str,
    kind: str,
    payload: dict[str, Any],
    at: str,
) -> RunEvent:
    """Create the next chained event after ``previous`` (or the first)."""
    seq = 1 if previous is None else previous.event_seq + 1
    prev = None if previous is None else previous.event_digest
    return RunEvent.create(
        event_seq=seq,
        operation_id=operation_id,
        kind=kind,
        timestamp=at,
        payload=payload,
        previous_event_digest=prev,
    )


def validate_event_chain(events: list[RunEvent]) -> None:
    """Validate a chain: sequences contiguous, digests recompute, links match."""
    if not events:
        return
    for i, event in enumerate(events):
        if event.event_seq != i + 1:
            raise EventChainError(
                f"event_seq {event.event_seq} at position {i}; expected {i + 1}"
            )
        recomputed = event.body_digest
        if recomputed != event.event_digest:
            raise EventChainError(
                f"event {event.event_id} digest does not match its body "
                f"(chain mutated at event_seq {event.event_seq})"
            )
        if i > 0:
            expected_prev = events[i - 1].event_digest
            if event.previous_event_digest != expected_prev:
                raise EventChainError(
                    f"event {event.event_id} links to {event.previous_event_digest!r} "
                    f"but predecessor is {expected_prev!r} (chain reordered or "
                    "truncated)"
                )
    if events and events[0].previous_event_digest is not None:
        raise EventChainError("first event must have a null previous_event_digest")
