import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest


# The graded submission is /app in the harness; dev runs point this at a real
# smoke workspace so every negative fixture tampers a disposable copy.
APP = Path(os.environ.get("MATCLAW_031_SUBMISSION", "/app")).resolve()
EXPECTED_PROFILE = os.environ.get("MATCLAW_PROFILE", "paper")
SOLUTION_DIR = Path(__file__).resolve().parent.parent / "solution"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verifier import verify  # noqa: E402


@pytest.fixture()
def submission(tmp_path: Path) -> Path:
    if not APP.is_dir():
        pytest.skip("no submission workspace (set MATCLAW_031_SUBMISSION)")
    target = tmp_path / "submission"
    shutil.copytree(APP, target)
    return target


def test_real_student_training_and_active_iteration_are_verified(submission: Path) -> None:
    report = verify(submission, EXPECTED_PROFILE)
    assert report["valid"], report["errors"]
    assert len(report["recomputed_heldout_mae_eV_A"]) >= 1


def test_forged_mae_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["history"][0]["heldout_force_mae_eV_A"] = 0.001
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("MAE is not model-derived" in error for error in report["errors"])


def test_paper_protocol_seed_is_locked(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    if result.get("profile") != "paper":
        pytest.skip("paper-only protocol-seed contract")
    result["protocol_seed"] = 0
    path.write_text(json.dumps(result))
    report = verify(submission, "paper")
    assert not report["valid"]
    assert "paper protocol seed mismatch" in report["errors"]


def test_zero_active_iterations_are_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["profile"] = "paper"
    result["formal_result"] = True
    result["history"] = result["history"][:1]
    path.write_text(json.dumps(result))
    report = verify(submission)
    assert not report["valid"]
    assert any("iteration 0 plus at least one later" in error for error in report["errors"])


def test_train_test_leakage_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["history"][0]["train_configuration_hashes"][0] = result["heldout"]["configuration_hashes"][0]
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]


def test_modified_student_model_is_rejected(submission: Path) -> None:
    path = submission / "models/iteration_0/member_0/student.pb"
    if not path.is_file():
        pytest.skip("fixture lacks iteration_0 student model")
    path.write_bytes(path.read_bytes() + b"tampered")
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("student model evidence mismatch" in error for error in report["errors"])


def test_modified_teacher_labels_are_rejected(submission: Path) -> None:
    path = submission / "data/heldout/set.000/force.npy"
    if not path.is_file():
        pytest.skip("fixture lacks held-out force labels")
    forces = np.load(path)
    forces.flat[0] += 1.0
    np.save(path, forces)
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("held-out forces are not teacher-labelled" in error for error in report["errors"])


def test_teacher_model_identity_forge_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["teacher_model_sha256"] = "0" * 64
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("teacher model identity mismatch" in error for error in report["errors"])


def test_declared_profile_forge_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["formal_result"] = not result["formal_result"]
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("formal_result does not match profile" in error for error in report["errors"])


# ---- Task 6: fail-closed rejection of the old inactive false success ----


def test_paper_with_zero_active_iterations_is_rejected(submission: Path) -> None:
    """A paper run that claims a later iteration without ever selecting a frame
    must fail: iteration 1 before any selection evidence is a forged active
    step, not a valid formal convergence."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["profile"] = "paper"
    result["formal_result"] = True
    # A formal result may contain more than one active transition.  Retain
    # exactly one transition before removing its selection evidence so this
    # fixture genuinely has zero active iterations.
    result["history"] = result["history"][:2]
    zero, one = result["history"][0], result["history"][1]
    zero["selected_frames"] = 0
    zero["selected_indices"] = []
    zero["selected_configuration_hashes"] = []
    zero["teacher_label_sha256"] = None
    zero["exploration_frames"] = 0
    zero["committee_deviation_eV_A"] = []
    zero["exploration_trajectories"] = []
    zero["training_frames_after"] = zero["training_frames"]
    # Make iteration 1 identical to iteration 0: no growth, no active step.
    one["training_frames"] = zero["training_frames"]
    one["train_configuration_hashes"] = list(zero["train_configuration_hashes"])
    path.write_text(json.dumps(result))
    if (submission / "data" / "iteration_1").is_dir():
        shutil.rmtree(submission / "data" / "iteration_1")
    shutil.copytree(submission / "data" / "iteration_0", submission / "data" / "iteration_1")
    report = verify(submission)
    assert not report["valid"]
    assert report["active_iterations"] == 0
    assert any("no active distillation iteration" in error for error in report["errors"])


def test_paper_final_mae_exceeding_acceptance_is_rejected(submission: Path) -> None:
    """A paper run whose recomputed final MAE is not below the 0.10 eV/A
    acceptance threshold must fail even when every internal bookkeeping check
    is self-consistent."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["profile"] = "paper"
    result["formal_result"] = True
    # Reuse the real pre-distillation model as the final model.  Its recorded
    # and independently recomputed held-out MAE exceeds 0.10 eV/A, unlike the
    # accepted final model in the formal workspace.
    result["history"][-1]["models"] = result["history"][0]["models"]
    result["history"][-1]["heldout_force_mae_eV_A"] = result["history"][0][
        "heldout_force_mae_eV_A"
    ]
    path.write_text(json.dumps(result))
    report = verify(submission)
    assert not report["valid"]
    assert any("exceeds acceptance" in error for error in report["errors"])
    assert report["recomputed_final_mae_eV_A"] >= 0.10


def test_selected_configuration_not_in_recomputed_candidates_is_rejected(submission: Path) -> None:
    """A selected configuration hash that no recomputed candidate produced must
    be rejected: selection is derived from the raw trajectories and models, not
    from the submission's own selected-hash list."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    selected = result["history"][0].get("selected_configuration_hashes")
    if not selected:
        pytest.skip("fixture has no selected configuration to forge")
    result["history"][0]["selected_configuration_hashes"][0] = "0" * 64
    path.write_text(json.dumps(result))
    report = verify(submission)
    assert not report["valid"]
    assert any("not among recomputed candidates" in error for error in report["errors"])


def test_duplicated_selected_hash_is_rejected(submission: Path) -> None:
    """Appending the same selected configuration twice is never a valid growth."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    zero = result["history"][0]
    selected = zero.get("selected_configuration_hashes")
    if not selected:
        pytest.skip("fixture has no selected configuration to duplicate")
    zero["selected_configuration_hashes"] = list(selected) + list(selected)
    zero["selected_indices"] = list(zero.get("selected_indices", [])) + list(zero.get("selected_indices", []))
    zero["selected_frames"] = len(zero["selected_configuration_hashes"])
    path.write_text(json.dumps(result))
    report = verify(submission)
    assert not report["valid"]
    assert any("duplicated" in error for error in report["errors"])


def test_out_of_band_selection_is_rejected(submission: Path) -> None:
    """A selected frame whose recorded committee deviation leaves the fixed
    [0.05, 0.15] eV/A band must fail, and the deviation must also be
    model-derived (the recomputed value differs from the forged one)."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    zero = result["history"][0]
    deviations = zero.get("committee_deviation_eV_A", [])
    selected_indices = zero.get("selected_indices", [])
    if not deviations or not selected_indices:
        pytest.skip("fixture has no recorded deviations/selection to forge")
    # The recorded deviation array is in candidate (trajectory-frame) order, so
    # a selected frame is not necessarily index 0. Forge the deviation of an
    # actually-selected frame to leave the fixed band.
    deviations[selected_indices[0]] = 0.5
    zero["committee_deviation_eV_A"] = deviations
    path.write_text(json.dumps(result))
    report = verify(submission)
    assert not report["valid"]
    assert any("outside committee-deviation band" in error for error in report["errors"])
    assert any("not model-derived" in error for error in report["errors"])


def test_wrong_dataset_growth_is_rejected(submission: Path) -> None:
    """Growth that does not equal before + selected must fail closed."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    zero = result["history"][0]
    before = zero.get("training_frames_before")
    if not isinstance(before, int):
        pytest.skip("fixture has no training-frame audit")
    zero["training_frames_after"] = before + 999
    path.write_text(json.dumps(result))
    report = verify(submission)
    assert not report["valid"]
    assert any("training-frame audit mismatch" in error for error in report["errors"])


def test_forged_teacher_label_digest_is_rejected(submission: Path) -> None:
    """The canonical teacher-label digest on the selected frames must equal the
    digest re-derived from the pinned teacher model and the raw frames."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    zero = result["history"][0]
    if zero.get("teacher_label_sha256") is None:
        pytest.skip("fixture has no teacher-label digest to forge")
    zero["teacher_label_sha256"] = "0" * 64
    path.write_text(json.dumps(result))
    report = verify(submission)
    assert not report["valid"]
    assert any("teacher-label digest is not model-derived" in error for error in report["errors"])


def test_identical_committee_models_are_rejected(submission: Path) -> None:
    """Two committee members that are the same model cannot drive a genuine
    active step: their disagreement is identically zero."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    models = result["history"][0].get("models", [])
    if len(models) < 2:
        pytest.skip("fixture lacks iteration_0 committee models")
    models[1] = dict(models[0])
    result["history"][0]["models"] = models
    path.write_text(json.dumps(result))
    report = verify(submission)
    assert not report["valid"]
    assert any("lacks two independent committee models" in error for error in report["errors"])


def test_changed_checkpoint_artifact_hash_is_rejected(submission: Path) -> None:
    """A checkpoint whose recorded artifact hash no longer matches the file on
    disk must fail closed, even when result.json was left untouched."""
    path = submission / "checkpoint.json"
    if not path.is_file():
        pytest.skip("fixture lacks checkpoint.json")
    checkpoint = json.loads(path.read_text())
    recorded = checkpoint.get("artifact_hashes", {})
    if not recorded:
        pytest.skip("fixture checkpoint has no recorded artifact hashes")
    key = next(iter(recorded))
    checkpoint["artifact_hashes"][key] = "0" * 64
    path.write_text(json.dumps(checkpoint))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("checkpoint artifact mismatch" in error for error in report["errors"])


# ---- Task 7: the independent alternative implementation satisfies the same
# ---- external artifact contract when run from a fresh workspace.

# Distinct alternative smoke seed (Task 8 interface: smoke seeds are
# 2026081101 primary and 2026081102 alternative).
ALT_SEED = 2026081102


def _stage_public_inputs(workspace: Path) -> None:
    """Copy the graded public inputs into a fresh workspace so the independent
    alternative solver can be executed from scratch and then verified by the
    same hidden evaluator."""
    for name in ("CuInP2S6.cif", "teacher_model.pb", "run_profiles.json"):
        source = APP / name
        if not source.is_file():
            pytest.skip(f"no staged {name} in submission workspace")
        shutil.copy2(source, workspace / name)


def test_alternative_does_not_import_primary_workflow() -> None:
    """The alternative solver must stay organizationally independent: it may
    share the pure contract/audit modules (active_contract, active_utils), but
    it must never import the primary workflow's orchestration code."""
    source = (SOLUTION_DIR / "alt_distillation.py").read_text()
    import_lines = [
        line.strip() for line in source.splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    assert not any("run_distillation" in line for line in import_lines)
    # The module docstring may reference the primary by name for context, but no
    # import statement may. A bare "import run_distillation" anywhere is a hard
    # rejection of the independent-implementation acceptance point.
    for line in source.splitlines():
        assert "import run_distillation" not in line
        assert "from run_distillation" not in line


def test_alternative_workflow_produces_a_verified_active_transition(tmp_path: Path) -> None:
    """The independently-written alt solver must satisfy the same hidden
    verifier with a real active transition and exact dataset growth, without
    importing or sharing the primary workflow's orchestration functions."""
    workspace = tmp_path / "alt-workspace"
    workspace.mkdir()
    _stage_public_inputs(workspace)
    alt_script = SOLUTION_DIR / "alt_distillation.py"
    environment = dict(os.environ, MATCLAW_OUTPUT=str(workspace))
    completed = subprocess.run(
        [sys.executable, str(alt_script), "--profile", "smoke",
         "--seed", str(ALT_SEED), "--output", str(workspace)],
        env=environment, capture_output=True, text=True)
    assert completed.returncode == 0, (
        f"alt run failed (exit {completed.returncode})\n"
        f"stdout tail:\n{completed.stdout[-4000:]}\n"
        f"stderr tail:\n{completed.stderr[-4000:]}"
    )
    report = verify(workspace, "smoke")
    assert report["valid"], report["errors"]
    assert report["active_iterations"] >= 1
    assert len(report["recomputed_heldout_mae_eV_A"]) >= 2
