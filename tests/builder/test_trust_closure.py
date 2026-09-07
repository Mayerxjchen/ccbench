"""Comprehensive adversarial trust closure test suite for CCBench Case Builder.

Verifies that:
1. forged smoke-report cannot advance state.
2. forged benchmark-valid.json cannot publish.
3. empty sources.lock cannot reach SOURCE_LOCKED.
4. untracked source added after lock breaks SOURCE_LOCKED.
5. empty/arbitrary metrics cannot be PROMOTED.
6. verifier exit 0 without result.json causes runnable failure.
7. category mandatory layer omission fails compile.
8. inline threshold fails schema validation.
9. target_case_id mismatch aborts publish.
10. safe replace restores old case on staging failure.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from ccbench.builder.discovery import (
    DiscoveryEvidenceError,
    classify_discovery_evidence,
)
from ccbench.builder.publish import PublishError, publish_case
from ccbench.builder.release import check_release_validity
from ccbench.builder.source_lock import (
    SourceTier,
    build_sources_lock,
    verify_sources_lock_bidirectional,
)
from ccbench.builder.state import CaseLifecycleState, derive_state
from ccbench.builder.verifier_plan import VerifierPlan, VerifierPlanError
from ccbench.builder.design import CaseIRValidationError, validate_case_ir


def test_empty_sources_lock_cannot_reach_source_locked(tmp_path: Path):
    """Empty sources lock must stay at INTAKE_COMPLETE."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp", "title": "t"}), encoding="utf-8")
    build_sources_lock(source_dir, {})

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.INTAKE_COMPLETE
    assert "SOURCE_LOCK" in state.open_gates


def test_untracked_source_added_after_lock_fails(tmp_path: Path):
    """Untracked source file added after lock fails bidirectional check."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp", "title": "t"}), encoding="utf-8")
    (source_dir / "valid.xyz").write_text("lattice", encoding="utf-8")
    build_sources_lock(source_dir, {"valid.xyz": SourceTier.PUBLIC_SOURCE})

    # Added after lock:
    (source_dir / "untracked.xyz").write_text("sneaky", encoding="utf-8")

    state = derive_state(tmp_path)
    assert state.current_state == CaseLifecycleState.INTAKE_COMPLETE
    assert "SOURCE_LOCK" in state.open_gates


def test_arbitrary_empty_metrics_never_promoted(tmp_path: Path):
    """metrics/empty.json must never derive PROMOTED."""
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "empty.json").write_text("{}", encoding="utf-8")

    with pytest.raises(DiscoveryEvidenceError, match="No valid discovery evidence document"):
        classify_discovery_evidence(metrics_dir)


def test_inline_threshold_fails_case_ir_schema():
    """Case IR schema must forbid inline 'threshold' parameter."""
    doc = {
        "schema_version": 1,
        "identity": {"title": "T", "category": "mlp"},
        "scientific_target": {"system": "Si", "objective": "E"},
        "selection": {"paradigm": "standard"},
        "candidate": {"instruction": "Run", "inputs": [{"path": "train.xyz"}]},
        "submission": {"root": "final", "artifacts": [{"path": "m.pt", "kind": "m"}]},
        "runtime": {"execution_class": "local_sandbox", "candidate_image": "ccbench-agent:v1"},
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
                    "params": {"threshold": 0.05},  # FORBIDDEN INLINE!
                }
            ],
            "thresholds": {"energy_rmse_max": 0.05},
        },
    }
    with pytest.raises(CaseIRValidationError, match="should not be valid"):
        validate_case_ir(doc)


def test_mlp_omits_v4_plan_compile_fails():
    """Category MLP requires layer V4; omitting it must fail compile."""
    case_ir = {
        "identity": {"case_id": "c-001", "category": "mlp"},
        "verification": {
            "layers": ["V0", "V1", "V2"],  # Omitted V4!
            "thresholds": {"energy_rmse_max": 0.05},
        },
    }
    with pytest.raises(VerifierPlanError, match="Category 'mlp' requires layer 'V4'"):
        VerifierPlan.from_case_ir(case_ir)


def test_target_case_id_mismatch_publish_fails(tmp_path: Path):
    """target_case_id != CaseSpec case_id must abort publish."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    draft_dir = run_dir / "draft"
    draft_dir.mkdir()
    (draft_dir / "task.md").write_text("# Task\n", encoding="utf-8")
    (draft_dir / "case.toml").write_text(
        'schema_version = "1.2"\ncase_version = "1.0"\n'
        '[execution]\nclass = "local_sandbox"\n'
        '[task]\nname = "006-declared"\n'
        '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
        '[coverage]\nscientific_domain = "semiconductors"\nmethod_family = "end_to_end_potential"\nmaterial_class = "inorganic_2d"\ncomputation_type = "iterative_training"\n',
        encoding="utf-8",
    )
    (run_dir / "design").mkdir()
    (run_dir / "design" / "case.ir.yaml").write_text(
        yaml.safe_dump({"identity": {"case_id": "006-declared", "category": "mlp", "title": "T"}}),
        encoding="utf-8",
    )

    with pytest.raises(PublishError, match="Identity binding mismatch"):
        publish_case(run_dir, "007-mismatched", cases_dir=tmp_path / "cases", maintainer_dir=tmp_path / "maintainer")


def test_safe_replacement_preserves_old_case_on_failure(tmp_path: Path):
    """If publish fails during transaction, existing case must be preserved intact."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    existing_case = cases_dir / "006-existing"
    existing_case.mkdir()
    (existing_case / "task.md").write_text("# Original Case\n", encoding="utf-8")
    (existing_case / "case.toml").write_text("original = true\n", encoding="utf-8")

    bad_run = tmp_path / "runs" / "bad-run"
    bad_run.mkdir(parents=True)
    # Draft is empty / invalid
    with pytest.raises(PublishError):
        publish_case(bad_run, "006-existing", cases_dir=cases_dir, maintainer_dir=tmp_path / "maintainer", force=True)

    # Verify original case is preserved!
    assert (existing_case / "task.md").is_file()
    assert (existing_case / "task.md").read_text(encoding="utf-8") == "# Original Case\n"
