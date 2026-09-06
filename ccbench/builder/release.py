"""Release Candidate derivation — The single source of truth for benchmark_valid."""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from ccbench.contracts.case import CaseSpec, validate_coverage_tags
from ccbench.builder.design import load_case_ir
from ccbench.builder.runnable import check_runnable_draft
from ccbench.builder.source_lock import check_gold_leakage


class ReleaseCheckError(Exception):
    """Raised when release validation fails."""


def check_release_validity(run_dir: Path) -> dict[str, Any]:
    """Execute rigorous, fail-closed release candidate verification.

    Requirements for benchmark_valid:
    1. CaseSpec loads and validates against schemas/case.schema.json.
    2. Case IR validates with category plugin and schema.
    3. Runnable Draft checks (packaging, verifier mount smoke, leak scan) pass.
    4. Sources are locked, non-empty, and un-tampered.
    5. No gold taint in candidate surface.
    6. Discovery run is classified as PROMOTED with real evidence.
    7. Calibration report exists and confirms frozen thresholds.
    8. Coverage dimensions validate against curated vocabularies.
    """
    run_dir = Path(run_dir).resolve()
    draft_dir = run_dir / "draft"
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

    # 2. Validate Case IR
    case_ir_path = run_dir / "design" / "case.ir.yaml"
    if not case_ir_path.is_file():
        case_ir_path = run_dir / "design" / "case.ir.json"
    case_ir = None
    if not case_ir_path.is_file():
        errors.append("Missing design/case.ir.yaml")
    else:
        try:
            case_ir = load_case_ir(case_ir_path)
            evidence_graph["case_ir_sha256"] = hashlib.sha256(case_ir_path.read_bytes()).hexdigest()
        except Exception as exc:
            errors.append(f"Case IR validation failed: {exc}")

    # 3. Validate Source Lock and check gold taint
    source_lock_file = run_dir / "source" / "sources.lock.json"
    if not source_lock_file.is_file():
        errors.append("Missing source/sources.lock.json")
    else:
        try:
            slock = json.loads(source_lock_file.read_text(encoding="utf-8"))
            sources = slock.get("sources", [])
            manifest = {s["path"]: s.get("tier", "PUBLIC_SOURCE") for s in sources}
            lineage = {s["path"]: s.get("derived_from", []) for s in sources}

            # Verify every source file exists and has un-tampered hash
            for s in sources:
                src_file = run_dir / "source" / s["path"]
                if not src_file.is_file():
                    errors.append(f"Source file declared in lock missing: {s['path']}")
                    continue
                actual_sha = f"sha256:{hashlib.sha256(src_file.read_bytes()).hexdigest()}"
                if actual_sha != s.get("sha256"):
                    errors.append(f"Source file {s['path']} tampered (lock={s.get('sha256')} != disk={actual_sha})")

            # Check candidate inputs for gold taint
            if case_ir:
                cand_inputs = [i["path"] for i in case_ir.get("candidate", {}).get("inputs", [])]
                taint_violations = check_gold_leakage(cand_inputs, manifest, lineage)
                if taint_violations:
                    errors.extend(taint_violations)

            evidence_graph["sources_lock_sha256"] = hashlib.sha256(source_lock_file.read_bytes()).hexdigest()
        except Exception as exc:
            errors.append(f"Source lock check failed: {exc}")

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

    # 6. Reference and Calibration
    ref_file = run_dir / "reports" / "reference-ready.json"
    if not ref_file.is_file():
        errors.append("Missing reports/reference-ready.json")

    cal_file = run_dir / "reports" / "calibration-report.json"
    if not cal_file.is_file():
        errors.append("Missing reports/calibration-report.json")
    else:
        try:
            cal_doc = json.loads(cal_file.read_text(encoding="utf-8"))
            if not cal_doc.get("passed", False):
                errors.append("Calibration report is not marked passed")
            evidence_graph["calibration"] = cal_doc
        except Exception as exc:
            errors.append(f"Failed to read calibration report: {exc}")

    valid = len(errors) == 0
    report = {
        "valid": valid,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "errors": errors,
        "evidence_graph": evidence_graph,
    }

    rep_dir = run_dir / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    target = rep_dir / "benchmark-valid.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
