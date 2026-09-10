"""Event store and checkpoint tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.core.event_store import CheckpointError, EventStore


def _store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events.jsonl")


def test_append_writes_and_loads(tmp_path):
    store = _store(tmp_path)
    store.append("MODEL-1", "attempt_started", {})
    store.append("MODEL-1", "attempt_succeeded", {"attempt": 1})
    events = store.load_events()
    assert len(events) == 2
    assert events[0].event_seq == 1
    assert events[1].event_seq == 2
    assert events[1].previous_event_digest == events[0].event_digest


def test_event_chain_detects_reorder(tmp_path):
    store = _store(tmp_path)
    store.append("MODEL-1", "attempt_started", {})
    store.append("MODEL-1", "attempt_succeeded", {"attempt": 1})
    _swap_jsonl_lines(store.path, 0, 1)
    with pytest.raises(Exception):
        store.validate_chain()


def test_event_chain_detects_mutation(tmp_path):
    store = _store(tmp_path)
    store.append("MODEL-1", "attempt_started", {})
    store.append("MODEL-1", "attempt_succeeded", {"attempt": 1})
    lines = store.path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[0])
    forged["payload"] = {"hijacked": True}
    store.path.write_text(
        json.dumps(forged, sort_keys=True) + "\n" + lines[1] + "\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        store.validate_chain()


def test_checkpoint_write_and_load(tmp_path):
    store = _store(tmp_path)
    store.write_checkpoint({"lock_digest": "sha256:a", "cursor": 4})
    cp = store.load_checkpoint("sha256:a")
    assert cp is not None
    assert cp.state["cursor"] == 4


def test_checkpoint_rejects_changed_lock(tmp_path):
    store = _store(tmp_path)
    store.write_checkpoint({"lock_digest": "sha256:a", "cursor": 4})
    with pytest.raises(CheckpointError, match="lock digest"):
        store.load_checkpoint("sha256:b")


def test_checkpoint_returns_none_when_absent(tmp_path):
    store = _store(tmp_path)
    assert store.load_checkpoint() is None


def test_checkpoint_pointer_is_small_and_replaceable(tmp_path):
    store = _store(tmp_path)
    store.write_checkpoint({"lock_digest": "sha256:a", "cursor": 1})
    store.write_checkpoint({"lock_digest": "sha256:a", "cursor": 2})
    cp = store.load_checkpoint("sha256:a")
    assert cp is not None
    assert cp.state["cursor"] == 2
    pointer = tmp_path / "checkpoint-pointer.json"
    assert pointer.is_file()
    assert len(pointer.read_bytes()) < 256


def test_append_is_append_only(tmp_path):
    store = _store(tmp_path)
    store.append("OP-1", "a", {})
    first_line_count = len(store.path.read_text(encoding="utf-8").splitlines())
    store.append("OP-1", "b", {})
    assert len(store.path.read_text(encoding="utf-8").splitlines()) == first_line_count + 1


def test_corrupted_checkpoint_pointer_raises(tmp_path):
    store = _store(tmp_path)
    store.write_checkpoint({"lock_digest": "sha256:a", "cursor": 1})
    pointer = tmp_path / "checkpoint-pointer.json"
    pointer.write_text(json.dumps({"checkpoint_digest": "sha256:unknown"}), encoding="utf-8")
    with pytest.raises(CheckpointError, match="checkpoint file missing"):
        store.load_checkpoint()


def _swap_jsonl_lines(path: Path, i: int, j: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[i], lines[j] = lines[j], lines[i]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
