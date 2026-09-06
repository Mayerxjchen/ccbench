#!/usr/bin/env python3
"""Classify one real Discovery run without turning infra defects into grades."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


RUNTIME_SIGNALS = (
    "modulenotfound", "importerror", "no module", "version conflict",
    "cuda", "runtime unavailable", "image not found",
)
SOURCE_SIGNALS = (
    "missing source", "undisclosed method", "critical source conflict",
    "source unavailable", "paper conflict",
)
CASE_SIGNALS = (
    "cross-layer", "hidden scope", "verifier bug", "prompt conflict",
    "manifest drift", "hash mismatch",
)


def _decision(blocked_kind: str | None, evidence: list[str], confidence: str) -> dict:
    if blocked_kind == "SOURCE_BLOCKED":
        decision = "REJECT"
    elif blocked_kind in {
        "INFRA_INVALID", "CASE_DESIGN_BLOCKED", "RUNTIME_BLOCKED",
        "RESOURCE_BLOCKED",
    }:
        decision = "REFINE"
    else:
        decision = "PROMOTE"
    return {
        "blocked_kind": blocked_kind,
        "decision": decision,
        "evidence": evidence,
        "confidence": confidence,
    }


def _errors(run: dict[str, Any]) -> list[str]:
    levels = ((run.get("verifier") or {}).get("levels") or {})
    errors: list[str] = []
    for level in levels.values():
        if isinstance(level, dict):
            errors.extend(str(item) for item in (level.get("errors") or []))
    return errors


def classify(run: dict[str, Any]) -> dict:
    """Return one fail-closed Discovery decision from structured evidence."""
    source = run.get("source") or {}
    infra = run.get("infrastructure") or {}
    case_design = run.get("case_design") or {}
    runtime = run.get("runtime") or {}
    telemetry = run.get("telemetry") or {}
    failure_code = str(run.get("failure_code", ""))

    # Priority is deliberate: a broken case/runtime cannot become an Agent grade.
    if source.get("critical_conflict") or source.get("available") is False:
        return _decision("SOURCE_BLOCKED", [f"source={source}"], "high")
    if failure_code == "INFRA_INVALID" or infra.get("valid") is False:
        return _decision("INFRA_INVALID", [f"failure_code={failure_code}"], "high")
    if case_design.get("cross_layer_valid") is False or case_design.get("valid") is False:
        return _decision("CASE_DESIGN_BLOCKED", [f"case_design={case_design}"], "high")
    if runtime.get("available") is False or runtime.get("compatible") is False:
        return _decision("RUNTIME_BLOCKED", [f"runtime={runtime}"], "high")
    if telemetry.get("oom") or telemetry.get("walltime_exceeded") or telemetry.get("resource_unavailable"):
        return _decision("RESOURCE_BLOCKED", [f"telemetry={telemetry}"], "high")

    errors = _errors(run)
    error_text = " ".join(errors).lower()
    for kind, signals in (
        ("SOURCE_BLOCKED", SOURCE_SIGNALS),
        ("CASE_DESIGN_BLOCKED", CASE_SIGNALS),
        ("RUNTIME_BLOCKED", RUNTIME_SIGNALS),
    ):
        if any(signal in error_text for signal in signals):
            return _decision(kind, errors[:3], "medium")

    levels = ((run.get("verifier") or {}).get("levels") or {})
    structural = [levels.get(name) for name in ("V0", "V1", "V2", "V3") if levels.get(name)]
    scientific = [
        level for name, level in levels.items()
        if name not in {"V0", "V1", "V2", "V3"} and isinstance(level, dict)
    ]
    if structural and scientific:
        if all(level.get("ok") for level in structural) and not all(level.get("ok") for level in scientific):
            return _decision(
                "AGENT_LIMITATION",
                ["structural/provenance gates pass; scientific outcome fails"],
                "high",
            )

    if str(run.get("result_class", "")) == "VALID_RESULT" or failure_code == "PASS":
        return _decision(None, ["valid result"], "high")
    return _decision(
        "CASE_DESIGN_BLOCKED",
        ["unclassified failure requires case review before attribution"],
        "low",
    )


def _load(path: Path | None) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8") if path else sys.stdin.read()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Discovery record root must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path)
    parser.add_argument("--json", action="store_true", help="retained for CLI symmetry")
    args = parser.parse_args()
    try:
        result = classify(_load(args.run))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
