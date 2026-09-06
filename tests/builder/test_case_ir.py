"""Tests for Case IR schema validation and scaffold compilation."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from ccbench.builder.design import CaseIRValidationError, load_case_ir, validate_case_ir
from ccbench.builder.scaffold import compile_case_ir_to_draft
from ccbench.contracts.case import CaseSpec


@pytest.fixture
def valid_case_ir_doc() -> dict:
    return {
        "schema_version": 1,
        "identity": {
            "title": "Au-Cu Alloy Potential",
            "category": "mlp",
            "case_id": "006-au-cu-potential",
            "version": "1.0",
        },
        "scientific_target": {
            "system": "AuCu3 Alloy",
            "objective": "Energy and force prediction",
            "observable": "formation_energy",
        },
        "selection": {
            "paradigm": "standard",
        },
        "candidate": {
            "instruction": "Train a neural network interatomic potential on supplied structures.",
            "inputs": [
                {"path": "train.xyz", "description": "Training structures"}
            ],
            "allowed_tools": ["bash"],
        },
        "submission": {
            "root": "final",
            "artifacts": [
                {"path": "model.pt", "kind": "checkpoint", "required": True},
                {"path": "metrics.json", "kind": "metrics", "required": True},
            ],
        },
        "runtime": {
            "execution_class": "local_sandbox",
            "candidate_image": "ccbench-agent:v1",
            "verifier_image": "ccbench-agent:v1",
            "timeout_sec": 1200.0,
            "gpus": 0,
        },
        "coverage": {
            "scientific_domain": "materials",
            "method_family": "end_to_end_potential",
            "material_class": "metallic_alloy",
            "computation_type": "iterative_training",
            "paradigm": "standard",
        },
        "verification": {
            "layers": ["V0", "V1", "V2", "V4"],
            "primitives": [
                {
                    "primitive": "mlp.energy_rmse",
                    "target": "metrics.json",
                    "params": {"metric": "energy_rmse", "threshold": 0.05},
                }
            ],
            "thresholds": {
                "energy_rmse_max": 0.05,
            },
        },
    }


def test_valid_case_ir_passes_schema(valid_case_ir_doc: dict):
    validate_case_ir(valid_case_ir_doc)


def test_research_question_requires_hypothesis(valid_case_ir_doc: dict):
    doc = dict(valid_case_ir_doc)
    doc["selection"] = {"paradigm": "research_question"}
    with pytest.raises(CaseIRValidationError, match="selection.hypothesis"):
        validate_case_ir(doc)


def test_compile_case_ir_to_draft(valid_case_ir_doc: dict, tmp_path: Path):
    draft_dir = tmp_path / "006-au-cu-potential"
    artifacts = compile_case_ir_to_draft(valid_case_ir_doc, draft_dir)

    assert "task_md" in artifacts
    assert "case_toml" in artifacts
    assert "submission_contract" in artifacts

    assert (draft_dir / "task.md").is_file()
    assert (draft_dir / "case.toml").is_file()
    assert (draft_dir / "submission-contract.json").is_file()

    # Verify compiled case.toml loads cleanly via CaseSpec
    spec = CaseSpec.load(draft_dir)
    assert spec.case_id == "006-au-cu-potential"
    assert spec.execution_class == "local_sandbox"
    assert spec.submission_root == "final"
    assert spec.coverage.scientific_domain == "materials"
