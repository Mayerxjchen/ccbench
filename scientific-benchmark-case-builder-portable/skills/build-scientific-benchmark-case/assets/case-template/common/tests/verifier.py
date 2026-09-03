#!/usr/bin/env python3
"""Minimal harness-compatible submission verifier (MLP Discovery MVP).

Reads the sealed candidate submission and runs the technical chain:

    V0 identity, V1 provenance, V2 model artifact, V3 workflow lineage,
    C-V7 runtime receipts, C-V8 integrity / filesystem safety.

It always emits a standard result.json (run_id, result_class, failure_code,
reason, retryable, is_counted_scientifically) into the result directory so a
failure is attributable:

    VALID_RESULT / PASS                  technical chain passed
    AGENT_FAILURE / NO_SUBMISSION        sealed root absent or empty
    AGENT_FAILURE / INVALID_SUBMISSION   manifest missing, unreadable, malformed,
                                         or type-invalid (checked before any
                                         int()/sorted() conversion)
    AGENT_FAILURE / SCIENTIFIC_FAIL      a chain check failed with evidence
    INFRA_INVALID / VERIFIER_FAILURE     internal error (retryable, uncounted)

Every chain check reports ALL of its findings: C-V8 verifies existence and
hash of every declared artifact and never stops after the first failure, so
one result names every problem instead of hiding later ones behind an
earlier error.

MLP-V4/V5/V6 hidden science stays `deferred` at Discovery MVP: this file
never fakes a scientific PASS. Case-specific science belongs in
`case_science_checks()` below and in `tests/hidden/` data, not in a linter.
tests/test_verifier_contract.py is the construction-time self-test of this
file; it is never invoked from tests/test.sh.

Exit codes: 0 = VALID_RESULT, 1 = AGENT_FAILURE, 2 = INFRA_INVALID.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path, PurePosixPath
from typing import Any

VALID_CLASSES = {"VALID_RESULT", "AGENT_FAILURE", "INFRA_INVALID"}
RESULT_FIELDS = {
    "run_id", "result_class", "failure_code", "reason",
    "retryable", "is_counted_scientifically",
}


def _write_result(result_dir: Path, payload: dict[str, Any]) -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    tmp = result_dir / "result.json.tmp"
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(result_dir / "result.json")


def _result(run_id: str, result_class: str, failure_code: str, reason: str,
            retryable: bool = False, counted: bool = True) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "result_class": result_class,
        "failure_code": failure_code,
        "reason": reason[:1500],
        "retryable": retryable,
        "is_counted_scientifically": counted,
    }


def _path_safety_error(value: str) -> str | None:
    """Reject anything that is not a clean sealed-root-relative path."""
    if not isinstance(value, str) or not value:
        return f"empty path {value!r}"
    posix = PurePosixPath(value)
    if posix.is_absolute() or value.startswith("~"):
        return f"absolute path {value!r} is forbidden"
    if "\\" in value:
        return f"backslash in path {value!r}"
    if ".." in posix.parts or "." in posix.parts:
        return f"path {value!r} traverses outside the sealed root"
    return None


def _scan_filesystem(root: Path) -> list[str]:
    """C-V8 filesystem safety: reject symlinks and hardlinked payloads."""
    errors: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                errors.append(f"C-V8: symlink inside sealed root: {rel}")
            elif path.is_file():
                try:
                    if path.stat().st_nlink > 1:
                        errors.append(f"C-V8: hardlink inside sealed root: {rel}")
                except OSError as exc:  # pragma: no cover
                    errors.append(f"C-V8: cannot stat {rel}: {exc}")
    return errors


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Submission:
    """Sealed submission tree plus its parsed manifest."""

    def __init__(self, root: Path):
        self.root = root
        self.manifest: dict[str, Any] = {}

    def resolve(self, rel: str) -> Path | None:
        if _path_safety_error(rel):
            return None
        return self.root / PurePosixPath(rel)


def _is_hex64(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def manifest_type_errors(manifest: dict[str, Any]) -> list[str]:
    """Structural type gate for the sealed manifest.

    Runs BEFORE any int()/sorted() in the chain, so a garbage manifest
    classifies as the Agent's INVALID_SUBMISSION and never escapes as a
    retryable INFRA_INVALID crash."""
    errors: list[str] = []
    if not _is_plain_int(manifest.get("schema_version")):
        errors.append("schema_version must be an integer")
    if not isinstance(manifest.get("case_id", ""), str):
        errors.append("case_id must be a string")
    for key in ("artifacts", "lineage", "runtime_receipts", "provenance_sources"):
        if not isinstance(manifest.get(key), list):
            errors.append(f"{key} must be a list")
    for index, artifact in enumerate(manifest.get("artifacts") or []):
        if not isinstance(artifact, dict):
            errors.append(f"artifacts[{index}] must be an object")
            continue
        if not isinstance(artifact.get("path"), str) or not artifact.get("path"):
            errors.append(f"artifacts[{index}].path must be a non-empty string")
        if "sha256" in artifact and not _is_hex64(artifact["sha256"]):
            errors.append(f"artifacts[{index}].sha256 must be a 64-char hex string")
        if "frames" in artifact and not _is_plain_int(artifact["frames"]):
            errors.append(f"artifacts[{index}].frames must be an integer")
        for str_key in ("role", "source"):
            if str_key in artifact and not isinstance(artifact[str_key], str):
                errors.append(f"artifacts[{index}].{str_key} must be a string")
    for index, entry in enumerate(manifest.get("lineage") or []):
        if not isinstance(entry, dict):
            errors.append(f"lineage[{index}] must be an object")
            continue
        if not _is_plain_int(entry.get("round")):
            errors.append(
                f"lineage[{index}].round must be an integer, "
                f"got {type(entry.get('round')).__name__}"
            )
        for str_key in ("dataset", "model", "action"):
            if str_key in entry and not isinstance(entry[str_key], str):
                errors.append(f"lineage[{index}].{str_key} must be a string")
    for index, receipt in enumerate(manifest.get("runtime_receipts") or []):
        if not isinstance(receipt, dict):
            errors.append(f"runtime_receipts[{index}] must be an object")
    for index, source in enumerate(manifest.get("provenance_sources") or []):
        if not isinstance(source, str):
            errors.append(f"provenance_sources[{index}] must be a string")
    return errors


# --------------------------------------------------------------------------
# Chain checks. Each returns (ok, detail) with detail a list[str] of evidence.
# --------------------------------------------------------------------------

def check_v0_identity(sub: Submission) -> tuple[bool, list[str]]:
    errors: list[str] = []
    manifest = sub.manifest
    if manifest.get("schema_version") != 1:
        errors.append("V0: manifest schema_version must be 1")
    case_id = manifest.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        errors.append("V0: manifest case_id must be a non-empty string")
    expected = os.environ.get("BENCH_EXPECTED_CASE_ID", "")
    if expected and case_id != expected:
        errors.append(f"V0: case_id {case_id!r} != expected {expected!r}")
    for key in ("artifacts", "lineage", "runtime_receipts", "provenance_sources"):
        if not isinstance(manifest.get(key), list) or not manifest.get(key):
            errors.append(f"V0: manifest {key} must be a non-empty list")
    return not errors, errors


def check_v1_provenance(sub: Submission) -> tuple[bool, list[str]]:
    errors: list[str] = []
    sources = sub.manifest.get("provenance_sources") or []
    if not all(isinstance(s, str) and s.strip() for s in sources):
        errors.append("V1: every provenance_sources entry must be a non-empty string")
    for artifact in sub.manifest.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("role") in {"teacher_model", "dataset", "labels"}:
            if not str(artifact.get("source", "")).strip():
                errors.append(
                    f"V1: {artifact.get('role')} artifact "
                    f"{artifact.get('path')!r} lacks a source provenance string"
                )
    return not errors, errors


def check_v2_model_artifact(sub: Submission) -> tuple[bool, list[str]]:
    errors: list[str] = []
    models = [a for a in sub.manifest.get("artifacts") or []
              if isinstance(a, dict) and a.get("role") == "student_model"]
    if not models:
        errors.append("V2: no artifact with role 'student_model' in the submission")
    for artifact in models:
        rel = artifact.get("path", "")
        problem = _path_safety_error(str(rel))
        if problem:
            errors.append(f"V2: {problem}")
            continue
        path = sub.resolve(str(rel))
        if path is None or not path.is_file():
            errors.append(f"V2: declared model artifact missing: {rel}")
            continue
        expected = artifact.get("sha256")
        actual = _sha256(path)
        if expected != actual:
            errors.append(f"V2: hash mismatch for {rel}: manifest {expected}, actual {actual}")
    return not errors, errors


def check_v3_lineage(sub: Submission) -> tuple[bool, list[str]]:
    errors: list[str] = []
    lineage = [e for e in sub.manifest.get("lineage") or [] if isinstance(e, dict)]
    rounds = [int(e.get("round", 0)) for e in lineage]
    if sorted(set(rounds)) != list(range(1, len(set(rounds)) + 1)):
        errors.append(f"V3: lineage rounds must be contiguous from 1, got {sorted(set(rounds))}")
    declared = {str(a.get("path")) for a in sub.manifest.get("artifacts") or [] if isinstance(a, dict)}
    for entry in lineage:
        for key in ("dataset", "model"):
            value = str(entry.get(key, ""))
            if not value:
                errors.append(f"V3: round {entry.get('round')} lacks {key}")
            elif value not in declared:
                errors.append(f"V3: round {entry.get('round')} {key} {value!r} not in artifacts")
    artifacts = declared_map(sub)
    frame_counts = [
        (str(entry.get("dataset")), entry.get("round"), artifacts[str(entry.get("dataset"))].get("frames"))
        for entry in lineage
        if str(entry.get("dataset", "")) in artifacts
        and isinstance(artifacts[str(entry.get("dataset"))].get("frames"), int)
    ]
    for (prev_ds, prev_round, prev_frames), (name, _round, curr) in zip(frame_counts, frame_counts[1:]):
        if curr < prev_frames:
            errors.append(
                f"V3: dataset {name} frame count regressed from round "
                f"{prev_round} ({prev_frames} -> {curr})"
            )
    return not errors, errors


def declared_map(sub: Submission) -> dict[str, dict[str, Any]]:
    return {
        str(a.get("path")): a for a in sub.manifest.get("artifacts") or [] if isinstance(a, dict)
    }


def check_v7_runtime_receipts(sub: Submission) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for receipt in sub.manifest.get("runtime_receipts") or []:
        if not isinstance(receipt, dict):
            errors.append("C-V7: receipt entries must be objects")
            continue
        if not str(receipt.get("job_id", "")).strip():
            errors.append("C-V7: receipt lacks a job_id")
        if not str(receipt.get("backend", "")).strip():
            errors.append(f"C-V7: receipt {receipt.get('job_id')} lacks backend")
        if str(receipt.get("exit_status")) not in {"0", "completed", "success"}:
            errors.append(
                f"C-V7: receipt {receipt.get('job_id')} exit_status "
                f"{receipt.get('exit_status')!r} is not a success status"
            )
    return not errors, errors


def check_v8_integrity(sub: Submission) -> tuple[bool, list[str]]:
    """C-V8: filesystem safety plus integrity of EVERY declared artifact.

    Each declared artifact is checked for existence and hash independently;
    an earlier finding never suppresses later ones, and a missing declared
    artifact is itself a finding."""
    errors = _scan_filesystem(sub.root)
    for artifact in sub.manifest.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        rel = str(artifact.get("path", ""))
        problem = _path_safety_error(rel)
        if problem:
            errors.append(f"C-V8: manifest {problem}")
            continue
        path = sub.resolve(rel)
        if path is None or not path.is_file():
            errors.append(f"C-V8: declared artifact missing from sealed root: {rel}")
            continue
        expected = artifact.get("sha256")
        if _is_hex64(expected) and expected != _sha256(path):
            errors.append(f"C-V8: integrity mismatch for {rel}")
    return not errors, errors


# Case-specific scientific checks go here. Keep this hook small (load models,
# recompute one metric, or prove one overlap property); at Discovery MVP it
# may stay empty, which the deferred list in the reason string makes explicit.
def case_science_checks(sub: Submission) -> list[str]:
    return []


CHAIN_CHECKS = (
    ("V0", check_v0_identity),
    ("V1", check_v1_provenance),
    ("V2", check_v2_model_artifact),
    ("V3", check_v3_lineage),
    ("C-V7", check_v7_runtime_receipts),
    ("C-V8", check_v8_integrity),
)

DEFERRED_NOTE = "MLP-V4/V5/V6 hidden science deferred at Discovery MVP"


def verify(submission_root: Path, run_id: str) -> dict[str, Any]:
    if not submission_root.is_dir():
        return _result(run_id, "AGENT_FAILURE", "NO_SUBMISSION",
                       f"sealed submission root {submission_root} is not a directory")
    entries = [p for p in submission_root.iterdir()
               if p.name not in {".gitkeep", "README.md"}]
    if not entries:
        return _result(run_id, "AGENT_FAILURE", "NO_SUBMISSION",
                       "sealed submission contains nothing (empty submission)")
    sub = Submission(submission_root)
    manifest_path = submission_root / "manifest.json"
    if not manifest_path.is_file():
        return _result(run_id, "AGENT_FAILURE", "NO_SUBMISSION",
                       "no submission delivered: manifest.json absent at the sealed root")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest root is not an object")
    except (OSError, ValueError) as exc:
        return _result(run_id, "AGENT_FAILURE", "INVALID_SUBMISSION",
                       f"manifest.json unreadable: {exc}")
    type_errors = manifest_type_errors(manifest)
    if type_errors:
        return _result(run_id, "AGENT_FAILURE", "INVALID_SUBMISSION",
                       "manifest type validation failed: " + "; ".join(type_errors[:6]))
    sub.manifest = manifest

    failures: list[str] = []
    for _layer_id, check in CHAIN_CHECKS:
        ok, detail = check(sub)
        if not ok:
            failures.extend(detail)
    failures.extend(case_science_checks(sub))
    if failures:
        return _result(run_id, "AGENT_FAILURE", "SCIENTIFIC_FAIL",
                       "technical chain failed: " + "; ".join(failures[:8]))
    return _result(
        run_id, "VALID_RESULT", "PASS",
        "MLP technical chain passed (V0/V1/V2/V3/C-V7/C-V8); "
        + DEFERRED_NOTE + "; outcome is a Discovery diagnostic, not "
        "benchmark scientific acceptance",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", type=Path, required=True,
                        help="sealed submission root (e.g. /submission or /app)")
    parser.add_argument("--result-dir", type=Path, default=Path("/logs/verifier"))
    parser.add_argument("--run-id", default="mlp-mvp")
    args = parser.parse_args(argv)
    try:
        result = verify(args.submission, args.run_id)
    except Exception:  # noqa: BLE001 - classified as infrastructure, never as Agent failure
        result = _result(args.run_id, "INFRA_INVALID", "VERIFIER_FAILURE",
                         "verifier internal error: " + traceback.format_exc(limit=3),
                         retryable=True, counted=False)
    _write_result(args.result_dir, result)
    print(json.dumps(result, sort_keys=True))
    if result["result_class"] == "VALID_RESULT":
        return 0
    if result["result_class"] == "AGENT_FAILURE":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
