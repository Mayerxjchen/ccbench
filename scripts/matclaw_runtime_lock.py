#!/usr/bin/env python3
"""Fail-closed runtime lock for the MatClaw GPU SIF (Cases 031-033).

Formal paper runs are only eligible on a GPU-amd64 Apptainer SIF that has been
qualified on an NVIDIA A100 and cross-checked against the embedded qualification
receipt. ``validate_runtime_lock`` fails the lock closed on any digest, arch, or
receipt mismatch. ``formal_eligible`` is a hand-set convenience flag and is never
trusted alone: a true value with a non-qualifying embedded receipt is itself
invalid — eligibility is always re-derived from the receipt, never read from the
flag. Python 3.12+, standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SHA256_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Qualification parity and MD shape that a formal-grade receipt must prove.
FORMAL_THRESHOLD = 1e-6
FORMAL_STEPS = 100
FORMAL_FRAMES = 101


class RuntimeLockError(ValueError):
    """Raised when a runtime lock file is missing or not valid JSON."""


def load_runtime_lock(path: Path) -> dict:
    """Load a runtime lock JSON document."""
    lock_path = Path(path)
    try:
        with open(lock_path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise RuntimeLockError(f"runtime lock not found: {lock_path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeLockError(
            f"runtime lock is not valid JSON ({lock_path}): {exc}"
        ) from exc


def _strictly_lt(value: object, threshold: float) -> bool:
    """True when *value* is a number strictly below *threshold*."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value < threshold


def validate_qualification(lock: dict, receipt: dict) -> list[str]:
    """Check a qualification receipt against the runtime lock.

    Returns human-readable error strings; an empty list means the receipt
    qualifies the locked SIF for formal paper runs.
    """
    if not isinstance(receipt, dict):
        return ["qualification receipt must be a JSON object"]
    if not isinstance(lock, dict):
        return ["runtime lock must be a JSON object"]
    errors: list[str] = []

    if receipt.get("schema_version") != 2:
        errors.append("receipt schema_version must be 2")
    if receipt.get("qualified") is not True:
        errors.append("receipt qualified must be true")

    if receipt.get("gpu_visible") is not True:
        errors.append("gpu_visible must be true")
    if not receipt.get("gpu_name"):
        errors.append("gpu_name must be non-empty")
    if "A100" not in str(receipt.get("gpu", "")):
        errors.append("gpu must be an NVIDIA A100 (gpu string lacks 'A100')")

    if receipt.get("sif_sha256") != lock.get("sif_sha256"):
        errors.append(
            "receipt sif_sha256 does not match the locked SIF (different SIF)"
        )
    if receipt.get("qualification_sha256") != lock.get("qualification_sha256"):
        errors.append(
            "receipt qualification_sha256 does not match the locked "
            "qualification script"
        )
    if receipt.get("architecture") != "amd64":
        errors.append("receipt architecture must be amd64")
    if receipt.get("uname_m") != "x86_64":
        errors.append("receipt uname_m must be x86_64")

    if not _strictly_lt(receipt.get("energy_abs_diff_eV"), FORMAL_THRESHOLD):
        errors.append("energy_abs_diff_eV must be strictly < 1e-6")
    if not _strictly_lt(
        receipt.get("max_force_component_abs_diff_eV_A"), FORMAL_THRESHOLD
    ):
        errors.append("max_force_component_abs_diff_eV_A must be strictly < 1e-6")
    if receipt.get("parity_ok") is not True:
        errors.append("parity_ok must be true")

    if receipt.get("md_steps") != FORMAL_STEPS:
        errors.append("md_steps must be 100")
    if receipt.get("md_frames") != FORMAL_FRAMES:
        errors.append("md_frames must be 101")
    if receipt.get("md_finite") is not True:
        errors.append("md_finite must be true")
    if receipt.get("frames_ok") is not True:
        errors.append("frames_ok must be true")

    return errors


def validate_runtime_lock(lock: dict, require_formal: bool = False) -> list[str]:
    """Fail-closed validation of a runtime lock for formal paper runs.

    Returns human-readable error strings; an empty list means the lock is
    eligible. ``formal_eligible`` is a hint, never trusted: a true value still
    requires a clean embedded qualification receipt.
    """
    if not isinstance(lock, dict):
        return ["runtime lock must be a JSON object"]
    errors: list[str] = []

    if lock.get("schema_version") != 2:
        errors.append("schema_version must be 2")
    if lock.get("runtime_type") != "gpu":
        errors.append('runtime_type must be "gpu"')
    if lock.get("os") != "linux":
        errors.append('os must be "linux"')
    if lock.get("architecture") != "amd64":
        errors.append('architecture must be "amd64"')
    if lock.get("compute_uname_m") != "x86_64":
        errors.append('compute_uname_m must be "x86_64"')

    if not isinstance(lock.get("source_commit"), str) or not lock["source_commit"]:
        errors.append("source_commit must be a non-empty string")
    if not _SHA256_IMAGE.fullmatch(str(lock.get("cpu_image_id") or "")):
        errors.append("cpu_image_id must match sha256:<64 hex>")
    if not _SHA256_IMAGE.fullmatch(str(lock.get("gpu_image_id") or "")):
        errors.append("gpu_image_id must match sha256:<64 hex>")

    oci = lock.get("oci_repo_digest")
    if oci is not None and not _SHA256_IMAGE.fullmatch(str(oci)):
        errors.append("oci_repo_digest must be null or match sha256:<64 hex>")

    for key in ("docker_archive_sha256", "sif_sha256", "qualification_sha256"):
        if not _SHA256.fullmatch(str(lock.get(key) or "")):
            errors.append(f"{key} must be <64 hex>")

    for key in ("sif_path_remote", "qualification_path_remote"):
        if not isinstance(lock.get(key), str) or not lock[key]:
            errors.append(f"{key} must be a non-empty string")

    if not isinstance(lock.get("formal_eligible"), bool):
        errors.append("formal_eligible must be a boolean")

    qualification = lock.get("qualification")
    if qualification is not None and not isinstance(qualification, dict):
        errors.append("qualification must be a JSON object")

    qualification_errors: list[str] = []
    if isinstance(qualification, dict):
        qualification_errors = validate_qualification(lock, qualification)
        errors.extend(qualification_errors)

    if lock.get("formal_eligible") is True:
        if not isinstance(qualification, dict):
            errors.append(
                "formal_eligible is true but the lock embeds no valid "
                "qualification receipt"
            )
        elif qualification_errors:
            errors.append(
                "formal_eligible is true but the embedded qualification "
                "receipt does not qualify"
            )

    if require_formal and lock.get("formal_eligible") is not True:
        errors.append("formal_eligible must be true (require_formal)")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matclaw_runtime_lock")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate a runtime lock JSON")
    validate.add_argument("lock", type=Path)
    validate.add_argument("--require-formal", action="store_true")

    receipt = sub.add_parser(
        "validate-receipt",
        help="validate a qualification receipt against a runtime lock",
    )
    receipt.add_argument("lock", type=Path)
    receipt.add_argument("receipt", type=Path)

    args = parser.parse_args(argv)

    try:
        lock = load_runtime_lock(args.lock)
    except RuntimeLockError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.command == "validate":
        errors = validate_runtime_lock(lock, require_formal=args.require_formal)
    elif args.command == "validate-receipt":
        try:
            receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"qualification receipt unreadable: {exc}", file=sys.stderr)
            return 1
        errors = validate_qualification(lock, receipt)
    else:
        parser.error("unsupported command")
        return 1

    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
