#!/usr/bin/env python3
"""Derive an MLP verifier plan from a case design.

The category proposes applicable verifier layers (capability-driven); Common
Core layers are always on. Fixture closure is derived from the selected hard-
outcome layers. Unknown capability names fail against the MLP catalog.
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

SCHEMA_VERSION = 1
# id -> (name, capability condition or None). None = always on.
MLP_LAYERS = {
    "MLP-V0": ("system/submission identity", None),
    "MLP-V1": ("data/label provenance", None),
    "MLP-V2": ("model authenticity", None),
    "MLP-V3": ("iterative workflow integrity", "iterative_improvement"),
    "MLP-V4": ("hidden static accuracy", "hidden_static_accuracy"),
    "MLP-V5": ("hidden dynamic stability", "hidden_dynamic_stability"),
    "MLP-V6": ("hidden physical observable", "hidden_physical_observable"),
}
COMMON_LAYERS = (
    ("C-V7", "resource and provenance compliance"),
    ("C-V8", "submission integrity, filesystem safety, and manifest contract"),
)
HARD_OUTCOME = {"MLP-V3", "MLP-V4", "MLP-V5", "MLP-V6"}
FIXTURE_CLOSURE = {
    "positive": 1,
    "alternative_valid": 1,
}
# Full MLP capability catalog (workflow-capabilities.md). A capability may be
# a layer condition or only a design requirement; both are accepted.
CATALOG = {
    "structure_generation", "dft_dynamics", "model_training",
    "iterative_improvement", "hidden_static_accuracy",
    "hidden_dynamic_stability", "hidden_physical_observable",
}


def fail(message: str) -> None:
    raise SystemExit(f"derive_verifier_plan: {message}")


def derive(design: dict) -> dict:
    caps = design.get("workflow_capabilities") or []
    if not isinstance(caps, list):
        fail("workflow_capabilities must be a list")
    for cap in caps:
        if cap not in CATALOG:
            fail(f"unknown capability: {cap}")

    layers = []
    for lid, (name, condition) in MLP_LAYERS.items():
        selected = condition is None or condition in caps
        if not selected:
            continue
        layers.append({
            "id": lid,
            "name": name,
            "conditional": condition is not None,
            "hard_outcome": lid in HARD_OUTCOME,
        })
    for cid, cname in COMMON_LAYERS:
        layers.append({
            "id": cid,
            "name": cname,
            "conditional": False,
            "hard_outcome": False,
        })

    negative_required = sum(1 for layer in layers if layer["hard_outcome"])
    fixtures = {
        "positive": FIXTURE_CLOSURE["positive"],
        "negative": negative_required,
        "alternative_valid": FIXTURE_CLOSURE["alternative_valid"],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "category": "mlp",
        "layers": layers,
        "fixtures": fixtures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    design = yaml.safe_load(args.design.read_text(encoding="utf-8")) or {}
    if not isinstance(design, dict):
        fail(f"{args.design}: root must be a mapping")
    plan = derive(design)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")

    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        ids = [layer["id"] for layer in plan["layers"]]
        print(f"layers: {', '.join(ids)}")
        print(f"fixtures required: {plan['fixtures']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
