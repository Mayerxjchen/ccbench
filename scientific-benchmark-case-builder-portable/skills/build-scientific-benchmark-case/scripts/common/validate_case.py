#!/usr/bin/env python3
"""Structural validator for benchmark case directories.

Validates the Common Core case contract: required files, maturity state,
execution class, and the fail-closed validity rule. It does not recompute
gate status from evidence bytes; derive_validation_state.py (Task 9) owns
that and check_release.py is the only writer of benchmark_valid=true.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc

ALLOWED_STATES = [
    "draft",
    "constructed",
    "runnable_draft",
    "discovery_complete",
    "promoted",
    "reference_validated",
    "verifier_validated",
    "benchmark_valid",
    "experiment_ready",
]
ALLOWED_EXECUTION = ["local_sandbox", "hpc_controller"]
ADVANCED_STATES = {
    "reference_validated",
    "verifier_validated",
    "benchmark_valid",
    "experiment_ready",
}
SITE_FIELDS = {"hostname", "partition", "account", "ssh", "scheduler"}
HPC_REQUIRED_FILES = [
    "profiles/resource.yaml",
    "profiles/platform.yaml",
    "reference/compute-runtime.lock.json",
]
REQUIRED_PATHS = [
    "CONTRACT.md",
    "instruction.md",
    "task.toml",
    "case-design.yaml",
    "public",
    "source",
    "reference",
    "solution/expert",
    "tests/fixtures/positive",
    "tests/fixtures/negative",
    "tests/fixtures/alternative-valid",
    "profiles/smoke.yaml",
    "profiles/formal.yaml",
    "tools",
    "evidence/retention-policy.yaml",
    "evidence/manifest.json",
    "evaluator-manifest.json",
    "VALIDATION.json",
    "benchmark_valid.json",
]
_SITE_KEY_PATTERN = re.compile(r"^\s*(hostname|partition|account|ssh|scheduler)\s*:", re.M)
_STRUCTURED_SUFFIXES = {".yaml", ".yml", ".json", ".toml"}
SKILL_ROOT = Path(__file__).resolve().parents[2]
MLP_DRAFT_CHECKER = (
    SKILL_ROOT / "scripts/categories/mlp/check_draft_consistency.py"
)


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: root must be a mapping")
    return value


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: root must be a mapping")
    return value


def find_site_fields(case_dir: Path) -> list[str]:
    """Find site-specific keys in structured config files of a case."""
    hits: list[str] = []
    for path in case_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _STRUCTURED_SUFFIXES:
            continue
        rel_parts = path.relative_to(case_dir).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if _SITE_KEY_PATTERN.search(path.read_text(encoding="utf-8", errors="replace")):
            hits.append(str(path.relative_to(case_dir)))
    return sorted(hits)


def category_quality_checks(case_dir: Path, design: dict[str, Any]) -> dict:
    """Run optional category checks without weakening Common validation."""
    if design.get("category") != "mlp" or not (case_dir / "public/system.json").is_file():
        return {"applicable": False, "valid": True, "errors": []}
    spec = importlib.util.spec_from_file_location("mlp_draft_consistency", MLP_DRAFT_CHECKER)
    if spec is None or spec.loader is None:
        return {
            "applicable": True,
            "valid": False,
            "errors": [f"cannot load MLP quality checker: {MLP_DRAFT_CHECKER}"],
        }
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.check_case(case_dir)
    return {"applicable": True, **result}


def validate_case(case_dir: Path) -> dict:
    errors: list[str] = []
    for rel in REQUIRED_PATHS:
        if not (case_dir / rel).exists():
            errors.append(f"missing required path: {rel}")

    design: dict[str, Any] = {}
    design_path = case_dir / "case-design.yaml"
    if design_path.is_file():
        design = load_yaml(design_path)
    exec_class = (design.get("execution") or {}).get("class")
    if exec_class not in ALLOWED_EXECUTION:
        errors.append(f"unknown execution class: {exec_class!r}")
    elif exec_class == "hpc_controller":
        for rel in HPC_REQUIRED_FILES:
            path = case_dir / rel
            if not path.is_file() or not path.read_text(encoding="utf-8").strip():
                errors.append(f"hpc_controller requires non-empty {rel}")
    else:
        for rel in HPC_REQUIRED_FILES:
            if (case_dir / rel).exists():
                errors.append(f"local_sandbox must not contain {rel}")
        for hit in find_site_fields(case_dir):
            errors.append(f"local_sandbox contains site field in {hit}")

    validation: dict[str, Any] = {}
    validation_path = case_dir / "VALIDATION.json"
    if validation_path.is_file():
        validation = load_json(validation_path)
    status = validation.get("case_status")
    if status not in ALLOWED_STATES:
        errors.append(f"unknown case_status: {status!r}")
    elif status in ADVANCED_STATES and not (validation.get("evidence_pointers") or {}):
        errors.append(f"case_status {status} requires evidence_pointers")

    bv: dict[str, Any] = {}
    bv_path = case_dir / "benchmark_valid.json"
    if bv_path.is_file():
        bv = load_json(bv_path)
    if bv.get("benchmark_valid") is True:
        if status not in {"benchmark_valid", "experiment_ready"}:
            errors.append(
                "benchmark_valid=true but case_status is not benchmark_valid/experiment_ready"
            )
        if validation.get("open_gates"):
            errors.append("benchmark_valid=true with open release gates")

    quality = category_quality_checks(case_dir, design)
    if not quality.get("valid", False):
        errors.extend(f"quality: {error}" for error in quality.get("errors", []))

    return {
        "case_dir": str(case_dir),
        "case_status": status,
        "execution_class": exec_class,
        "benchmark_valid": bv.get("benchmark_valid", False) is True,
        "valid": not errors,
        "errors": errors,
        "quality_checks": quality,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="emit JSON verdict")
    args = parser.parse_args()
    result = validate_case(args.case_dir)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for error in result["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
