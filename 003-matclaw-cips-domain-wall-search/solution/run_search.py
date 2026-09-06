#!/opt/matclaw/bin/python
"""Execute the bounded MatClaw CIPS electric-field search evidence-adaptively.

Only public/ reaches the graded workspace; the search protocol and the
sequential-refinement policy live in solution/ and are never staged into /app.

The search runs one measured round at a time. For every round after the first, the
decision object (``decision_input_sha256`` = SHA-256 of the canonical JSON summary of
every preceding measured job, ``decision_made_at`` timestamp, and
``preceding_job_count``) is committed atomically to ``checkpoint.json`` *before* the
round's trajectories are started. A restart therefore resumes completed rounds and
completed jobs verbatim and can never re-decide a prior round under the same run
identity. Every job's trajectory is written to a ``*.partial.traj`` file, validated,
and only then atomically renamed to its final name; the science (domino slope etc.) is
always recomputed from the delivered raw trajectory.
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

from domino_metrics import analyze
from field_calculator import CHARGES_E, UniformElectricForce
from search_policy import _slope_of, feedback_sha256, policy_for, propose_round, select_best, should_stop

FRAME_INTERVAL_STEPS = 10


# Public inputs (structure, teacher model, profiles) are staged into the
# graded workspace (/app by default); never read them from this script's own
# directory, which lives in /solution in the container.
WORKSPACE = Path(os.environ.get("MATCLAW_OUTPUT", "/app")).resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_job(structure: Path, model: Path, supercell: list[int], ez: float, temperature: int,
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


def feedback(previous: list[dict]) -> str:
    if not previous:
        return "locked start point and paired stronger-field probe"
    best = max(previous, key=_slope_of)
    return (f"preceding measurements: best slope={best.get('slope_ps_per_site')}, "
            f"flipped={best['n_flipped']}/{best['n_sites']}; climbing toward the best measured slope")


def checkpoint(output: Path, state: dict) -> None:
    tmp = output / "checkpoint.json.tmp"
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(output / "checkpoint.json")


def load_checkpoint(output: Path, identity: dict) -> dict | None:
    path = output / "checkpoint.json"
    if not path.is_file():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    for key in ("profile", "seed", "structure_sha256", "model_sha256"):
        if state.get(key) != identity[key]:
            raise RuntimeError(f"checkpoint identity mismatch on {key}: run is not resumable")
    return state


def flatten_rows(state: dict) -> list[dict]:
    return [row for round_state in state.get("rounds", []) for row in round_state.get("rows", [])]


def validate_completed_rows(output: Path, history: list[dict]) -> None:
    """Fail closed if any recorded completed job's trajectory is missing or tampered."""
    for row in history:
        trajectory = output / row["trajectory"]
        if not trajectory.is_file() or sha256(trajectory) != row.get("trajectory_sha256"):
            raise RuntimeError(f"recorded completed trajectory {row['trajectory']} is missing or tampered")


def run_one_job(structure: Path, model: Path, supercell: list[int], atom_count: int,
                ez: float, temperature: int, steps: int, seed: int, output: Path,
                iteration: int, job_in_iteration: int, run_number: int, decision: dict) -> dict:
    """Run (or resume) one trajectory and return its measured history row."""
    runs = output / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    stem = f"iter{iteration:02d}_job{run_number:02d}_E{abs(ez):.3f}_T{temperature}"
    partial = runs / f"{stem}.partial.traj"
    final = runs / f"{stem}.traj"
    expected_frames = steps // FRAME_INTERVAL_STEPS + 1

    if final.is_file():
        # A restart may resume a completed job: reuse the trajectory only if it parses
        # to the exact expected frame count; otherwise discard and re-run.
        try:
            frames = list(Trajectory(str(final)))
        except Exception:
            frames = []
        if len(frames) == expected_frames:
            print(f"resume: reused completed {final.name}")
        else:
            final.unlink(missing_ok=True)
            frames = []
    if not final.is_file():
        print(f"running job {run_number}: E={ez:.3f} V/A, T={temperature} K, seed {seed}")
        run_job(structure, model, supercell, ez, temperature, steps, seed, partial)
        frames = list(Trajectory(str(partial)))
        if len(frames) != expected_frames:
            raise RuntimeError(f"trajectory {final} has {len(frames)} frames, expected {expected_frames}")
        if any(len(frame) != atom_count for frame in frames):
            raise RuntimeError(f"trajectory {final} atom count mismatch")
        partial.replace(final)  # atomic rename to the production name

    metrics = analyze(frames)
    row = {
        "iteration": int(iteration),
        "job_in_iteration": int(job_in_iteration),
        "Ez_V_A": float(ez),
        "temperature_K": int(temperature),
        "decision_input_sha256": decision["decision_input_sha256"],
        "decision_made_at": decision["decision_made_at"],
        "preceding_job_count": decision["preceding_job_count"],
        "decision_basis": decision["basis"],
        "completed_at": _now_iso(),
        "trajectory": str(final.relative_to(output)),
        "trajectory_sha256": sha256(final),
        "steps": int(steps),
        **metrics,
    }
    print(f"completed {final.name}: slope={metrics['slope_ps_per_site']}, flipped={metrics['n_flipped']}/{metrics['n_sites']}")
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("smoke", "paper"), default="smoke")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", type=Path, default=Path("/app"))
    args = parser.parse_args()
    output = args.output.resolve()
    runs = output / "runs"
    runs.mkdir(parents=True, exist_ok=True)

    # Public inputs (structure, model, run setup) come from the graded workspace;
    # the search strategy (round cap, steps, bounds, early-stop) is the hidden policy
    # that lives in solution/ and is never staged into /app.
    setup = json.loads((WORKSPACE / "run_profiles.json").read_text())[args.profile]
    base_seed = args.seed if args.seed is not None else setup["seed"]
    policy = policy_for(args.profile)
    cap = int(policy["max_jobs_per_iteration"])
    max_rounds = int(policy["max_rounds"])
    structure, model = WORKSPACE / "cips_monolayer.cif", WORKSPACE / "teacher_model.pb"
    atom_count = len(read(structure).repeat(setup["supercell"]))

    identity = {"profile": args.profile, "seed": base_seed,
                "structure_sha256": sha256(structure), "model_sha256": sha256(model)}
    state = load_checkpoint(output, identity) if (output / "checkpoint.json").is_file() else None
    if state is not None and state.get("stage") == "result":
        (output / "result.json").write_text(
            json.dumps(state["result"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("resume: result already complete; nothing to do")
        return
    if state is None:
        state = {**identity, "rounds": [], "stage": "running"}
    history = flatten_rows(state)
    if history:
        validate_completed_rows(output, history)

    # One measured round at a time: decide, commit the decision, then measure. The
    # round cap and the early-stop rule come from the hidden policy, so the path is a
    # deterministic function of the measured history (variable length, never a preset
    # schedule).
    bounds = policy["bounds"]
    for iteration in range(1, max_rounds + 1):
        round_state = next((r for r in state["rounds"] if r["round_index"] == iteration), None)
        if round_state is None:
            decision = {
                "round_index": int(iteration),
                "decision_input_sha256": feedback_sha256(history),
                "decision_made_at": _now_iso(),
                "preceding_job_count": len(history),
                "basis": feedback(history),
            }
            jobs = propose_round(history, iteration, base_seed)
            if len(jobs) > cap:
                raise RuntimeError("profile violates two-job iteration cap")
            for ez, temperature in jobs:
                if not (bounds["Ez_V_A"][0] <= ez <= bounds["Ez_V_A"][1]
                        and bounds["temperature_K"][0] <= temperature <= bounds["temperature_K"][1]):
                    raise RuntimeError("search point outside locked domain")
            round_state = {"round_index": int(iteration), "decision": decision,
                           "proposed_jobs": [[float(ez), int(temperature)] for ez, temperature in jobs],
                           "rows": []}
            state["rounds"].append(round_state)
            checkpoint(output, state)  # decision committed before any trajectory runs
            print(f"round {iteration}: decision_input_sha256={decision['decision_input_sha256'][:12]}…, "
                  f"preceding_job_count={decision['preceding_job_count']}")

        run_number = len(history)
        for job_in_iteration, (ez, temperature) in enumerate(round_state["proposed_jobs"], start=1):
            if any(r["job_in_iteration"] == job_in_iteration for r in round_state["rows"]):
                continue  # resume: this job already completed
            run_number += 1
            row = run_one_job(structure, model, setup["supercell"], atom_count, ez, temperature,
                              setup["md_steps"], base_seed + run_number, output,
                              iteration, job_in_iteration, run_number, round_state["decision"])
            round_state["rows"].append(row)
            history.append(row)
            checkpoint(output, state)  # per-job checkpoint so a restart resumes precisely

        if len(round_state["rows"]) < cap:
            raise RuntimeError(f"round {iteration} not fully measured")
        if should_stop(history, policy):
            print(f"round {iteration}: best row in the literature band with sequential slope; stopping")
            break

    # ---- Result assembly (the science is recomputed from delivered trajectories).
    # The best row is the maximum *valid* domino slope inside the literature bands
    # (Ez/T), falling back to the global max-slope valid row: a row whose Cu flipping
    # is sparse (< 30 %) is a gray-X non-event (per the paper) and can never be the
    # best. Ez is the reproducible physical target; slope is a seed-sensitive quality.
    best = select_best(history, policy)
    best_source = output / best["trajectory"]
    best_target = output / "best_trajectory.traj"
    best_target.write_bytes(best_source.read_bytes())
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

    result = {"schema_version": "1.0", "case": "033", "profile": args.profile,
              "formal_result": bool(setup["formal_result"]),
              "search_mode": "smoke" if args.profile == "smoke" else "sequential-adaptive-search",
              "supercell": setup["supercell"], "start": {"Ez_V_A": history[0]["Ez_V_A"], "temperature_K": history[0]["temperature_K"]},
              "bounds": {"Ez_V_A": [-0.3, 0.0], "temperature_K": [0, 250]},
              "max_jobs_per_iteration": cap, "field_charges_e": CHARGES_E,
              "field_physics": {"force": "q_i*E", "energy": "-sum(q_i*r_i.E)"},
              "history": history, "best": best,
              "best_trajectory_sha256": sha256(best_target),
              "inputs": {"structure_sha256": sha256(structure), "model_sha256": sha256(model)}}
    (output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state.update({"result": result, "stage": "result"})
    checkpoint(output, state)
    print(f"run complete: best slope={best.get('slope_ps_per_site')} at "
          f"E={best['Ez_V_A']:.3f} V/A, T={best['temperature_K']} K")


if __name__ == "__main__":
    main()
