"""Self-contained hidden evaluator for Case 032 Curie-temperature MD.

Security model: inspection is limited to the graded submission directory
(default /app). Profile constants and identity hashes are embedded; every
scientific claim is recomputed from the delivered raw trajectories. Nothing is
read from public/ or reference/ at runtime, so the same file grades both
in-container (/tests) and via the reference wrapper.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from ase.io.trajectory import Trajectory

EXPECTED_STRUCTURE = "b9e3b0c4470274d5e3e1ce19e7c8834bda323e50483eef9791b1e744bbd629de"
EXPECTED_MODEL = "a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d"
FRAME_DT_PS = 0.04
FRAME_INTERVAL_STEPS = 20
TIMESTEP_FS = 2.0
STEPS_PER_PS = 500.0
SOURCE_TC_K = 261.3
SOURCE_TC_TOLERANCE_K = 10.0

PROFILES = {
    "smoke": {"formal_result": False, "supercell": [2, 2, 1],
              "temperatures_K": [200, 350, 500], "md_steps": 40},
    "paper": {"formal_result": True, "supercell": [6, 6, 1],
              "temperatures_K": [100, 150, 200, 250, 275, 300, 325, 350, 375, 400, 450, 500, 600],
              "coarse_temperatures_K": [100, 150, 200, 250, 450, 500, 600],
              "near_transition_temperatures_K": [275, 300, 325, 350, 375, 400],
              "pilot_temperature_K": 350, "pilot_ps": 100.0,
              "coarse_ps": 60.0, "near_transition_ps": 100.0},
}
EXPECTED_ATOMS = {name: 10 * int(np.prod(profile["supercell"])) for name, profile in PROFILES.items()}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _close(actual: float, expected: float, tolerance: float = 1e-8) -> bool:
    return bool(np.isclose(actual, expected, rtol=1e-7, atol=tolerance))


def unwrap_cluster(z_fractional: np.ndarray) -> np.ndarray:
    z = np.mod(np.asarray(z_fractional, dtype=float), 1.0)
    if z.ndim != 1 or z.size == 0:
        raise ValueError("fractional z coordinates must be a non-empty vector")
    ordered = np.sort(z)
    gaps = np.diff(np.concatenate((ordered, ordered[:1] + 1.0)))
    origin = ordered[(int(np.argmax(gaps)) + 1) % ordered.size]
    return np.mod(z - origin, 1.0) + origin


def frame_order_parameter_A(atoms: Any) -> float:
    symbols = np.asarray(atoms.get_chemical_symbols())
    cu = symbols == "Cu"
    sulfur = symbols == "S"
    if not np.any(cu) or not np.any(sulfur):
        raise ValueError("frame must contain both Cu and S atoms")
    z = unwrap_cluster(atoms.get_scaled_positions(wrap=True)[:, 2])
    c_length = float(atoms.cell.lengths()[2])
    return c_length * (float(np.mean(z[cu])) - float(np.mean(z[sulfur])))


def _block_sem(values: np.ndarray) -> float:
    if values.size < 2:
        return 0.0
    n_blocks = min(10, max(2, values.size // 25))
    blocks = [block for block in np.array_split(values, n_blocks) if block.size]
    means = np.asarray([np.mean(block) for block in blocks], dtype=float)
    if means.size < 2:
        return float(np.std(values, ddof=1) / np.sqrt(values.size))
    return float(np.std(means, ddof=1) / np.sqrt(means.size))


def analyze_eta(eta: np.ndarray, frame_dt_ps: float = FRAME_DT_PS) -> dict[str, float | int | bool]:
    values = np.asarray(eta, dtype=float)
    if values.ndim != 1 or values.size < 3 or not np.all(np.isfinite(values)):
        raise ValueError("eta must contain at least three finite samples")
    if frame_dt_ps <= 0:
        raise ValueError("frame_dt_ps must be positive")
    equilibrium = values[values.size // 2:]
    abs_equilibrium = np.abs(equilibrium)
    signs = np.sign(equilibrium)
    nonzero_signs = signs[signs != 0]
    sign_changes = int(np.sum(nonzero_signs[1:] != nonzero_signs[:-1]))
    third_quarter = values[values.size // 2: 3 * values.size // 4]
    fourth_quarter = values[3 * values.size // 4:]
    quarter_drift = abs(float(np.mean(third_quarter)) - float(np.mean(fourth_quarter)))
    mean_eta = float(np.mean(equilibrium))
    sem_eta = _block_sem(equilibrium)
    convergence_limit = max(0.05 * abs(mean_eta), 2.0 * sem_eta, 0.02)
    return {
        "total_frames": int(values.size),
        "used_frames": int(equilibrium.size),
        "equilibrated_time_ps": float(equilibrium.size * frame_dt_ps),
        "mean_eta_A": mean_eta,
        "mean_abs_eta_A": float(np.mean(abs_equilibrium)),
        "block_sem_abs_eta_A": _block_sem(abs_equilibrium),
        "sign_changes_eq": sign_changes,
        "quarter_drift_A": quarter_drift,
        "convergence_limit_A": convergence_limit,
        "converged": bool(quarter_drift < convergence_limit),
    }


def estimate_curie_temperature(temperatures_K: np.ndarray, order_parameters_A: np.ndarray) -> dict[str, float | int]:
    temperatures = np.asarray(temperatures_K, dtype=float)
    q = np.asarray(order_parameters_A, dtype=float)
    if temperatures.ndim != 1 or q.ndim != 1 or temperatures.size != q.size:
        raise ValueError("temperature and order-parameter arrays must have equal length")
    if temperatures.size < 7:
        raise ValueError("temperature grid does not bracket the transition")
    if not np.all(np.isfinite(temperatures)) or not np.all(np.isfinite(q)):
        raise ValueError("temperature curve must be finite")
    order = np.argsort(temperatures)
    temperatures, q = temperatures[order], q[order]
    if np.any(np.diff(temperatures) <= 0):
        raise ValueError("temperature grid must be strictly increasing")
    q_low = float(np.mean(q[:4]))
    q_high = float(np.mean(q[-3:]))
    if q_low <= q_high or q_low - q_high < 0.1:
        raise ValueError("temperature grid does not bracket a decreasing transition")
    half_level = 0.5 * (q_low + q_high)
    crossings = np.flatnonzero((q[:-1] - half_level) * (q[1:] - half_level) <= 0)
    if crossings.size == 0:
        raise ValueError("temperature grid does not bracket the half-height transition")
    crossing = int(crossings[0])
    q0, q1 = q[crossing], q[crossing + 1]
    half_height = float(np.mean(temperatures[crossing: crossing + 2])) if q0 == q1 else float(
        temperatures[crossing] + (half_level - q0) * (temperatures[crossing + 1] - temperatures[crossing]) / (q1 - q0)
    )
    candidates: list[tuple[float, int]] = []
    for split in range(2, temperatures.size - 2):
        left_fit = np.polyval(np.polyfit(temperatures[: split + 1], q[: split + 1], 1), temperatures[: split + 1])
        right_fit = np.polyval(np.polyfit(temperatures[split + 1:], q[split + 1:], 1), temperatures[split + 1:])
        sse = float(np.sum((q[: split + 1] - left_fit) ** 2) + np.sum((q[split + 1:] - right_fit) ** 2))
        candidates.append((sse, split))
    _, best_split = min(candidates)
    piecewise = float(np.mean(temperatures[best_split: best_split + 2]))
    tc = 0.5 * (half_height + piecewise)
    uncertainty = max(10.0, 0.5 * abs(half_height - piecewise))
    return {"low_plateau_A": q_low, "high_plateau_A": q_high, "half_level_A": half_level,
            "half_height_K": half_height, "piecewise_breakpoint_K": piecewise,
            "piecewise_split_index": int(best_split), "Tc_K": tc, "Tc_uncertainty_K": uncertainty}


def verify(submission: Path, expected_profile: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    result_path = submission / "result.json"
    if not result_path.is_file():
        return {"valid": False, "errors": ["missing result.json"]}
    try:
        claimed = json.loads(result_path.read_text())
    except (ValueError, OSError) as exc:
        return {"valid": False, "errors": [f"invalid result.json: {exc}"]}

    partials = [p.name for p in submission.rglob("*.partial.traj")]
    if partials:
        errors.append(f"partial trajectory files present: {partials}")

    profile_name = claimed.get("profile")
    if profile_name not in PROFILES:
        errors.append("unknown profile")
        return {"valid": False, "errors": errors}
    if expected_profile and profile_name != expected_profile:
        errors.append(f"expected profile {expected_profile}, got {profile_name}")
    profile = PROFILES[profile_name]
    if claimed.get("formal_result") is not bool(profile["formal_result"]):
        errors.append("formal_result does not match the locked profile")
    if claimed.get("timestep_fs") != TIMESTEP_FS:
        errors.append("timestep contract mismatch")
    if claimed.get("frame_interval_steps") != FRAME_INTERVAL_STEPS:
        errors.append("frame-interval contract mismatch")
    if claimed.get("inputs", {}).get("structure_sha256") != EXPECTED_STRUCTURE:
        errors.append("structure identity mismatch")
    if claimed.get("inputs", {}).get("model_sha256") != EXPECTED_MODEL:
        errors.append("teacher model identity mismatch")
    expected_atoms = EXPECTED_ATOMS[profile_name]
    if claimed.get("atom_count") != expected_atoms:
        errors.append("supercell atom count mismatch")

    recomputed_rows: list[dict[str, Any]] = []
    records = claimed.get("trajectories", [])
    if len(records) != len(profile["temperatures_K"]):
        errors.append("temperature/trajectory coverage mismatch")
    record_by_temperature = {record.get("temperature_K"): record for record in records}
    claim_by_temperature = {row.get("temperature_K"): row for row in claimed.get("curve", [])}
    for temperature in profile["temperatures_K"]:
        record = record_by_temperature.get(temperature)
        row_claim = claim_by_temperature.get(temperature)
        if not record or not row_claim:
            errors.append(f"missing production evidence at {temperature} K")
            continue
        relative = Path(str(record.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"unsafe trajectory path at {temperature} K")
            continue
        path = submission / relative
        if not path.is_file():
            errors.append(f"missing trajectory at {temperature} K")
            continue
        if sha256(path) != record.get("sha256"):
            errors.append(f"trajectory hash mismatch at {temperature} K")
            continue
        frames = list(Trajectory(str(path)))
        if len(frames) < int(record.get("steps", 0)) // FRAME_INTERVAL_STEPS + 1:
            errors.append(f"trajectory too short at {temperature} K")
            continue
        if any(len(frame) != expected_atoms for frame in frames):
            errors.append(f"trajectory atom count mismatch at {temperature} K")
            continue
        eta = np.asarray([frame_order_parameter_A(frame) for frame in frames])
        recomputed = {"temperature_K": temperature, **analyze_eta(eta, frame_dt_ps=FRAME_DT_PS)}
        recomputed_rows.append(recomputed)
        for key in ("mean_eta_A", "mean_abs_eta_A"):
            if not _close(float(row_claim.get(key, np.nan)), float(recomputed[key])):
                errors.append(f"reported {key} is not trajectory-derived at {temperature} K")

    estimate: dict[str, Any] = {}
    if profile["formal_result"]:
        pilot = claimed.get("pilot", {})
        pilot_path = submission / "md/pilot_350K.traj"
        if not pilot_path.is_file():
            errors.append("formal profile lacks pilot evidence")
        else:
            pilot_frames = list(Trajectory(str(pilot_path)))
            pilot_steps = int(profile["pilot_ps"] * STEPS_PER_PS)
            if len(pilot_frames) != pilot_steps // FRAME_INTERVAL_STEPS + 1:
                errors.append("pilot trajectory frame count mismatch")
            elif not pilot.get("converged"):
                errors.append("formal profile pilot did not converge")
        if len(recomputed_rows) == len(profile["temperatures_K"]):
            q = np.asarray([row["mean_abs_eta_A"] for row in recomputed_rows])
            try:
                estimate = estimate_curie_temperature(np.asarray(profile["temperatures_K"]), q)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                for key in ("half_height_K", "piecewise_breakpoint_K", "Tc_K", "Tc_uncertainty_K"):
                    if not _close(float(claimed.get("estimate", {}).get(key, np.nan)), float(estimate[key]), 1e-6):
                        errors.append(f"reported {key} is not trajectory-derived")
                if abs(estimate["Tc_K"] - SOURCE_TC_K) > SOURCE_TC_TOLERANCE_K:
                    errors.append("recomputed Tc outside source tolerance")

    required = ("order_parameter.csv", "curie_temperature.png", "report.md")
    for name in required:
        if not (submission / name).is_file():
            errors.append(f"missing {name}")
    return {"valid": not errors, "errors": errors, "profile": profile_name,
            "recomputed_curve": recomputed_rows, "recomputed_estimate": estimate}
