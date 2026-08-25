"""Durable invalid-run ledger.

One immutable JSONL entry per Formal (``skill-ablation-v1``) INFRA_INVALID
record, appended atomically at ``RunStore.create`` time.  Aggregation
(``aggregate``) refuses an unledgered record, a missing record, or a
duplicate conflicting entry, so a formal invalid run can never silently leave
or re-enter the ledger, and its stored record can never diverge from the
ledger entry (each entry pins a digest of the exact persisted record).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import fcntl

from dftworld_bench.contracts.result import ResultClass
from dftworld_bench.contracts.run_record import RunRecord
from dftworld_bench.experiments.ablation import FORMAL_EXPERIMENT_ID

LEDGER_SCHEMA = "invalid-run-ledger/v1"
_THREAD_LOCK = threading.Lock()


class LedgerError(Exception):
    """The invalid-run ledger is missing, duplicated, or conflicting."""


def _record_digest(record: RunRecord) -> str:
    canonical = json.dumps(record.to_dict(), sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _entry(record: RunRecord) -> dict[str, Any]:
    return {
        "schema": LEDGER_SCHEMA,
        "run_id": record.run_id,
        "record_digest": _record_digest(record),
        "result_class": record.result.result_class.value,
        "failure_code": (
            None
            if record.result.failure_code is None
            else record.result.failure_code.value
        ),
        "experiment_id": record.experiment_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }


@dataclass(frozen=True)
class InvalidRunLedger:
    entries: tuple[dict[str, Any], ...]
    path: Path | None = None

    @property
    def run_ids(self) -> set[str]:
        return {entry["run_id"] for entry in self.entries}

    @classmethod
    def should_ledger(cls, record: RunRecord) -> bool:
        """A record is ledgered iff it is a Formal INFRA_INVALID observation.

        SMOKE runs are never ledgered: their identity is real but non-formal,
        and the invalid-run ledger only tracks formal observations.  v1
        records (no run_mode field) predate RunMode and are formal by default.
        """
        return (
            record.result.result_class is ResultClass.INFRA_INVALID
            and record.experiment_id == FORMAL_EXPERIMENT_ID
            and getattr(record, "run_mode", "formal") == "formal"
        )

    @classmethod
    def load(cls, path: Path) -> "InvalidRunLedger":
        """Parse the JSONL ledger; duplicate run_ids are tampering."""
        path = Path(path)
        if not path.is_file():
            return cls(entries=(), path=path)
        entries: list[dict[str, Any]] = []
        seen: dict[str, int] = {}
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                entry = json.loads(line)
                run_id = str(entry["run_id"])
                if run_id in seen:
                    raise LedgerError(
                        f"duplicate ledger entry for run_id={run_id} "
                        f"(lines {seen[run_id]} and {lineno})"
                    )
                seen[run_id] = lineno
                entries.append(entry)
        return cls(entries=tuple(entries), path=path)

    @classmethod
    @contextmanager
    def exclusive_lock(cls, path: Path):
        """Serialize ledger transactions across threads and processes."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(path.name + ".lock")
        with _THREAD_LOCK:
            with open(lock_path, "a+b") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def record_digest(record: RunRecord) -> str:
        return _record_digest(record)

    @classmethod
    def append(
        cls,
        record: RunRecord,
        path: Path,
        *,
        assume_locked: bool = False,
        idempotent: bool = False,
    ) -> None:
        """Append one immutable entry under an exclusive ledger lock.

        Transaction recovery may request an idempotent append: an existing
        byte-equivalent record digest is accepted, while any conflict still
        fails closed. Direct callers retain create-once duplicate rejection.
        """
        path = Path(path)
        if assume_locked:
            cls._append_locked(record, path, idempotent=idempotent)
            return
        with cls.exclusive_lock(path):
            cls._append_locked(record, path, idempotent=idempotent)

    @classmethod
    def _append_locked(
        cls, record: RunRecord, path: Path, *, idempotent: bool
    ) -> None:
        existing = cls.load(path)
        matches = [entry for entry in existing.entries if entry["run_id"] == record.run_id]
        if matches:
            expected = _record_digest(record)
            if idempotent and matches[0].get("record_digest") == expected:
                return
            detail = "conflicting" if matches[0].get("record_digest") != expected else "duplicate"
            raise LedgerError(f"{detail} ledger entry for run_id={record.run_id}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(_entry(record), sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @classmethod
    def aggregate(
        cls,
        records: Iterable[RunRecord],
        path: Path,
    ) -> tuple[list[RunRecord], list[RunRecord]]:
        """Partition records into (formal candidates, ledgered invalids).

        Raises ``LedgerError`` on any inconsistency: a Formal INFRA_INVALID
        record with no ledger entry, a ledger entry whose record is missing,
        or an entry whose pinned digest conflicts with the stored record.
        """
        ledger = cls.load(path)
        ledger_ids = ledger.run_ids
        records = list(records)
        by_id = {record.run_id: record for record in records}

        formal_invalids = {
            record.run_id for record in records if cls.should_ledger(record)
        }
        missing_in_ledger = formal_invalids - ledger_ids
        if missing_in_ledger:
            raise LedgerError(
                "formal INFRA_INVALID record(s) not in the invalid ledger: "
                + ", ".join(sorted(missing_in_ledger))
            )
        ledger_without_record = ledger_ids - set(by_id)
        if ledger_without_record:
            raise LedgerError(
                "ledger entry with no matching record: "
                + ", ".join(sorted(ledger_without_record))
            )
        for run_id in sorted(ledger_ids):
            entry = next(e for e in ledger.entries if e["run_id"] == run_id)
            if entry["record_digest"] != _record_digest(by_id[run_id]):
                raise LedgerError(
                    f"ledger entry conflicts with stored record for run_id={run_id}"
                )

        candidates = [record for record in records if not cls.should_ledger(record)]
        invalids = [by_id[run_id] for run_id in sorted(ledger_ids)]
        return candidates, invalids
