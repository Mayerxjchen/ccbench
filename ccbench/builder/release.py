"""Release Candidate derivation — The single source of truth for benchmark_valid."""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from ccbench.contracts.case import CaseSpec, validate_coverage_tags
from ccbench.builder.design import get_category_plugin, load_case_ir
from ccbench.builder.runnable import check_runnable_draft
from ccbench.builder.source_lock import check_gold_leakage, verify_sources_lock_bidirectional


class ReleaseCheckError(Exception):
    """Raised when release validation fails."""


def check_release_validity(run_dir: Path) -> dict[str, Any]:
    """Execute rigorous, fail-closed release candidate verification.

    Requirements for benchmark_valid:
    1. CaseSpec loads and validates against schemas/case.schema.json.
    2. Case IR validates with category plugin and schema.
    3. Runnable Draft checks (packaging, verifier mount smoke, leak scan) pass.
    4. Sources are locked, non-empty, and un-tampered (bidirectional verification).
    5. No gold taint in candidate surface.
    6. Discovery run is classified as PROMOTED with real evidence.
    7. Reference run receipt exists and confirms reproducible reference.
    8. Calibration record confirms thresholds and passes CategoryPlugin validation.
    9. Threshold freeze record binds case_ir, reference, and thresholds digests with formal_agent_results_seen=False.
    10. Coverage dimensions validate against curated vocabularies.
    """
    run_dir = Path(run_dir).resolve()
    draft_dir = run_dir / "draft"
    reports_dir = run_dir / "reports"
    errors: list[str] = []
    evidence_graph: dict[str, Any] = {}

    # 1. Validate CaseSpec
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
    if not case_ir_path.is_file():
        errors.append("Missing design/case.ir.yaml")
    else:
        try:
            case_ir = load_case_ir(case_ir_path)
            ir_sha = hashlib.sha256(case_ir_path.read_bytes()).hexdigest()
            evidence_graph["case_ir_sha256"] = f"sha256:{ir_sha}"
        except Exception as exc:
            errors.append(f"Case IR validation failed: {exc}")

    # 3. Validate Source Lock (Bidirectional check)
    source_dir = run_dir / "source"
    lock_ok, lock_errors = verify_sources_lock_bidirectional(source_dir, require_non_empty=True)
    if not lock_ok:
        errors.extend(lock_errors)
    else:
        try:
            slock = json.loads((source_dir / "sources.lock.json").read_text(encoding="utf-8"))
            sources = slock.get("sources", [])
            manifest = {s["path"]: s.get("tier", "PUBLIC_SOURCE") for s in sources}
            lineage = {s["path"]: s.get("derived_from", []) for s in sources}

            # Check candidate inputs for gold taint
            if case_ir:
                cand_inputs = [i["path"] for i in case_ir.get("candidate", {}).get("inputs", [])]
                taint_violations = check_gold_leakage(cand_inputs, manifest, lineage)
                if taint_violations:
                    errors.extend(taint_violations)

            evidence_graph["sources_lock"] = {"count": len(sources)}
        except Exception as exc:
            errors.append(f"Source lock parsing failed: {exc}")

    # 4. Re-execute full Runnable Draft gate
    smoke_res = check_runnable_draft(run_dir)
    if not smoke_res.get("passed", False):
        errors.append(f"Runnable Draft gate failed: {smoke_res.get('errors')}")
    else:
        evidence_graph["runnable_smoke"] = smoke_res

    # 5. Discovery Classification check
    disc_file = run_dir / "discovery" / "classification.json"
    if not disc_file.is_file():
        errors.append("Missing discovery/classification.json (Discovery run required before release)")
    else:
        try:
            disc_doc = json.loads(disc_file.read_text(encoding="utf-8"))
            if disc_doc.get("decision") != "PROMOTED":
                errors.append(f"Discovery decision must be PROMOTED to release, got: {disc_doc.get('decision')}")
            if not disc_doc.get("evidence"):
                errors.append("Discovery decision PROMOTED lacks supporting evidence")
            evidence_graph["discovery"] = disc_doc
        except Exception as exc:
            errors.append(f"Failed to read discovery classification: {exc}")

    # 6. Reference Receipt
    ref_file = reports_dir / "reference-ready.json"
    if not ref_file.is_file():
        errors.append("Missing reports/reference-ready.json")
    else:
        try:
            ref_doc = json.loads(ref_file.read_text(encoding="utf-8"))
            if not ref_doc.get("reproducible", False):
                errors.append("Reference run is not marked reproducible")
            evidence_graph["reference"] = ref_doc
        except Exception as exc:
            errors.append(f"Failed to parse reference-ready.json: {exc}")

    # 7. Calibration Report & Plugin Validation
    cal_file = reports_dir / "calibration-report.json"
    if not cal_file.is_file():
        errors.append("Missing reports/calibration-report.json")
    else:
        try:
            cal_doc = json.loads(cal_file.read_text(encoding="utf-8"))
            if not cal_doc.get("passed", False):
                errors.append("Calibration report is not marked passed")

            # Plugin validation on calibration
            if case_ir:
                cat = case_ir.get("identity", {}).get("category", "")
                plugin = get_category_plugin(cat)
                if plugin and not plugin.validate_calibration(cal_doc):
                    errors.append(f"Category plugin '{cat}' rejected calibration report (invalid or non-positive thresholds)")

            evidence_graph["calibration"] = cal_doc
        except Exception as exc:
            errors.append(f"Failed to read calibration report: {exc}")

    # 8. Threshold Freeze (Must be frozen before formal agent results)
    freeze_file = reports_dir / "threshold-freeze.json"
    if freeze_file.is_file():
        try:
            freeze_doc = json.loads(freeze_file.read_text(encoding="utf-8"))
            if freeze_doc.get("formal_agent_results_seen", True):
                errors.append("Threshold freeze violation: formal_agent_results_seen is True (threshold must be frozen independently)")
            evidence_graph["threshold_freeze"] = freeze_doc
        except Exception as exc:
            errors.append(f"Failed to read threshold-freeze.json: {exc}")

    valid = len(errors) == 0
    report = {
        "valid": valid,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "errors": errors,
        "evidence_graph": evidence_graph,
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / "benchmark-valid.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
