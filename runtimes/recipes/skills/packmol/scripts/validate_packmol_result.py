#!/usr/bin/env python3
"""Validate Packmol exit status, official log markers, and packed composition."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from packmol_common import (
    composition,
    emit_result,
    expected_composition,
    fail,
    load_json,
    parse_packmol_version,
    read_xyz,
)

TARGET_RE = re.compile(
    r"Maximum violation of target distance\s*:\s*([0-9.eE+-]+)", re.I
)
CONSTRAINT_RE = re.compile(
    r"Maximum violation of (?:the )?constraints\s*:\s*([0-9.eE+-]+)", re.I
)


def _match_float(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    return float(match.group(1)) if match else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--exit-code", required=True, type=int)
    args = parser.parse_args()
    source = Path(args.manifest)
    actual_count: int | None = None
    target_violation: float | None = None
    constraint_violation: float | None = None
    packmol_version: str | None = None
    try:
        manifest = load_json(source)
        log_path = Path(manifest["packmol"]["log_path"])
        output_path = Path(manifest["packmol"]["output_path"])
        if args.exit_code != 0:
            raise ValueError(f"Packmol exit code is {args.exit_code}, expected 0")
        if not log_path.is_file():
            raise ValueError(f"Packmol log does not exist: {log_path}")
        log = log_path.read_text(encoding="utf-8", errors="replace")
        packmol_version = parse_packmol_version(log)
        if "Success!" not in log:
            raise ValueError("Packmol log does not contain Success!")
        if re.search(r"(?im)^\s*(ERROR|FATAL|STOP)\b", log):
            raise ValueError("Packmol log contains a fatal error marker")
        target_violation = _match_float(TARGET_RE, log)
        constraint_violation = _match_float(CONSTRAINT_RE, log)
        if target_violation is None or constraint_violation is None:
            raise ValueError("Packmol log is missing official maximum violation values")
        if target_violation > 0.01 or constraint_violation > 0.01:
            raise ValueError("Packmol maximum violation exceeds 0.01")
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise ValueError(f"packed output is missing or empty: {output_path}")
        atoms = read_xyz(output_path)
        actual_count = len(atoms)
        expected_count = int(manifest["derived"]["expected_atom_count"])
        if actual_count != expected_count:
            raise ValueError(
                f"packed atom count mismatch: expected {expected_count}, got {actual_count}"
            )
        actual_composition = composition(atoms)
        expected = expected_composition(manifest)
        if actual_composition != expected:
            raise ValueError(
                f"packed composition mismatch: expected {expected}, got {actual_composition}"
            )
    except Exception as exc:
        paths = [str(source)]
        if "manifest" in locals():
            paths.extend(
                [manifest["packmol"]["log_path"], manifest["packmol"]["output_path"]]
            )
        return fail(
            check="validation",
            name="packmol_execution",
            error=exc,
            source_paths=paths,
            exit_code=args.exit_code,
            actual_atom_count=actual_count,
            max_target_violation=target_violation,
            max_constraint_violation=constraint_violation,
            packmol_version=packmol_version,
        )
    emit_result(
        check="validation",
        name="packmol_execution",
        status="PASS",
        preparation_stage="executed",
        source_paths=[str(source), str(log_path), str(output_path)],
        exit_code=args.exit_code,
        actual_atom_count=actual_count,
        max_target_violation=target_violation,
        max_constraint_violation=constraint_violation,
        packmol_version=packmol_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
