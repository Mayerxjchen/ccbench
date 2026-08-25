#!/usr/bin/env python3
"""Structural validator for literature-to-mlp-spec YAML artifacts.

This script validates bookkeeping only. It does not judge scientific adequacy.
Requires PyYAML (`python -m pip install pyyaml`) when run directly.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc

ROOT_FIELDS = [
    "schema_version",
    "paper",
    "reproduction_scope",
    "target",
    "system",
    "dataset",
    "labels",
    "reference_method",
    "implementation",
    "model",
    "training",
    "validation",
    "access",
    "licenses",
]
CLAIM_STATUSES = {"observed", "derived", "inferred", "unknown", "conflicting"}
SOURCE_KINDS = {
    "paper",
    "supporting_information",
    "repository_config",
    "repository_code",
    "repository_release",
    "dataset_metadata",
    "model_artifact",
    "author_documentation",
    "external_reference",
}
CONFLICT_RESOLUTIONS = {"unresolved", "resolved", "waived"}
CONFLICT_SEVERITIES = {"critical", "noncritical"}
SCOPES = {
    "final_model_retraining",
    "full_data_generation_and_training",
    "published_model_execution",
    "model_evaluation_only",
    "training_recipe_recovery",
    "reproducibility_screening",
}
MODEL_ROLES = {
    "production", "data_generation", "committee", "baseline", "teacher",
    "student", "pretrained", "fine_tuned", "evaluation_only", "unknown",
}
ACCESS_STATUSES = {"available", "restricted", "unavailable", "unknown"}


def load_yaml(path: Path) -> Any:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    return data


def walk(obj: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    yield path, obj
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            yield from walk(value, child)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from walk(value, f"{path}[{i}]")


def evidence_refs(obj: Any) -> set[str]:
    refs: set[str] = set()
    for _, node in walk(obj):
        if isinstance(node, dict) and "evidence" in node:
            value = node["evidence"]
            if isinstance(value, str):
                refs.add(value)
            elif isinstance(value, list):
                refs.update(x for x in value if isinstance(x, str))
    return refs


def validate_spec(spec: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(spec, dict):
        errors.append("spec root must be a mapping")
        return
    missing = [field for field in ROOT_FIELDS if field not in spec]
    if missing:
        errors.append("missing root fields: " + ", ".join(missing))
    if spec.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    scope = ((spec.get("reproduction_scope") or {}).get("target")
             if isinstance(spec.get("reproduction_scope"), dict) else None)
    if scope not in SCOPES:
        errors.append(f"reproduction_scope.target invalid: {scope!r}")

    target = spec.get("target") or {}
    primary = target.get("primary") if isinstance(target, dict) else None
    if not isinstance(primary, dict):
        errors.append("target.primary must be a mapping")
    elif primary.get("role") not in MODEL_ROLES:
        errors.append(f"target.primary.role invalid: {primary.get('role')!r}")
    for i, model in enumerate(target.get("related_models", []) if isinstance(target, dict) else []):
        if not isinstance(model, dict) or model.get("role") not in MODEL_ROLES:
            errors.append(f"target.related_models[{i}].role invalid")

    access = spec.get("access") or {}
    if isinstance(access, dict):
        for key, value in access.items():
            status = value.get("status") if isinstance(value, dict) else None
            if status not in ACCESS_STATUSES:
                errors.append(f"access.{key}.status invalid: {status!r}")

    for path, node in walk(spec):
        if isinstance(node, dict) and "claim_status" in node:
            status = node["claim_status"]
            if status not in CLAIM_STATUSES:
                errors.append(f"{path}.claim_status invalid: {status!r}")
            if status == "unknown" and node.get("value", None) not in (None, "unknown"):
                errors.append(f"{path}: claim_status=unknown should not carry a concrete value")


def validate_evidence(evidence_map: dict[str, Any], errors: list[str]) -> set[str]:
    if not isinstance(evidence_map, dict):
        errors.append("evidence root must be a mapping")
        return set()
    sources = evidence_map.get("sources", {})
    evidence = evidence_map.get("evidence", {})
    conflicts = evidence_map.get("conflicts", [])
    if evidence_map.get("schema_version") != 1:
        errors.append("evidence schema_version must be 1")
    if not isinstance(sources, dict):
        errors.append("sources must be a mapping")
        sources = {}
    if not isinstance(evidence, dict):
        errors.append("evidence must be a mapping")
        evidence = {}

    for sid, src in sources.items():
        if not isinstance(src, dict):
            errors.append(f"source {sid} must be a mapping")
            continue
        sk = src.get("source_kind")
        if sk not in SOURCE_KINDS:
            errors.append(f"source {sid} has invalid source_kind {sk!r}")

    for eid, ev in evidence.items():
        if not isinstance(ev, dict):
            errors.append(f"evidence {eid} must be a mapping")
            continue
        source_id = ev.get("source_id")
        if source_id not in sources:
            errors.append(f"evidence {eid} references missing source_id {source_id!r}")
        sk = ev.get("source_kind")
        if sk not in SOURCE_KINDS:
            errors.append(f"evidence {eid} has invalid source_kind {sk!r}")
        elif source_id in sources and isinstance(sources[source_id], dict):
            source_kind = sources[source_id].get("source_kind")
            # repository_config/code evidence may legitimately point at a
            # repository_release source identity.
            compatible = source_kind == sk or (
                source_kind == "repository_release"
                and sk in {"repository_config", "repository_code"}
            )
            if not compatible:
                errors.append(
                    f"evidence {eid} source_kind {sk!r} mismatches source {source_id!r} kind {source_kind!r}"
                )

    if not isinstance(conflicts, list):
        errors.append("conflicts must be a list")
        conflicts = []
    for i, conflict in enumerate(conflicts):
        pfx = f"conflicts[{i}]"
        if not isinstance(conflict, dict):
            errors.append(f"{pfx} must be a mapping")
            continue
        if conflict.get("severity") not in CONFLICT_SEVERITIES:
            errors.append(f"{pfx}.severity must be one of {sorted(CONFLICT_SEVERITIES)}")
        evs = conflict.get("evidence", [])
        if not isinstance(evs, list) or len(evs) < 2:
            errors.append(f"{pfx}.evidence must list at least two evidence ids")
        else:
            for eid in evs:
                if eid not in evidence:
                    errors.append(f"{pfx} references missing evidence {eid!r}")
        resolution = conflict.get("resolution", {})
        if not isinstance(resolution, dict) or resolution.get("status") not in CONFLICT_RESOLUTIONS:
            errors.append(f"{pfx}.resolution.status must be one of {sorted(CONFLICT_RESOLUTIONS)}")

    return set(evidence)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    spec = load_yaml(args.spec)
    validate_spec(spec, errors)

    if args.evidence:
        ev_map = load_yaml(args.evidence)
        known = validate_evidence(ev_map, errors)
        for ref in sorted(evidence_refs(spec)):
            if ref not in known:
                errors.append(f"spec references missing evidence id {ref!r}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("OK: spec structure and evidence references are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
