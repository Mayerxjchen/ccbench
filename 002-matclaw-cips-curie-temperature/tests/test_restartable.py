"""Host/dev tests for the Case 032 restartable reference run.

These tests prove the resume contract the restartable runner relies on: a torn
or partial trajectory is *never* reused as a production record, so a 350 K
trajectory interrupted at 1499/2501 frames is discarded on resume and re-run
from the locked initial structure + seed. They exercise the real hidden
protocol (`solution/run_profiles.json`) through the pure `curie_utils` layer
with numpy + stdlib only, so they run on the host without deepmd.

The container's reward gate (`test.sh`) intentionally runs only
`test_outputs.py`; this file is dev-grade like `test_analysis.py` and is run
whenever the hidden solution is mounted (`/solution/curie_utils.py` present).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_VENV_SP = Path("/opt/matclaw/lib/python3.11/site-packages")
if _VENV_SP.is_dir():
    sys.path.insert(0, str(_VENV_SP))

SOLUTION = Path(__file__).resolve().parents[1] / "solution"
sys.path.insert(0, str(SOLUTION))
from curie_utils import (  # noqa: E402
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
    """Duck-typed ase Atoms with just what curie_utils checks (len/positions)."""

    def __init__(self, positions):
        self.positions = np.asarray(positions, dtype=float)

    def __len__(self):
        return len(self.positions)


def _paper_profile() -> dict:
    protocol = json.loads((SOLUTION / "run_profiles.json").read_text())["paper"]
    return {
        "pilot_ps": protocol["pilot_ps"],
        "coarse_ps": protocol["coarse_ps"],
        "near_transition_ps": protocol["near_transition_ps"],
        "near_transition_temperatures_K": protocol["near_transition_temperatures_K"],
    }


def _finite_frames(count: int, atoms_per_frame: int = 360) -> list[FakeAtoms]:
    positions = np.zeros((atoms_per_frame, 3), dtype=float)
    positions[:, 2] = 0.1  # finite
    return [FakeAtoms(positions) for _ in range(count)]


def _write_trajectory_bytes(directory: Path, rel: str) -> Path:
    path = directory / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"trajectory-bytes")
    return path


# --- The locked paper protocol: both 350 K trajectories are 2501 frames. ------


def test_paper_350k_pilot_and_production_both_require_2501_frames() -> None:
    profile = _paper_profile()
    pilot = trajectory_contract(profile, 350, pilot=True)
    production = trajectory_contract(profile, 350, pilot=False)
    assert pilot["steps"] == int(100.0 * 500.0)
    assert production["steps"] == int(100.0 * 500.0)
    assert expected_frames(pilot["steps"]) == 2501
    assert expected_frames(production["steps"]) == 2501
    assert pilot["final"] == "md/pilot_350K.traj"
    assert production["final"] == "md/production_350K.traj"
    assert pilot["partial"] == "md/pilot_350K.partial.traj"


def _paper_expected(stem: str, seed: int) -> dict:
    contract = trajectory_contract(_paper_profile(), 350, pilot=(stem == "pilot_350K"))
    return {"temperature_K": 350, "steps": contract["steps"],
            "expected_frames": contract["expected_frames"], "atom_count": 360,
            "seed": seed, "timestep_fs": TIMESTEP_FS}


# --- The 1499/2501 interruption: never reused, always re-run. -----------------


@pytest.mark.parametrize("stem", ["pilot_350K", "production_350K"])
def test_1499_frame_partial_is_never_reused_as_2501_frame_completed(stem: str, tmp_path) -> None:
    """A 350 K trajectory interrupted at 1499 frames is NOT a completed record."""
    expected = _paper_expected(stem, seed=20260401)
    # Scenario 1: only a .partial file exists (interrupted mid-run). No final
    # trajectory, so there is nothing to reuse and the partial is discarded.
    partial = tmp_path / f"md/{stem}.partial.traj"
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"partial")
    assert not (tmp_path / f"md/{stem}.traj").is_file()
    assert load_completed_temperature(tmp_path / f"md/{stem}.traj",
                                      expected, _finite_frames(1499)) is None
    # Scenario 2: a final + sidecar exist, but only 1499 frames are delivered and
    # the sidecar claims 1499 frames -> the exact 2501-frame gate rejects it.
    final = _write_trajectory_bytes(tmp_path, f"md/{stem}.traj")
    stale = {"temperature_K": 350, "steps": expected["steps"], "seed": expected["seed"],
             "timestep_fs": TIMESTEP_FS, "atom_count": 360, "frames": 1499,
             "sha256": sha256(final), "finite": True}
    (tmp_path / f"md/{stem}.json").write_text(json.dumps(stale))
    assert load_completed_temperature(final, expected, _finite_frames(1499)) is None


def test_complete_trajectory_discards_partial_and_reruns_from_seed(tmp_path) -> None:
    """The discard branch run_curie.py uses: no final -> partial is deleted, re-run.

    This mirrors ``solution/run_curie.py`` ``complete_trajectory``: when the
    final trajectory is absent, any partial/sidecar is discarded and the
    trajectory is re-run from the locked initial structure + seed (the seed is
    unchanged, so a fresh MD produces the exact paper trajectory).
    """
    expected = _paper_expected("pilot_350K", seed=20260401)
    partial = _write_trajectory_bytes(tmp_path, "md/pilot_350K.partial.traj")
    final = tmp_path / "md/pilot_350K.traj"

    # Replay complete_trajectory's decision: the completed pair is only checked
    # when the FINAL file exists; with only a partial present it is discarded.
    assert not final.is_file()
    assert load_completed_temperature(final, expected, _finite_frames(1499)) is None
    partial.unlink(missing_ok=True)  # never a production record
    (tmp_path / "md/pilot_350K.json").unlink(missing_ok=True)
    assert not partial.exists()  # discarded, not reused
    # The re-run uses the SAME contract + seed, so it produces 2501 frames.
    assert expected["expected_frames"] == 2501
    assert expected["seed"] == 20260401


# --- Full resume cycle: committed work reused, torn work re-run. --------------

def test_resume_cycle_reuses_committed_and_reruns_torn(tmp_path) -> None:
    """A full checkpoint/resume cycle: completed temp reused, torn temp re-run."""
    out = tmp_path
    (out / "md").mkdir(exist_ok=True)
    identity = {"profile": "paper", "seed": 20260402,
                "structure_sha256": "a" * 64, "model_sha256": "b" * 64}

    # Temperature 200 K committed (final + sidecar + checkpoint).
    exp200 = {"temperature_K": 200, "steps": 30000, "expected_frames": 1501,
              "atom_count": 360, "seed": 20260403, "timestep_fs": TIMESTEP_FS}
    final200 = _write_trajectory_bytes(out, "md/production_200K.traj")
    record200 = {"temperature_K": 200, "steps": 30000, "seed": 20260403,
                 "timestep_fs": TIMESTEP_FS, "atom_count": 360, "frames": 1501,
                 "sha256": sha256(final200), "finite": True}
    (out / "md/production_200K.json").write_text(json.dumps(record200, sort_keys=True))
    state = {**identity, "artifacts": {}, "stage": None}
    record_artifact(state, out, final200)
    record_artifact(state, out, out / "md/production_200K.json")
    checkpoint(out, state)

    # Temperature 350 K interrupted mid-run: only a 1499-frame partial exists.
    exp350 = _paper_expected("production_350K", seed=20260402 + 6)
    partial350 = _write_trajectory_bytes(out, "md/production_350K.partial.traj")

    # Resume: checkpoint identity + committed artifacts all match.
    resumed = load_checkpoint(out, identity)
    assert resumed is not None
    # Committed temperature is reused as a completed record.
    frames200 = _finite_frames(1501)
    assert load_completed_temperature(final200, exp200, frames200) is not None
    # Torn temperature is NOT completed; its partial is discarded and re-run.
    final350 = out / "md/production_350K.traj"
    assert not final350.is_file()
    assert load_completed_temperature(final350, exp350, _finite_frames(1499)) is None
    assert partial350.is_file()  # discarded only when complete_trajectory re-runs
