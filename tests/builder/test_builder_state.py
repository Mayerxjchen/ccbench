"""Tests for ccbench.builder.state deterministic state machine."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from ccbench.builder.state import BuilderState, CaseLifecycleState, derive_state


def test_empty_workspace_is_new(tmp_path: Path):
    """Empty run workspace must derive NEW state."""
    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.NEW
    assert "INTAKE" in state.open_gates


def test_intake_complete_derivation(tmp_path: Path):
    """Intake file present advances state to INTAKE_COMPLETE."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp", "title": "test"}), encoding="utf-8")

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.INTAKE_COMPLETE
    assert "INTAKE" in state.closed_gates
    assert "SOURCE_LOCK" in state.open_gates


def test_source_locked_derivation(tmp_path: Path):
    """Source lock advances state to SOURCE_LOCKED."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp"}), encoding="utf-8")
    (source_dir / "sources.lock.json").write_text(json.dumps({"schema_version": 1, "sources": []}), encoding="utf-8")

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.SOURCE_LOCKED
    assert "SOURCE_LOCK" in state.closed_gates
    assert "DESIGN_IR" in state.open_gates


def test_design_valid_derivation(tmp_path: Path):
    """Presence of case.ir.yaml advances to DESIGN_VALID."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp"}), encoding="utf-8")
    (source_dir / "sources.lock.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    design_dir = tmp_path / "design"
    design_dir.mkdir()
    (design_dir / "case.ir.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.DESIGN_VALID
    assert "DESIGN_IR" in state.closed_gates
    assert "DRAFT_FILES" in state.open_gates


def test_draft_and_runnable_derivation(tmp_path: Path):
    """Draft files + passing smoke report advances to RUNNABLE_DRAFT."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp"}), encoding="utf-8")
    (source_dir / "sources.lock.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "case.ir.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    draft_dir = tmp_path / "draft"
    draft_dir.mkdir()
    (draft_dir / "task.md").write_text("# Task\n", encoding="utf-8")
    (draft_dir / "case.toml").write_text('case_id = "test"\n', encoding="utf-8")

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.DRAFT

    smoke_dir = tmp_path / "verifier-smoke"
    smoke_dir.mkdir()
    (smoke_dir / "smoke-report.json").write_text(json.dumps({"passed": True}), encoding="utf-8")

    state_runnable = derive_state(tmp_path)
    assert state_runnable.current_state == CaseLifecycleState.RUNNABLE_DRAFT
    assert "RUNNABLE_GATE" in state_runnable.closed_gates
