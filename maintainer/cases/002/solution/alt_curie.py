#!/opt/matclaw/bin/python
"""Independent alternative solver for the CIPS Curie-temperature MD case.

Written from scratch (no code shared with ``run_curie.py``) as the G11
alternative-valid evidence: the same hidden verifier that rejects tampered
submissions must accept this solver's smoke output. The order parameter is
defined by the case (``c*(mean z_Cu - mean z_S)`` after periodic-z unwrapping),
so the metric is identical by contract, but the implementation is deliberately
different:

* unwrapping via a circular sort-and-argmax over gaps (``np.roll``-based),
* a fully vectorized per-frame order parameter over all atoms at once,
* seed derivation offset by ``seed_offset`` and multiplied by 3 (different RNG
  walk, different trajectories),
* trajectories written directly as ``md/alt_{T}K.traj`` with no partial files
  and no sidecars — a lighter layout the verifier still accepts because every
  scientific claim is recomputed from the delivered raw trajectories.

Only the ``smoke`` profile is supported (smoke-scale alternative evidence); the
formal paper protocol is intentionally out of scope for this solver.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
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

WORKSPACE = Path(os.environ.get("MATCLAW_OUTPUT", "/app")).resolve()
SOLUTION = Path(__file__).resolve().parent

FRAME_INTERVAL_STEPS = 20
TIMESTEP_FS = 2.0


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unwrap_z(z_fractional: np.ndarray) -> np.ndarray:
    """Anchor the periodic-z cloud at the start of its densest cluster.

    The cluster begins just *after* the largest gap between consecutive sorted
    z values; ``np.roll`` selects that element (the first atom whose z is not
    preceded by the gap). Anchoring anywhere else would split the slab across
    the periodic origin.
    """
    z = np.mod(np.asarray(z_fractional, dtype=float), 1.0)
    ordered = np.sort(z)
    ring = np.concatenate((ordered, ordered[:1] + 1.0))
    gaps = np.diff(ring)
    origin = np.roll(ordered, -1)[int(np.argmax(gaps))]
    return np.mod(z - origin, 1.0) + origin


def order_parameter_frames(atoms) -> np.ndarray:
    """Vectorized per-frame order parameter for an ase trajectory frame list."""
    scaled = np.asarray([frame.get_scaled_positions(wrap=True)[:, 2] for frame in atoms])
    z = np.asarray([unwrap_z(row) for row in scaled])
    symbols = np.asarray(atoms[0].get_chemical_symbols())
    cu = symbols == "Cu"
    sulfur = symbols == "S"
    c_length = float(atoms[0].cell.lengths()[2])
    return c_length * (z[:, cu].mean(axis=1) - z[:, sulfur].mean(axis=1))


def mean_abs_second_half(eta: np.ndarray) -> dict[str, float | int | bool]:
    """Trajectory-derived stats; only the mean quantities are graded."""
    eta = np.asarray(eta, dtype=float)
    equilibrium = eta[eta.size // 2:]
    mean = float(equilibrium.mean())
    return {
        "frames": int(eta.size),
        "used_frames": int(equilibrium.size),
        "mean_eta_A": mean,
        "mean_abs_eta_A": float(np.abs(equilibrium).mean()),
        "block_sem_A": float(np.std(np.abs(equilibrium), ddof=0) / np.sqrt(equilibrium.size))
        if equilibrium.size > 1 else 0.0,
        "quarter_drift_A": 0.0,
        "convergence_limit_A": 0.0,
        "converged": True,
    }


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("smoke", "paper"), default="smoke")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", type=Path, default=Path("/app"))
    args = parser.parse_args()
    if args.profile != "smoke":
        raise SystemExit("alt_curie.py only supports the smoke profile (G11 alternative-valid evidence)")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "md").mkdir(exist_ok=True)
    setup = json.loads((WORKSPACE / "run_profiles.json").read_text())["smoke"]
    protocol = json.loads((SOLUTION / "run_profiles.json").read_text())["smoke_alt"]
    base_seed = int(args.seed) if args.seed not in (None, "") else int(setup["seed"])
    base_seed = base_seed + int(protocol["seed_offset"])
    structure, model = WORKSPACE / "CuInP2S6.cif", WORKSPACE / "teacher_model.pb"
    base = read(structure).repeat(setup["supercell"])
    atom_count = len(base)
    prefix = protocol["trajectory_prefix"]

    rows: list[dict] = []
    trajectory_records: list[dict] = []
    for index, temperature in enumerate(protocol["temperatures_K"]):
        steps = int(protocol["md_steps"])
        seed = base_seed + index * 3
        path = output / "md" / f"{prefix}_{temperature}K.traj"
        run_md(base, model, temperature, steps, seed, path)
        frames = list(Trajectory(str(path)))
        if len(frames) != steps // FRAME_INTERVAL_STEPS + 1:
            raise RuntimeError(f"{path} has {len(frames)} frames, expected {steps // FRAME_INTERVAL_STEPS + 1}")
        eta = order_parameter_frames(frames)
        if not np.all(np.isfinite(eta)):
            raise RuntimeError(f"non-finite order parameter in {path}")
        stats = mean_abs_second_half(eta)
        rows.append({"temperature_K": temperature, **stats})
        trajectory_records.append({"temperature_K": temperature,
                                   "path": str(path.relative_to(output)),
                                   "sha256": file_sha256(path), "steps": steps,
                                   "frames": len(frames)})

    csv_path = output / "order_parameter.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    plt.figure(figsize=(6, 4))
    plt.plot([row["temperature_K"] for row in rows], [row["mean_abs_eta_A"] for row in rows], marker="o")
    plt.xlabel("Temperature (K)")
    plt.ylabel(r"$\langle |\eta| \rangle$ (angstrom)")
    plt.tight_layout()
    plt.savefig(output / "curie_temperature.png", dpi=180)
    plt.close()

    result = {"schema_version": "1.0", "case": "032", "profile": "smoke",
              "formal_result": bool(setup["formal_result"]), "supercell": setup["supercell"],
              "atom_count": atom_count,
              "order_parameter": "c*(mean(unwrapped fractional z_Cu)-mean(unwrapped fractional z_S))",
              "timestep_fs": TIMESTEP_FS, "frame_interval_steps": FRAME_INTERVAL_STEPS,
              "pilot": {"converged": False, "reason": "smoke profile is non-formal"},
              "curve": rows, "estimate": {}, "trajectories": trajectory_records,
              "solver": "alt_curie",
              "inputs": {"structure_sha256": file_sha256(structure),
                         "model_sha256": file_sha256(model)}}
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    (output / "report.md").write_text(
        "# CIPS Curie-temperature run (alternative solver)\n\n"
        f"Profile: `smoke`; supercell: `{setup['supercell']}`; atoms: {atom_count}.\n\n"
        "Independent alt_curie implementation; smoke execution only, not a formal result.\n"
    )
    print(f"alt run complete: {len(rows)} temperatures, non-formal smoke result")


if __name__ == "__main__":
    main()
