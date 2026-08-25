"""Pure, host-testable helpers for Case 032 Curie-temperature MD.

This module deliberately uses only ``numpy`` and the standard library, so the
per-trajectory contract and the atomic resume checks can be unit-tested on the
host without deepmd or ase (the heavy MD loops stay in the scripts that run in
the container). It provides:

* identity: :func:`sha256`
* protocol: :func:`expected_frames`, :func:`trajectory_contract` — the exact
  simulation spec (step count, frame count, partial/final/sidecar names) for a
  pilot or production trajectory
* completion: :func:`load_completed_temperature` — returns a completed record
  only after every identity and integrity check passes, so a partial or torn
  trajectory is never reused as a production record
* run state: :func:`checkpoint`, :func:`load_checkpoint`, :func:`record_artifact`
  — an atomic, hash-verified run state so a paper run is resumable and auditable
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

# MD protocol constants (the paper's simulation contract).
STEPS_PER_PS = 500.0
FRAME_INTERVAL_STEPS = 20
TIMESTEP_FS = 2.0
# Time between stored frames: 2.0 fs * 20 steps = 0.04 ps.
FRAME_DT_PS = TIMESTEP_FS * 1e-3 * FRAME_INTERVAL_STEPS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_frames(steps: int) -> int:
    """Number of frames an MD run of ``steps`` writes at the frame interval."""
    return int(steps) // FRAME_INTERVAL_STEPS + 1


def trajectory_contract(
    profile: dict[str, Any], temperature_K: int, pilot: bool
) -> dict[str, Any]:
    """The exact simulation spec for one trajectory.

    ``pilot=True`` uses the pilot duration and the ``pilot_{T}K`` stem. A
    production temperature is measured with either the coarse or the
    near-transition duration based on the paper's grid split (``md_steps`` is
    used for the non-formal smoke profile). Returns the step count, the exact
    expected frame count, and the partial/final/sidecar paths relative to the
    output workspace.
    """
    temperature_K = int(temperature_K)
    if pilot:
        stem = f"pilot_{temperature_K}K"
        steps = int(profile["pilot_ps"] * STEPS_PER_PS)
    elif profile.get("md_steps") is not None:
        stem = f"production_{temperature_K}K"
        steps = int(profile["md_steps"])
    elif temperature_K in profile.get("near_transition_temperatures_K", []):
        stem = f"production_{temperature_K}K"
        steps = int(profile["near_transition_ps"] * STEPS_PER_PS)
    else:
        stem = f"production_{temperature_K}K"
        steps = int(profile["coarse_ps"] * STEPS_PER_PS)
    return {
        "temperature_K": temperature_K,
        "pilot": bool(pilot),
        "stem": stem,
        "steps": steps,
        "expected_frames": expected_frames(steps),
        "partial": f"md/{stem}.partial.traj",
        "final": f"md/{stem}.traj",
        "sidecar": f"md/{stem}.json",
    }


def load_completed_temperature(
    path: Path, expected: dict[str, Any], frames
) -> dict[str, Any] | None:
    """Return the completed record only when every check passes, else ``None``.

    ``path`` is the *final* ``.traj`` (never a ``.partial.traj``); its sidecar is
    the same stem with ``.json``. ``expected`` carries the exact contract for
    this trajectory (``temperature_K``, ``steps``, ``expected_frames``,
    ``atom_count``, ``seed``, ``timestep_fs``). ``frames`` is the trajectory's
    atoms (duck-typed ase Atoms) already read by the caller.

    All of these must hold before a record is returned: the sidecar exists and
    parses, the recorded identity fields (temperature/steps/seed/timestep) match
    the contract, the recorded sha256 matches the current trajectory bytes, the
    recorded frame count matches the contract, every frame has the expected atom
    count, every position is finite, and the recorded ``finite`` flag is True.
    Any failure returns ``None`` — a torn, tampered, or partial trajectory is
    never reused as a production record.
    """
    sidecar = path.with_suffix(".json")
    if not path.is_file() or not sidecar.is_file():
        return None
    try:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    frame_list = list(frames)
    checks = (
        record.get("temperature_K") == expected["temperature_K"],
        record.get("steps") == expected["steps"],
        record.get("seed") == expected["seed"],
        record.get("timestep_fs") == expected["timestep_fs"],
        record.get("atom_count") == expected["atom_count"],
        record.get("frames") == expected["expected_frames"],
        record.get("sha256") == sha256(path),
        record.get("finite") is True,
        len(frame_list) == expected["expected_frames"],
        all(len(frame) == expected["atom_count"] for frame in frame_list),
        all(bool(np.all(np.isfinite(np.asarray(frame.positions, dtype=float))))
            for frame in frame_list),
    )
    if not all(checks):
        return None
    return record


def checkpoint(output: Path, state: dict[str, Any]) -> None:
    """Atomically persist run state: write ``checkpoint.json.tmp`` then rename.

    ``Path.replace`` is atomic on POSIX, so a crash mid-write can never leave a
    torn ``checkpoint.json`` — a reader sees either the previous complete state
    or the new one.
    """
    output.mkdir(parents=True, exist_ok=True)
    target = output / "checkpoint.json"
    tmp = output / "checkpoint.json.tmp"
    tmp.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(target)


def load_checkpoint(
    output: Path, expected: dict[str, Any]
) -> dict[str, Any] | None:
    """Resume state only when identity and every recorded artifact still match.

    Returns ``None`` when no checkpoint exists. Raises ``RuntimeError`` when the
    checkpoint's identity fields (profile/seed/structure/teacher) or any recorded
    artifact hash no longer match the workspace — an incompatible state is
    **never** reused, so a tampered or partially-overwritten workspace fails
    closed instead of silently resuming from stale artifacts.
    """
    path = output / "checkpoint.json"
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"unreadable checkpoint.json: {exc}") from exc
    for key, value in expected.items():
        if state.get(key) != value:
            raise RuntimeError(f"checkpoint identity mismatch for {key}")
    for rel, digest in state.get("artifacts", {}).items():
        artifact = output / rel
        if not artifact.is_file() or sha256(artifact) != digest:
            raise RuntimeError(f"checkpoint artifact mismatch: {rel}")
    return state


def record_artifact(state: dict[str, Any], output: Path, path: Path) -> None:
    """Record one artifact's hash in ``state["artifacts"]`` (relative to output)."""
    rel = str(path.resolve().relative_to(output.resolve()))
    state.setdefault("artifacts", {})[rel] = sha256(path)
