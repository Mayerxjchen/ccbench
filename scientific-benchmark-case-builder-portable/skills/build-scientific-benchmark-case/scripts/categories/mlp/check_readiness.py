#!/usr/bin/env python3
"""Deterministic information-readiness checker for MLIP reproduction specs.

This script does NOT judge scientific adequacy or production readiness.
Requires PyYAML when run directly.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc

READY = "ready"
RECOVERABLE = "recoverable"
BLOCKED = "blocked"
NA = "not_applicable"


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: root must be a mapping")
    return data


def get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def unwrap(value: Any) -> tuple[Any, str | None]:
    if isinstance(value, dict) and "claim_status" in value:
        return value.get("value"), value.get("claim_status")
    return value, None


def known(value: Any) -> bool:
    raw, status = unwrap(value)
    if status in {"unknown", "conflicting"}:
        return False
    if raw is None:
        return False
    if raw == "":
        return False
    if isinstance(raw, (list, dict)) and not raw:
        return False
    return True


def any_known(obj: dict[str, Any], paths: list[str]) -> bool:
    return any(known(get_path(obj, p)) for p in paths)


def missing_paths(obj: dict[str, Any], paths: list[str]) -> list[str]:
    return [p for p in paths if not known(get_path(obj, p))]


def unresolved_critical_conflicts(evidence_map: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for conflict in evidence_map.get("conflicts", []) or []:
        if not isinstance(conflict, dict):
            continue
        resolution = conflict.get("resolution", {}) or {}
        if conflict.get("severity") == "critical" and resolution.get("status") == "unresolved":
            result.append(conflict)
    return result


def conflict_dimensions(field: str) -> set[str]:
    """Return readiness dimensions affected by a critical conflict."""
    if field.startswith("licenses."):
        return {"benchmark"}
    if field.startswith("validation."):
        return {"verification", "benchmark"}
    if field.startswith("access."):
        return {"execution", "verification", "benchmark"}
    return {"execution", "verification", "benchmark"}


def unknown_claim_paths(obj: Any, path: str = "") -> list[str]:
    result: list[str] = []
    if isinstance(obj, dict):
        if obj.get("claim_status") == "unknown":
            result.append(path)
        for key, value in obj.items():
            child = f"{path}.{key}" if path else str(key)
            result.extend(unknown_claim_paths(value, child))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            result.extend(unknown_claim_paths(value, f"{path}[{index}]"))
    return result


def access_status(spec: dict[str, Any], item: str) -> str:
    value = get_path(spec, f"access.{item}.status")
    raw, _ = unwrap(value)
    return raw if isinstance(raw, str) else "unknown"


def license_value(spec: dict[str, Any], item: str) -> str:
    value = get_path(spec, f"licenses.{item}.license")
    raw, _ = unwrap(value)
    return raw if isinstance(raw, str) else "unknown"


def result_status(missing: list[str], hard_block: bool, recovery_possible: bool = True) -> str:
    if hard_block:
        return BLOCKED
    if missing:
        return RECOVERABLE if recovery_possible else BLOCKED
    return READY


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    spec = load_yaml(args.spec)
    ev_map = load_yaml(args.evidence) if args.evidence else {}
    scope = get_path(spec, "reproduction_scope.target") or "unknown"

    missing: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    critical_conflicts = unresolved_critical_conflicts(ev_map)

    extraction_paths = [
        "paper.title",
        "reproduction_scope.target",
        "target.primary.model_family",
        "target.primary.role",
        "system.elements",
        "model.family",
    ]
    extraction_missing = missing_paths(spec, extraction_paths)

    exec_required: list[str] = []
    verify_required: list[str] = []
    execution_na = False
    verification_na = False

    if scope == "final_model_retraining":
        exec_required = [
            "target.primary.model_family",
            "target.primary.role",
            "model.family",
            "model.architecture",
            "training.data_binding.train_systems",
        ]
        if not any_known(spec, ["dataset.artifact.source_id", "dataset.artifact.manifest", "dataset.artifact.paths"]):
            exec_required.append("dataset.artifact")
        if not any_known(spec, ["implementation.framework", "implementation.repository", "implementation.entrypoint"]):
            exec_required.append("implementation.framework_or_entrypoint")
        if not any_known(spec, ["training.steps", "training.epochs"]):
            exec_required.append("training.steps_or_epochs")
        verify_required = ["validation.model_level_contract"]
    elif scope == "full_data_generation_and_training":
        exec_required = [
            "target.primary.model_family",
            "model.family",
            "reference_method",
            "dataset.data_generation",
            "training",
        ]
        exec_required.extend(["model.architecture", "training.data_binding.train_systems"])
        verify_required = ["validation.model_level_contract"]
    elif scope == "published_model_execution":
        if not any_known(spec, ["access.pretrained_model.source", "dataset.artifact.source_id"]):
            exec_required.append("pretrained_model_artifact")
        if not any_known(spec, ["implementation.framework", "implementation.entrypoint"]):
            exec_required.append("implementation.framework_or_entrypoint")
        verification_na = True
    elif scope == "model_evaluation_only":
        execution_na = True
        verify_required = ["validation.model_level_contract"]
    elif scope == "training_recipe_recovery":
        execution_na = True
        verification_na = True
    elif scope == "reproducibility_screening":
        execution_na = False
        verification_na = False
        exec_required = ["target.primary.model_family", "model.family"]
        verify_required = ["validation.model_level_contract"]
    else:
        exec_required = ["target.primary.model_family", "model.family"]
        verify_required = ["validation.model_level_contract"]

    # Resolve synthetic OR-path markers and ordinary paths.
    def unresolved(reqs: list[str]) -> list[str]:
        result: list[str] = []
        for p in reqs:
            if p == "dataset.artifact":
                if not any_known(spec, ["dataset.artifact.source_id", "dataset.artifact.manifest", "dataset.artifact.paths"]):
                    result.append(p)
            elif p == "implementation.framework_or_entrypoint":
                if not any_known(spec, ["implementation.framework", "implementation.repository", "implementation.entrypoint"]):
                    result.append(p)
            elif p == "training.steps_or_epochs":
                if not any_known(spec, ["training.steps", "training.epochs"]):
                    result.append(p)
            elif p == "pretrained_model_artifact":
                if not any_known(spec, ["access.pretrained_model.source", "implementation.repository"]):
                    result.append(p)
            elif p == "model.architecture":
                if not any_known(spec, [
                    "model.architecture.cutoff", "model.architecture.parameters"
                ]):
                    result.append(p)
            elif p == "validation.model_level_contract":
                if not any_known(spec, [
                    "validation.model_level.dataset_or_split",
                    "validation.model_level.split",
                ]):
                    result.append("validation.model_level.dataset_or_split")
                if not known(get_path(spec, "validation.model_level.metrics")):
                    result.append("validation.model_level.metrics")
                if not any_known(spec, [
                    "validation.model_level.procedure",
                    "validation.model_level.reference_values",
                ]):
                    result.append("validation.model_level.procedure_or_reference")
            elif not known(get_path(spec, p)):
                result.append(p)
        return result

    exec_missing = unresolved(exec_required)
    verify_missing = unresolved(verify_required)

    # Access hard blocks only when the scope actually needs the asset.
    exec_conflicts = [
        c for c in critical_conflicts
        if "execution" in conflict_dimensions(str(c.get("field") or ""))
    ]
    verify_conflicts = [
        c for c in critical_conflicts
        if "verification" in conflict_dimensions(str(c.get("field") or ""))
    ]
    benchmark_conflicts = [
        c for c in critical_conflicts
        if "benchmark" in conflict_dimensions(str(c.get("field") or ""))
    ]

    exec_hard_block = bool(exec_conflicts)
    if scope in {"final_model_retraining", "full_data_generation_and_training"}:
        if access_status(spec, "dataset") == "unavailable":
            exec_hard_block = True
            blockers.append({"field": "access.dataset", "reason": "required dataset unavailable"})
    if scope == "published_model_execution" and access_status(spec, "pretrained_model") == "unavailable":
        exec_hard_block = True
        blockers.append({"field": "access.pretrained_model", "reason": "required model artifact unavailable"})

    verify_hard_block = bool(verify_conflicts)
    model_level_status = get_path(spec, "validation.model_level.status")
    if model_level_status in {"unavailable", "restricted"}:
        verify_hard_block = True
        blockers.append({
            "field": "validation.model_level.status",
            "reason": f"model-level validation is {model_level_status}",
        })

    extraction_status = result_status(extraction_missing, False)
    execution_status = NA if execution_na else result_status(exec_missing, exec_hard_block)
    verification_status = NA if verification_na else result_status(verify_missing, verify_hard_block)

    # Benchmark packaging is stricter on redistributability but still not a scientific-validity claim.
    if execution_status == BLOCKED or verification_status == BLOCKED:
        benchmark_status = BLOCKED
    elif benchmark_conflicts:
        benchmark_status = BLOCKED
    else:
        license_unknown = any(license_value(spec, item) in {"unknown", "", None} for item in ("code", "dataset", "model"))
        benchmark_status = RECOVERABLE if license_unknown or execution_status == RECOVERABLE or verification_status == RECOVERABLE else READY

    missing_by_field: dict[str, dict[str, Any]] = {}

    def add_missing(field: str, dimension: str | None, recovery: str) -> None:
        row = missing_by_field.setdefault(field, {
            "field": field, "critical_for": [], "recovery": recovery,
        })
        if dimension and dimension not in row["critical_for"]:
            row["critical_for"].append(dimension)

    for p in extraction_missing:
        add_missing(p, "extraction_completeness", "inspect available source artifacts")
    for p in exec_missing:
        add_missing(p, "execution_readiness", "inspect version-matched config/dataset/repository artifact")
    for p in verify_missing:
        add_missing(p, "verification_readiness", "recover exact validation split/metric/procedure from source artifacts")

    # Preserve important noncritical unknowns even when they do not change a
    # readiness verdict. This keeps the assessment reproducible and honest.
    tracked_noncritical = {
        "training.seed",
        "training.batch_size",
        "implementation.version",
        "model.precision",
    }
    for p in unknown_claim_paths(spec):
        if p in tracked_noncritical:
            add_missing(p, None, "inspect version-matched training artifacts if exact stochastic reproduction is required")
    missing = [missing_by_field[key] for key in sorted(missing_by_field)]

    for conflict in critical_conflicts:
        blockers.append({
            "field": conflict.get("field"),
            "reason": "unresolved critical conflict",
            "conflict_id": conflict.get("conflict_id"),
            "affects": sorted(conflict_dimensions(str(conflict.get("field") or ""))),
        })

    assessment = {
        "schema_version": 1,
        "scope": scope,
        "missing": missing,
        "conflicts": critical_conflicts,
        "blockers": blockers,
        "readiness": {
            "extraction_completeness": extraction_status,
            "execution_readiness": execution_status,
            "verification_readiness": verification_status,
            "benchmark_case_readiness": benchmark_status,
        },
        "production_readiness": {"status": "not_assessed", "owner": "mlp"},
        "note": "Deterministic information-readiness only; no scientific adequacy judgment performed.",
    }

    text = yaml.safe_dump(assessment, sort_keys=False, allow_unicode=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
