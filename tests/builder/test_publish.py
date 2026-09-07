"""Tests for Atomic Case Publisher transaction, safe replace, and identity binding."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
import pytest
import yaml

from ccbench.builder.publish import PublishError, publish_case
from ccbench.builder.source_lock import build_sources_lock, SourceTier
from ccbench.builder.state import CaseLifecycleState, derive_state
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
    draft_verifier = run_dir / "draft" / "verifier"
    if draft_verifier.exists():
        shutil.rmtree(draft_verifier)
    shutil.copytree(verifier_dir, draft_verifier)


def _setup_full_lifecycle_for_publish(run_dir: Path):
    """Set up a complete lifecycle that reaches BENCHMARK_VALID."""
    source_dir = run_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp", "title": "test"}), encoding="utf-8")
    (source_dir / "input.txt").write_text("data", encoding="utf-8")
    build_sources_lock(source_dir, {"input.txt": SourceTier.PUBLIC_SOURCE})

    (run_dir / "design").mkdir()
    _write_valid_case_ir(run_dir / "design" / "case.ir.yaml", case_id="006-toy")

    # Scaffold via compiler
    from ccbench.builder.scaffold import compile_case_ir_to_draft
    from ccbench.builder.design import load_case_ir
    ir_doc = load_case_ir(run_dir / "design" / "case.ir.yaml")
    compile_case_ir_to_draft(ir_doc, run_dir / "draft", source_dir=source_dir)

    # Build real verifier
    _build_real_verifier(run_dir)

    # Compute real candidate bundle digest
    from ccbench.contracts.case import CaseSpec as _CaseSpec
    from ccbench.core.packager import package_candidate as _package_candidate
    import tempfile as _tempfile
    spec = _CaseSpec.load(run_dir / "draft")
    with _tempfile.TemporaryDirectory() as tmp_str:
        bundle = _package_candidate(spec, Path(tmp_str))
        real_cb_digest = f"sha256:{bundle.public_digest}"

    # Discovery with real digests
    ir_sha = hashlib.sha256((run_dir / "design" / "case.ir.yaml").read_bytes()).hexdigest()
    disc_doc = {
        "run_id": "disc-001",
        "candidate_bundle_digest": real_cb_digest,
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
    reports_dir = run_dir / "reports"
    reports_dir.mkdir()
    (reports_dir / "reference-ready.json").write_text(
        json.dumps({"reproducible": True}), encoding="utf-8",
    )
    ref_sha = hashlib.sha256((reports_dir / "reference-ready.json").read_bytes()).hexdigest()

    (reports_dir / "calibration-report.json").write_text(
        json.dumps({"passed": True, "thresholds": {"energy_rmse_max": 0.05}}),
        encoding="utf-8",
    )
    cal_thresholds = {"energy_rmse_max": 0.05}
    thresholds_digest = f"sha256:{hashlib.sha256(json.dumps(cal_thresholds, sort_keys=True).encode('utf-8')).hexdigest()}"

    (reports_dir / "threshold-freeze.json").write_text(
        json.dumps({
            "case_ir_digest": f"sha256:{ir_sha}",
            "reference_digest": f"sha256:{ref_sha}",
            "formal_agent_results_seen": False,
            "thresholds_digest": thresholds_digest,
        }),
        encoding="utf-8",
    )


def test_publish_enforces_benchmark_valid_and_four_objects(tmp_path: Path):
    run_dir = tmp_path / "runs" / "006-toy"
    cases_dir = tmp_path / "cases"
    maintainer_dir = tmp_path / "maintainer"

    _setup_full_lifecycle_for_publish(run_dir)

    # Verify state reaches BENCHMARK_VALID via re-execution
    state = derive_state(run_dir)
    assert state.current_state == CaseLifecycleState.BENCHMARK_VALID, (
        f"Expected BENCHMARK_VALID, got {state.current_state}. Open gates: {state.open_gates}"
    )

    # Publish
    published = publish_case(
        run_dir, "006-toy", cases_dir=cases_dir, maintainer_dir=maintainer_dir,
    )

    assert published.is_dir()
    names = {p.name for p in published.iterdir()}
    assert names == {"task.md", "case.toml", "input", "verifier"}

    maint_case = maintainer_dir / "cases" / "006-toy"
    assert maint_case.is_dir()
    assert (maint_case / "source" / "intake.json").is_file()


def test_forged_benchmark_valid_receipt_cannot_publish(tmp_path: Path):
    """A hand-written benchmark-valid.json must not allow publishing."""
    run_dir = tmp_path / "runs" / "forged"
    cases_dir = tmp_path / "cases"
    maintainer_dir = tmp_path / "maintainer"

    # Set up minimal files but don't go through full lifecycle
    run_dir.mkdir(parents=True)
    (run_dir / "draft").mkdir()
    (run_dir / "draft" / "task.md").write_text("# Task\n", encoding="utf-8")
    _write_valid_case_toml(run_dir / "draft" / "case.toml")
    (run_dir / "design").mkdir()
    _write_valid_case_ir(run_dir / "design" / "case.ir.yaml", case_id="forged-case")
    source_dir = run_dir / "source"
    source_dir.mkdir()
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp", "title": "t"}), encoding="utf-8")
    (source_dir / "input.txt").write_text("data", encoding="utf-8")
    build_sources_lock(source_dir, {"input.txt": SourceTier.PUBLIC_SOURCE})

    # Hand-write forged benchmark-valid.json
    reports_dir = run_dir / "reports"
    reports_dir.mkdir()
    (reports_dir / "benchmark-valid.json").write_text(
        json.dumps({"valid": True, "evidence_graph": {"checks": True}}),
        encoding="utf-8",
    )

    # derive_state must NOT advance to BENCHMARK_VALID
    state = derive_state(run_dir)
    assert state.current_state != CaseLifecycleState.BENCHMARK_VALID


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
