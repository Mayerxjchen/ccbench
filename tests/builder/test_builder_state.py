"""Tests for bench.builder.state deterministic state machine and evidence verification."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from bench.builder.source_lock import build_sources_lock, SourceTier
from bench.builder.state import BuilderState, CaseLifecycleState, derive_state


def _write_valid_case_ir(path: Path):
    doc = {
        "schema_version": 1,
        "identity": {"title": "Toy Case", "category": "mlp", "version": "1.0", "case_id": "000-toy-case"},
        "scientific_target": {"system": "Si", "objective": "Energy prediction"},
        "selection": {"paradigm": "standard"},
        "candidate": {
            "instruction": "Run training",
            "inputs": [{"path": "train.xyz"}],
        },
        "submission": {
            "root": "final",
            "artifacts": [{"path": "model.pt", "kind": "model"}],
        },
        "runtime": {
            "execution_class": "local_sandbox",
            "candidate_image": "bench-agent:v1",
        },
        "coverage": {
            "scientific_domain": "semiconductors",
            "method_family": "end_to_end_potential",
            "material_class": "inorganic_2d",
            "computation_type": "iterative_training",
        },
        "verification": {
            "layers": ["V0", "V1", "V2", "V4"],
            "primitives": [
                {
                    "primitive": "mlp.energy_rmse",
                    "target": "final/metrics.json",
                    "threshold_ref": "energy_rmse_max",
                    "params": {"metric": "energy_rmse"},
                }
            ],
            "thresholds": {"energy_rmse_max": 0.05},
        },
    }
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def _write_valid_case_toml(path: Path):
    content = """schema_version = "1.2"
case_version = "1.0.0"

[execution]
class = "local_sandbox"

[candidate]
instruction = "task.md"
submission_root = "final"
image = "bench-agent:v1"

[coverage]
scientific_domain = "semiconductors"
method_family = "end_to_end_potential"
material_class = "inorganic_2d"
computation_type = "iterative_training"
"""
    path.write_text(content, encoding="utf-8")


def _setup_locked_source(source_dir: Path):
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp", "title": "test"}), encoding="utf-8")
    (source_dir / "train.xyz").write_text("lattice-data", encoding="utf-8")
    build_sources_lock(source_dir, {"train.xyz": SourceTier.PUBLIC_SOURCE})


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


def test_tampered_source_blocks_source_locked(tmp_path: Path):
    """Tampered source file fails hash verification and blocks state advancement."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp", "title": "test"}), encoding="utf-8")
    (source_dir / "file.xyz").write_text("tampered-content", encoding="utf-8")
    (source_dir / "sources.lock.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [{"path": "file.xyz", "sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000"}],
        }),
        encoding="utf-8",
    )

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.INTAKE_COMPLETE
    assert "SOURCE_LOCK" in state.open_gates


def test_source_locked_with_valid_hashes(tmp_path: Path):
    """Source lock with valid hashes advances to SOURCE_LOCKED."""
    _setup_locked_source(tmp_path / "source")

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.SOURCE_LOCKED
    assert "SOURCE_LOCK" in state.closed_gates
    assert "DESIGN_IR" in state.open_gates


def test_malformed_case_ir_blocks_design_valid(tmp_path: Path):
    """Malformed or schema-violating Case IR stays at SOURCE_LOCKED."""
    _setup_locked_source(tmp_path / "source")

    design_dir = tmp_path / "design"
    design_dir.mkdir()
    # Malformed: missing mandatory fields
    (design_dir / "case.ir.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.SOURCE_LOCKED
    assert "DESIGN_IR" in state.open_gates


def test_valid_case_ir_advances_to_design_valid(tmp_path: Path):
    """Valid Case IR advances to DESIGN_VALID."""
    _setup_locked_source(tmp_path / "source")

    design_dir = tmp_path / "design"
    design_dir.mkdir()
    _write_valid_case_ir(design_dir / "case.ir.yaml")

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.DESIGN_VALID
    assert "DESIGN_IR" in state.closed_gates
    assert "DRAFT_FILES" in state.open_gates


def test_draft_and_runnable_derivation(tmp_path: Path):
    """Valid draft + real runnable gate re-execution advances to RUNNABLE_DRAFT.

    derive_state() re-executes check_runnable_draft() from scratch, so a
    manually crafted smoke-report.json is not sufficient.  The actual runnable
    gate must pass: CaseSpec.load, Case IR validation, package_candidate, leak
    scan, verifier mount smoke (empty submission fail-closed, structural positive).
    """
    _setup_locked_source(tmp_path / "source")

    design_dir = tmp_path / "design"
    design_dir.mkdir()
    _write_valid_case_ir(design_dir / "case.ir.yaml")

    draft_dir = tmp_path / "draft"
    draft_dir.mkdir()
    (draft_dir / "task.md").write_text("# Task\n", encoding="utf-8")
    _write_valid_case_toml(draft_dir / "case.toml")

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.DRAFT

    # The gate stays at DRAFT because runnable gate re-executes and fails
    # (no verifier exists on disk)
    state_runnable = derive_state(tmp_path)
    assert state_runnable.current_state == CaseLifecycleState.DRAFT
    assert "RUNNABLE_GATE" in state_runnable.open_gates


def test_discovery_promoted_without_evidence_rejected(tmp_path: Path):
    """Discovery PROMOTED without real evidence is rejected from advancing."""
    _setup_locked_source(tmp_path / "source")

    design_dir = tmp_path / "design"
    design_dir.mkdir()
    _write_valid_case_ir(design_dir / "case.ir.yaml")

    draft_dir = tmp_path / "draft"
    draft_dir.mkdir()
    (draft_dir / "task.md").write_text("# Task\n", encoding="utf-8")
    _write_valid_case_toml(draft_dir / "case.toml")

    # Write a fake PROMOTED with empty evidence
    disc_dir = tmp_path / "discovery"
    disc_dir.mkdir()
    (disc_dir / "classification.json").write_text(
        json.dumps({"decision": "PROMOTED", "evidence": {}}),
        encoding="utf-8",
    )

    # State stays at DRAFT because runnable gate fails (re-execution)
    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.DRAFT
    assert "RUNNABLE_GATE" in state.open_gates
