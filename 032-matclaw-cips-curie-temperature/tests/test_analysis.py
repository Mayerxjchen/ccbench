"""Host unit tests for Case 032's analysis, protocol, and resume logic.

The analysis and estimator tests exercise the single hidden verifier
implementation. The protocol and resume tests exercise ``solution/curie_utils.py``
— trajectory contracts, atomic checkpointing, and per-trajectory completion
verification — with numpy and the standard library only, so they run on the host
without deepmd. (The container's ``test.sh`` runs only ``test_outputs.py``; this
file is dev-grade.)
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest
from ase import Atoms

_VENV_SP = Path("/opt/matclaw/lib/python3.11/site-packages")
if _VENV_SP.is_dir():
    sys.path.insert(0, str(_VENV_SP))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verifier import (  # noqa: E402
    EXPECTED_ATOMS,
    EXPECTED_MODEL,
    EXPECTED_STRUCTURE,
    PROFILES,
    SOURCE_TC_K,
    SOURCE_TC_TOLERANCE_K,
    analyze_eta,
    estimate_curie_temperature,
    frame_order_parameter_A,
    unwrap_cluster,
)

SOLUTION = Path(__file__).resolve().parents[1] / "solution"
sys.path.insert(0, str(SOLUTION))
from curie_utils import (  # noqa: E402
    STEPS_PER_PS,
    TIMESTEP_FS,
    checkpoint,
    expected_frames,
    load_checkpoint,
    load_completed_temperature,
    record_artifact,
    sha256,
    trajectory_contract,
)


class FakeAtoms:
    def __init__(self, positions):
        self.positions = np.asarray(positions, dtype=float)

    def __len__(self):
        return len(self.positions)


PAPER = PROFILES["paper"]
SMOKE = PROFILES["smoke"]


def _paper_profile(**overrides):
    return {
        "coarse_ps": 60.0,
        "near_transition_ps": 100.0,
        "pilot_ps": 100.0,
        "near_transition_temperatures_K": [275, 300, 325, 350, 375, 400],
        **overrides,
    }


# --- existing analysis tests (single hidden verifier implementation) ---


def test_order_parameter_unwraps_a_monolayer_across_periodic_z() -> None:
    atoms = Atoms(
        symbols=["Cu", "S", "S"],
        scaled_positions=[[0, 0, 0.04], [0, 0, 0.98], [0, 0, 0.06]],
        cell=[5.0, 5.0, 10.0],
        pbc=True,
    )
    assert frame_order_parameter_A(atoms) == pytest.approx(0.2)


def test_analyze_eta_uses_equilibrium_half_and_absolute_order_parameter() -> None:
    eta = np.array([3.0, 2.0, 1.0, -1.0, 1.0, -1.0])
    result = analyze_eta(eta, frame_dt_ps=0.04)
    assert result["used_frames"] == 3
    assert result["mean_eta_A"] == pytest.approx(-1.0 / 3.0)
    assert result["mean_abs_eta_A"] == pytest.approx(1.0)
    assert result["sign_changes_eq"] == 2


def test_published_curve_reproduces_official_curie_estimators() -> None:
    temperatures = np.array([100, 150, 200, 250, 275, 300, 325, 350, 375, 400, 450, 500, 600])
    q = np.array([1.254077, 1.244562, 1.139428, 0.953123, 0.200339, 0.156477,
                  0.122752, 0.110472, 0.110259, 0.152330, 0.122421, 0.223979, 0.099090])
    result = estimate_curie_temperature(temperatures, q)
    assert result["half_height_K"] == pytest.approx(260.1, abs=0.1)
    assert result["piecewise_breakpoint_K"] == pytest.approx(262.5, abs=0.1)
    assert result["Tc_K"] == pytest.approx(261.3, abs=0.1)
    assert result["Tc_uncertainty_K"] == pytest.approx(10.0)


def test_curie_estimator_rejects_a_grid_that_does_not_bracket_transition() -> None:
    with pytest.raises(ValueError, match="bracket"):
        estimate_curie_temperature(
            np.array([100.0, 150.0, 200.0]), np.array([1.2, 1.18, 1.15])
        )


# --- locked contract: exact grid, atom count, and source tolerance ---


def test_paper_grid_and_atom_count_are_exactly_locked() -> None:
    assert PAPER["temperatures_K"] == [100, 150, 200, 250, 275, 300, 325, 350, 375, 400, 450, 500, 600]
    assert PAPER["near_transition_temperatures_K"] == [275, 300, 325, 350, 375, 400]
    assert EXPECTED_ATOMS["paper"] == 360
    assert EXPECTED_ATOMS["smoke"] == 40


def test_published_tc_is_within_source_tolerance() -> None:
    q = np.array([1.254077, 1.244562, 1.139428, 0.953123, 0.200339, 0.156477,
                  0.122752, 0.110472, 0.110259, 0.152330, 0.122421, 0.223979, 0.099090])
    estimate = estimate_curie_temperature(np.asarray(PAPER["temperatures_K"]), q)
    assert abs(estimate["Tc_K"] - SOURCE_TC_K) <= SOURCE_TC_TOLERANCE_K


def test_locked_structure_and_model_hashes() -> None:
    assert len(EXPECTED_STRUCTURE) == 64 and len(EXPECTED_MODEL) == 64


# --- exact protocol: trajectory contracts and frame counts ---


def test_expected_frames_matches_protocol() -> None:
    assert expected_frames(0) == 1
    assert expected_frames(20) == 2
    assert expected_frames(40) == 3
    assert expected_frames(30000) == 1501
    assert expected_frames(50000) == 2501


def test_trajectory_contract_pilot_350k() -> None:
    contract = trajectory_contract(_paper_profile(), 350, pilot=True)
    assert contract["stem"] == "pilot_350K"
    assert contract["steps"] == int(100.0 * STEPS_PER_PS)
    assert contract["expected_frames"] == expected_frames(contract["steps"])
    assert contract["partial"] == "md/pilot_350K.partial.traj"
    assert contract["final"] == "md/pilot_350K.traj"
    assert contract["sidecar"] == "md/pilot_350K.json"


def test_trajectory_contract_production_coarse_vs_near_transition() -> None:
    coarse = trajectory_contract(_paper_profile(), 200, pilot=False)
    assert coarse["steps"] == int(60.0 * STEPS_PER_PS)
    assert coarse["expected_frames"] == 1501
    near = trajectory_contract(_paper_profile(), 350, pilot=False)
    assert near["steps"] == int(100.0 * STEPS_PER_PS)
    assert near["expected_frames"] == 2501


def test_trajectory_contract_smoke_uses_md_steps() -> None:
    contract = trajectory_contract({"md_steps": 40}, 200, pilot=False)
    assert contract["steps"] == 40
    assert contract["expected_frames"] == 3


# --- atomic checkpoint / resume identity ---


def _clean_dir(prefix: str) -> Path:
    path = Path(f"/tmp/matclaw-032-{prefix}-{np.random.randint(0, 2**31)}")
    path.mkdir(exist_ok=True)
    return path


def test_checkpoint_writes_atomically_and_leaves_no_tmp() -> None:
    out = _clean_dir("checkpoint")
    try:
        checkpoint(out, {"profile": "smoke", "artifacts": {}})
        assert (out / "checkpoint.json").is_file()
        assert not (out / "checkpoint.json.tmp").exists()
        assert load_checkpoint(out, {"profile": "smoke"}) == {"profile": "smoke", "artifacts": {}}
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


def test_load_checkpoint_absent_returns_none() -> None:
    out = _clean_dir("absent")
    try:
        assert load_checkpoint(out, {"profile": "smoke"}) is None
    finally:
        out.rmdir()


def test_load_checkpoint_identity_mismatch_raises() -> None:
    out = _clean_dir("identity")
    try:
        checkpoint(out, {"profile": "paper", "seed": 1, "artifacts": {}})
        with pytest.raises(RuntimeError, match="identity mismatch"):
            load_checkpoint(out, {"profile": "paper", "seed": 2})
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


def test_load_checkpoint_artifact_mismatch_raises() -> None:
    out = _clean_dir("artifact")
    try:
        artifact = out / "md" / "production_200K.traj"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"original")
        state = {"profile": "paper", "seed": 1, "artifacts": {}}
        record_artifact(state, out, artifact)
        checkpoint(out, state)
        artifact.write_bytes(b"tampered")  # tamper after commit
        with pytest.raises(RuntimeError, match="artifact mismatch"):
            load_checkpoint(out, {"profile": "paper", "seed": 1})
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


# --- per-trajectory completion verification ---


def _write_completed_pair(directory: Path, expected: dict, frames, stem: str = "production_200K"):
    import json as _json

    final = directory / "md" / f"{stem}.traj"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"trajectory-bytes")
    record = {"temperature_K": expected["temperature_K"], "steps": expected["steps"],
              "seed": expected["seed"], "timestep_fs": expected["timestep_fs"],
              "atom_count": expected["atom_count"], "frames": expected["expected_frames"],
              "sha256": sha256(final), "finite": True}
    (directory / "md" / f"{stem}.json").write_text(_json.dumps(record, sort_keys=True))
    return final


def test_load_completed_temperature_valid_pair_returns_record() -> None:
    out = _clean_dir("valid")
    expected = {"temperature_K": 200, "steps": 40, "expected_frames": 3,
                "atom_count": 2, "seed": 7, "timestep_fs": 2.0}
    frames = [FakeAtoms([[0.0, 0.0, 0.1], [0.0, 0.0, 0.9]]),
              FakeAtoms([[0.0, 0.0, 0.1], [0.0, 0.0, 0.9]]),
              FakeAtoms([[0.0, 0.0, 0.1], [0.0, 0.0, 0.9]])]
    try:
        final = _write_completed_pair(out, expected, frames)
        record = load_completed_temperature(final, expected, frames)
        assert record is not None
        assert record["temperature_K"] == 200
        assert record["sha256"] == sha256(final)
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


def test_load_completed_temperature_tampered_trajectory_is_none() -> None:
    out = _clean_dir("tamper")
    expected = {"temperature_K": 200, "steps": 40, "expected_frames": 3,
                "atom_count": 2, "seed": 7, "timestep_fs": 2.0}
    frames = [FakeAtoms([[0.0, 0.0, 0.1], [0.0, 0.0, 0.9]])] * 3
    try:
        final = _write_completed_pair(out, expected, frames)
        final.write_bytes(b"tampered-bytes")
        assert load_completed_temperature(final, expected, frames) is None
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


def test_load_completed_temperature_partial_only_is_none() -> None:
    out = _clean_dir("partial")
    expected = {"temperature_K": 200, "steps": 40, "expected_frames": 3,
                "atom_count": 2, "seed": 7, "timestep_fs": 2.0}
    try:
        partial = out / "md" / "production_200K.partial.traj"
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b"partial")
        assert load_completed_temperature(out / "md" / "production_200K.traj",
                                          expected, []) is None
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


def test_load_completed_temperature_frame_count_mismatch_is_none() -> None:
    out = _clean_dir("framecount")
    expected = {"temperature_K": 200, "steps": 40, "expected_frames": 3,
                "atom_count": 2, "seed": 7, "timestep_fs": 2.0}
    frames = [FakeAtoms([[0.0, 0.0, 0.1], [0.0, 0.0, 0.9]])] * 3
    try:
        final = _write_completed_pair(out, expected, frames)
        # Delivered trajectory has 2 frames, contract expects 3.
        assert load_completed_temperature(final, expected, frames[:2]) is None
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


def test_load_completed_temperature_seed_mismatch_is_none() -> None:
    out = _clean_dir("seed")
    expected = {"temperature_K": 200, "steps": 40, "expected_frames": 3,
                "atom_count": 2, "seed": 7, "timestep_fs": 2.0}
    frames = [FakeAtoms([[0.0, 0.0, 0.1], [0.0, 0.0, 0.9]])] * 3
    try:
        final = _write_completed_pair(out, expected, frames)
        wrong = dict(expected, seed=8)
        assert load_completed_temperature(final, wrong, frames) is None
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


def test_load_completed_temperature_nonfinite_positions_is_none() -> None:
    out = _clean_dir("nonfinite")
    expected = {"temperature_K": 200, "steps": 40, "expected_frames": 3,
                "atom_count": 2, "seed": 7, "timestep_fs": 2.0}
    frames = [FakeAtoms([[0.0, 0.0, 0.1], [0.0, 0.0, 0.9]])] * 3
    try:
        final = _write_completed_pair(out, expected, frames)
        bad = [FakeAtoms([[0.0, 0.0, np.nan], [0.0, 0.0, 0.9]])] * 3
        assert load_completed_temperature(final, expected, bad) is None
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


def test_load_completed_temperature_atom_count_mismatch_is_none() -> None:
    out = _clean_dir("atoms")
    expected = {"temperature_K": 200, "steps": 40, "expected_frames": 3,
                "atom_count": 2, "seed": 7, "timestep_fs": 2.0}
    frames = [FakeAtoms([[0.0, 0.0, 0.1], [0.0, 0.0, 0.9]])] * 3
    try:
        final = _write_completed_pair(out, expected, frames)
        wrong = dict(expected, atom_count=4)
        assert load_completed_temperature(final, wrong, frames) is None
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)
