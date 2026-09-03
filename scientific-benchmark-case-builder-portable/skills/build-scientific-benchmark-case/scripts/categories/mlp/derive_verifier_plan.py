#!/usr/bin/env python3
"""Derive an MLP verifier plan from a case design.

Every applicable verifier layer is emitted explicitly with a status —
`selected` or `deferred` — and never dropped silently. Applicability is the
union of (a) structural layers that are always on, (b) layers whose capability
condition is declared in the design, and (c) layers mandatory for the case
kind per `references/categories/mlp/verifier-policy.md`'s applicability table
(mirrored below as KIND_LAYERS; a package test fails on drift with the
table). A mandatory layer can only leave the executed chain as `deferred`
with a non-empty reason declared in the design's `verifier_deferrals`.

Fixture closure is derived from the SELECTED hard-outcome layers. Unknown
capability names and unknown case kinds fail against the catalogs.
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

SCHEMA_VERSION = 2
# id -> (name, capability condition or None). None = structural, always on.
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
# Machine mirror of the applicability table in
# references/categories/mlp/verifier-policy.md (test_v3_hardening parses the
# table and fails if the two disagree). "Mandatory" layers are applicable to
# every case of that kind and may only exit the chain via an explicit,
# reasoned deferral; "conditional" layers are applicable when their capability
# is declared (or when deferred by design).
KIND_LAYERS = {
    "final_model_retraining": {
        "mandatory": {"MLP-V0", "MLP-V1", "MLP-V2", "MLP-V4"},
        "conditional": {"MLP-V3", "MLP-V5", "MLP-V6"},
    },
    "end_to_end_model_development": {
        "mandatory": {"MLP-V0", "MLP-V1", "MLP-V2", "MLP-V4"},
        "conditional": {"MLP-V3", "MLP-V5", "MLP-V6"},
    },
    "active_learning_workflow": {
        "mandatory": {"MLP-V0", "MLP-V1", "MLP-V2", "MLP-V3", "MLP-V4"},
        "conditional": {"MLP-V5", "MLP-V6"},
    },
    "published_model_execution": {
        "mandatory": {"MLP-V0", "MLP-V2", "MLP-V4", "MLP-V6"},
        "conditional": {"MLP-V1", "MLP-V3", "MLP-V5"},
    },
    "model_evaluation": {
        "mandatory": {"MLP-V0", "MLP-V2", "MLP-V4"},
        "conditional": {"MLP-V1", "MLP-V3", "MLP-V5", "MLP-V6"},
    },
}
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


def _read_deferrals(design: dict) -> dict[str, str]:
    """Design-declared layer deferrals: {layer id: reason}."""
    raw = design.get("verifier_deferrals")
    if raw is None:
        return {}
    if not isinstance(raw, list):
        fail("verifier_deferrals must be a list of {layer, reason} objects")
    out: dict[str, str] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            fail("verifier_deferrals entries must be objects with layer and reason")
        layer = str(entry.get("layer", ""))
        reason = str(entry.get("reason", "")).strip()
        if not layer:
            fail("verifier_deferrals entry lacks layer")
        if not reason:
            fail(
                f"verifier_deferrals {layer}: a deferral without a reason is exactly "
                "the silent layer drop this schema forbids"
            )
        out[layer] = reason
    return out


def derive(design: dict) -> dict:
    kind = design.get("case_kind")
    if kind not in KIND_LAYERS:
        fail(f"case_kind {kind!r} unknown; expected one of {sorted(KIND_LAYERS)}")
    caps = design.get("workflow_capabilities") or []
    if not isinstance(caps, list):
        fail("workflow_capabilities must be a list")
    for cap in caps:
        if cap not in CATALOG:
            fail(f"unknown capability: {cap}")

    table = KIND_LAYERS[kind]
    deferrals = _read_deferrals(design)
    unknown_deferrals = sorted(set(deferrals) - set(MLP_LAYERS))
    if unknown_deferrals:
        fail(f"verifier_deferrals name unknown layers: {unknown_deferrals}")

    layers = []
    for lid, (name, condition) in MLP_LAYERS.items():
        structural = condition is None
        mandatory = lid in table["mandatory"]
        condition_met = condition is not None and condition in caps
        if not (structural or mandatory or condition_met):
            continue  # not applicable to this design at all (e.g. V5 undeclared)
        if lid in deferrals:
            if structural:
                fail(
                    f"{lid} is a structural layer: the technical chain is always "
                    "executed; deferral applies to hard-outcome and optional layers"
                )
            status, reason = "deferred", deferrals[lid]
        else:
            status, reason = "selected", None
        layer = {
            "id": lid,
            "name": name,
            "conditional": condition is not None,
            "hard_outcome": lid in HARD_OUTCOME,
            "mandatory": mandatory,
            "status": status,
        }
        if reason is not None:
            layer["reason"] = reason
        layers.append(layer)
    for cid, cname in COMMON_LAYERS:
        layers.append({
            "id": cid,
            "name": cname,
            "conditional": False,
            "hard_outcome": False,
            "mandatory": True,
            "status": "selected",
        })

    unapplied = sorted(set(deferrals) - {layer["id"] for layer in layers})
    if unapplied:
        fail(
            f"verifier_deferrals target inapplicable layers {unapplied}: they are not "
            "part of this design's scope (capability undeclared and not mandatory for "
            "the kind) — remove the deferral or add the capability"
        )

    negative_required = sum(
        1 for layer in layers
        if layer["hard_outcome"] and layer["status"] == "selected"
    )
    fixtures = {
        "positive": FIXTURE_CLOSURE["positive"],
        "negative": negative_required,
        "alternative_valid": FIXTURE_CLOSURE["alternative_valid"],
    }
    deferred_layers = [layer["id"] for layer in layers if layer["status"] == "deferred"]
    return {
        "schema_version": SCHEMA_VERSION,
        "category": "mlp",
        "case_kind": kind,
        "submission_root": ".",
        "result_path": "/logs/verifier/result.json",
        "layers": layers,
        "fixtures": fixtures,
        "deferred_layers": deferred_layers,
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
        for layer in plan["layers"]:
            mark = "deferred" if layer["status"] == "deferred" else "selected"
            star = "*" if layer["mandatory"] else " "
            print(f"  {star} {layer['id']:8s} {mark}")
        print(f"fixtures required: {plan['fixtures']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
