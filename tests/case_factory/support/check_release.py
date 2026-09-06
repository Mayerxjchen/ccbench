#!/usr/bin/env python3
"""Release derivation. The only writer of benchmark_valid=true.

Fail-closed release checks: every gate G0..G12 bound to restorable bytes,
thresholds frozen, reference reproducible, no Candidate leak, no evaluator
drift, no open gates. Writes benchmark_valid.json only with --write.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from audit_candidate_bundle import audit_case

SCHEMA_VERSION = 1
RELEASE_GATES = [f"G{i}" for i in range(13)]  # G0..G12
EVALUATOR_PATHS = ("tests", "tools", "reference", "solution/expert")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"__error__": str(exc)}


def check_release(case_dir: Path) -> dict:
    case_dir = case_dir.resolve()
    errors: list[str] = []

    thresholds = read_json(case_dir / "reference/thresholds.json")
    if thresholds.get("status") != "frozen":
        errors.append(f"thresholds not frozen: {thresholds.get('status')!r}")

    reference = read_json(case_dir / "reference/reference.json")
    if reference.get("state") != "reproducible":
        errors.append(
            f"insufficient references: state {reference.get('state')!r}, need reproducible"
        )

    validation = read_json(case_dir / "VALIDATION.json")
    open_gates = validation.get("open_gates") or []
    if open_gates:
        errors.append(f"open release gates: {sorted(open_gates)}")

    audit = audit_case(case_dir)
    if not audit["valid"]:
        errors.append(f"candidate leak: {audit['errors'][:3]}")

    manifest = read_json(case_dir / "evidence/manifest.json")
    gates = manifest.get("gates") or {}
    objects = manifest.get("objects") or {}
    missing_gates = [g for g in RELEASE_GATES if g not in gates]
    if missing_gates:
        errors.append(f"gates without evidence binding: {missing_gates}")
    for gate in RELEASE_GATES:
        if gate not in gates:
            continue
        obj_key = gates[gate]
        obj = objects.get(obj_key)
        if not isinstance(obj, dict):
            errors.append(f"gate {gate}: object {obj_key!r} not in manifest")
            continue
        rel = str(obj.get("rel_path") or "")
        path = case_dir / rel
        if not path.is_file():
            errors.append(f"gate {gate}: missing restorable byte object {rel}")
            continue
        digest = sha256_of(path)
        if digest != obj.get("sha256"):
            errors.append(f"gate {gate}: hash mismatch for {rel}")
        if obj.get("size") is not None and path.stat().st_size != obj["size"]:
            errors.append(f"gate {gate}: size mismatch for {rel}")

    eval_manifest = read_json(case_dir / "evaluator-manifest.json")
    if eval_manifest.get("frozen") is not True:
        errors.append("evaluator manifest not frozen")
    else:
        frozen = eval_manifest.get("objects") or {}
        seen: set[str] = set()
        for rel in EVALUATOR_PATHS:
            root = case_dir / rel
            if not root.exists():
                continue
            for f in root.rglob("*"):
                if not f.is_file():
                    continue
                key = str(f.relative_to(case_dir))
                seen.add(key)
                expected = frozen.get(key)
                if expected is None:
                    errors.append(f"evaluator drift: unfrozen file {key}")
                    continue
                if expected.get("sha256") != sha256_of(f):
                    errors.append(f"evaluator drift: hash changed {key}")
        missing_frozen = sorted(set(frozen) - seen)
        if missing_frozen:
            errors.append(f"evaluator drift: frozen files missing {missing_frozen}")

    return {
        "schema_version": SCHEMA_VERSION,
        "case_dir": str(case_dir),
        "valid": not errors,
        "errors": sorted(errors),
    }


def write_release(case_dir: Path, report: dict) -> dict:
    case_dir = case_dir.resolve()
    release = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_valid": True,
        "derived_by": "check_release.py",
    }
    (case_dir / "benchmark_valid.json").write_text(
        json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    validation_path = case_dir / "VALIDATION.json"
    validation = read_json(validation_path)
    validation["case_status"] = "benchmark_valid"
    validation["benchmark_valid"] = True
    validation["open_gates"] = []
    (validation_path).write_text(
        json.dumps(validation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return {"case_status": "benchmark_valid", "benchmark_valid": True,
            "derived_by": "check_release.py"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--write", action="store_true", help="write benchmark_valid.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = check_release(args.case_dir)
    if report["valid"] and args.write:
        report["written"] = write_release(args.case_dir, report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for err in report["errors"]:
            print(f"ERROR: {err}")
        print("OK" if report["valid"] else "FAILED")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
