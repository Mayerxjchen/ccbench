"""Adversarial and failure containment tests for CCBench Case Builder."""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from ccbench.cli import main as cli_main
from ccbench.builder.state import CaseLifecycleState, derive_state
from ccbench.builder.publish import PublishError, publish_case


def test_gold_taint_blocks_runnable_gate(tmp_path: Path):
    """If a candidate input is classified as GOLD_SOURCE, runnable gate must fail closed."""
    run_dir = tmp_path / "runs" / "tainted-case"
    run_dir.mkdir(parents=True)

    # 1. Intake
    cli_main([
        "case", "intake",
        "--run-dir", str(run_dir),
        "--category", "mlp",
        "--title", "Tainted Case",
    ])

    # Add candidate input file in draft/input and source/
    (run_dir / "source" / "secret_ground_truth.xyz").write_text("secret-gold", encoding="utf-8")
    (run_dir / "source" / "sources.lock.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [{
                "path": "secret_ground_truth.xyz",
                "tier": "GOLD_SOURCE",
                "sha256": "sha256:abc",
            }],
        }),
        encoding="utf-8",
    )

    # Design with input referencing secret_ground_truth.xyz
    ir_doc = {
        "schema_version": 1,
        "identity": {"title": "Tainted", "category": "mlp", "version": "1.0"},
        "scientific_target": {"system": "Si", "objective": "Energy"},
        "selection": {"paradigm": "standard"},
        "candidate": {
            "instruction": "Solve",
            "inputs": [{"path": "secret_ground_truth.xyz"}],
        },
        "submission": {"root": "final", "artifacts": [{"path": "m.pt", "kind": "m"}]},
        "runtime": {"execution_class": "local_sandbox", "candidate_image": "ccbench-agent:v1"},
        "coverage": {
            "scientific_domain": "semiconductors",
            "method_family": "end_to_end_potential",
            "material_class": "inorganic_2d",
            "computation_type": "iterative_training",
        },
        "verification": {"layers": ["V0", "V1"], "thresholds": {"t": 0.05}},
    }
    ir_path = tmp_path / "case.ir.yaml"
    ir_path.write_text(yaml.safe_dump(ir_doc), encoding="utf-8")

    cli_main(["case", "design", "--ir", str(ir_path), "--run-dir", str(run_dir)])
    cli_main(["case", "build", "--run-dir", str(run_dir)])

    # Put the tainted file in draft/input
    (run_dir / "draft" / "input" / "secret_ground_truth.xyz").write_text("secret-gold", encoding="utf-8")

    # Validate MUST FAIL due to gold taint
    rc = cli_main(["case", "validate", "--run-dir", str(run_dir)])
    assert rc != 0

    state = derive_state(run_dir)
    assert state.current_state != CaseLifecycleState.RUNNABLE_DRAFT


def test_empty_submission_fails_verifier_smoke(tmp_path: Path):
    """Empty candidate submission must fail verifier mount smoke."""
    run_dir = tmp_path / "runs" / "smoke-fail-case"
    run_dir.mkdir(parents=True)

    cli_main(["case", "intake", "--run-dir", str(run_dir), "--category", "mlp"])

    ir_doc = {
        "schema_version": 1,
        "identity": {"title": "Smoke Fail", "category": "mlp", "version": "1.0"},
        "scientific_target": {"system": "Si", "objective": "Energy"},
        "selection": {"paradigm": "standard"},
        "candidate": {
            "instruction": "Solve",
            "inputs": [{"path": "train.xyz"}],
        },
        "submission": {
            "root": "final",
            "artifacts": [{"path": "required_model.pt", "kind": "checkpoint", "required": True}],
        },
        "runtime": {"execution_class": "local_sandbox", "candidate_image": "ccbench-agent:v1"},
        "coverage": {
            "scientific_domain": "semiconductors",
            "method_family": "end_to_end_potential",
            "material_class": "inorganic_2d",
            "computation_type": "iterative_training",
        },
        "verification": {"layers": ["V0", "V1"], "thresholds": {"t": 0.05}},
    }
    ir_path = tmp_path / "case.ir.yaml"
    ir_path.write_text(yaml.safe_dump(ir_doc), encoding="utf-8")

    cli_main(["case", "design", "--ir", str(ir_path), "--run-dir", str(run_dir)])
    cli_main(["case", "build", "--run-dir", str(run_dir)])

    # Missing candidate inputs
    rc = cli_main(["case", "validate", "--run-dir", str(run_dir)])
    assert rc != 0
