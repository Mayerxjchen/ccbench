#!/usr/bin/env python3
"""Validate threshold freeze records (fail-closed).

Rejects one-run stochastic calibration, post-Agent freeze timestamps, expert
threshold writers, missing units/metric definitions, missing hidden-set
independence, and an expert value copied as a tight pass bound.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATES = ("draft", "calibrating", "frozen")
REQUIRED_ENTRY = (
    "pass_bound", "stochastic", "statistics", "reference_run_ids",
    "rationale", "calibrated_at",
)
REQUIRED_STATISTICS = ("mean", "std", "n_runs")


def check_freeze(data: dict, reference: dict | None = None) -> dict:
    errors: list[str] = []
    status = data.get("status")
    if status not in STATES:
        return {
            "valid": False,
            "errors": [f"unknown threshold status: {status!r}"],
            "status": status,
        }

    thresholds = data.get("thresholds") or {}
    if status != "frozen":
        return {"valid": True, "errors": [], "status": status,
                "threshold_keys": sorted(thresholds)}

    if not isinstance(thresholds, dict) or not thresholds:
        errors.append("frozen thresholds empty")

    if data.get("produced_by") != "threshold_calibration":
        errors.append(
            f"produced_by must be threshold_calibration, got {data.get('produced_by')!r}"
        )
    if data.get("agent_results_inspected_at") is not None:
        errors.append("freeze timestamp must precede Agent inspection")

    units = data.get("units") or {}
    definitions = data.get("metric_definitions") or {}
    case_version = data.get("case_version")
    verifier_version = data.get("verifier_version")
    if not case_version:
        errors.append("case_version missing")
    if not verifier_version:
        errors.append("verifier_version missing")

    hidden = data.get("hidden_set")
    if not isinstance(hidden, dict):
        errors.append("hidden_set missing")
    else:
        if not str(hidden.get("generation_provenance") or "").strip():
            errors.append("hidden_set.generation_provenance empty")
        if hidden.get("candidate_inaccessible") is not True:
            errors.append("hidden_set.candidate_inaccessible must be true")
        if hidden.get("submission_overlap_checked") is not True:
            errors.append("hidden_set.submission_overlap_checked must be true")

    for key, entry in thresholds.items():
        if not isinstance(entry, dict):
            errors.append(f"threshold {key!r} not a mapping")
            continue
        for field in REQUIRED_ENTRY:
            if field not in entry:
                errors.append(f"threshold {key!r} missing {field}")
        if key not in units:
            errors.append(f"threshold {key!r} missing units")
        if key not in definitions:
            errors.append(f"threshold {key!r} missing metric_definition")
        if "pass_bound" in entry and not isinstance(entry["pass_bound"], (int, float)):
            errors.append(f"threshold {key!r} pass_bound not numeric")
        stats = entry.get("statistics")
        if isinstance(stats, dict):
            for field in REQUIRED_STATISTICS:
                if field not in stats:
                    errors.append(f"threshold {key!r} statistics missing {field}")
            n_runs = stats.get("n_runs")
            if entry.get("stochastic") is True and (not isinstance(n_runs, int) or n_runs < 2):
                errors.append(
                    f"threshold {key!r} one-run stochastic calibration (n_runs < 2)"
                )
        else:
            errors.append(f"threshold {key!r} statistics missing")
        run_ids = entry.get("reference_run_ids")
        if not isinstance(run_ids, list) or not run_ids:
            errors.append(f"threshold {key!r} reference_run_ids empty")
        if reference is not None:
            expert_value = (reference.get("metrics") or {}).get(key)
            if isinstance(expert_value, (int, float)):
                bound = entry.get("pass_bound")
                if isinstance(bound, (int, float)):
                    denom = max(1.0, abs(expert_value))
                    if abs(bound - expert_value) / denom < 1e-6:
                        errors.append(
                            f"threshold {key!r} pass_bound equals expert value"
                        )

    return {
        "valid": not errors,
        "errors": sorted(errors),
        "status": status,
        "threshold_keys": sorted(thresholds),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("thresholds", type=Path)
    parser.add_argument("--reference", type=Path, help="reference.json for tight-bound check")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = json.loads(args.thresholds.read_text(encoding="utf-8"))
    reference = None
    if args.reference is not None:
        reference = json.loads(args.reference.read_text(encoding="utf-8"))
    report = check_freeze(data, reference)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for err in report["errors"]:
            print(f"ERROR: {err}")
        print("OK" if report["valid"] else "FAILED")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
