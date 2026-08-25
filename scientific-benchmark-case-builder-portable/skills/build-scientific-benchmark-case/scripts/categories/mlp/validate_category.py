#!/usr/bin/env python3
"""Structural validator for category modules in build-scientific-benchmark-case.

Verifies that a category registered in category-registry.yaml conforms to the
plugin contract: supported state, declared roots exist, and the required
category reference and script files are present. Does not judge scientific
adequacy.
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

REQUIRED_REFERENCES = [
    "reproduction-schema.md",
    "source-evidence-policy.md",
    "target-model-policy.md",
    "readiness-policy.md",
    "workflow-capabilities.md",
    "verifier-policy.md",
]
REQUIRED_SCRIPTS = [
    "validate_spec.py",
    "check_readiness.py",
    "hash_sources.py",
]
SUPPORTED_STATES = ("supported",)


def load(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: root must be a mapping")
    return value


def validate_category(root: Path, category: str) -> dict:
    """Validate one category module under a Skill root."""
    errors: list[str] = []
    registry_path = root / "references/category-registry.yaml"
    if not registry_path.is_file():
        return {
            "category": category,
            "valid": False,
            "errors": [f"registry missing: {registry_path}"],
        }
    registry = load(registry_path)
    entry = (registry.get("categories") or {}).get(category)
    if entry is None:
        supported = sorted((registry.get("categories") or {}))
        return {
            "category": category,
            "valid": False,
            "errors": [f"unknown category; supported: {supported}"],
        }
    if entry.get("state") not in SUPPORTED_STATES:
        errors.append(f"{category}: state must be one of {sorted(SUPPORTED_STATES)}")

    refs_root = root / entry.get("references_root", "")
    scripts_root = root / entry.get("scripts_root", "")
    template_root = root / entry.get("template_root", "")
    for label, path in (
        ("references_root", refs_root),
        ("scripts_root", scripts_root),
        ("template_root", template_root),
    ):
        if not path.is_dir():
            errors.append(f"{category}: {label} dir missing: {path}")

    for name in REQUIRED_REFERENCES:
        if not (refs_root / name).is_file():
            errors.append(f"{category}: missing reference {name}")
    for name in REQUIRED_SCRIPTS:
        if not (scripts_root / name).is_file():
            errors.append(f"{category}: missing script {name}")

    return {"category": category, "valid": not errors, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="Skill root containing references/, scripts/, assets/ (default: this Skill)",
    )
    parser.add_argument("--category", required=True)
    args = parser.parse_args()
    result = validate_category(args.root, args.category)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
