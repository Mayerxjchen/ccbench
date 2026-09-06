import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from ase import Atoms


_VENV_SP = Path("/opt/matclaw/lib/python3.11/site-packages")
if _VENV_SP.is_dir():
    sys.path.insert(0, str(_VENV_SP))

CASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CASE / "solution"))  # dev-only: solution code lives in solution/

from verifier import (  # noqa: E402
    PAPER_EZ_BAND,
    START,
    analyze_displacements,
    canonical_feedback as verifier_canonical_feedback,
    expected_round_jobs,
    feedback_sha256 as verifier_feedback_sha256,
    policy_for,
    propose_round_jobs,
    verify,
)
from field_calculator import CHARGES_E, UniformElectricForce  # noqa: E402
from search_policy import (  # noqa: E402
    canonical_feedback as solution_canonical_feedback,
    feedback_sha256 as solution_feedback_sha256,
    policy_for as solution_policy_for,
    propose_round,
    propose_round_jobs as solution_propose_round_jobs,
    should_stop,
)


def test_electric_field_force_and_energy_match_recovered_protocol() -> None:
    atoms = Atoms("CuS", positions=[[0, 0, 2], [0, 0, 5]])
    atoms.calc = UniformElectricForce(CHARGES_E, [0, 0, -0.2])
    expected_forces = np.array([[0, 0, -0.153], [0, 0, 0.017]])
    assert atoms.get_forces() == pytest.approx(expected_forces)
    expected_energy = -sum(q * z * -0.2 for q, z in [(0.765, 2), (-0.085, 5)])
    assert atoms.get_potential_energy() == pytest.approx(expected_energy)


def test_electric_field_matches_finite_difference_of_energy() -> None:
    """F_i = q_i E must equal -dU/dr_i from the exact energy U = -sum(q_i r_i . E)."""
    atoms = Atoms("CuInP2S6", positions=[[0, 0, 1.0], [0, 1, 2.0], [1, 0, 3.0],
                                         [0, 0, 4.0], [1, 1, 5.0], [0, 1, 6.0],
                                         [1, 0, 7.0], [0, 0, 8.0], [1, 1, 9.0], [0, 1, 10.0]])
    field = np.array([0.1, -0.2, 0.3])
    charges = np.array([CHARGES_E[s] for s in atoms.get_chemical_symbols()])
    atoms.calc = UniformElectricForce(CHARGES_E, field)
    forces = atoms.get_forces()
    energy = atoms.get_potential_energy()
    assert energy == pytest.approx(-float(np.sum(charges * (atoms.positions @ field))))
    h = 1e-5
    for i, a in enumerate(atoms):
        for j in range(3):
            original = atoms.positions.copy()
            shifted = original.copy()
            shifted[i, j] += h
            atoms.positions = shifted
            e_plus = atoms.get_potential_energy()
            shifted[i, j] -= 2 * h
            atoms.positions = shifted
            e_minus = atoms.get_potential_energy()
            atoms.positions = original
            assert forces[i, j] == pytest.approx(-(e_plus - e_minus) / (2 * h), abs=1e-3)


def test_field_calculator_is_deterministic_on_cpu() -> None:
    atoms = Atoms("CuInP2S6", positions=[[0, 0, 1.0], [0, 1, 2.0], [1, 0, 3.0],
                                         [0, 0, 4.0], [1, 1, 5.0], [0, 1, 6.0],
                                         [1, 0, 7.0], [0, 0, 8.0], [1, 1, 9.0], [0, 1, 10.0]])
    field = np.array([0.0, 0.0, -0.2])
    atoms.calc = UniformElectricForce(CHARGES_E, field)
    first = atoms.get_forces().copy()
    second = atoms.get_forces()
    np.testing.assert_array_equal(first, second)


def test_cpu_gpu_field_parity_qualification() -> None:
    """CPU/GPU parity is enforced by scripts/run_gpu_paper.sh via the pinned image's
    qualify_gpu.py (Task 6). This dev test re-checks the same contract when a GPU
    device is explicitly requested, and always asserts CPU determinism."""
    device = os.environ.get("MATCLAW_TEST_GPU_DEVICE")
    atoms = Atoms("CuInP2S6", positions=[[0, 0, 1.0], [0, 1, 2.0], [1, 0, 3.0],
                                         [0, 0, 4.0], [1, 1, 5.0], [0, 1, 6.0],
                                         [1, 0, 7.0], [0, 0, 8.0], [1, 1, 9.0], [0, 1, 10.0]])
    field = np.array([0.0, 0.0, -0.2])
    atoms.calc = UniformElectricForce(CHARGES_E, field)
    np.testing.assert_array_equal(atoms.get_forces(), atoms.get_forces())  # CPU determinism
    if not device:
        pytest.skip("no MATCLAW_TEST_GPU_DEVICE requested; parity gate is run_gpu_paper.sh")


def test_sequential_synthetic_front_has_expected_domino_slope() -> None:
    times = np.arange(0, 20, 0.02)
    flip_times = 2.0 + 0.4 * np.arange(20)
    displacement = np.column_stack([np.tanh((flip - times) / 0.08) for flip in flip_times])
    result = analyze_displacements(displacement, frame_dt_ps=0.02)
    assert result["n_flipped"] == 20
    assert result["slope_ps_per_site"] == pytest.approx(0.4, abs=0.02)
    assert result["sequential_propagation"] is True


def test_simultaneous_flips_are_not_misclassified_as_a_domain_wall() -> None:
    times = np.arange(0, 10, 0.02)
    displacement = np.column_stack([np.tanh((2.0 - times) / 0.1) for _ in range(20)])
    result = analyze_displacements(displacement, frame_dt_ps=0.02)
    assert result["slope_ps_per_site"] == pytest.approx(0.0, abs=1e-12)
    assert result["sequential_propagation"] is False


# --- single digest serialization: solution and verifier must agree ---


def test_solution_and_verifier_canonical_feedback_agree() -> None:
    history = [
        {"iteration": 1, "job_in_iteration": 1, "Ez_V_A": -0.01, "temperature_K": 200,
         "n_flipped": 3, "n_sites": 25, "slope_ps_per_site": 0.45, "trajectory_sha256": "a" * 64},
        {"iteration": 1, "job_in_iteration": 2, "Ez_V_A": -0.05, "temperature_K": 200,
         "n_flipped": 4, "n_sites": 25, "slope_ps_per_site": None, "trajectory_sha256": "b" * 64},
        {"iteration": 2, "job_in_iteration": 1, "Ez_V_A": -0.10, "temperature_K": 100,
         "n_flipped": 5, "n_sites": 25, "slope_ps_per_site": 0.38, "trajectory_sha256": "c" * 64},
    ]
    assert solution_canonical_feedback(history) == verifier_canonical_feedback(history).encode("utf-8")
    assert solution_feedback_sha256(history) == verifier_feedback_sha256(history)


def test_feedback_digest_changes_when_any_measured_field_changes() -> None:
    base = [{"iteration": 1, "job_in_iteration": 1, "Ez_V_A": -0.01, "temperature_K": 200,
             "n_flipped": 3, "n_sites": 25, "slope_ps_per_site": 0.45, "trajectory_sha256": "a" * 64}]
    original = verifier_feedback_sha256(base)
    for key, value in (("Ez_V_A", -0.02), ("n_flipped", 4), ("slope_ps_per_site", 0.5),
                       ("trajectory_sha256", "b" * 64), ("temperature_K", 201)):
        mutated = [dict(base[0], **{key: value})]
        assert verifier_feedback_sha256(mutated) != original


# --- proposal policy: strict measurement gating ---


@pytest.fixture(autouse=True)
def _smoke_policy() -> None:
    os.environ["MATCLAW_PROFILE"] = "smoke"
    yield


def _row(iteration: int, job: int = 1) -> dict:
    return {"iteration": iteration, "job_in_iteration": job, "Ez_V_A": -0.01,
            "temperature_K": 200, "n_flipped": 3, "n_sites": 25,
            "slope_ps_per_site": 0.4, "trajectory_sha256": "a" * 64}


def test_propose_round_1_requires_no_measurements() -> None:
    jobs = propose_round([], 1, 20260403)
    assert len(jobs) == 2


def test_propose_round_1_rejects_any_preceding_measurements() -> None:
    with pytest.raises(ValueError, match="no preceding measurements"):
        propose_round([_row(1)], 1, 20260403)


def test_propose_round_2_requires_complete_round_1() -> None:
    complete = [_row(1, 1), _row(1, 2)]
    jobs = propose_round(complete, 2, 20260403)
    assert len(jobs) == 2
    with pytest.raises(ValueError, match="needs exactly 2 measured jobs in round 1"):
        propose_round([_row(1, 1)], 2, 20260403)  # round 1 incomplete
    with pytest.raises(ValueError, match="requires complete measured rounds"):
        propose_round([], 2, 20260403)  # no measurements at all


def test_propose_round_3_requires_rounds_1_and_2() -> None:
    # Round 3 exists in the paper protocol (7 rounds); smoke only has 2.
    os.environ["MATCLAW_PROFILE"] = "paper"
    try:
        only_round_1 = [_row(1, 1), _row(1, 2)]
        with pytest.raises(ValueError, match="requires complete measured rounds"):
            propose_round(only_round_1, 3, 20260403)
    finally:
        os.environ["MATCLAW_PROFILE"] = "smoke"


# --- adaptive policy: deterministic, seed-free, verifier-mirrored ---


def test_propose_round_is_seed_free_and_deterministic() -> None:
    """The proposal depends only on measured rows; the MD seed never affects it."""
    previous = [_row(1, 1), _row(1, 2)]
    assert propose_round(previous, 2, 20260403) == propose_round(previous, 2, 0) == propose_round(previous, 2, 999999)


def test_round1_jobs_are_the_locked_start_plus_probe() -> None:
    jobs = propose_round([], 1, 20260403)
    assert jobs == [(-0.01, 200), (-0.05, 200)]


def test_adaptive_round2_climbs_the_best_slope() -> None:
    previous = [
        dict(_row(1, 1), slope_ps_per_site=0.4, n_flipped=20),  # best row (first max, valid)
        dict(_row(1, 2), slope_ps_per_site=0.2, n_flipped=20),
    ]
    # cross-climb best.Ez - ez_step AND best.T - t_step, and subdivide T at best.Ez
    assert propose_round(previous, 2, 20260403) == [(-0.04, 160), (-0.01, 160)]


def test_adaptive_round_dedupes_against_tried_points() -> None:
    # Round 1 = (-0.01, 200), (-0.05, 200); round 2 = (-0.04, 200), (-0.01, 160).
    # Round 3 must skip those tried points and pick the next untried cross-climb candidates.
    # Rows are valid (>= 30 % flip) so the best row anchors the climb.
    previous = [
        {"iteration": 1, "job_in_iteration": 1, "Ez_V_A": -0.01, "temperature_K": 200,
         "slope_ps_per_site": 0.4, "n_flipped": 20, "n_sites": 25, "trajectory_sha256": "a" * 64},
        {"iteration": 1, "job_in_iteration": 2, "Ez_V_A": -0.05, "temperature_K": 200,
         "slope_ps_per_site": 0.4, "n_flipped": 20, "n_sites": 25, "trajectory_sha256": "b" * 64},
        {"iteration": 2, "job_in_iteration": 1, "Ez_V_A": -0.04, "temperature_K": 200,
         "slope_ps_per_site": 0.4, "n_flipped": 20, "n_sites": 25, "trajectory_sha256": "c" * 64},
        {"iteration": 2, "job_in_iteration": 2, "Ez_V_A": -0.01, "temperature_K": 160,
         "slope_ps_per_site": 0.4, "n_flipped": 20, "n_sites": 25, "trajectory_sha256": "d" * 64},
    ]
    os.environ["MATCLAW_PROFILE"] = "paper"
    try:
        jobs = propose_round(previous, 3, 20260403)
    finally:
        os.environ["MATCLAW_PROFILE"] = "smoke"
    assert jobs == [(-0.04, 160), (-0.01, 120)]


def test_no_finite_slope_uses_deterministic_grid() -> None:
    previous = [dict(_row(1, 1), slope_ps_per_site=None),
                dict(_row(1, 2), slope_ps_per_site=None)]
    # no valid slope: anchor on the start point and cross-climb from there
    jobs = propose_round(previous, 2, 20260403)
    assert len(jobs) == 2
    assert jobs == [(-0.04, 160), (-0.01, 160)]


def test_verifier_and_solution_propose_identical_jobs() -> None:
    """The verifier's mirrored policy must recompute the exact same proposals."""
    for profile in ("smoke", "paper"):
        policy = policy_for(profile)
        history = [
            {"iteration": 1, "job_in_iteration": 1, "Ez_V_A": -0.01, "temperature_K": 200,
             "n_flipped": 3, "n_sites": 25, "slope_ps_per_site": 0.5, "trajectory_sha256": "a" * 64},
            {"iteration": 1, "job_in_iteration": 2, "Ez_V_A": -0.05, "temperature_K": 200,
             "n_flipped": 4, "n_sites": 25, "slope_ps_per_site": None, "trajectory_sha256": "b" * 64},
            {"iteration": 2, "job_in_iteration": 1, "Ez_V_A": -0.04, "temperature_K": 200,
             "n_flipped": 5, "n_sites": 25, "slope_ps_per_site": 0.6, "trajectory_sha256": "c" * 64},
            {"iteration": 2, "job_in_iteration": 2, "Ez_V_A": -0.01, "temperature_K": 160,
             "n_flipped": 5, "n_sites": 25, "slope_ps_per_site": 0.2, "trajectory_sha256": "d" * 64},
        ]
        for round_index in (1, 2, 3):
            if round_index > int(policy["max_rounds"]):
                continue
            previous = [r for r in history if r["iteration"] < round_index]
            assert propose_round_jobs(previous, round_index, policy) == solution_propose_round_jobs(previous, round_index, policy)


def test_verifier_and_solution_policy_params_agree() -> None:
    """The verifier's embedded strategy must match solution/run_profiles.json."""
    for profile in ("smoke", "paper"):
        solution = solution_policy_for(profile)
        verifier_policy = policy_for(profile)
        for key in ("max_jobs_per_iteration", "max_rounds", "ez_step", "t_step", "round1_probe_ez"):
            assert solution[key] == verifier_policy[key], (profile, key)
        assert solution["start"] == verifier_policy["start"]
        assert list(solution["bounds"]["Ez_V_A"]) == list(verifier_policy["bounds"]["Ez_V_A"])
        assert list(solution["bounds"]["temperature_K"]) == list(verifier_policy["bounds"]["temperature_K"])
        if "early_stop_slope" in solution or "early_stop_band" in solution:
            assert solution.get("early_stop_slope") == verifier_policy.get("early_stop_slope")
            assert list(solution.get("early_stop_band", [])) == list(verifier_policy.get("early_stop_band", []))
        else:
            assert "early_stop_slope" not in verifier_policy
            assert "early_stop_band" not in verifier_policy


def _policy_path(profile: str, slope: float) -> list[dict]:
    """Generate a history that exactly follows the deterministic policy (fixed slope)."""
    policy = policy_for(profile)
    history: list[dict] = []
    for iteration in range(1, int(policy["max_rounds"]) + 1):
        previous = [r for r in history if r["iteration"] < iteration]
        jobs = solution_propose_round_jobs(previous, iteration, policy)
        for job_in_iteration, (ez, temperature) in enumerate(jobs, start=1):
            history.append({
                "iteration": iteration, "job_in_iteration": job_in_iteration,
                "Ez_V_A": ez, "temperature_K": temperature,
                "n_flipped": 5, "n_sites": 10, "slope_ps_per_site": slope,
                "trajectory_sha256": "a" * 64,
            })
    return history


def test_self_generated_path_replays_consistently() -> None:
    """A path generated by the policy must be exactly what the verifier replays."""
    for profile in ("smoke", "paper"):
        policy = policy_for(profile)
        history = _policy_path(profile, 0.4)
        replayed = expected_round_jobs(history, policy)
        assert sorted(replayed) == list(range(1, int(policy["max_rounds"]) + 1))
        for round_index, jobs in replayed.items():
            declared = [(r["Ez_V_A"], r["temperature_K"]) for r in history if r["iteration"] == round_index]
            assert declared == jobs, (profile, round_index)


def test_paper_early_stop_is_mirrored_by_replay() -> None:
    """Once the best measured row is in-band with a sequential slope, the policy stops."""
    policy = policy_for("paper")
    history = _policy_path("paper", 0.4)
    # Force a round-4 best that is inside the band and above the slope threshold.
    history.append({"iteration": 4, "job_in_iteration": 1, "Ez_V_A": -0.16, "temperature_K": 50,
                    "n_flipped": 42, "n_sites": 50, "slope_ps_per_site": 0.5, "trajectory_sha256": "a" * 64})
    history.append({"iteration": 4, "job_in_iteration": 2, "Ez_V_A": -0.16, "temperature_K": 50,
                    "n_flipped": 42, "n_sites": 50, "slope_ps_per_site": 0.3, "trajectory_sha256": "a" * 64})
    replayed = expected_round_jobs(history, policy)
    assert set(replayed) == {1, 2, 3, 4}  # stops after round 4, not the full 7


def test_paper_band_constants_are_sane() -> None:
    assert PAPER_EZ_BAND == (-0.18, -0.14)
    assert PAPER_EZ_BAND[0] <= -0.16 <= PAPER_EZ_BAND[1]


# --- verifier chronology enforcement (host, no trajectories needed) ---


# Points the adaptive smoke policy proposes given constant 0.4 slopes in every round:
# round 1 = locked start + stronger-field probe; round 2 cross-climbs from round-1 job 1
# (best.Ez - ez_step, best.T - t_step) and subdivides T at the base Ez.
_SMOKE_ROUND_POINTS = {
    1: {1: (-0.01, 200), 2: (-0.05, 200)},
    2: {1: (-0.04, 160), 2: (-0.01, 160)},
}


def _smoke_history(gap: bool = False, digests: bool = True) -> list[dict]:
    rows: list[dict] = []
    for iteration in (1, 2):
        # Both jobs in a round share the decision recorded before the round's
        # trajectories: the digest of every *preceding* measured job.
        round_digest = verifier_feedback_sha256([r for r in rows if r["iteration"] < iteration])
        for job in (1, 2):
            ez, temperature = _SMOKE_ROUND_POINTS[iteration][job]
            row = {
                "iteration": iteration, "job_in_iteration": job,
                "Ez_V_A": ez, "temperature_K": temperature,
                "n_flipped": 5, "n_sites": 10, "slope_ps_per_site": 0.4,
                "trajectory_sha256": f"{iteration}{job}" + "a" * 62,
                "trajectory": f"runs/iter{iteration:02d}_job{job:02d}.traj",
                "steps": 20,
                "decision_basis": ("locked start point and paired stronger-field probe"
                                   if iteration == 1 else
                                   "preceding measurements: best slope=0.4, flipped=5/10; continue"),
                "decision_input_sha256": round_digest,
                "decision_made_at": "2026-08-01T00:00:01Z" if iteration == 1 else "2026-08-01T00:00:04Z",
                "preceding_job_count": 0 if iteration == 1 else 2,
                "completed_at": "2026-08-01T00:00:03Z" if iteration == 1 else "2026-08-01T00:00:05Z",
            }
            if gap and iteration == 2 and job == 1:
                row["iteration"] = 3
            rows.append(row)
    if not digests:
        for row in rows:
            if row["iteration"] > 1:
                row.pop("decision_input_sha256", None)
    return rows


def _synthetic_result(history: list[dict]) -> dict:
    return {
        "schema_version": "1.0", "case": "033", "profile": "smoke", "formal_result": False,
        "search_mode": "smoke", "supercell": [1, 3, 1],
        "start": START, "bounds": {"Ez_V_A": [-0.3, 0.0], "temperature_K": [0, 250]},
        "max_jobs_per_iteration": 2, "field_charges_e": CHARGES_E,
        "field_physics": {"force": "q_i*E", "energy": "-sum(q_i*r_i.E)"},
        "history": history, "best": dict(history[-1]),
        "best_trajectory_sha256": "b" * 64,
        "inputs": {"structure_sha256": "c1fac0de0180624d818886602aa33721f708c757900fde24581f4e723b036180",
                   "model_sha256": "a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d"},
    }


def _errors_for(history: list[dict]) -> list[str]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "result.json").write_text(json.dumps(_synthetic_result(history)))
        return verify(Path(tmp), "smoke")["errors"]


def test_synthetic_valid_chain_passes_chronology() -> None:
    errors = _errors_for(_smoke_history())
    for marker in ("decision input digest mismatch", "rounds are not contiguous",
                   "predates completed", "preceding_job_count mismatch",
                   "missing a parseable decision_made_at"):
        assert not any(marker in error for error in errors), errors


def test_synthetic_precomputed_history_digest_rejected() -> None:
    errors = _errors_for(_smoke_history(digests=False))
    assert any("decision input digest mismatch" in error for error in errors)


def test_synthetic_round_gap_rejected() -> None:
    errors = _errors_for(_smoke_history(gap=True))
    assert any("rounds are not contiguous" in error for error in errors)


def test_synthetic_round2_garbage_digest_rejected() -> None:
    history = _smoke_history()
    for row in history:
        if row["iteration"] > 1:
            row["decision_input_sha256"] = "0" * 64
    errors = _errors_for(history)
    assert any("decision input digest mismatch" in error for error in errors)


def test_synthetic_decision_timestamp_before_prior_completion_rejected() -> None:
    history = _smoke_history()
    for row in history:
        if row["iteration"] > 1:
            row["decision_made_at"] = "1970-01-01T00:00:00Z"
    errors = _errors_for(history)
    assert any("predates completed" in error for error in errors)


def test_synthetic_missing_decision_timestamp_rejected() -> None:
    history = _smoke_history()
    for row in history:
        if row["iteration"] > 1:
            row.pop("decision_made_at", None)
    errors = _errors_for(history)
    assert any("missing a parseable decision_made_at" in error for error in errors)


# --- adaptive-policy data-dependency and band gates (host, no trajectories needed) ---


def _paper_synthetic_result(best_ez: float) -> dict:
    """Minimal paper result whose only meaningful property is the declared best Ez."""
    return {
        "schema_version": "1.0", "case": "033", "profile": "paper", "formal_result": True,
        "search_mode": "sequential-adaptive-search", "supercell": [1, 25, 1],
        "start": {"Ez_V_A": -0.01, "temperature_K": 200},
        "bounds": {"Ez_V_A": [-0.3, 0.0], "temperature_K": [0, 250]},
        "max_jobs_per_iteration": 2,
        "field_charges_e": CHARGES_E,
        "field_physics": {"force": "q_i*E", "energy": "-sum(q_i*r_i.E)"},
        "history": [],
        "best": {"Ez_V_A": best_ez, "temperature_K": 50, "n_flipped": 42, "n_sites": 50,
                 "slope_ps_per_site": 0.321, "sequential_propagation": True},
        "best_trajectory_sha256": "b" * 64,
        "inputs": {"structure_sha256": "c1fac0de0180624d818886602aa33721f708c757900fde24581f4e723b036180",
                   "model_sha256": "a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d"},
    }


def _paper_errors_for(best_ez: float) -> list[str]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "result.json").write_text(json.dumps(_paper_synthetic_result(best_ez)))
        return verify(Path(tmp), "paper")["errors"]


def test_paper_best_outside_literature_band_is_rejected() -> None:
    """The old diagnostic best Ez=-0.13 is outside [-0.18, -0.14] and must fail the band."""
    errors = _paper_errors_for(-0.13)
    assert any("best Ez outside literature band" in error for error in errors)


def test_paper_best_inside_literature_band_passes_band_gate() -> None:
    """An in-band best Ez does not produce the band error."""
    errors = _paper_errors_for(-0.16)
    assert not any("best Ez outside literature band" in error for error in errors)


def test_synthetic_preset_path_is_rejected_by_adaptive_policy() -> None:
    """A fixed literature-answer round-2 that ignores the measured history is rejected."""
    history = _smoke_history()
    for row in history:
        if row["iteration"] == 2:
            row["Ez_V_A"] = -0.16
            row["temperature_K"] = 50
    errors = _errors_for(history)
    assert any("does not match the adaptive policy" in error for error in errors)


def test_synthetic_missing_preceding_job_count_rejected() -> None:
    history = _smoke_history()
    for row in history:
        if row["iteration"] > 1:
            row.pop("preceding_job_count", None)
    errors = _errors_for(history)
    assert any("preceding_job_count mismatch" in error for error in errors)


def test_synthetic_missing_full_chronology_chain_rejected() -> None:
    history = _smoke_history()
    for row in history:
        if row["iteration"] > 1:
            row.pop("decision_input_sha256", None)
            row.pop("decision_made_at", None)
            row.pop("preceding_job_count", None)
    errors = _errors_for(history)
    assert any(
        "decision input digest mismatch" in error
        or "missing a parseable decision_made_at" in error
        or "preceding_job_count mismatch" in error
        for error in errors
    )


def test_synthetic_adaptive_path_rounds_are_accepted() -> None:
    """A policy-generated smoke path passes the replay (no adaptive-policy errors)."""
    errors = _errors_for(_smoke_history())
    assert not any("does not match the adaptive policy" in error for error in errors)
