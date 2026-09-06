"""Deterministic lifecycle state machine for the CCBench Benchmark Case Factory."""

from __future__ import annotations

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
    """Immutable state snapshot for a case builder run.

    State is purely derived from on-disk evidence via derive_state().
    LLMs are strictly prohibited from manually writing or declaring the state.
    """

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
    """Mechanically derive the state of a case build run from filesystem artifacts.

    Zero self-reporting: only files present on disk and passing checks advance state.
    """
    run_dir = Path(run_dir)
    run_id = run_dir.name

    state = CaseLifecycleState.NEW
    open_gates: list[str] = []
    closed_gates: list[str] = []
    evidence: dict[str, Any] = {}

    category = "unknown"
    cat_file = run_dir / "category.txt"
    if cat_file.is_file():
        category = cat_file.read_text(encoding="utf-8").strip()

    # 1. Check Intake
    intake_file = run_dir / "source" / "intake.json"
    if intake_file.is_file():
        try:
            intake_doc = json.loads(intake_file.read_text(encoding="utf-8"))
            category = intake_doc.get("category", category)
            closed_gates.append("INTAKE")
            state = CaseLifecycleState.INTAKE_COMPLETE
        except Exception:
            open_gates.append("INTAKE")
    else:
        open_gates.append("INTAKE")

    # 2. Check Source Lock
    source_lock = run_dir / "source" / "sources.lock.json"
    if state == CaseLifecycleState.INTAKE_COMPLETE:
        if source_lock.is_file():
            closed_gates.append("SOURCE_LOCK")
            state = CaseLifecycleState.SOURCE_LOCKED
        else:
            open_gates.append("SOURCE_LOCK")

    # 3. Check Case IR Design
    case_ir = run_dir / "design" / "case.ir.yaml"
    if not case_ir.is_file():
        case_ir = run_dir / "design" / "case.ir.json"

    if state == CaseLifecycleState.SOURCE_LOCKED:
        if case_ir.is_file():
            closed_gates.append("DESIGN_IR")
            state = CaseLifecycleState.DESIGN_VALID
        else:
            open_gates.append("DESIGN_IR")

    # 4. Check Draft Files
    draft_dir = run_dir / "draft"
    task_md = draft_dir / "task.md"
    case_toml = draft_dir / "case.toml"
    if state == CaseLifecycleState.DESIGN_VALID:
        if task_md.is_file() and case_toml.is_file():
            closed_gates.append("DRAFT_FILES")
            state = CaseLifecycleState.DRAFT
        else:
            open_gates.append("DRAFT_FILES")

    # 5. Check Runnable Draft (mount smoke + verifier smoke)
    smoke_report = run_dir / "verifier-smoke" / "smoke-report.json"
    if state == CaseLifecycleState.DRAFT:
        if smoke_report.is_file():
            try:
                rep = json.loads(smoke_report.read_text(encoding="utf-8"))
                if rep.get("passed", False):
                    closed_gates.append("RUNNABLE_GATE")
                    state = CaseLifecycleState.RUNNABLE_DRAFT
                else:
                    open_gates.append("RUNNABLE_GATE")
            except Exception:
                open_gates.append("RUNNABLE_GATE")
        else:
            open_gates.append("RUNNABLE_GATE")

    # 6. Check Discovery Classification
    disc_report = run_dir / "discovery" / "classification.json"
    if state == CaseLifecycleState.RUNNABLE_DRAFT:
        if disc_report.is_file():
            try:
                drep = json.loads(disc_report.read_text(encoding="utf-8"))
                dec = drep.get("decision")
                if dec == "PROMOTED":
                    closed_gates.append("DISCOVERY")
                    state = CaseLifecycleState.DISCOVERY_CLASSIFIED
                elif dec == "REFINE":
                    state = CaseLifecycleState.DISCOVERY_REFINE
                    open_gates.append("DISCOVERY_REFINE")
                elif dec == "REJECT":
                    state = CaseLifecycleState.DISCOVERY_REJECTED
                    open_gates.append("DISCOVERY_REJECTED")
            except Exception:
                open_gates.append("DISCOVERY")
        else:
            open_gates.append("DISCOVERY")

    # 7. Check Reference Ready
    ref_file = run_dir / "reports" / "reference-ready.json"
    if state == CaseLifecycleState.DISCOVERY_CLASSIFIED:
        if ref_file.is_file():
            closed_gates.append("REFERENCE_READY")
            state = CaseLifecycleState.REFERENCE_READY
        else:
            open_gates.append("REFERENCE_READY")

    # 8. Check Calibration
    cal_file = run_dir / "reports" / "calibration-report.json"
    if state == CaseLifecycleState.REFERENCE_READY:
        if cal_file.is_file():
            closed_gates.append("CALIBRATION")
            state = CaseLifecycleState.CALIBRATED
        else:
            open_gates.append("CALIBRATION")

    # 9. Check Benchmark Valid (Release Candidate checks)
    rel_file = run_dir / "reports" / "benchmark-valid.json"
    if state == CaseLifecycleState.CALIBRATED:
        if rel_file.is_file():
            try:
                rel = json.loads(rel_file.read_text(encoding="utf-8"))
                if rel.get("valid", False):
                    closed_gates.append("BENCHMARK_VALID")
                    state = CaseLifecycleState.BENCHMARK_VALID
                else:
                    open_gates.append("BENCHMARK_VALID")
            except Exception:
                open_gates.append("BENCHMARK_VALID")
        else:
            open_gates.append("BENCHMARK_VALID")

    # 10. Check Published (Atomic Publish marker)
    pub_file = run_dir / "reports" / "published.json"
    if state == CaseLifecycleState.BENCHMARK_VALID:
        if pub_file.is_file():
            closed_gates.append("PUBLISHED")
            state = CaseLifecycleState.PUBLISHED

    builder_state = BuilderState(
        run_id=run_id,
        category=category,
        current_state=state,
        evidence=evidence,
        open_gates=open_gates,
        closed_gates=closed_gates,
    )
    return builder_state
