"""Tests for Case IR schema validation, category plugin enforcement, and scaffold compilation."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from bench.builder.design import CaseIRValidationError, load_case_ir, validate_case_ir
from bench.builder.scaffold import compile_case_ir_to_draft
from bench.contracts.case import CaseSpec


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
            "candidate_image": "bench-agent:v1",
            "verifier_image": "bench-agent:v1",
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
                    "threshold_ref": "energy_rmse_max",
                    "params": {"metric": "energy_rmse"},
                }
            ],
            "thresholds": {
                "energy_rmse_max": 0.05,
            },
        },
    }


def test_valid_case_ir_passes_schema(valid_case_ir_doc: dict):
    validate_case_ir(valid_case_ir_doc)


def test_unsupported_category_rejected(valid_case_ir_doc: dict):
    doc = dict(valid_case_ir_doc)
    doc["identity"] = {**doc["identity"], "category": "quantum_gravity"}
    with pytest.raises(CaseIRValidationError, match="Unsupported case category"):
        validate_case_ir(doc)


def test_mlp_missing_thresholds_rejected(valid_case_ir_doc: dict):
    doc = dict(valid_case_ir_doc)
    doc["verification"] = {**doc["verification"], "thresholds": {}}
    with pytest.raises(CaseIRValidationError, match="must declare numeric verification thresholds"):
        validate_case_ir(doc)


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
    assert (draft_dir / "environment" / "Dockerfile").is_file()
    assert (draft_dir / "solution" / "solve.sh").is_file()
    assert (draft_dir / "tests" / "test.sh").is_file()
    assert "private" in (draft_dir / "solution" / "README.md").read_text().lower()
    assert "private" in (draft_dir / "tests" / "README.md").read_text().lower()

    # Verify compiled case.toml loads cleanly via CaseSpec
    spec = CaseSpec.load(draft_dir)
    assert spec.case_id == "006-au-cu-potential"
    assert spec.execution_class == "local_sandbox"
    assert spec.submission_root == "final"
    assert spec.coverage.scientific_domain == "materials"

    # The new private authoring boundaries must not become Candidate inputs;
    # the IR only declares the explicit input list as the public allowlist.
    from bench.core.packager import package_candidate
    bundle = tmp_path / "candidate-bundle"
    package_candidate(spec, bundle)
    bundle_paths = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    assert not any(path.startswith(("environment/", "solution/", "tests/")) for path in bundle_paths)
