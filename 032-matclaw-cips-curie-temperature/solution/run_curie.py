#!/opt/matclaw/bin/python
"""Run the CIPS Curie-temperature MD protocol resumably and auditably.

The paper protocol — which temperatures to measure, the coarse/near-transition
split, and the 350 K pilot — lives in the hidden ``solution/run_profiles.json``
and is never staged into ``/app``, so an agent that only sees the graded
workspace cannot replay a precomputed protocol. ``public/run_profiles.json``
(which reaches ``/app``) supplies only the run setup: supercell, seed, and the
formal flag.

Every trajectory is written to a ``*.partial.traj`` file, validated against the
exact expected frame/atom/finiteness contract, then atomically renamed to its
final ``.traj`` name with a ``.json`` sidecar recording its sha256 and run
identity. ``checkpoint.json`` records the run identity (profile, seed, structure
hash, teacher hash) and a hash of every committed artifact; a resume reuses only
completed trajectories whose identity and hashes still match, and never treats a
partial trajectory as a production record.

For the paper profile the 350 K pilot must complete and converge before any
production trajectory, and ``formal_result`` is only set once all 13 production
records exist. The science (eta, Tc) is recomputed from the delivered raw
trajectories on every resume, so the sidecars are integrity records, not the
source of truth.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from ase import units
from ase.io import read
from ase.io.trajectory import Trajectory
from ase.md.langevin import Langevin
from ase.md.velocitydistribution import MaxwellBoltzmannDistribution, Stationary, ZeroRotation
from deepmd.calculator import DP

from curie_utils import (
    FRAME_DT_PS,
    FRAME_INTERVAL_STEPS,
    STEPS_PER_PS,
    TIMESTEP_FS,
    checkpoint,
    load_checkpoint,
    load_completed_temperature,
    record_artifact,
    sha256,
    trajectory_contract,
)

# Public inputs (structure, teacher model, run setup) are staged into the
# graded workspace (/app by default); never read them from this script's own
# directory, which lives in /solution in the container. The protocol grid lives
# in the hidden solution/run_profiles.json next to this script.
WORKSPACE = Path(os.environ.get("MATCLAW_OUTPUT", "/app")).resolve()
SOLUTION = Path(__file__).resolve().parent


def unwrap_cluster(z_fractional: np.ndarray) -> np.ndarray:
    z = np.mod(np.asarray(z_fractional, dtype=float), 1.0)
    if z.ndim != 1 or z.size == 0:
        raise ValueError("fractional z coordinates must be a non-empty vector")
    ordered = np.sort(z)
    gaps = np.diff(np.concatenate((ordered, ordered[:1] + 1.0)))
    origin = ordered[(int(np.argmax(gaps)) + 1) % ordered.size]
    return np.mod(z - origin, 1.0) + origin


def eta_A(atoms) -> float:
    symbols = np.asarray(atoms.get_chemical_symbols())
    cu = symbols == "Cu"
    sulfur = symbols == "S"
    if not np.any(cu) or not np.any(sulfur):
        raise ValueError("frame must contain both Cu and S atoms")
    z = unwrap_cluster(atoms.get_scaled_positions(wrap=True)[:, 2])
    c_length = float(atoms.cell.lengths()[2])
    return c_length * (float(np.mean(z[cu])) - float(np.mean(z[sulfur])))


def analyze(values: np.ndarray) -> dict[str, float | int | bool]:
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or values.size < 3 or not np.all(np.isfinite(values)):
        raise ValueError("eta must contain at least three finite samples")
    equilibrium = values[values.size // 2:]
    n_blocks = min(10, max(2, equilibrium.size // 25))
    block_means = np.asarray(
        [np.mean(block) for block in np.array_split(np.abs(equilibrium), n_blocks) if block.size]
    )
    sem = float(np.std(block_means, ddof=1) / np.sqrt(block_means.size)) if block_means.size > 1 else 0.0
    third_quarter = values[values.size // 2: 3 * values.size // 4]
    fourth_quarter = values[3 * values.size // 4:]
    drift = abs(float(np.mean(third_quarter)) - float(np.mean(fourth_quarter)))
    mean = float(np.mean(equilibrium))
    limit = max(0.05 * abs(mean), 2.0 * sem, 0.02)
    return {"frames": int(values.size), "used_frames": int(equilibrium.size),
            "mean_eta_A": mean, "mean_abs_eta_A": float(np.mean(np.abs(equilibrium))),
            "block_sem_A": sem, "quarter_drift_A": drift,
            "convergence_limit_A": limit, "converged": bool(drift < limit)}


def estimate_tc(temperatures: np.ndarray, q: np.ndarray) -> dict[str, float]:
    q_low, q_high = float(np.mean(q[:4])), float(np.mean(q[-3:]))
    half = (q_low + q_high) / 2
    crossings = np.flatnonzero((q[:-1] - half) * (q[1:] - half) <= 0)
    if not len(crossings):
        raise RuntimeError("production sweep does not bracket the transition")
    i = int(crossings[0])
    half_tc = float(temperatures[i] + (half - q[i]) * (temperatures[i + 1] - temperatures[i]) / (q[i + 1] - q[i]))
    fits = []
    for split in range(2, len(q) - 2):
        lfit = np.polyval(np.polyfit(temperatures[:split + 1], q[:split + 1], 1), temperatures[:split + 1])
        rfit = np.polyval(np.polyfit(temperatures[split + 1:], q[split + 1:], 1), temperatures[split + 1:])
        fits.append((float(np.sum((q[:split + 1] - lfit) ** 2) + np.sum((q[split + 1:] - rfit) ** 2)), split))
    split = min(fits)[1]
    piecewise = float(np.mean(temperatures[split:split + 2]))
    return {"low_plateau_A": q_low, "high_plateau_A": q_high, "half_height_K": half_tc,
            "piecewise_breakpoint_K": piecewise, "Tc_K": (half_tc + piecewise) / 2,
            "Tc_uncertainty_K": max(10.0, abs(half_tc - piecewise) / 2)}


def run_md(atoms, model: Path, temperature: int, steps: int, seed: int, output: Path) -> None:
    atoms = atoms.copy()
    atoms.calc = DP(model=str(model))
    rng = np.random.default_rng(seed)
    MaxwellBoltzmannDistribution(atoms, temperature_K=temperature, rng=rng)
    Stationary(atoms)
    ZeroRotation(atoms)
    dynamics = Langevin(atoms, timestep=TIMESTEP_FS * units.fs, temperature_K=temperature,
                        friction=0.01 / units.fs, rng=rng)
    trajectory = Trajectory(str(output), "w", atoms)
    dynamics.attach(trajectory.write, interval=FRAME_INTERVAL_STEPS)
    dynamics.run(steps)
    trajectory.close()


def complete_trajectory(
    contract: dict, expected: dict, output: Path, base, model: Path,
    temperature: int, seed: int, state: dict,
) -> tuple[dict, dict, bool]:
    """Run or reuse one trajectory and return ``(record, stats, reused)``.

    A completed pair (final ``.traj`` + ``.json`` sidecar) is reused only when
    :func:`load_completed_temperature` passes every check; a torn or partial
    pair is discarded and re-run. The science (``stats``) is always recomputed
    from the delivered raw trajectory, never trusted from a sidecar.
    """
    partial = output / contract["partial"]
    final = output / contract["final"]
    sidecar = output / contract["sidecar"]
    if final.is_file():
        frames = list(Trajectory(str(final)))
        record = load_completed_temperature(final, expected, frames)
        if record is not None:
            print(f"resume: reused completed {contract['final']}")
            stats = analyze(np.asarray([eta_A(frame) for frame in frames]))
            return record, stats, True
        raise RuntimeError(f"existing completed trajectory fails verification: {final}")
    if partial.is_file() or sidecar.is_file():
        # A torn or partial pair is never a production record: discard and re-run.
        partial.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)

    print(f"running MD: {temperature} K, {contract['steps']} steps, seed {seed}")
    run_md(base, model, temperature, contract["steps"], seed, partial)
    frames = list(Trajectory(str(partial)))
    if len(frames) != contract["expected_frames"]:
        raise RuntimeError(f"trajectory {final} has {len(frames)} frames, expected {contract['expected_frames']}")
    if any(len(frame) != expected["atom_count"] for frame in frames):
        raise RuntimeError(f"trajectory {final} atom count mismatch")
    if not all(bool(np.all(np.isfinite(np.asarray(frame.positions, dtype=float)))) for frame in frames):
        raise RuntimeError(f"trajectory {final} contains non-finite positions")
    values = np.asarray([eta_A(frame) for frame in frames])
    if not np.all(np.isfinite(values)):
        raise RuntimeError(f"non-finite order parameter in {partial}")
    stats = analyze(values)
    partial.replace(final)  # atomic rename to the production name
    record = {
        "schema_version": "1.0",
        "temperature_K": int(temperature),
        "pilot": bool(contract["pilot"]),
        "steps": contract["steps"],
        "seed": seed,
        "timestep_fs": TIMESTEP_FS,
        "frame_interval_steps": FRAME_INTERVAL_STEPS,
        "atom_count": expected["atom_count"],
        "frames": len(frames),
        "sha256": sha256(final),
        "finite": True,
    }
    tmp = output / (contract["sidecar"] + ".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    tmp.replace(sidecar)  # atomic sidecar write
    record_artifact(state, output, final)
    record_artifact(state, output, sidecar)
    checkpoint(output, state)
    print(f"completed {contract['final']}: {len(frames)} frames, mean|eta|={stats['mean_abs_eta_A']:.4f}")
    return record, stats, False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("smoke", "paper"), default="smoke")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", type=Path, default=Path("/app"))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "md").mkdir(exist_ok=True)

    setup = json.loads((WORKSPACE / "run_profiles.json").read_text())[args.profile]
    protocol = json.loads((SOLUTION / "run_profiles.json").read_text())[args.profile]
    profile = {**setup, **protocol}
    base_seed = int(args.seed) if args.seed not in (None, "") else int(profile["seed"])
    structure, model = WORKSPACE / "CuInP2S6.cif", WORKSPACE / "teacher_model.pb"
    base = read(structure).repeat(profile["supercell"])
    atom_count = len(base)
    temperatures = profile["temperatures_K"]

    identity = {"profile": args.profile, "seed": base_seed,
                "structure_sha256": sha256(structure),
                "model_sha256": sha256(model)}
    state = load_checkpoint(output, identity) if (output / "checkpoint.json").is_file() else None
    if state is not None and state.get("stage") == "result":
        (output / "result.json").write_text(
            json.dumps(state["result"], indent=2, sort_keys=True) + "\n")
        print("resume: result already complete; nothing to do")
        return
    if state is None:
        state = {**identity, "artifacts": {}, "stage": None}

    # ---- Pilot first: production never starts before a converged 350 K pilot.
    if args.profile == "paper":
        pilot_contract = trajectory_contract(profile, profile["pilot_temperature_K"], pilot=True)
        pilot_seed = base_seed - 1
        pilot_expected = {"temperature_K": profile["pilot_temperature_K"],
                          "steps": pilot_contract["steps"],
                          "expected_frames": pilot_contract["expected_frames"],
                          "atom_count": atom_count, "seed": pilot_seed,
                          "timestep_fs": TIMESTEP_FS}
        pilot_record, pilot_stats, _ = complete_trajectory(
            pilot_contract, pilot_expected, output, base, model,
            profile["pilot_temperature_K"], pilot_seed, state)
        if not pilot_stats["converged"]:
            raise RuntimeError("pilot trajectory did not converge; production requires a converged pilot")
        pilot = {"converged": True, "seed": pilot_seed, "path": pilot_contract["final"],
                 "sha256": pilot_record["sha256"], "steps": pilot_record["steps"],
                 "frames": pilot_record["frames"], **pilot_stats}
    else:
        pilot = {"converged": False, "reason": "smoke profile is non-formal"}

    # ---- Production grid: reuse completed pairs, complete the rest.
    rows: list[dict] = []
    trajectory_records: list[dict] = []
    for index, temperature in enumerate(temperatures):
        contract = trajectory_contract(profile, temperature, pilot=False)
        seed = base_seed + index
        expected = {"temperature_K": temperature, "steps": contract["steps"],
                    "expected_frames": contract["expected_frames"],
                    "atom_count": atom_count, "seed": seed,
                    "timestep_fs": TIMESTEP_FS}
        record, stats, _ = complete_trajectory(contract, expected, output, base, model,
                                               temperature, seed, state)
        rows.append({"temperature_K": temperature, **stats})
        trajectory_records.append({"temperature_K": temperature, "path": contract["final"],
                                   "sha256": record["sha256"], "steps": record["steps"],
                                   "frames": record["frames"], "seed": seed,
                                   "timestep_fs": TIMESTEP_FS})

    # ---- Result assembly (all 13 records exist before formal_result is set).
    q = np.asarray([row["mean_abs_eta_A"] for row in rows], dtype=float)
    tc = estimate_tc(np.asarray(temperatures, dtype=float), q) if args.profile == "paper" else {}

    csv_path = output / "order_parameter.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    plt.figure(figsize=(6, 4))
    plt.errorbar(temperatures, q, yerr=[row["block_sem_A"] for row in rows], marker="o")
    if tc:
        plt.axvline(tc["Tc_K"], color="tab:red", linestyle="--")
    plt.xlabel("Temperature (K)")
    plt.ylabel(r"$\langle |\eta| \rangle$ (angstrom)")
    plt.tight_layout()
    plt.savefig(output / "curie_temperature.png", dpi=180)
    plt.close()

    result = {"schema_version": "1.0", "case": "032", "profile": args.profile,
              "formal_result": bool(profile["formal_result"]), "supercell": profile["supercell"],
              "atom_count": atom_count,
              "order_parameter": "c*(mean(unwrapped fractional z_Cu)-mean(unwrapped fractional z_S))",
              "timestep_fs": TIMESTEP_FS, "frame_interval_steps": FRAME_INTERVAL_STEPS,
              "pilot": pilot, "curve": rows, "estimate": tc,
              "trajectories": trajectory_records,
              "inputs": {"structure_sha256": sha256(structure), "model_sha256": sha256(model)}}
    (output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (output / "report.md").write_text(
        "# CIPS Curie-temperature run\n\n"
        f"Profile: `{args.profile}`; supercell: `{profile['supercell']}`; atoms: {atom_count}.\n\n"
        + (f"Trajectory-derived estimate: **{tc['Tc_K']:.1f} +/- {tc['Tc_uncertainty_K']:.1f} K**.\n" if tc else
           "Smoke execution only; it is not a formal Curie-temperature result.\n")
    )
    state.update({"result": result, "stage": "result"})
    checkpoint(output, state)
    if tc:
        print(f"run complete: Tc_K={tc['Tc_K']:.2f} +/- {tc['Tc_uncertainty_K']:.2f} "
              f"(half={tc['half_height_K']:.2f}, piecewise={tc['piecewise_breakpoint_K']:.2f})")
    else:
        print("run complete: non-formal smoke result")


if __name__ == "__main__":
    main()
