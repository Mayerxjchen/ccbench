#!/usr/bin/env python3
"""Verify fixture matrix closure for a case.

A hard-outcome verifier layer requires one positive expert fixture, at least
one alternative-valid fixture, and one negative fixture. Alternative-valid
fixtures must not require exact expert paths (validated by design tests, not
here). Local and HPC cases are checked identically.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc

FIXTURE_CLASSES = {
    "positive": "tests/fixtures/positive",
    "negative": "tests/fixtures/negative",
    "alternative_valid": "tests/fixtures/alternative-valid",
}


def count_entries(fixture_dir: Path) -> int:
    if not fixture_dir.is_dir():
        return 0
    return sum(1 for p in fixture_dir.iterdir() if p.name != ".gitkeep")


def build_matrix(case_dir: Path) -> dict:
    plan_path = case_dir / "verifier-plan.yaml"
    if not plan_path.is_file():
        raise SystemExit("generate_fixture_matrix: verifier-plan.yaml missing; derive it first")
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
    required = plan.get("fixtures") or {}

    actual = {
        "positive": count_entries(case_dir / FIXTURE_CLASSES["positive"]),
        "negative": count_entries(case_dir / FIXTURE_CLASSES["negative"]),
        "alternative_valid": count_entries(case_dir / FIXTURE_CLASSES["alternative_valid"]),
    }
    hard = [layer for layer in plan.get("layers") or [] if layer.get("hard_outcome")]

    errors: list[str] = []
    if actual["positive"] < int(required.get("positive", 1)):
        errors.append(f"positive fixtures {actual['positive']} < required {required.get('positive', 1)}")
    if actual["negative"] < int(required.get("negative", 1)):
        errors.append(f"negative fixtures {actual['negative']} < required {required.get('negative', 1)}")
    if actual["alternative_valid"] < int(required.get("alternative_valid", 1)):
        errors.append(
            f"alternative-valid fixtures {actual['alternative_valid']} < required "
            f"{required.get('alternative_valid', 1)}"
        )

    return {
        "schema_version": 1,
        "case_dir": str(case_dir),
        "hard_outcome_layers": [layer["id"] for layer in hard],
        "required": required,
        "actual": actual,
        "valid": not errors,
        "errors": sorted(errors),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    matrix = build_matrix(args.case_dir)
    if args.json:
        print(json.dumps(matrix, indent=2, sort_keys=True))
    else:
        for err in matrix["errors"]:
            print(f"ERROR: {err}")
        print("OK" if matrix["valid"] else "FAILED")
    return 0 if matrix["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
