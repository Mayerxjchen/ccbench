import json
import os
import shutil
import sys
from pathlib import Path

import pytest


APP = Path(os.environ.get("MATCLAW_032_SUBMISSION", "/app")).resolve()
EXPECTED_PROFILE = os.environ.get("MATCLAW_PROFILE", "paper")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verifier import verify  # noqa: E402


@pytest.fixture()
def submission(tmp_path: Path) -> Path:
    if not APP.is_dir():
        pytest.skip("no submission workspace (set MATCLAW_032_SUBMISSION)")
    target = tmp_path / "submission"
    shutil.copytree(APP, target)
    return target


def test_real_artifacts_are_independently_recomputed(submission: Path) -> None:
    report = verify(submission, EXPECTED_PROFILE)
    assert report["valid"], report["errors"]
    assert len(report["recomputed_curve"]) >= 1


def test_forged_order_parameter_is_rejected(submission: Path) -> None:
    result_path = submission / "result.json"
    result = json.loads(result_path.read_text())
    result["curve"][0]["mean_abs_eta_A"] += 1.0
    result_path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("not trajectory-derived" in error for error in report["errors"])


def test_modified_trajectory_is_rejected(submission: Path) -> None:
    result = json.loads((submission / "result.json").read_text())
    relative = result["trajectories"][0]["path"]
    trajectory = submission / relative
    trajectory.write_bytes(trajectory.read_bytes() + b"tampered")
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("trajectory hash mismatch" in error for error in report["errors"])


def test_deleted_trajectory_is_rejected(submission: Path) -> None:
    result = json.loads((submission / "result.json").read_text())
    relative = result["trajectories"][0]["path"]
    (submission / relative).unlink()
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("missing trajectory" in error for error in report["errors"])


def test_smoke_cannot_claim_a_formal_result(submission: Path) -> None:
    result_path = submission / "result.json"
    result = json.loads(result_path.read_text())
    result["formal_result"] = not result["formal_result"]
    result_path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("formal_result does not match the locked profile" in error for error in report["errors"])


def test_model_identity_forge_is_rejected(submission: Path) -> None:
    result_path = submission / "result.json"
    result = json.loads(result_path.read_text())
    result["inputs"]["model_sha256"] = "0" * 64
    result_path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("teacher model identity mismatch" in error for error in report["errors"])


def test_partial_trajectory_is_rejected(submission: Path) -> None:
    (submission / "md" / "production_350K.partial.traj").write_bytes(b"partial")
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("partial trajectory files present" in error for error in report["errors"])


def test_incomplete_grid_coverage_is_rejected(submission: Path) -> None:
    result_path = submission / "result.json"
    result = json.loads(result_path.read_text())
    removed = result["trajectories"].pop()
    result["curve"] = [row for row in result["curve"] if row["temperature_K"] != removed["temperature_K"]]
    result_path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("coverage mismatch" in error or "missing production evidence" in error
               for error in report["errors"])


def test_timestep_contract_forge_is_rejected(submission: Path) -> None:
    result_path = submission / "result.json"
    result = json.loads(result_path.read_text())
    result["timestep_fs"] = 1.0
    result_path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("timestep contract mismatch" in error for error in report["errors"])
