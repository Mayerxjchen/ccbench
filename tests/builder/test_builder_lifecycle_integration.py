"""Comprehensive end-to-end integration test for Benchmark Builder lifecycle."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
import pytest
import yaml

from ccbench.cli import main as cli_main
from ccbench.builder.state import CaseLifecycleState, derive_state
from ccbench.contracts.case import CaseSpec


def test_full_builder_lifecycle_end_to_end(tmp_path: Path):
    run_dir = tmp_path / "runs" / "006-end-to-end"
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True)
    maintainer_dir = tmp_path / "maintainer"
    maintainer_dir.mkdir(parents=True)

    # ── 1. Intake ────────────────────────────────────────────────────
    rc = cli_main([
        "case", "intake",
        "--run-dir", str(run_dir),
        "--category", "mlp",
        "--title", "Cu-Au Alloy Potential",
        "--system", "Cu-Au Alloy",
        "--objective", "Predict formation energy",
    ])
    assert rc == 0
    assert (run_dir / "source" / "intake.json").is_file()
    assert (run_dir / "source" / "sources.lock.json").is_file()
    assert (run_dir / "source" / "admission-report.json").is_file()

    state1 = derive_state(run_dir)
    assert state1.current_state == CaseLifecycleState.INTAKE_COMPLETE

    # Add real scientific source file and lock it
    from ccbench.builder.source_lock import build_sources_lock, SourceTier
    (run_dir / "source" / "train.xyz").write_text("dummy-xyz-data", encoding="utf-8")
    build_sources_lock(run_dir / "source", {"train.xyz": SourceTier.PUBLIC_SOURCE})

    state1_locked = derive_state(run_dir)
    assert state1_locked.current_state == CaseLifecycleState.SOURCE_LOCKED

    # ── 2. Design ────────────────────────────────────────────────────
    case_ir_file = tmp_path / "case.ir.yaml"
    ir_doc = {
        "schema_version": 1,
        "identity": {
            "title": "Cu-Au Alloy Potential",
            "category": "mlp",
            "case_id": "006-cu-au-potential",
            "version": "1.0.0",
        },
        "scientific_target": {
            "system": "Cu-Au Alloy",
            "objective": "Predict formation energy",
            "observable": "formation_energy",
        },
        "selection": {
            "paradigm": "standard",
        },
        "candidate": {
            "instruction": "Train neural potential on training structures.",
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
            "timeout_sec": 1200.0,
            "gpus": 0,
        },
        "coverage": {
            "scientific_domain": "metal_alloys",
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
                    "target": "final/metrics.json",
                    "threshold_ref": "energy_rmse_max",
                    "params": {"metric": "energy_rmse"},
                }
            ],
            "thresholds": {
                "energy_rmse_max": 0.05,
            },
        },
    }
    case_ir_file.write_text(yaml.safe_dump(ir_doc), encoding="utf-8")

    # Add mock input file in source and draft/input
    (run_dir / "source" / "train.xyz").write_text("dummy-xyz-data", encoding="utf-8")

    rc = cli_main([
        "case", "design",
        "--ir", str(case_ir_file),
        "--run-dir", str(run_dir),
    ])
    assert rc == 0
    assert (run_dir / "design" / "case.ir.yaml").is_file()

    state2 = derive_state(run_dir)
    assert state2.current_state == CaseLifecycleState.DESIGN_VALID

    # ── 3. Build ─────────────────────────────────────────────────────
    rc = cli_main([
        "case", "build",
        "--run-dir", str(run_dir),
    ])
    assert rc == 0
    assert (run_dir / "draft" / "task.md").is_file()
    assert (run_dir / "draft" / "case.toml").is_file()
    assert (run_dir / "verifier" / "verify.py").is_file()
    assert (run_dir / "verifier" / "test.sh").is_file()

    # Provide candidate input in draft/input so package_candidate succeeds
    (run_dir / "draft" / "input" / "train.xyz").write_text("dummy-structures", encoding="utf-8")

    # After build+input, runnable gate now passes automatically
    # (verify.py was compiled during build, and all checks pass)
    state3 = derive_state(run_dir)
    assert state3.current_state == CaseLifecycleState.RUNNABLE_DRAFT

    # ── 5. Discovery ─────────────────────────────────────────────────
    # 5a. Direct without evidence should fail
    rc_fail = cli_main([
        "case", "discovery",
        "--run-dir", str(run_dir),
    ])
    assert rc_fail != 0

    # 5b. Evidence-based classification with real case_ir digest
    import hashlib as _hashlib
    ir_sha = _hashlib.sha256((run_dir / "design" / "case.ir.yaml").read_bytes()).hexdigest()
    metrics_dir = tmp_path / "discovery_metrics"
    metrics_dir.mkdir()
    disc_doc = {
        "run_id": "disc-001",
        "candidate_bundle_digest": "sha256:" + "a" * 64,
        "case_ir_digest": f"sha256:{ir_sha}",
        "outcome": {"terminal_state": "COMPLETED", "candidate_exit_code": 0, "verifier_exit_code": 0},
        "metrics": {"energy_rmse": 0.03},
        "failure_class": "SUCCESS",
    }
    (metrics_dir / "run-01.json").write_text(json.dumps(disc_doc), encoding="utf-8")

    rc = cli_main([
        "case", "discovery",
        "--run-dir", str(run_dir),
        "--metrics-dir", str(metrics_dir),
    ])
    assert rc == 0
    assert (run_dir / "discovery" / "classification.json").is_file()

    state5 = derive_state(run_dir)
    assert state5.current_state == CaseLifecycleState.DISCOVERY_CLASSIFIED

    # ── 6. Reference & Calibration Setup ──────────────────────────────
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(exist_ok=True)
    (reports_dir / "reference-ready.json").write_text(
        json.dumps({"reproducible": True, "state": "REFERENCE_READY"}),
        encoding="utf-8",
    )
    (reports_dir / "calibration-report.json").write_text(
        json.dumps({"passed": True, "thresholds": {"energy_rmse_max": 0.05}}),
        encoding="utf-8",
    )

    # Threshold freeze (MANDATORY for release)
    ir_sha_freeze = _hashlib.sha256((run_dir / "design" / "case.ir.yaml").read_bytes()).hexdigest()
    cal_thresholds = {"energy_rmse_max": 0.05}
    thresholds_digest = f"sha256:{_hashlib.sha256(json.dumps(cal_thresholds, sort_keys=True).encode('utf-8')).hexdigest()}"
    (reports_dir / "threshold-freeze.json").write_text(
        json.dumps({
            "case_ir_digest": f"sha256:{ir_sha_freeze}",
            "formal_agent_results_seen": False,
            "thresholds_digest": thresholds_digest,
        }),
        encoding="utf-8",
    )

    state6 = derive_state(run_dir)
    assert state6.current_state == CaseLifecycleState.CALIBRATED

    # ── 7. Release Check ─────────────────────────────────────────────
    rc = cli_main([
        "case", "release-check",
        "--run-dir", str(run_dir),
    ])
    assert rc == 0
    assert (reports_dir / "benchmark-valid.json").is_file()

    state7 = derive_state(run_dir)
    assert state7.current_state == CaseLifecycleState.BENCHMARK_VALID

    # ── 8. Atomic Publish ────────────────────────────────────────────
    from ccbench.builder.publish import publish_case
    published = publish_case(
        run_dir,
        "006-cu-au-potential",
        cases_dir=cases_dir,
        maintainer_dir=maintainer_dir,
    )
    assert published.is_dir()
    assert {p.name for p in published.iterdir()} == {"task.md", "case.toml", "input", "verifier"}
    assert (maintainer_dir / "cases" / "006-cu-au-potential" / "design" / "case.ir.yaml").is_file()
