#!/usr/bin/env python3
"""Deterministically derive the provable case status from recorded evidence.

benchmark_valid is never derived here: only check_release.py writes true.
This recomputes the maximum maturity provable from sealed facts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REFERENCE_STATE_TO_MATURITY = {
    "planned": "draft",
    "inputs_frozen": "constructed",
    "smoke_executed": "constructed",
    "formal_executed": "constructed",
    "independently_verified": "reference_validated",
    "reproducible": "verifier_validated",
}


def derive(case_dir: Path) -> dict:
    case_dir = case_dir.resolve()
    reference_path = case_dir / "reference" / "reference.json"
    if not reference_path.is_file():
        return {
            "case_status": "draft",
            "benchmark_valid": False,
            "reference_state": None,
            "reason": "reference.json missing",
        }
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    state = reference.get("state")
    maturity = REFERENCE_STATE_TO_MATURITY.get(state)
    if maturity is None:
        return {
            "case_status": "draft",
            "benchmark_valid": False,
            "reference_state": state,
            "reason": f"unknown reference state {state!r}",
        }
    return {
        "case_status": maturity,
        "benchmark_valid": False,
        "reference_state": state,
        "reason": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = derive(args.case_dir)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
