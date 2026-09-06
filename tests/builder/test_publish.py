"""Tests for Atomic Case Publisher transaction, safe replace, and identity binding."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from ccbench.builder.publish import PublishError, publish_case
from ccbench.builder.source_lock import build_sources_lock, SourceTier
from ccbench.builder.state import CaseLifecycleState


def _write_valid_case_ir(path: Path, case_id: str = "006-toy"):
    doc = {
        "schema_version": 1,
        "identity": {"title": "Toy Case", "category": "mlp", "version": "1.0", "case_id": case_id},
        "scientific_target": {"system": "Si", "objective": "Energy prediction"},
        "selection": {"paradigm": "standard"},
        "candidate": {
            "instruction": "Run training",
            "inputs": [{"path": "input/input.txt"}],
        },
        "submission": {
            "root": "final",
            "artifacts": [{"path": "model.pt", "kind": "model"}],
        },
        "runtime": {
            "execution_class": "local_sandbox",
            "candidate_image": "ccbench-agent:v1",
        },
        "coverage": {
            "scientific_domain": "semiconductors",
            "method_family": "end_to_end_potential",
            "material_class": "inorganic_2d",
            "computation_type": "iterative_training",
        },
        "verification": {
            "layers": ["V0", "V1"],
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
image = "ccbench-agent:v1"

[coverage]
scientific_domain = "semiconductors"
method_family = "end_to_end_potential"
material_class = "inorganic_2d"
computation_type = "iterative_training"
"""
    path.write_text(content, encoding="utf-8")


def test_publish_enforces_benchmark_valid_and_four_objects(tmp_path: Path):
    run_dir = tmp_path / "runs" / "006-toy"
    cases_dir = tmp_path / "cases"
    maintainer_dir = tmp_path / "maintainer"

    # Setup draft
    draft_dir = run_dir / "draft"
    draft_dir.mkdir(parents=True)
    (draft_dir / "task.md").write_text("# Task\n", encoding="utf-8")
    _write_valid_case_toml(draft_dir / "case.toml")

    input_dir = draft_dir / "input"
    input_dir.mkdir()
    (input_dir / "input.txt").write_text("data", encoding="utf-8")

    verifier_dir = run_dir / "verifier"
    verifier_dir.mkdir(parents=True)
    (verifier_dir / "test.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    # Source & reports
    source_dir = run_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp", "title": "test"}), encoding="utf-8")
    (source_dir / "seed.txt").write_text("seed", encoding="utf-8")
    build_sources_lock(source_dir, {"seed.txt": SourceTier.PUBLIC_SOURCE})

    (run_dir / "design").mkdir()
    _write_valid_case_ir(run_dir / "design" / "case.ir.yaml", case_id="006-toy")

    (run_dir / "verifier-smoke").mkdir()
    (run_dir / "verifier-smoke" / "smoke-report.json").write_text(
        json.dumps({"passed": True, "checks": {"case_spec_load": True}}),
        encoding="utf-8",
    )
    (run_dir / "discovery").mkdir()
    (run_dir / "discovery" / "classification.json").write_text(
        json.dumps({
            "decision": "PROMOTED",
            "evidence": {
                "run_id": "disc-001",
                "candidate_bundle_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
                "case_ir_digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
                "outcome": {"terminal_state": "COMPLETED", "candidate_exit_code": 0, "verifier_exit_code": 0},
                "metrics": {"energy_rmse": 0.01},
            },
        }),
        encoding="utf-8",
    )
    (run_dir / "reports").mkdir()
    (run_dir / "reports" / "reference-ready.json").write_text("{}", encoding="utf-8")
    (run_dir / "reports" / "calibration-report.json").write_text(
        json.dumps({"passed": True, "thresholds": {"rmse": 0.05}}),
        encoding="utf-8",
    )
    (run_dir / "reports" / "benchmark-valid.json").write_text(
        json.dumps({"valid": True, "evidence_graph": {"checks": True}}),
        encoding="utf-8",
    )

    # Publish
    published = publish_case(
        run_dir,
        "006-toy",
        cases_dir=cases_dir,
        maintainer_dir=maintainer_dir,
    )

    assert published.is_dir()
    # Check that EXACTLY the 4 canonical objects are present
    names = {p.name for p in published.iterdir()}
    assert names == {"task.md", "case.toml", "input", "verifier"}

    # Check maintainer assets were saved separately
    maint_case = maintainer_dir / "cases" / "006-toy"
    assert maint_case.is_dir()
    assert (maint_case / "source" / "intake.json").is_file()


def test_publish_identity_mismatch_rejected(tmp_path: Path):
    """Publishing to a case_id that differs from Case IR must fail."""
    run_dir = tmp_path / "runs" / "006-toy"
    run_dir.mkdir(parents=True)
    draft_dir = run_dir / "draft"
    draft_dir.mkdir()
    (draft_dir / "task.md").write_text("# Task\n", encoding="utf-8")
    _write_valid_case_toml(draft_dir / "case.toml")

    (run_dir / "design").mkdir()
    _write_valid_case_ir(run_dir / "design" / "case.ir.yaml", case_id="006-actual-id")

    source_dir = run_dir / "source"
    source_dir.mkdir()
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp", "title": "test"}), encoding="utf-8")
    (source_dir / "s.txt").write_text("s")
    build_sources_lock(source_dir, {"s.txt": SourceTier.PUBLIC_SOURCE})

    (run_dir / "reports").mkdir()
    (run_dir / "reports" / "benchmark-valid.json").write_text(
        json.dumps({"valid": True, "evidence_graph": {}}),
        encoding="utf-8",
    )

    with pytest.raises(PublishError, match="Identity binding mismatch"):
        publish_case(run_dir, "006-different-target", cases_dir=tmp_path / "cases", maintainer_dir=tmp_path / "maintainer")


def test_publish_refuses_unvalidated_case(tmp_path: Path):
    """Publishing a case that is not BENCHMARK_VALID must fail closed."""
    run_dir = tmp_path / "runs" / "unvalidated"
    run_dir.mkdir(parents=True)
    with pytest.raises(PublishError, match="Run must reach 'CaseLifecycleState.BENCHMARK_VALID'"):
        publish_case(run_dir, "999-bad", cases_dir=tmp_path / "cases", maintainer_dir=tmp_path / "maintainer")
