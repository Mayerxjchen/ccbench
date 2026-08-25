#!/usr/bin/env python3
"""Restore-aware evidence gate for a case's formal runs (plan Task 7).

For each formal run, validation accepts local curated bytes or restores the
exact bundle digest from the content-addressed store into a private temporary
directory and validates there. It never trusts remote object metadata alone:
the bundle SHA-256 is recomputed, extraction is safe, every per-file hash is
recomputed, and the evaluator/policy digests must match the case directory
*before* scientific validation runs.

``--restore``:
- ``always``: the store is required. Unavailable or corrupt objects fail the
  run closed with a failure code.
- ``auto`` (default): try the store; if it cannot produce the bundle, fall back
  to validating the bytes adjacent to the manifest and report ``restored=false``.
- ``never``: never contact the store; validate adjacent bytes only.

v1 manifests have no bundle: they are validated against adjacent workspace
bytes exactly as before and reported with ``migration_required=true`` — a v1
record never claims durable storage.

Failure codes: ``EVIDENCE_UNAVAILABLE`` (neither store has the object),
``EVIDENCE_HASH_MISMATCH`` (object or per-file bytes differ from the manifest),
``EVIDENCE_NOT_RESTORABLE`` (bytes match but cannot be unpacked),
``EVALUATOR_IDENTITY_MISMATCH`` (case evaluator/policy digest changed).

Exit 0. Verdicts go to stdout as JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.evidence.store import EvidenceStore  # noqa: E402
from scripts.matclaw_validation import (  # noqa: E402
    CASE_NAMES,
    RUN_IDS,
    load_policy,
    sha256_file,
    validate_run_manifest,
)

POLICY = ROOT / "benchmark" / "sources" / "matclaw" / "acceptance.json"
V2_SCHEMA = ROOT / "schemas" / "evidence-manifest-v2.schema.json"

EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
EVIDENCE_HASH_MISMATCH = "EVIDENCE_HASH_MISMATCH"
EVIDENCE_NOT_RESTORABLE = "EVIDENCE_NOT_RESTORABLE"
EVALUATOR_IDENTITY_MISMATCH = "EVALUATOR_IDENTITY_MISMATCH"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _policy_sha(case_dir: Path) -> str:
    policy_path = case_dir / "reference" / "evidence-policy.json"
    return hashlib.sha256(
        json.dumps(json.loads(policy_path.read_text(encoding="utf-8")),
                   sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _evaluator_sha(case_dir: Path) -> str | None:
    manifest = case_dir / "evaluator-manifest.json"
    if not manifest.is_file():
        return None
    return json.loads(manifest.read_text(encoding="utf-8")).get("bundle_sha256")


def _local_verdict(run_dir: Path, case_id: str, policy: dict[str, Any],
                   migration_required: bool, mode: str) -> dict[str, Any]:
    """v1-compatible adjacent-bytes validation; never claims durable storage."""
    manifest_path = run_dir / "manifest.json"
    errors: list[str] = []

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"valid": False, "errors": [f"manifest unreadable: {exc}"],
                "failure_code": None, "restored": False,
                "migration_required": migration_required, "mode": mode}

    for art in manifest.get("artifacts", []):
        rel = art.get("path")
        digest = art.get("sha256")
        target = run_dir / rel
        if not target.is_file():
            errors.append(f"missing artifact: {rel}")
        elif digest and _sha256_file(target) != digest:
            errors.append(f"artifact hash mismatch: {rel}")

    # timestamp order sanity (032 run-1 finished_at predated started_at)
    started_raw = manifest.get("started_at")
    finished_raw = manifest.get("finished_at")
    if not started_raw or not finished_raw:
        errors.append(f"missing timestamp: started={started_raw!r} finished={finished_raw!r}")
    else:
        try:
            started = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
            finished = datetime.fromisoformat(finished_raw.replace("Z", "+00:00"))
            if finished < started:
                errors.append(f"finished_at {finished_raw} < started_at {started_raw}")
        except (ValueError, TypeError) as exc:
            errors.append(f"timestamp anomaly: {exc}")

    vr = manifest.get("verifier_report", {})
    if vr.get("valid") is True:
        pass  # hidden verifier already ran for this record
    elif manifest.get("evidence_class") == "formal":
        errors.append("formal manifest without a valid verifier_report")

    eligible = validate_run_manifest(manifest_path, case_id, policy)["eligible"]
    if not eligible:
        errors.append("validate_run_manifest: not eligible")

    return {"valid": not errors, "errors": errors, "failure_code": None,
            "restored": False, "migration_required": migration_required, "mode": mode}


def _retrieve(store: EvidenceStore, digest: str, dest: Path) -> list[dict]:
    """Restore from primary, falling back to replica on corruption.

    Returns the extracted per-file records. Raises ``RestoreFailure`` with the
    appropriate failure code.
    """
    missing: list[str] = []
    corrupt: list[str] = []
    for backend in (store.primary, store.replica):
        if not backend.exists(digest):
            missing.append(str(backend.root))
            continue
        try:
            return backend.get(digest, dest)
        except ValueError as exc:
            corrupt.append(f"{backend.root}: {exc}")
    if corrupt:
        raise RestoreFailure(EVIDENCE_HASH_MISMATCH, corrupt)
    raise RestoreFailure(EVIDENCE_UNAVAILABLE, missing)


class RestoreFailure(RuntimeError):
    def __init__(self, code: str, detail: list[str]) -> None:
        super().__init__("; ".join(detail))
        self.code = code
        self.detail = detail


def _validate_v2(run_dir: Path, manifest: dict[str, Any], case_id: str,
                 policy: dict[str, Any], case_dir: Path, restore_mode: str) -> dict[str, Any]:
    errors: list[str] = []
    schema = json.loads(V2_SCHEMA.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(manifest, schema)
    except jsonschema.ValidationError as exc:
        return {"valid": False, "errors": [f"v2 schema: {exc.message}"],
                "failure_code": None, "restored": False,
                "migration_required": False, "mode": restore_mode}

    bundle = manifest.get("bundle") or {}
    digest = bundle.get("sha256")

    if restore_mode == "never":
        # local adjacent bytes only; no durable claim
        local = _local_verdict(run_dir, case_id, policy, migration_required=False,
                               mode=restore_mode)
        local["failure_code"] = EVIDENCE_UNAVAILABLE if not local["valid"] else None
        return local

    tmp = Path(tempfile.mkdtemp(prefix=f"evidence-restore-{case_id}-"))
    try:
        try:
            store = EvidenceStore(bundle["primary_uri"], bundle["replica_uri"])
            extracted = _retrieve(store, digest, tmp / "restored")
        except RestoreFailure as exc:
            if restore_mode == "auto":
                local = _local_verdict(run_dir, case_id, policy,
                                       migration_required=False, mode=restore_mode)
                local["failure_code"] = exc.code
                return local
            return {"valid": False, "errors": [f"restore failed: {exc.code}: {exc.detail}"],
                    "failure_code": exc.code, "restored": False,
                    "migration_required": False, "mode": restore_mode}
        except Exception as exc:  # decompression / tar / IO — bytes present but unusable
            return {"valid": False, "errors": [f"not restorable: {exc}"],
                    "failure_code": EVIDENCE_NOT_RESTORABLE, "restored": False,
                    "migration_required": False, "mode": restore_mode}

        # per-file hashes vs the manifest's artifact list (restored/<rel>)
        expected = {a["path"].removeprefix("restored/"): a for a in manifest.get("artifacts", [])}
        restored_root = tmp / "restored"
        for record in extracted:
            rel = record["path"]
            art = expected.get(rel)
            if art is None:
                errors.append(f"restored file not in manifest: {rel}")
                continue
            if (restored_root / rel).stat().st_size != art.get("size_bytes"):
                errors.append(f"restored size mismatch: {rel}")
            elif _sha256_file(restored_root / rel) != art.get("sha256"):
                errors.append(f"restored digest mismatch: {rel}")
        if set(record["path"] for record in extracted) != set(expected):
            for rel in set(expected) - set(record["path"] for record in extracted):
                errors.append(f"manifest artifact missing from bundle: {rel}")

        # evaluator + policy identity before scientific validation
        expected_eval = _evaluator_sha(case_dir)
        if expected_eval is None:
            errors.append(f"case has no evaluator-manifest.json: {case_dir}")
        elif manifest.get("evaluator_bundle_sha256") != expected_eval:
            errors.append(
                f"evaluator digest mismatch: manifest {manifest.get('evaluator_bundle_sha256')} "
                f"!= case {expected_eval}")
        expected_policy = _policy_sha(case_dir)
        if manifest.get("artifact_policy_sha256") != expected_policy:
            errors.append(
                f"policy digest mismatch: manifest {manifest.get('artifact_policy_sha256')} "
                f"!= case {expected_policy}")

        # scientific validation over the restored tree
        eligible = validate_run_manifest(run_dir / "manifest.json", case_id, policy,
                                         artifact_base=tmp)["eligible"]
        if not eligible:
            errors.append("validate_run_manifest: not eligible over restored tree")

        identity_faults = [e for e in errors if "evaluator" in e or "policy digest" in e]
        storage_faults = [e for e in errors if any(
            k in e for k in ("restored", "digest mismatch", "not in manifest",
                             "missing from bundle", "size mismatch"))]
        code = EVALUATOR_IDENTITY_MISMATCH if identity_faults else (
            EVIDENCE_HASH_MISMATCH if storage_faults else None)
        return {"valid": not errors, "errors": errors, "failure_code": code,
                "restored": True, "migration_required": False, "mode": restore_mode}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _validate_run(case_id: str, run_dir: Path, policy: dict[str, Any],
                  case_dir: Path, restore_mode: str) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return {"valid": False, "errors": ["manifest.json missing"],
                "failure_code": EVIDENCE_UNAVAILABLE, "restored": False,
                "migration_required": None, "mode": restore_mode}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"valid": False, "errors": [f"manifest unreadable: {exc}"],
                "failure_code": None, "restored": False,
                "migration_required": None, "mode": restore_mode}

    if manifest.get("schema_version") == "2.0":
        return _validate_v2(run_dir, manifest, case_id, policy, case_dir, restore_mode)
    return _local_verdict(run_dir, case_id, policy, migration_required=True, mode=restore_mode)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", required=True, choices=("031", "032", "033"))
    ap.add_argument("--evidence-root", type=Path,
                    default=ROOT / "evidence" / "matclaw" / "formal")
    ap.add_argument("--restore", choices=("auto", "always", "never"), default="auto")
    args = ap.parse_args(argv)

    policy = load_policy(POLICY)
    case_dir = ROOT / CASE_NAMES[args.case]
    case_root = args.evidence_root / args.case
    verdict: dict[str, Any] = {"case_id": args.case, "restore": args.restore, "runs": {}}
    all_valid = True
    for run_id in RUN_IDS:
        run_dir = case_root / run_id
        result = _validate_run(args.case, run_dir, policy, case_dir, args.restore)
        verdict["runs"][run_id] = result
        all_valid = all_valid and result["valid"]

    verdict["evidence_complete"] = all_valid
    print(json.dumps(verdict, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
