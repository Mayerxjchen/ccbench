"""Tests for Atomic Case Publisher transaction, safe replace, and identity binding."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from ccbench.builder.publish import PublishError, publish_case
from ccbench.builder.source_lock import build_sources_lock, SourceTier
from ccbench.builder.state import CaseLifecycleState
from ccbench.builder.verifier_plan import VerifierPlan, VerifierRule
from ccbench.builder.verifier_compile import compile_verifier


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
            "artifacts": [
                {"path": "model.pt", "kind": "model"},
                {"path": "metrics.json", "kind": "metrics"},
            ],
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


def _write_valid_case_toml(path: Path, case_id: str = "006-toy"):
    content = f"""schema_version = "1.2"
case_version = "1.0.0"

[execution]
class = "local_sandbox"

[task]
name = "{case_id}"

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


def _build_real_verifier(run_dir: Path):
    """Build a real verifier for the run directory."""
    ir_file = run_dir / "design" / "case.ir.yaml"
    ir_doc = yaml.safe_load(ir_file.read_text(encoding="utf-8"))
    plan = VerifierPlan.from_case_ir(ir_doc)
    verifier_dir = run_dir / "verifier"
    compile_verifier(plan, verifier_dir)
    # Also mirror to draft/verifier
    import shutil
    draft_verifier = run_dir / "draft" / "verifier"
    if draft_verifier.exists():
        shutil.rmtree(draft_verifier)
    shutil.copytree(verifier_dir, draft_verifier)


def test_publish_enforces_benchmark_valid_and_four_objects(tmp_path: Path):
    run_dir = tmp_path / "runs" / "006-toy"
    cases_dir = tmp_path / "cases"
    maintainer_dir = tmp_path / "maintainer"

    # Setup draft via scaffold compiler
    from ccbench.builder.scaffold import compile_case_ir_to_draft
    from ccbench.builder.design import load_case_ir

    draft_dir = run_dir / "draft"
    source_dir = run_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp", "title": "test"}), encoding="utf-8")
    (source_dir / "input.txt").write_text("data", encoding="utf-8")
    build_sources_lock(source_dir, {"input.txt": SourceTier.PUBLIC_SOURCE})

    (run_dir / "design").mkdir()
    _write_valid_case_ir(run_dir / "design" / "case.ir.yaml", case_id="006-toy")

    # Use scaffold to create draft with submission-contract.json
    ir_doc = load_case_ir(run_dir / "design" / "case.ir.yaml")
    compile_case_ir_to_draft(ir_doc, draft_dir, source_dir=source_dir)

    # Build real verifier
    _build_real_verifier(run_dir)

    # Discovery with proper evidence
    ir_sha = hashlib.sha256((run_dir / "design" / "case.ir.yaml").read_bytes()).hexdigest()
    disc_doc = {
        "run_id": "disc-001",
        "candidate_bundle_digest": "sha256:" + "a" * 64,
        "case_ir_digest": f"sha256:{ir_sha}",
        "outcome": {"terminal_state": "COMPLETED", "candidate_exit_code": 0, "verifier_exit_code": 0},
        "metrics": {"energy_rmse": 0.01},
        "failure_class": "SUCCESS",
    }
    (run_dir / "discovery").mkdir()
    (run_dir / "discovery" / "classification.json").write_text(
        json.dumps({"decision": "PROMOTED", "evidence": disc_doc}),
        encoding="utf-8",
    )

    # Reports
    (run_dir / "reports").mkdir()
    (run_dir / "reports" / "reference-ready.json").write_text(
        json.dumps({"reproducible": True}), encoding="utf-8",
    )
    (run_dir / "reports" / "calibration-report.json").write_text(
        json.dumps({"passed": True, "thresholds": {"energy_rmse_max": 0.05}}),
        encoding="utf-8",
    )
    (run_dir / "reports" / "threshold-freeze.json").write_text(
        json.dumps({
            "case_ir_digest": f"sha256:{ir_sha}",
            "formal_agent_results_seen": False,
            "thresholds_digest": "sha256:" + "b" * 64,
        }),
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
    """Publishing to a case_id that differs from CaseSpec must fail."""
    run_dir = tmp_path / "runs" / "006-toy"
    run_dir.mkdir(parents=True)
    draft_dir = run_dir / "draft"
    draft_dir.mkdir()
    (draft_dir / "task.md").write_text("# Task\n", encoding="utf-8")
    _write_valid_case_toml(draft_dir / "case.toml", case_id="006-toy")

    (run_dir / "design").mkdir()
    _write_valid_case_ir(run_dir / "design" / "case.ir.yaml", case_id="006-actual-id")

    with pytest.raises(PublishError, match="Identity binding mismatch"):
        publish_case(run_dir, "006-different-target", cases_dir=tmp_path / "cases", maintainer_dir=tmp_path / "maintainer")


def test_publish_refuses_unvalidated_case(tmp_path: Path):
    """Publishing a case that is not BENCHMARK_VALID must fail closed."""
    run_dir = tmp_path / "runs" / "unvalidated"
    run_dir.mkdir(parents=True)
    with pytest.raises(PublishError):
        publish_case(run_dir, "999-bad", cases_dir=tmp_path / "cases", maintainer_dir=tmp_path / "maintainer")


import hashlib
