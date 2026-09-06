"""Append-only, hash-chained event and checkpoint store.

Events are appended to a JSONL file with flush + fsync and never rewritten.
Checkpoints are written atomically to a content-addressed file; a small pointer
file is updated with ``os.replace`` so readers never observe a partial state.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ccbench.config.profiles import canonical_json, digest_bytes
from ccbench.contracts.events import (
    EventChainError,
    RunEvent,
    next_event,
    validate_event_chain,
)


class CheckpointError(ValueError):
    """Raised when a checkpoint is missing, conflicted, or corrupted."""


@dataclass(frozen=True)
class Checkpoint:
    """A durable, content-addressed state snapshot."""

    state: dict[str, Any]
    digest: str

    @classmethod
    def create(cls, state: dict[str, Any]) -> "Checkpoint":
        canonical = canonical_json(state)
        return cls(json.loads(canonical), digest_bytes(canonical))

    def to_dict(self) -> dict[str, Any]:
        return {**self.state, "checkpoint_digest": self.digest}


class EventStore:
    """Append-only event log with hash-chained integrity and checkpoints."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- events --------------------------------------------------------------

    def append(
        self,
        operation_id: str,
        kind: str,
        payload: dict[str, Any],
        at: str = "t0",
    ) -> RunEvent:
        """Append one event to the chain and fsync."""
        previous = self._last_event()
        event = next_event(previous, operation_id, kind, payload, at)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def _last_event(self) -> RunEvent | None:
        if not self.path.is_file():
            return None
        last_line = None
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    last_line = line
        if last_line is None:
            return None
        try:
            return RunEvent.from_dict(json.loads(last_line))
        except (json.JSONDecodeError, ValueError):
            # Truncated or corrupted last line — skip it and return None
            # so the next append can overwrite the damaged tail.
            return None

    def load_events(self) -> list[RunEvent]:
        """Load and validate the full event chain."""
        if not self.path.is_file():
            return []
        events: list[RunEvent] = []
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                events.append(RunEvent.from_dict(json.loads(line)))
        validate_event_chain(events)
        return events

    def validate_chain(self) -> None:
        """Validate the entire chain; raises EventChainError on tampering."""
        self.load_events()

    # -- checkpoints ---------------------------------------------------------

    def write_checkpoint(self, state: dict[str, Any]) -> Checkpoint:
        """Atomically persist a content-addressed checkpoint.

        The checkpoint file is named by its own digest; a tiny pointer file is
        replaced atomically so a crash never leaves a torn pointer. Both tmp
        files are fsynced before os.replace to prevent data loss on crash.
        """
        checkpoint = Checkpoint.create(state)
        data = checkpoint.to_dict()
        blob = (json.dumps(data, sort_keys=True) + "\n").encode("utf-8")
        checkpoint_path = self.path.parent / f"checkpoint-{checkpoint.digest}.json"
        tmp_path = checkpoint_path.with_suffix(".json.tmp")
        tmp_path.write_bytes(blob)
        with open(tmp_path, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp_path, checkpoint_path)

        pointer_path = self.path.parent / "checkpoint-pointer.json"
        pointer_tmp = pointer_path.with_suffix(".json.tmp")
        pointer_tmp.write_text(
            json.dumps({"checkpoint_digest": checkpoint.digest}, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        with open(pointer_tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(pointer_tmp, pointer_path)
        return checkpoint

    def load_checkpoint(self, lock_digest: str | None = None) -> Checkpoint | None:
        """Load the current checkpoint, optionally pinned to a lock digest."""
        pointer_path = self.path.parent / "checkpoint-pointer.json"
        if not pointer_path.is_file():
            return None
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        digest = pointer.get("checkpoint_digest")
        if not digest:
            raise CheckpointError("checkpoint pointer is empty")
        checkpoint_path = self.path.parent / f"checkpoint-{digest}.json"
        if not checkpoint_path.is_file():
            raise CheckpointError(f"checkpoint file missing: {checkpoint_path}")
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        state = {k: v for k, v in data.items() if k != "checkpoint_digest"}
        checkpoint = Checkpoint.create(state)
        if checkpoint.digest != digest:
            raise CheckpointError(
                "checkpoint body digest does not match the pointer; corrupted"
            )
        if lock_digest is not None:
            actual = state.get("lock_digest")
            if actual != lock_digest:
                raise CheckpointError(
                    f"checkpoint lock digest {actual!r} != requested {lock_digest!r}"
                )
        return checkpoint
