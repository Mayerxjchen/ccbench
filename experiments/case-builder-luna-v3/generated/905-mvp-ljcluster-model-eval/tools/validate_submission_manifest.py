#!/usr/bin/env python3
"""Construction-time linter for a submission manifest (developer tool only).

Validates a submission directory against public/submission-schema.json shape
rules: required fields, path safety, hash presence, contiguous lineage rounds.
This is a DRY-RUN aid while authoring a case. It is NOT the runtime verifier
and must never be wired into tests/test.sh — the graded chain lives in
tests/verifier.py, which additionally loads artifacts, recomputes hashes, and
emits the common result.json.

Usage: python3 tools/validate_submission_manifest.py <submission-dir>
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

REQUIRED_LISTS = ("provenance_sources", "artifacts", "lineage", "runtime_receipts")


def lint(submission: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = submission / "manifest.json"
    if not manifest_path.is_file():
        return [f"{manifest_path}: manifest.json missing"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"{manifest_path}: unreadable JSON: {exc}"]
    if not isinstance(manifest, dict):
        return [f"{manifest_path}: root must be an object"]
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not str(manifest.get("case_id", "")).strip():
        errors.append("case_id must be a non-empty string")
    for key in REQUIRED_LISTS:
        value = manifest.get(key)
        if not isinstance(value, list) or not value:
            errors.append(f"{key} must be a non-empty list")
    seen_paths: set[str] = set()
    for artifact in manifest.get("artifacts") or []:
        if not isinstance(artifact, dict):
            errors.append("artifact entries must be objects")
            continue
        rel = str(artifact.get("path", ""))
        posix = PurePosixPath(rel)
        if not rel or posix.is_absolute() or ".." in posix.parts:
            errors.append(f"artifact path unsafe: {rel!r}")
            continue
        if rel in seen_paths:
            errors.append(f"duplicate artifact path: {rel}")
        seen_paths.add(rel)
        path = submission / posix
        if not path.is_file():
            errors.append(f"artifact declared but missing: {rel}")
            continue
        sha = artifact.get("sha256")
        if not (isinstance(sha, str) and len(sha) == 64):
            errors.append(f"artifact {rel}: sha256 must be a 64-hex digest")
        elif sha != hashlib.sha256(path.read_bytes()).hexdigest():
            errors.append(f"artifact {rel}: sha256 does not match staged bytes")
    rounds = sorted({
        int(e.get("round", 0)) for e in manifest.get("lineage") or [] if isinstance(e, dict)
    })
    if rounds and rounds != list(range(1, len(rounds) + 1)):
        errors.append(f"lineage rounds must be contiguous from 1, got {rounds}")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    errors = lint(Path(argv[1]))
    for error in errors:
        print(f"LINT: {error}")
    if not errors:
        print("lint OK (reminder: a clean lint is not a verifier result)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
