"""Append-only RunRecord store.

``RunStore.create`` is create-or-refuse: an existing ``run_id`` raises
``RunAlreadyExists`` and the record is never updated. Persistence is atomic —
the payload is written to ``run-record.json.tmp``, fsynced, validated against
``schemas/run-record.schema.json``, then renamed into place. The canonical
record lives at ``jobs/<run_id>/run-record.json``; ``summary.json`` remains a
derived compatibility view.

Only a secret-free ``site_config_digest`` is stored — never the site config
contents. ``legacy_normalized`` records when a legacy execution was normalized
into the record shape.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema

from bench.contracts.run_record import RunRecord, RunRecordError, RunRecordV2

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "run-record.schema.json"
RECORD_NAME = "run-record.json"
TMP_NAME = "run-record.json.tmp"
LEDGER_NAME = "invalid-ledger.jsonl"
PENDING_NAME = "invalid-ledger.pending.json"


class RunAlreadyExists(Exception):
    """A run with this run_id is already recorded and will never be updated."""


class RunStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def ledger_path(self) -> Path:
        """Durable invalid-run ledger (one immutable JSONL entry per Formal
        INFRA_INVALID record), appended atomically at ``create`` time."""
        return self.root / LEDGER_NAME

    def create(self, record: RunRecord) -> Path:
        if not isinstance(record, RunRecordV2) or record.schema_version != 2:
            raise RunRecordError("RunStore writes only v2 records; v1 is read-only")
        record.validate()
        payload = record.to_dict()
        self._validate_schema(payload)
        from bench.experiments.invalid_ledger import InvalidRunLedger

        if InvalidRunLedger.should_ledger(record):
            return self._create_formal_invalid(record, payload, InvalidRunLedger)
        return self._create_record(record.run_id, payload)

    def _create_record(self, run_id: str, payload: dict) -> Path:
        run_dir = self.root / run_id
        target = run_dir / RECORD_NAME
        if target.exists():
            raise RunAlreadyExists(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(target, payload, TMP_NAME)
        return target

    def _create_formal_invalid(self, record, payload: dict, ledger_cls) -> Path:
        """Commit record + invalid-ledger entry as a recoverable transaction."""
        run_dir = self.root / record.run_id
        target = run_dir / RECORD_NAME
        pending = run_dir / PENDING_NAME
        with ledger_cls.exclusive_lock(self.ledger_path):
            retrying_pending = pending.is_file()
            self._reconcile_pending_locked(ledger_cls)
            if retrying_pending:
                persisted = self.load(record.run_id)
                if ledger_cls.record_digest(persisted) != ledger_cls.record_digest(record):
                    raise RunAlreadyExists(record.run_id)
                return target
            if target.exists():
                raise RunAlreadyExists(record.run_id)

            run_dir.mkdir(parents=True, exist_ok=True)
            marker = {
                "schema": "invalid-ledger-transaction/v1",
                "run_id": record.run_id,
                "record_digest": ledger_cls.record_digest(record),
                "record": payload,
            }
            self._write_json_atomic(pending, marker, PENDING_NAME + ".tmp")
            try:
                self._write_json_atomic(target, payload, TMP_NAME)
                ledger_cls.append(
                    record,
                    self.ledger_path,
                    assume_locked=True,
                    idempotent=True,
                )
            except BaseException:
                # The fsynced marker deliberately survives. A retry or any
                # later create() reconciles it without rewriting the record.
                raise
            pending.unlink()
            self._fsync_directory(run_dir)
            return target

    def _reconcile_pending_locked(self, ledger_cls) -> None:
        """Finish every durable invalid-ledger transaction found under root."""
        if not self.root.is_dir():
            return
        for pending in sorted(self.root.glob(f"*/{PENDING_NAME}")):
            marker = json.loads(pending.read_text(encoding="utf-8"))
            if marker.get("schema") != "invalid-ledger-transaction/v1":
                raise RunRecordError(f"invalid ledger transaction marker: {pending}")
            payload = marker.get("record")
            if not isinstance(payload, dict) or payload.get("run_id") != marker.get("run_id"):
                raise RunRecordError(f"invalid ledger transaction payload: {pending}")
            self._validate_schema(payload)
            record = (
                RunRecordV2.from_dict(payload)
                if payload.get("schema_version") == 2
                else RunRecord.from_dict(payload)
            )
            record.validate()
            if ledger_cls.record_digest(record) != marker.get("record_digest"):
                raise RunRecordError(f"invalid ledger transaction digest: {pending}")

            target = pending.parent / RECORD_NAME
            if target.is_file():
                persisted = self.load(record.run_id)
                if ledger_cls.record_digest(persisted) != marker["record_digest"]:
                    raise RunRecordError(
                        f"pending ledger transaction conflicts with record {record.run_id}"
                    )
            else:
                self._write_json_atomic(target, payload, TMP_NAME)
            ledger_cls.append(
                record,
                self.ledger_path,
                assume_locked=True,
                idempotent=True,
            )
            pending.unlink()
            self._fsync_directory(pending.parent)

    @staticmethod
    def _write_json_atomic(target: Path, payload: dict, tmp_name: str) -> None:
        tmp = target.parent / tmp_name
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        RunStore._fsync_directory(target.parent)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        directory_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def load(self, run_id: str) -> RunRecord:
        target = self.root / run_id / RECORD_NAME
        if not target.is_file():
            raise FileNotFoundError(f"no run record for run_id={run_id}")
        payload = json.loads(target.read_text(encoding="utf-8"))
        if payload.get("schema_version") == 2:
            return RunRecordV2.from_dict(payload)
        return RunRecord.from_dict(payload)

    def _validate_schema(self, payload: dict) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(payload),
            key=lambda e: list(e.path),
        )
        if errors:
            raise RunRecordError(
                f"run-record violates run-record.schema.json: {errors[0].message}"
            )
