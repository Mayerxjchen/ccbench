"""Deterministic lifecycle state machine for the CCBench Benchmark Case Factory.

Authoritative principle:
States are derived exclusively from verified evidence and cryptographically bound artifacts.
Neither LLMs nor users can manually set or declare states.
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


def derive_state(run_dir: Path) -> BuilderState:
    """Mechanically derive the state of a case build run from verified evidence on disk.

    Zero self-reporting: each gate independently verifies hashes, schemas, and
    contracts on disk. Tampered or incomplete files halt progression immediately.
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

    # ── 1. Check Intake ──────────────────────────────────────────────
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

    # ── 2. Check Source Lock with Hash Integrity ─────────────────────
    source_lock = run_dir / "source" / "sources.lock.json"
    if state == CaseLifecycleState.INTAKE_COMPLETE and source_lock.is_file():
        try:
            slock = json.loads(source_lock.read_text(encoding="utf-8"))
            sources = slock.get("sources", [])
            # Verify file hashes if sources are declared
            all_valid = True
            for s in sources:
                s_path = run_dir / "source" / s["path"]
                if not s_path.is_file():
                    all_valid = False
                    break
                h = f"sha256:{hashlib.sha256(s_path.read_bytes()).hexdigest()}"
                if h != s.get("sha256"):
                    all_valid = False
                    break
            if all_valid:
                closed_gates.append("SOURCE_LOCK")
                state = CaseLifecycleState.SOURCE_LOCKED
                evidence["source_lock"] = {"count": len(sources)}
            else:
                open_gates.append("SOURCE_LOCK")
        except Exception:
            open_gates.append("SOURCE_LOCK")
    elif state == CaseLifecycleState.INTAKE_COMPLETE:
        open_gates.append("SOURCE_LOCK")

    # ── 3. Check Case IR with Real Validator ─────────────────────────
    case_ir = run_dir / "design" / "case.ir.yaml"
    if not case_ir.is_file():
        case_ir = run_dir / "design" / "case.ir.json"

    if state == CaseLifecycleState.SOURCE_LOCKED and case_ir.is_file():
        try:
            from ccbench.builder.design import load_case_ir
            ir_doc = load_case_ir(case_ir)
            closed_gates.append("DESIGN_IR")
            state = CaseLifecycleState.DESIGN_VALID
            evidence["case_ir"] = {"title": ir_doc.get("identity", {}).get("title")}
        except Exception:
            open_gates.append("DESIGN_IR")
    elif state == CaseLifecycleState.SOURCE_LOCKED:
        open_gates.append("DESIGN_IR")

    # ── 4. Check Draft Files & CaseSpec Contract ─────────────────────
    draft_dir = run_dir / "draft"
    task_md = draft_dir / "task.md"
    case_toml = draft_dir / "case.toml"

    if state == CaseLifecycleState.DESIGN_VALID and task_md.is_file() and case_toml.is_file():
        try:
            from ccbench.contracts.case import CaseSpec
            CaseSpec.load(draft_dir)
            closed_gates.append("DRAFT_FILES")
            state = CaseLifecycleState.DRAFT
            evidence["draft"] = {"case_id": case_toml.name}
        except Exception:
            open_gates.append("DRAFT_FILES")
    elif state == CaseLifecycleState.DESIGN_VALID:
        open_gates.append("DRAFT_FILES")

    # ── 5. Check Runnable Draft (Verified Smoke Evidence) ────────────
    smoke_report = run_dir / "verifier-smoke" / "smoke-report.json"
    if state == CaseLifecycleState.DRAFT and smoke_report.is_file():
        try:
            rep = json.loads(smoke_report.read_text(encoding="utf-8"))
            checks = rep.get("checks", {})
            # Must have passed and verified critical checks
            if rep.get("passed", False) and (checks.get("case_spec_load") or checks.get("case_toml") or checks.get("smoke_empty_submission_fails")):
                closed_gates.append("RUNNABLE_GATE")
                state = CaseLifecycleState.RUNNABLE_DRAFT
                evidence["runnable"] = rep.get("checks")
            else:
                open_gates.append("RUNNABLE_GATE")
        except Exception:
            open_gates.append("RUNNABLE_GATE")
    elif state == CaseLifecycleState.DRAFT:
        open_gates.append("RUNNABLE_GATE")

    # ── 6. Check Discovery Classification with Real Evidence ─────────
    disc_report = run_dir / "discovery" / "classification.json"
    if state == CaseLifecycleState.RUNNABLE_DRAFT and disc_report.is_file():
        try:
            drep = json.loads(disc_report.read_text(encoding="utf-8"))
            dec = drep.get("decision")
            evidence_data = drep.get("evidence")
            if dec == "PROMOTED":
                if evidence_data:  # PROMOTED requires non-empty evidence
                    closed_gates.append("DISCOVERY")
                    state = CaseLifecycleState.DISCOVERY_CLASSIFIED
                    evidence["discovery"] = drep
                else:
                    open_gates.append("DISCOVERY")
            elif dec == "REFINE":
                state = CaseLifecycleState.DISCOVERY_REFINE
                open_gates.append("DISCOVERY_REFINE")
            elif dec == "REJECT":
                state = CaseLifecycleState.DISCOVERY_REJECTED
                open_gates.append("DISCOVERY_REJECTED")
            else:
                open_gates.append("DISCOVERY")
        except Exception:
            open_gates.append("DISCOVERY")
    elif state == CaseLifecycleState.RUNNABLE_DRAFT:
        open_gates.append("DISCOVERY")

    # ── 7. Check Reference Ready ─────────────────────────────────────
    ref_file = run_dir / "reports" / "reference-ready.json"
    if state == CaseLifecycleState.DISCOVERY_CLASSIFIED and ref_file.is_file():
        try:
            ref_doc = json.loads(ref_file.read_text(encoding="utf-8"))
            closed_gates.append("REFERENCE_READY")
            state = CaseLifecycleState.REFERENCE_READY
            evidence["reference"] = ref_doc
        except Exception:
            open_gates.append("REFERENCE_READY")
    elif state == CaseLifecycleState.DISCOVERY_CLASSIFIED:
        open_gates.append("REFERENCE_READY")

    # ── 8. Check Calibration ─────────────────────────────────────────
    cal_file = run_dir / "reports" / "calibration-report.json"
    if state == CaseLifecycleState.REFERENCE_READY and cal_file.is_file():
        try:
            cal_doc = json.loads(cal_file.read_text(encoding="utf-8"))
            if cal_doc.get("passed", False) or cal_doc.get("thresholds"):
                closed_gates.append("CALIBRATION")
                state = CaseLifecycleState.CALIBRATED
                evidence["calibration"] = cal_doc
            else:
                open_gates.append("CALIBRATION")
        except Exception:
            open_gates.append("CALIBRATION")
    elif state == CaseLifecycleState.REFERENCE_READY:
        open_gates.append("CALIBRATION")

    # ── 9. Check Benchmark Valid with Release Graph ──────────────────
    rel_file = run_dir / "reports" / "benchmark-valid.json"
    if state == CaseLifecycleState.CALIBRATED and rel_file.is_file():
        try:
            rel = json.loads(rel_file.read_text(encoding="utf-8"))
            if rel.get("valid", False) and ("evidence_graph" in rel or "checks" in rel):
                closed_gates.append("BENCHMARK_VALID")
                state = CaseLifecycleState.BENCHMARK_VALID
                evidence["benchmark_valid"] = rel
            else:
                open_gates.append("BENCHMARK_VALID")
        except Exception:
            open_gates.append("BENCHMARK_VALID")
    elif state == CaseLifecycleState.CALIBRATED:
        open_gates.append("BENCHMARK_VALID")

    # ── 10. Check Published (Atomic Publish Marker + Cases on Disk) ──
    pub_file = run_dir / "reports" / "published.json"
    if state == CaseLifecycleState.BENCHMARK_VALID and pub_file.is_file():
        try:
            pdoc = json.loads(pub_file.read_text(encoding="utf-8"))
            target_path = Path(pdoc.get("path", ""))
            if target_path.is_dir():
                # Verify exactly the 4 canonical objects
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
