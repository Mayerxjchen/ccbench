"""Release Candidate derivation — The single source of truth for benchmark_valid.

Public API:
    evaluate_release_validity(run_dir) -> dict   Pure evaluation, no side-effects.
    check_release_validity(run_dir)    -> dict   Evaluate + write receipt.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from bench.contracts.case import CaseSpec, validate_coverage_tags
from bench.builder.design import get_category_plugin, load_case_ir
from bench.builder.runnable import evaluate_runnable_draft
from bench.builder.source_lock import check_gold_leakage, verify_sources_lock_bidirectional


class ReleaseCheckError(Exception):
    """Raised when release validation fails."""


def evaluate_release_validity(run_dir: Path) -> dict[str, Any]:
    """Pure evaluation of release candidate validity. No file writes.

    Returns dict with keys: valid, errors, evidence_graph.
    """
    run_dir = Path(run_dir).resolve()
    draft_dir = run_dir / "draft"
    reports_dir = run_dir / "reports"
    errors: list[str] = []
    evidence_graph: dict[str, Any] = {}

    # 1. Validate CaseSpec
    spec = None
    try:
        spec = CaseSpec.load(draft_dir)
        evidence_graph["case_spec"] = {
            "case_id": spec.case_id,
            "case_version": spec.case_version,
            "execution_class": spec.execution_class,
        }
        validate_coverage_tags(spec.coverage)
        evidence_graph["coverage"] = spec.coverage.to_dict()
    except Exception as exc:
        errors.append(f"CaseSpec validation failed: {exc}")

    # 2. Validate Case IR & Category Plugin
    case_ir_path = run_dir / "design" / "case.ir.yaml"
    if not case_ir_path.is_file():
        case_ir_path = run_dir / "design" / "case.ir.json"
    case_ir = None
    ir_sha = None
    if not case_ir_path.is_file():
        errors.append("Missing design/case.ir.yaml")
    else:
        try:
            case_ir = load_case_ir(case_ir_path)
            ir_sha = hashlib.sha256(case_ir_path.read_bytes()).hexdigest()
            evidence_graph["case_ir_sha256"] = f"sha256:{ir_sha}"

            # Identity SSOT: CaseSpec.case_id == Case IR case_id
            if spec and case_ir:
                ir_case_id = case_ir.get("identity", {}).get("case_id")
                if ir_case_id and ir_case_id != spec.case_id:
                    errors.append(
                        f"Identity mismatch: Case IR case_id '{ir_case_id}' != "
                        f"CaseSpec case_id '{spec.case_id}'"
                    )
        except Exception as exc:
            errors.append(f"Case IR validation failed: {exc}")

    # 3. Source Lock (bidirectional + gold taint)
    source_dir = run_dir / "source"
    lock_ok, lock_errors = verify_sources_lock_bidirectional(source_dir, require_non_empty=True)
    if not lock_ok:
        errors.extend(lock_errors)
    elif case_ir:
        try:
            slock = json.loads((source_dir / "sources.lock.json").read_text(encoding="utf-8"))
            sources = slock.get("sources", [])
            manifest = {s["path"]: s.get("tier", "PUBLIC_SOURCE") for s in sources}
            lineage = {s["path"]: s.get("derived_from", []) for s in sources}
            cand_inputs = [i["path"] for i in case_ir.get("candidate", {}).get("inputs", [])]
            taint_violations = check_gold_leakage(cand_inputs, manifest, lineage)
            if taint_violations:
                errors.extend(taint_violations)
        except Exception as exc:
            errors.append(f"Source lock parsing failed: {exc}")

    # 4. Runnable Draft gate (pure evaluation, no side-effects)
    smoke_res = evaluate_runnable_draft(run_dir)
    if not smoke_res.get("passed", False):
        errors.append(f"Runnable Draft gate failed: {smoke_res.get('errors')}")
    else:
        evidence_graph["runnable_smoke"] = smoke_res

    # 5. Discovery classification (canonical verifier)
    from bench.builder.discovery import verify_discovery_classification
    disc_ok, disc_result = verify_discovery_classification(run_dir)
    if not disc_ok:
        errors.append(f"Discovery classification failed: {disc_result.get('errors', disc_result)}")
    else:
        evidence_graph["discovery"] = disc_result

    # 6. Reference Receipt
    ref_file = reports_dir / "reference-ready.json"
    if not ref_file.is_file():
        errors.append("Missing reports/reference-ready.json")
    else:
        try:
            ref_doc = json.loads(ref_file.read_text(encoding="utf-8"))
            if not ref_doc.get("reproducible", False):
                errors.append("Reference run is not marked reproducible")
            ref_digest = f"sha256:{hashlib.sha256(ref_file.read_bytes()).hexdigest()}"
            evidence_graph["reference"] = ref_doc
            evidence_graph["reference_digest"] = ref_digest
        except Exception as exc:
            errors.append(f"Failed to parse reference-ready.json: {exc}")

    # 7. Calibration Report & Plugin Validation
    cal_file = reports_dir / "calibration-report.json"
    cal_doc = None
    if not cal_file.is_file():
        errors.append("Missing reports/calibration-report.json")
    else:
        try:
            cal_doc = json.loads(cal_file.read_text(encoding="utf-8"))
            if not cal_doc.get("passed", False):
                errors.append("Calibration report is not marked passed")
            if case_ir:
                cat = case_ir.get("identity", {}).get("category", "")
                plugin = get_category_plugin(cat)
                if plugin and not plugin.validate_calibration(cal_doc):
                    errors.append(f"Category plugin '{cat}' rejected calibration (invalid thresholds)")
                # Verify calibration thresholds match Case IR thresholds
                cal_thresholds = cal_doc.get("thresholds", {})
                ir_thresholds = case_ir.get("verification", {}).get("thresholds", {})
                for key, val in ir_thresholds.items():
                    if key not in cal_thresholds:
                        errors.append(f"Calibration missing Case IR threshold '{key}'")
                    elif abs(float(cal_thresholds[key]) - float(val)) > 1e-9:
                        errors.append(
                            f"Calibration threshold mismatch for '{key}': "
                            f"cal={cal_thresholds[key]} != ir={val}"
                        )
            evidence_graph["calibration"] = cal_doc
        except Exception as exc:
            errors.append(f"Failed to read calibration report: {exc}")

    # 8. Threshold Freeze (MANDATORY)
    freeze_file = reports_dir / "threshold-freeze.json"
    if not freeze_file.is_file():
        errors.append(
            "Missing reports/threshold-freeze.json "
            "(MANDATORY: thresholds must be frozen independently before formal agent evaluation)"
        )
    else:
        try:
            freeze_doc = json.loads(freeze_file.read_text(encoding="utf-8"))
            if not isinstance(freeze_doc, dict):
                errors.append("threshold-freeze.json is not a valid JSON object")
            else:
                # Verify case_ir_digest binding (mandatory)
                if ir_sha:
                    actual_ir_digest = f"sha256:{ir_sha}"
                    if freeze_doc.get("case_ir_digest") != actual_ir_digest:
                        errors.append(
                            f"threshold-freeze case_ir_digest mismatch: "
                            f"freeze={freeze_doc.get('case_ir_digest')} != actual={actual_ir_digest}"
                        )

                # Verify formal_agent_results_seen is False (mandatory)
                if freeze_doc.get("formal_agent_results_seen", True):
                    errors.append(
                        "threshold-freeze violation: formal_agent_results_seen must be False"
                    )

                # Verify thresholds_digest is present and matches calibration (mandatory)
                freeze_thresholds_digest = freeze_doc.get("thresholds_digest", "")
                if not freeze_thresholds_digest:
                    errors.append("threshold-freeze thresholds_digest is mandatory but missing")
                elif cal_doc:
                    cal_thresholds = cal_doc.get("thresholds", {})
                    expected_thresholds_digest = _sha256_from_obj(cal_thresholds)
                    if freeze_thresholds_digest != expected_thresholds_digest:
                        errors.append(
                            f"threshold-freeze thresholds_digest mismatch: "
                            f"freeze={freeze_thresholds_digest} != actual={expected_thresholds_digest}"
                        )

                # Verify reference_digest binding (mandatory)
                freeze_ref_digest = freeze_doc.get("reference_digest", "")
                if not freeze_ref_digest:
                    errors.append("threshold-freeze reference_digest is mandatory but missing")
                elif evidence_graph.get("reference_digest") and freeze_ref_digest != evidence_graph["reference_digest"]:
                    errors.append(
                        f"threshold-freeze reference_digest mismatch: "
                        f"freeze={freeze_ref_digest} != actual={evidence_graph['reference_digest']}"
                    )

            evidence_graph["threshold_freeze"] = freeze_doc
        except Exception as exc:
            errors.append(f"Failed to read threshold-freeze.json: {exc}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "evidence_graph": evidence_graph,
    }


def check_release_validity(run_dir: Path) -> dict[str, Any]:
    """Evaluate release validity and write the receipt file."""
    result = evaluate_release_validity(run_dir)

    report = {
        "valid": result["valid"],
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "errors": result["errors"],
        "evidence_graph": result["evidence_graph"],
    }

    reports_dir = Path(run_dir).resolve() / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / "benchmark-valid.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _sha256_from_obj(obj: Any) -> str:
    """Compute sha256 of a JSON-serializable object."""
    data = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"
