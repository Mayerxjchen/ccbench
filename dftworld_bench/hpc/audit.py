"""GatewayAudit: append-only, hash-chained record of trust-boundary actions.

Every capability mutation (token issue/revoke, job submit/cancel) appends one
entry whose ``prev`` field is the prior entry's digest.  Rewriting any past
entry breaks the chain for every entry after it, so a tampered audit log is
detectable by ``verify()``.  The log is also a file when a path is given, so it
survives gateway restarts.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class GatewayAudit:
    """Hash-chained event ledger for one gateway's trust-boundary actions."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._entries: list[dict[str, Any]] = []
        self._prev: str | None = None
        self._load()

    def append(self, event: dict[str, Any], *, durable: bool = False) -> str:
        """Append one event; returns the new entry's chain digest.

        Protects concurrent append with flock(LOCK_EX). Inside the lock:
        reloads the latest tail from disk, computes continuous seq and prev digest,
        writes, flushes, fsyncs, and updates in-memory entries.
        """
        if self._path is not None:
            import fcntl

            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a+", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.seek(0)
                    lines = [ln.strip() for ln in fh.read().splitlines() if ln.strip()]
                    entries = [json.loads(ln) for ln in lines]
                    last_seq = entries[-1]["seq"] if entries else 0
                    last_digest = entries[-1]["digest"] if entries else None

                    entry = {
                        "seq": last_seq + 1,
                        "prev": last_digest,
                        "event": event,
                    }
                    entry["digest"] = _digest(_canonical(entry))
                    fh.seek(0, os.SEEK_END)
                    fh.write(json.dumps(entry, sort_keys=True) + "\n")
                    fh.flush()
                    if durable:
                        os.fsync(fh.fileno())

                    self._entries = entries + [entry]
                    self._prev = entry["digest"]
                    return entry["digest"]
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        else:
            entry = {
                "seq": len(self._entries) + 1,
                "prev": self._prev,
                "event": event,
            }
            entry["digest"] = _digest(_canonical(entry))
            self._entries.append(entry)
            self._prev = entry["digest"]
            return entry["digest"]

    def tail_digest(self) -> str:
        """Returns the digest of the latest entry, or empty string if empty."""
        if self._path is not None and self._path.is_file():
            self._load()
        if not self._entries:
            return ""
        return str(self._entries[-1].get("digest") or "")

    def entries(self) -> list[dict[str, Any]]:
        """A copy of the entry list (the dicts are the live entries)."""
        if self._path is not None and self._path.is_file():
            self._load()
        return list(self._entries)

    def verify(self) -> list[str]:
        """Digests of every entry where the chain is broken, or ``[]`` when the
        trail is sound.  Each entry must hash to its stored digest AND link to
        the previous entry's digest."""
        broken: list[str] = []
        prev: str | None = None
        for entry in self._entries:
            if _digest(_canonical(entry)) != entry.get("digest"):
                broken.append(entry.get("digest", "<missing>"))
            if entry.get("prev") != prev:
                broken.append(entry.get("digest", "<missing>"))
            prev = entry.get("digest")
        return broken

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        entries: list[dict[str, Any]] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            entries.append(entry)
        self._entries = entries
        self._prev = entries[-1]["digest"] if entries else None


def _canonical(entry: dict[str, Any]) -> str:
    body = {k: v for k, v in entry.items() if k != "digest"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"))
