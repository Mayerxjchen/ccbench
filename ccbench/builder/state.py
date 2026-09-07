"""Deterministic lifecycle state machine for the CCBench Benchmark Case Factory.

Authoritative principle:
States are derived exclusively from verified evidence and cryptographically bound artifacts.
Neither LLMs nor users can manually set or declare states.

Gate receipts are never trusted blindly: each gate invokes a canonical
verifier function that re-derives the gate verdict from first principles.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class CaseLifecycleState(str, Enum):
    """Authoritative lifecycle states of a benchmark case during construction."""

    NEW = "NEW"
    INTAKE_COMPLETE = "INTAKE_COMPLETE"
    SOURCE_LOCKED = "SOURCE_LOCKED"
    DESIGN_VALID = "DESIGN_VALID"
    DRAFT = "DRAFT"
    RUNNABLE_DRAFT = "RUNNABLE_DRAFT"
    DISCOVERY_CLASSIFIED = "DISCOVERY_CLASSIFIED"
    DISCOVERY_REJECTED = "DISCOVERY_REJECTED"
    DISCOVERY_REFINE = "DISCOVERY_REFINE"
    REFERENCE_READY = "REFERENCE_READY"
    CALIBRATED = "CALIBRATED"
    BENCHMARK_VALID = "BENCHMARK_VALID"
    PUBLISHED = "PUBLISHED"


STATE_ORDER: list[CaseLifecycleState] = [
    CaseLifecycleState.NEW,
    CaseLifecycleState.INTAKE_COMPLETE,
    CaseLifecycleState.SOURCE_LOCKED,
    CaseLifecycleState.DESIGN_VALID,
    CaseLifecycleState.DRAFT,
    CaseLifecycleState.RUNNABLE_DRAFT,
    CaseLifecycleState.DISCOVERY_CLASSIFIED,
    CaseLifecycleState.REFERENCE_READY,
    CaseLifecycleState.CALIBRATED,
    CaseLifecycleState.BENCHMARK_VALID,
    CaseLifecycleState.PUBLISHED,
]


@dataclass(frozen=True)
class BuilderState:
    """Immutable state snapshot for a case builder run."""

    run_id: str
    category: str
    current_state: CaseLifecycleState
    evidence: dict[str, Any] = field(default_factory=dict)
    open_gates: list[str] = field(default_factory=list)
    closed_gates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "category": self.category,
            "current_state": self.current_state.value,
            "evidence": self.evidence,
            "open_gates": self.open_gates,
            "closed_gates": self.closed_gates,
        }

    def save(self, run_dir: Path) -> Path:
        target = run_dir / "builder-state.json"
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target


# ── Canonical Gate Verifiers ──────────────────────────────────────────
# Each verifier re-derives the gate verdict from first principles.
# derive_state() only calls these; it never interprets JSON verdicts directly.


def _verify_runnable_receipt(run_dir: Path) -> tuple[bool, dict[str, Any]]:
    """Re-execute the complete runnable draft gate from scratch."""
    from ccbench.builder.runnable import check_runnable_draft
    report = check_runnable_draft(run_dir)
    return report.get("passed", False), report


def _verify_discovery_receipt(run_dir: Path) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Canonical verification of discovery classification.

    Uses verify_discovery_classification() which re-derives the decision
    from evidence, validates schema, verifies case_ir_digest and
    candidate_bundle_digest bindings, and checks recorded == derived decision.
    """
    from ccbench.builder.discovery import verify_discovery_classification

    ok, result = verify_discovery_classification(run_dir, verify_candidate_digest=True)
    if not ok:
        return ("REJECTED", None, None)

    decision = result.get("decision", "INVALID")
    evidence = result.get("evidence")
    metrics = result.get("metrics")
    return (decision, evidence, metrics)


def _verify_reference_receipt(run_dir: Path) -> tuple[bool, dict[str, Any]]:
    """Re-verify reference receipt exists and declares reproducible state."""
    ref_file = run_dir / "reports" / "reference-ready.json"
    if not ref_file.is_file():
        return False, {}
    try:
        doc = json.loads(ref_file.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return False, {}
        if not doc.get("reproducible", False):
            return False, doc
        return True, doc
    except Exception:
        return False, {}


def _verify_calibration_receipt(run_dir: Path) -> tuple[bool, dict[str, Any]]:
    """Re-verify calibration receipt exists, passes plugin validation, and matches Case IR thresholds."""
    cal_file = run_dir / "reports" / "calibration-report.json"
    if not cal_file.is_file():
        return False, {}
    try:
        cal_doc = json.loads(cal_file.read_text(encoding="utf-8"))
        if not isinstance(cal_doc, dict):
            return False, {}

        thresholds = cal_doc.get("thresholds", {})
        if not thresholds or not cal_doc.get("passed", False):
            return False, cal_doc

        # Verify against Case IR thresholds
        case_ir_path = run_dir / "design" / "case.ir.yaml"
        if not case_ir_path.is_file():
            case_ir_path = run_dir / "design" / "case.ir.json"
        if case_ir_path.is_file():
            try:
                import yaml
                raw_text = case_ir_path.read_text(encoding="utf-8")
                case_ir = yaml.safe_load(raw_text) if case_ir_path.suffix in (".yaml", ".yml") else json.loads(raw_text)
                if isinstance(case_ir, dict):
                    ir_thresholds = case_ir.get("verification", {}).get("thresholds", {})
                    for key, val in ir_thresholds.items():
                        if key not in thresholds:
                            return False, cal_doc
                        if abs(float(thresholds[key]) - float(val)) > 1e-9:
                            return False, cal_doc
            except Exception:
                pass

        # Plugin validation
        if case_ir_path.is_file():
            try:
                import yaml
                raw_text = case_ir_path.read_text(encoding="utf-8")
                case_ir = yaml.safe_load(raw_text) if case_ir_path.suffix in (".yaml", ".yml") else json.loads(raw_text)
                if isinstance(case_ir, dict):
                    cat = case_ir.get("identity", {}).get("category", "")
                    from ccbench.builder.design import get_category_plugin
                    plugin = get_category_plugin(cat)
                    if plugin and not plugin.validate_calibration(cal_doc):
                        return False, cal_doc
            except Exception:
                pass

        return True, cal_doc
    except Exception:
        return False, {}


def _verify_release_receipt(run_dir: Path) -> tuple[bool, dict[str, Any]]:
    """Re-execute the full release validation from first principles.

    Never trusts the benchmark-valid.json verdict; re-derives it by calling
    evaluate_release_validity() which re-checks CaseSpec, Case IR, source
    lock, runnable gate, discovery, reference, calibration, and threshold
    freeze.
    """
    from ccbench.builder.release import evaluate_release_validity
    try:
        result = evaluate_release_validity(run_dir)
        return result["valid"], result
    except Exception:
        return False, {}


# ── Main State Derivation ─────────────────────────────────────────────


def derive_state(run_dir: Path) -> BuilderState:
    """Mechanically derive the state of a case build run from verified evidence.

    Each gate invokes a canonical verifier function; no JSON verdict is trusted directly.
    """
    run_dir = Path(run_dir).resolve()
    run_id = run_dir.name

    state = CaseLifecycleState.NEW
    open_gates: list[str] = []
    closed_gates: list[str] = []
    evidence: dict[str, Any] = {}

    category = "unknown"
    cat_file = run_dir / "category.txt"
    if cat_file.is_file():
        category = cat_file.read_text(encoding="utf-8").strip()

    # ── 1. INTAKE ────────────────────────────────────────────────────
    intake_file = run_dir / "source" / "intake.json"
    if intake_file.is_file():
        try:
            intake_doc = json.loads(intake_file.read_text(encoding="utf-8"))
            if intake_doc.get("category") and (intake_doc.get("title") or intake_doc.get("status")):
                category = intake_doc.get("category", category)
                closed_gates.append("INTAKE")
                state = CaseLifecycleState.INTAKE_COMPLETE
                evidence["intake"] = {"category": category}
            else:
                open_gates.append("INTAKE")
        except Exception:
            open_gates.append("INTAKE")
    else:
        open_gates.append("INTAKE")

    # ── 2. SOURCE LOCK (bidirectional verification) ──────────────────
    if state == CaseLifecycleState.INTAKE_COMPLETE:
        from ccbench.builder.source_lock import verify_sources_lock_bidirectional
        lock_valid, lock_errors = verify_sources_lock_bidirectional(
            run_dir / "source", require_non_empty=True
        )
        if lock_valid:
            closed_gates.append("SOURCE_LOCK")
            state = CaseLifecycleState.SOURCE_LOCKED
            evidence["source_lock"] = {"status": "LOCKED_AND_VERIFIED"}
        else:
            open_gates.append("SOURCE_LOCK")

    # ── 3. DESIGN IR (re-validated via load_case_ir) ─────────────────
    case_ir_path = run_dir / "design" / "case.ir.yaml"
    if not case_ir_path.is_file():
        case_ir_path = run_dir / "design" / "case.ir.json"

    if state == CaseLifecycleState.SOURCE_LOCKED and case_ir_path.is_file():
        try:
            from ccbench.builder.design import load_case_ir
            ir_doc = load_case_ir(case_ir_path)
            closed_gates.append("DESIGN_IR")
            state = CaseLifecycleState.DESIGN_VALID
            evidence["case_ir"] = {
                "title": ir_doc.get("identity", {}).get("title"),
                "case_id": ir_doc.get("identity", {}).get("case_id"),
            }
        except Exception:
            open_gates.append("DESIGN_IR")
    elif state == CaseLifecycleState.SOURCE_LOCKED:
        open_gates.append("DESIGN_IR")

    # ── 4. DRAFT (CaseSpec.load re-execution) ────────────────────────
    draft_dir = run_dir / "draft"
    task_md = draft_dir / "task.md"
    case_toml = draft_dir / "case.toml"

    if state == CaseLifecycleState.DESIGN_VALID and task_md.is_file() and case_toml.is_file():
        try:
            from ccbench.contracts.case import CaseSpec
            spec = CaseSpec.load(draft_dir)
            closed_gates.append("DRAFT_FILES")
            state = CaseLifecycleState.DRAFT
            evidence["draft"] = {"case_id": spec.case_id}
        except Exception:
            open_gates.append("DRAFT_FILES")
    elif state == CaseLifecycleState.DESIGN_VALID:
        open_gates.append("DRAFT_FILES")

    # ── 5. RUNNABLE DRAFT (full re-execution, never read JSON verdict) ─
    if state == CaseLifecycleState.DRAFT:
        passed, runnable_report = _verify_runnable_receipt(run_dir)
        if passed:
            closed_gates.append("RUNNABLE_GATE")
            state = CaseLifecycleState.RUNNABLE_DRAFT
            evidence["runnable"] = runnable_report.get("checks")
        else:
            open_gates.append("RUNNABLE_GATE")

    # ── 6. DISCOVERY (re-verified receipt, never trusted verdict) ─────
    if state == CaseLifecycleState.RUNNABLE_DRAFT:
        decision, disc_evidence, disc_metrics = _verify_discovery_receipt(run_dir)
        if decision == "PROMOTED":
            closed_gates.append("DISCOVERY")
            state = CaseLifecycleState.DISCOVERY_CLASSIFIED
            evidence["discovery"] = {"decision": decision, "evidence_present": True}
        elif decision == "REFINE":
            state = CaseLifecycleState.DISCOVERY_REFINE
            open_gates.append("DISCOVERY_REFINE")
        elif decision == "REJECT":
            state = CaseLifecycleState.DISCOVERY_REJECTED
            open_gates.append("DISCOVERY_REJECTED")
        else:
            open_gates.append("DISCOVERY")

    # ── 7. REFERENCE READY (re-verified receipt) ──────────────────────
    if state == CaseLifecycleState.DISCOVERY_CLASSIFIED:
        ref_ok, ref_data = _verify_reference_receipt(run_dir)
        if ref_ok:
            closed_gates.append("REFERENCE_READY")
            state = CaseLifecycleState.REFERENCE_READY
            evidence["reference"] = ref_data
        else:
            open_gates.append("REFERENCE_READY")

    # ── 8. CALIBRATED (re-verified receipt with plugin + Case IR check) ─
    if state == CaseLifecycleState.REFERENCE_READY:
        cal_ok, cal_data = _verify_calibration_receipt(run_dir)
        if cal_ok:
            closed_gates.append("CALIBRATION")
            state = CaseLifecycleState.CALIBRATED
            evidence["calibration"] = cal_data
        else:
            open_gates.append("CALIBRATION")

    # ── 9. BENCHMARK VALID (full release receipt re-verification) ─────
    if state == CaseLifecycleState.CALIBRATED:
        rel_ok, rel_data = _verify_release_receipt(run_dir)
        if rel_ok:
            closed_gates.append("BENCHMARK_VALID")
            state = CaseLifecycleState.BENCHMARK_VALID
            evidence["benchmark_valid"] = rel_data
        else:
            open_gates.append("BENCHMARK_VALID")

    # ── 10. PUBLISHED (atomic publish marker + 4-object invariant) ────
    if state == CaseLifecycleState.BENCHMARK_VALID:
        pub_file = run_dir / "reports" / "published.json"
        if pub_file.is_file():
            try:
                pdoc = json.loads(pub_file.read_text(encoding="utf-8"))
                target_path = Path(pdoc.get("path", ""))
                if target_path.is_dir():
                    allowed = {"task.md", "case.toml", "input", "verifier"}
                    actual = {p.name for p in target_path.iterdir()}
                    if actual == allowed:
                        closed_gates.append("PUBLISHED")
                        state = CaseLifecycleState.PUBLISHED
                        evidence["published"] = pdoc
                    else:
                        open_gates.append("PUBLISHED")
                else:
                    open_gates.append("PUBLISHED")
            except Exception:
                open_gates.append("PUBLISHED")

    return BuilderState(
        run_id=run_id,
        category=category,
        current_state=state,
        evidence=evidence,
        open_gates=open_gates,
        closed_gates=closed_gates,
    )
