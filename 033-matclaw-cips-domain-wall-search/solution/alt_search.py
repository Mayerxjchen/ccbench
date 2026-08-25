#!/opt/matclaw/bin/python
"""Independent alternative solver for the CIPS domain-wall search (G11 evidence).

Written from scratch — no code shared with ``run_search.py`` except the digest
primitives (``feedback_sha256``), which must be identical to the verifier's by
contract. The same hidden verifier that rejects tampered submissions must accept
this solver's smoke output, so the physics is fixed (``F=q_i E``,
``U=-sum(q_i r_i . E)``, the same Langevin schedule and the same domino metric),
and the search follows the *same* deterministic evidence-adaptive policy, but the
implementation is deliberately different:

* a ring-argmax unwrap helper instead of the sorted-cluster origin,
* an explicit per-site loop for flip detection (vs the vectorized crossing scan),
* a seed scheme offset by ``seed_offset`` and multiplied by 7,
* its own ``propose_alt_round`` adaptive proposer (same behavior as
  ``search_policy.propose_round``, independently coded — never a preset schedule),
* trajectories written directly as ``runs/alt_iterXX_jobN_...traj`` with no
  partial files and no resume checkpoint — a lighter layout that the verifier
  still accepts because every claim is recomputed from delivered trajectories.

Only the ``smoke`` profile is supported (smoke-scale alternative evidence); the
formal paper protocol is intentionally out of scope for this solver.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from ase import units
from ase.calculators.mixing import SumCalculator
from ase.io import read
from ase.io.trajectory import Trajectory
from ase.md.langevin import Langevin
from ase.md.velocitydistribution import MaxwellBoltzmannDistribution, Stationary, ZeroRotation
from deepmd.calculator import DP
from scipy.ndimage import gaussian_filter1d

from field_calculator import CHARGES_E, UniformElectricForce
from search_policy import feedback_sha256

WORKSPACE = Path(os.environ.get("MATCLAW_OUTPUT", "/app")).resolve()
SOLUTION = Path(__file__).resolve().parent
FRAME_INTERVAL_STEPS = 10


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def unwrap_ring(z_fractional: np.ndarray) -> np.ndarray:
    """Unwrap a periodic-z cloud by anchoring at the start of its densest cluster."""
    z = np.mod(np.asarray(z_fractional, dtype=float), 1.0)
    ring = np.concatenate((np.sort(z), np.sort(z)[:1] + 1.0))
    gap_at = int(np.argmax(np.diff(ring)))
    origin = ring[gap_at + 1] if gap_at + 1 < len(ring) else ring[0]
    return np.mod(z - origin, 1.0) + origin


def domino_slope(frames, frame_dt_ps: float = 0.02) -> dict:
    """Same domino metric as the case contract, coded independently."""
    first = frames[0]
    symbols = np.asarray(first.get_chemical_symbols())
    cu = np.flatnonzero(symbols == "Cu")
    cu = cu[np.argsort(first.get_scaled_positions(wrap=True)[cu, 1])]
    others = np.flatnonzero(symbols != "Cu")
    c_length = float(first.cell.lengths()[2])
    displacement = []
    for frame in frames:
        z = unwrap_ring(frame.get_scaled_positions(wrap=True)[:, 2]) * c_length
        displacement.append(z[cu] - float(np.mean(z[others])))
    displacement = np.asarray(displacement)
    smooth = gaussian_filter1d(displacement, sigma=frame_dt_ps / 0.02 * 0.5, axis=0, mode="nearest")
    flip_times = np.full(len(cu), np.nan)
    for site in range(len(cu)):
        for step in range(len(smooth) - 1):
            if smooth[step, site] > 0.0 and smooth[step + 1, site] <= 0.0:
                flip_times[site] = (step + 1) * frame_dt_ps
                break
    distances, delays = [], []
    for distance in range(1, min(10, len(cu) - 1) + 1):
        samples = []
        for i in range(len(cu) - distance):
            left, right = flip_times[i], flip_times[i + distance]
            if np.isfinite(left) and np.isfinite(right):
                samples.append(abs(right - left))
        if samples:
            distances.append(distance)
            delays.append(float(np.mean(samples)))
    slope = float(np.polyfit(distances, delays, 1)[0]) if len(distances) >= 3 else None
    return {"n_sites": int(len(cu)), "n_flipped": int(np.sum(np.isfinite(flip_times))),
            "flip_times_ps": [None if not np.isfinite(x) else float(x) for x in flip_times],
            "distances_site": distances, "mean_abs_delay_ps": delays,
            "slope_ps_per_site": slope,
            "sequential_propagation": bool(slope is not None and slope > 0.3),
            "max_abs_displacement_A": float(np.max(np.abs(displacement - displacement[0])))}


def run_md(structure: Path, model: Path, supercell: list[int], ez: float, temperature: int,
           steps: int, seed: int, path: Path) -> None:
    atoms = read(structure).repeat(supercell)
    atoms.calc = SumCalculator([DP(model=str(model)), UniformElectricForce(CHARGES_E, (0, 0, ez))])
    rng = np.random.default_rng(seed)
    MaxwellBoltzmannDistribution(atoms, temperature_K=temperature, rng=rng)
    Stationary(atoms)
    ZeroRotation(atoms)
    dynamics = Langevin(atoms, 2.0 * units.fs, temperature_K=temperature,
                        friction=0.01 / units.fs, rng=rng)
    trajectory = Trajectory(str(path), "w", atoms)
    dynamics.attach(trajectory.write, interval=FRAME_INTERVAL_STEPS)
    dynamics.run(steps)
    trajectory.close()


# --- independent deterministic adaptive policy (same behavior, different code) ---


def _alt_slope_of(row: dict) -> float:
    slope = row.get("slope_ps_per_site")
    return float(slope) if slope is not None else -1e9


def _alt_clamp(ez: float, temperature: int, policy: dict) -> tuple[float, int]:
    bounds = policy["bounds"]
    ez = min(max(float(ez), float(bounds["Ez_V_A"][0])), float(bounds["Ez_V_A"][1]))
    temperature = int(min(max(int(temperature), float(bounds["temperature_K"][0])),
                          float(bounds["temperature_K"][1])))
    return round(ez, 4), temperature


def propose_alt_round(previous: list[dict], round_index: int, policy: dict) -> list[tuple[float, int]]:
    """Independent adaptive proposal: climb the best measured slope toward more
    negative Ez and lower T, deduped against tried points, with a deterministic
    bounded-grid fallback. Never uses the MD seed."""
    cap = int(policy["max_jobs_per_iteration"])
    max_rounds = int(policy["max_rounds"])
    if not 1 <= round_index <= max_rounds:
        raise SystemExit(f"round {round_index} outside protocol rounds 1..{max_rounds}")
    if round_index == 1:
        if previous:
            raise SystemExit("round 1 is the locked start; no preceding measurements expected")
        start = policy["start"]
        return [(float(start["Ez_V_A"]), int(start["temperature_K"])),
                (float(policy["round1_probe_ez"]), int(start["temperature_K"]))]

    preceding = sorted(previous, key=lambda r: (int(r.get("iteration", -1)), int(r.get("job_in_iteration", 0))))
    preceding_iterations = sorted({int(r.get("iteration", -1)) for r in preceding})
    if preceding_iterations != list(range(1, round_index)):
        raise SystemExit(
            f"round {round_index} requires complete measured rounds 1..{round_index - 1}; "
            f"have iterations {preceding_iterations}"
        )
    for iteration in preceding_iterations:
        if sum(1 for r in preceding if int(r["iteration"]) == iteration) != cap:
            raise SystemExit(f"round {round_index} needs exactly {cap} measured jobs in round {iteration}")

    tried = {(float(r["Ez_V_A"]), int(r["temperature_K"])) for r in preceding}
    best = max(preceding, key=_alt_slope_of)
    ez_step = float(policy["ez_step"])
    t_step = int(policy["t_step"])
    if best.get("slope_ps_per_site") is not None:
        base_ez, base_t = float(best["Ez_V_A"]), int(best["temperature_K"])
    else:
        start = policy["start"]
        base_ez, base_t = float(start["Ez_V_A"]), int(start["temperature_K"])

    jobs: list[tuple[float, int]] = []
    for i in range(1, 9):  # job 1: climb toward more negative Ez at the base temperature
        point = _alt_clamp(base_ez - i * ez_step, base_t, policy)
        if point not in tried and point not in jobs:
            jobs.append(point)
            break
    for i in range(1, 9):  # job 2: subdivide temperature toward low T at the base Ez
        point = _alt_clamp(base_ez, base_t - i * t_step, policy)
        if point not in tried and point not in jobs:
            jobs.append(point)
            break
    if len(jobs) >= cap:
        return jobs

    # deterministic bounded-grid fallback
    bounds = policy["bounds"]
    ez_min, ez_max = float(bounds["Ez_V_A"][0]), float(bounds["Ez_V_A"][1])
    t_min, t_max = float(bounds["temperature_K"][0]), float(bounds["temperature_K"][1])
    ez_values: list[float] = []
    e = float(policy["start"]["Ez_V_A"])
    while e >= ez_min - 1e-9:
        ez_values.append(round(e, 4))
        e -= ez_step
    e = float(policy["start"]["Ez_V_A"]) + ez_step
    while e <= ez_max + 1e-9:
        ez_values.append(round(e, 4))
        e += ez_step
    for ez in ez_values:
        for temperature in range(int(t_min), int(t_max) + 1, t_step):
            point = _alt_clamp(ez, temperature, policy)
            if point in tried or point in jobs:
                continue
            jobs.append(point)
            if len(jobs) >= cap:
                return jobs
    raise SystemExit("unable to produce a full round within the search domain")


def should_stop_alt(history: list[dict], policy: dict) -> bool:
    """Independent mirror of the early-stop rule (band-gated, same as the verifier)."""
    if "early_stop_slope" not in policy or "early_stop_band" not in policy:
        return False
    if not history:
        return False
    best = max(history, key=_alt_slope_of)
    slope = best.get("slope_ps_per_site")
    if slope is None or float(slope) <= float(policy["early_stop_slope"]):
        return False
    band = policy["early_stop_band"]
    return float(band[0]) <= float(best["Ez_V_A"]) <= float(band[1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("smoke", "paper"), default="smoke")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", type=Path, default=Path("/app"))
    args = parser.parse_args()
    if args.profile != "smoke":
        raise SystemExit("alt_search.py only supports the smoke profile (G11 alternative-valid evidence)")
    output = args.output.resolve()
    runs = output / "runs"
    runs.mkdir(parents=True, exist_ok=True)

    setup = json.loads((WORKSPACE / "run_profiles.json").read_text())["smoke"]
    protocol = json.loads((SOLUTION / "run_profiles.json").read_text())["smoke_alt"]
    base_seed = args.seed if args.seed is not None else int(setup["seed"])
    base_seed = base_seed + int(protocol["seed_offset"])
    policy = dict(protocol)  # smoke_alt carries the same strategy params as smoke
    policy.pop("seed_offset", None)
    max_rounds = int(policy["max_rounds"])
    structure, model = WORKSPACE / "cips_monolayer.cif", WORKSPACE / "teacher_model.pb"
    supercell = setup["supercell"]
    atom_count = len(read(structure).repeat(supercell))

    history: list[dict] = []
    run_number = 0
    for iteration in range(1, max_rounds + 1):
        jobs = propose_alt_round(history, iteration, policy)
        decision = {
            "round_index": int(iteration),
            "decision_input_sha256": feedback_sha256(history),
            "decision_made_at": _now_iso(),
            "preceding_job_count": len(history),
            "basis": ("locked start point and paired stronger-field probe"
                      if iteration == 1 else
                      f"preceding measurements: best slope={max((r['slope_ps_per_site'] or -1e9) for r in history)}; "
                      "climbing toward the best measured slope"),
        }
        for ez, temperature in jobs:
            run_number += 1
            seed = base_seed + run_number * 7
            stem = f"alt_iter{iteration:02d}_job{run_number:02d}_E{abs(ez):.3f}_T{temperature}"
            path = runs / f"{stem}.traj"
            run_md(structure, model, supercell, ez, temperature,
                   int(setup["md_steps"]), seed, path)
            frames = list(Trajectory(str(path)))
            if len(frames) != int(setup["md_steps"]) // FRAME_INTERVAL_STEPS + 1:
                raise RuntimeError(f"{path} has {len(frames)} frames, expected {int(setup['md_steps']) // FRAME_INTERVAL_STEPS + 1}")
            if any(len(frame) != atom_count for frame in frames):
                raise RuntimeError(f"{path} atom count mismatch")
            metrics = domino_slope(frames)
            history.append({
                "iteration": int(iteration),
                "job_in_iteration": len([r for r in history if r["iteration"] == iteration]) + 1,
                "Ez_V_A": float(ez), "temperature_K": int(temperature),
                "decision_input_sha256": decision["decision_input_sha256"],
                "decision_made_at": decision["decision_made_at"],
                "preceding_job_count": decision["preceding_job_count"],
                "decision_basis": decision["basis"],
                "completed_at": _now_iso(),
                "trajectory": str(path.relative_to(output)),
                "trajectory_sha256": file_sha256(path),
                "steps": int(setup["md_steps"]),
                **metrics,
            })
        if should_stop_alt(history, policy):
            print(f"round {iteration}: best row in the literature band with sequential slope; stopping")
            break

    best = max(history, key=lambda row: row.get("slope_ps_per_site") or -1e9)
    best_target = output / "best_trajectory.traj"
    best_target.write_bytes((output / best["trajectory"]).read_bytes())
    with (output / "search_history.csv").open("w", newline="") as handle:
        fields = ["iteration", "job_in_iteration", "Ez_V_A", "temperature_K", "n_flipped", "n_sites",
                  "slope_ps_per_site", "sequential_propagation", "decision_basis",
                  "decision_input_sha256", "decision_made_at", "preceding_job_count",
                  "trajectory", "trajectory_sha256", "steps"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(history)
    with (output / "domino_analysis.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["distance_site", "mean_abs_delay_ps"])
        writer.writerows(zip(best["distances_site"], best["mean_abs_delay_ps"]))
    plt.figure(figsize=(6, 4))
    slopes = [np.nan if row["slope_ps_per_site"] is None else row["slope_ps_per_site"] for row in history]
    plt.scatter([row["Ez_V_A"] for row in history], [row["temperature_K"] for row in history], c=slopes, cmap="viridis")
    plt.colorbar(label="domino slope (ps/site)")
    plt.xlabel("Ez (V/angstrom)")
    plt.ylabel("Temperature (K)")
    plt.tight_layout()
    plt.savefig(output / "search_results.png", dpi=180)
    plt.close()

    result = {"schema_version": "1.0", "case": "033", "profile": "smoke",
              "formal_result": False, "search_mode": "smoke",
              "supercell": supercell, "start": {"Ez_V_A": history[0]["Ez_V_A"], "temperature_K": history[0]["temperature_K"]},
              "bounds": {"Ez_V_A": [-0.3, 0.0], "temperature_K": [0, 250]},
              "max_jobs_per_iteration": 2, "field_charges_e": CHARGES_E,
              "field_physics": {"force": "q_i*E", "energy": "-sum(q_i*r_i.E)"},
              "history": history, "best": best,
              "best_trajectory_sha256": file_sha256(best_target),
              "inputs": {"structure_sha256": file_sha256(structure), "model_sha256": file_sha256(model)}}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (output / "report.md").write_text(
        "# CIPS domain-wall search (alternative solver)\n\n"
        f"Profile: `smoke`; supercell: `{supercell}`; atoms: {atom_count}.\n\n"
        "Independent alt_search implementation; smoke execution only, not a formal result.\n"
    )
    print(f"alt run complete: {len(history)} jobs, best slope={best.get('slope_ps_per_site')}")


if __name__ == "__main__":
    main()
