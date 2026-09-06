import json
import os
import shutil
import sys
from pathlib import Path

import pytest


APP = Path(os.environ.get("MATCLAW_033_SUBMISSION", "/app")).resolve()
EXPECTED_PROFILE = os.environ.get("MATCLAW_PROFILE", "paper")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from verifier import verify  # noqa: E402


@pytest.fixture()
def submission(tmp_path: Path) -> Path:
    if not APP.is_dir():
        pytest.skip("no submission workspace (set MATCLAW_033_SUBMISSION)")
    target = tmp_path / "submission"
    shutil.copytree(APP, target)
    return target


def test_real_smoke_search_is_recomputed(submission: Path) -> None:
    report = verify(submission, EXPECTED_PROFILE)
    assert report["valid"], report["errors"]
    assert len(report["recomputed"]) >= 1


def test_forged_domino_slope_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["history"][0]["slope_ps_per_site"] = 0.9
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("not trajectory-derived" in error for error in report["errors"])


def test_more_than_two_jobs_in_an_iteration_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    duplicate = dict(result["history"][0])
    duplicate["job_in_iteration"] = 3
    result["history"].append(duplicate)
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("exceeds two-job cap" in error for error in report["errors"])


def test_forged_decision_basis_without_prior_measurements_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    # iteration 1 is exempt (locked start); forge a later-iteration decision basis.
    index = next(i for i, row in enumerate(result["history"]) if row.get("iteration") != 1)
    result["history"][index]["decision_basis"] = "narrative-only"
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("lacks measured feedback" in error for error in report["errors"])


def test_modified_best_trajectory_is_rejected(submission: Path) -> None:
    path = submission / "best_trajectory.traj"
    path.write_bytes(path.read_bytes() + b"tampered")
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("best trajectory evidence mismatch" in error for error in report["errors"])


def test_model_identity_forge_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["inputs"]["model_sha256"] = "0" * 64
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("model identity mismatch" in error for error in report["errors"])


def test_declared_profile_forge_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["formal_result"] = not result["formal_result"]
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("formal_result does not match profile" in error for error in report["errors"])


def _later_rows(result: dict) -> list[dict]:
    return [row for row in result["history"] if row.get("iteration", 0) > 1]


def test_precomputed_flat_history_is_rejected(submission: Path) -> None:
    """A flat, precomputed history with no per-round decision digests is rejected."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    for row in _later_rows(result):
        row.pop("decision_input_sha256", None)
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("decision input digest mismatch" in error for error in report["errors"])


def test_round2_without_prior_hashes_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    for row in _later_rows(result):
        row["decision_input_sha256"] = "0" * 64
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("decision input digest mismatch" in error for error in report["errors"])


def test_round_gap_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    target = _later_rows(result)[0]
    target["iteration"] = 3
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("rounds are not contiguous" in error for error in report["errors"])


def test_decision_timestamp_before_prior_completion_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    for row in _later_rows(result):
        row["decision_made_at"] = "1970-01-01T00:00:00Z"
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("predates completed" in error for error in report["errors"])


def test_missing_decision_timestamp_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    for row in _later_rows(result):
        row.pop("decision_made_at", None)
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("missing a parseable decision_made_at" in error for error in report["errors"])


def test_forged_best_row_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    result["best"]["slope_ps_per_site"] = (result["best"].get("slope_ps_per_site") or 0.0) + 0.2
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("declared best row does not match the recomputed best" in error for error in report["errors"])


# --- evidence-adaptive path: a preset schedule that ignores measured results ---


def test_preset_fixed_path_is_rejected_by_adaptive_policy(submission: Path) -> None:
    """A fixed literature-answer round (best Ez=-0.16, T=50) that the adaptive policy
    would never propose from the preceding measurements is rejected."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    later = [row for row in result["history"] if row.get("iteration", 0) > 1]
    if not later:
        pytest.skip("no later rounds to forge")
    for row in later:
        row["Ez_V_A"] = -0.16
        row["temperature_K"] = 50
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("does not match the adaptive policy" in error for error in report["errors"])


# --- chronology chain: missing decision fields (pre-T9 oracle style) ---


def test_missing_preceding_job_count_is_rejected(submission: Path) -> None:
    path = submission / "result.json"
    result = json.loads(path.read_text())
    for row in _later_rows(result):
        row.pop("preceding_job_count", None)
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any("preceding_job_count mismatch" in error for error in report["errors"])


def test_missing_full_chronology_chain_is_rejected(submission: Path) -> None:
    """Rows missing decision_input_sha256 / decision_made_at / preceding_job_count
    all at once are rejected (the pre-T9 oracle lacked this chain)."""
    path = submission / "result.json"
    result = json.loads(path.read_text())
    for row in _later_rows(result):
        row.pop("decision_input_sha256", None)
        row.pop("decision_made_at", None)
        row.pop("preceding_job_count", None)
    path.write_text(json.dumps(result))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert any(
        "decision input digest mismatch" in error
        or "missing a parseable decision_made_at" in error
        or "preceding_job_count mismatch" in error
        for error in report["errors"]
    )


# --- paper formal band: best Ez must land inside the literature band ---


def _paper_band_fixture(best_ez: float) -> dict:
    """Minimal paper result whose only meaningful property is the declared best Ez.

    Built as a fixed fixture (the exact old diagnostic outcome, best Ez=-0.13, is the
    out-of-band case) so the band gate is exercised without a GPU paper run.
    """
    return {
        "schema_version": "1.0", "case": "033", "profile": "paper", "formal_result": True,
        "search_mode": "sequential-adaptive-search", "supercell": [1, 25, 1],
        "start": {"Ez_V_A": -0.01, "temperature_K": 200},
        "bounds": {"Ez_V_A": [-0.3, 0.0], "temperature_K": [0, 250]},
        "max_jobs_per_iteration": 2,
        "field_charges_e": {"Cu": 0.765, "In": -0.085, "P": -0.085, "S": -0.085},
        "field_physics": {"force": "q_i*E", "energy": "-sum(q_i*r_i.E)"},
        "history": [],
        "best": {"Ez_V_A": best_ez, "temperature_K": 50, "n_flipped": 42, "n_sites": 50,
                 "slope_ps_per_site": 0.321, "sequential_propagation": True},
        "best_trajectory_sha256": "b" * 64,
        "inputs": {"structure_sha256": "c1fac0de0180624d818886602aa33721f708c757900fde24581f4e723b036180",
                   "model_sha256": "a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d"},
    }


def test_paper_best_ez_outside_literature_band_is_rejected(submission: Path) -> None:
    """The old diagnostic best Ez=-0.13 is outside [-0.18, -0.14] and must fail the band."""
    path = submission / "result.json"
    path.write_text(json.dumps(_paper_band_fixture(best_ez=-0.13)))
    report = verify(submission, "paper")
    assert not report["valid"]
    assert any("best Ez outside literature band" in error for error in report["errors"])


def test_paper_best_ez_inside_literature_band_passes_band_gate(submission: Path) -> None:
    """An in-band best Ez does not trigger the band error (discriminates on Ez)."""
    path = submission / "result.json"
    path.write_text(json.dumps(_paper_band_fixture(best_ez=-0.16)))
    report = verify(submission, "paper")
    assert not any("best Ez outside literature band" in error for error in report["errors"])
