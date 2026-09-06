#!/usr/bin/env python3
"""Deterministic fixture generator for the 042 test suite (verifier contract v2).

Builds a structurally-complete synthetic submission ("good") plus tampered
negatives into tests/fixtures/.  The good fixture is the STRUCTURAL positive:
it satisfies the verifier's V0-V3 contract —

  * final/manifest.json (DPMP, deepmd-jax, model_files -> real files, CP2K engine)
  * a primary CP2K AIMD family: `aimd-pos-1.xyz` (24 frames, header
    `i = N, time = T, E = E_ha`), `aimd-frc-1.xyz`, `aimd-1.ener`, `aimd-1.cell`
  * a second CP2K label family (the active-learning round): `al-001-pos-1.xyz`
    with NEW configurations (so V3 sees dataset growth beyond the initial AIMD)
  * two DeepMD raw training sets (round-1 = AIMD subset, round-2 = AL labels)
    whose coords trace to the CP2K outputs (V1) while round-2 adds new configs (V3)
  * model.pkl (>=1000 B placeholder; the container oracle replaces it with a
    real deepmd-jax model for V2/V4-V6)
  * AL round dirs at distinct parents (round-001/round-002) so V3 counts 2 rounds
  * an acquisition artifact (model_devi.out / lammpstrj) for the explore step

The scientific positive (V4-V6 with a real model) is the container oracle run.

All random data is seeded; fixtures are committed for reproducibility.

Usage:
    python3 tests/fixtures/generate_fixture.py [--out tests/fixtures]
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np

SEED = 20260820
N_AIMD = 24
N_AL = 12
N_ATOMS = 48
N_H2O_AL = 16  # 48 atoms -> 16 H2O; per-atom raw eV ~ -156 (shifted convention)
CELL = np.array([12.0, 0.0, 0.0, 0.0, 12.0, 0.0, 0.0, 0.0, 24.0])
LENGTHS = np.array([12.0, 12.0, 24.0])
E_HARROW_PER_ATOM_HA = -45.0  # ~ -1224 eV/atom if in Ha; within [-2000,0] eV/atom bound
E_TRAIN_PER_ATOM_EV = -156.0  # shifted DeepMD convention, matches published ~ -156


def _rng():
    return np.random.default_rng(SEED)


def _write_xyz(frames, out: Path, header_prefix: str, kind: str, energies_ha,
               force_mag: float, symbols):
    """Write CP2K-style *-1.xyz. kind='pos' -> coords; kind='frc' -> forces."""
    lines = []
    for i, (coords, e_ha) in enumerate(zip(frames, energies_ha)):
        lines.append(f"{N_ATOMS}\n")
        lines.append(f"i = {i}, time = {i * 1.0:.3f}, E = {e_ha:.8f}\n")
        for j in range(N_ATOMS):
            if kind == "pos":
                x, y, z = coords[j]
            else:
                x, y, z = (np.random.RandomState(1000 + i * N_ATOMS + j).normal(0, force_mag)
                           for _ in range(3))
            lines.append(f"{symbols[j]} {x:.6f} {y:.6f} {z:.6f}\n")
    out.write_text("".join(lines))
    return out


def _make_frames(rng, n: int) -> np.ndarray:
    return np.round(rng.uniform(0, 1, (n, N_ATOMS, 3)) * LENGTHS, 6)


def _energies_ha(n: int, base: float, step: float = 0.01) -> list[float]:
    return [base - i * step for i in range(n)]


def _write_ener(out: Path, n: int, temp: float = 300.0, base: float = 0.0):
    lines = [f"# step time energy_temp temperature energy\n"]
    for i in range(n + 1):
        lines.append(f"{i} {i * 1.0:.3f} 1.0 {temp:.1f} {base - i * 0.01:.8f}\n")
    out.write_text("".join(lines))


def _write_cell(out: Path, n: int):
    # CP2K cell format: step i ax ay az bx by bz cx cy cz alpha beta gamma volume
    # (>=11 cols so the verifier's _run_cell / parse_cell both parse it).
    lines = ["# step i ax ay az bx by bz cx cy cz alpha beta gamma volume\n"]
    for i in range(n):
        lines.append(f"{i} {i} {CELL[0]} {CELL[1]} {CELL[2]} "
                     f"{CELL[3]} {CELL[4]} {CELL[5]} {CELL[6]} {CELL[7]} {CELL[8]} "
                     f"90.0 90.0 90.0 {CELL[0] * CELL[4] * CELL[8]:.3f}\n")
    out.write_text("".join(lines))


def _make_structure(out: Path, rng):
    """48-atom slab: 12 C (graphite layer), 12 O, 24 H (water)."""
    out.mkdir(parents=True, exist_ok=True)
    typ, pos = [], []
    for i in range(12):
        typ.append(2)
        pos.append([(i % 4) * 3.0, ((i // 4) % 3) * 4.0, 1.5 + 0.05 * rng.normal()])
    for i in range(12):
        typ.append(0)
        pos.append([(i % 3) * 4.0, ((i // 3) % 4) * 3.0, 4.0 + 0.2 * rng.normal()])
    for i in range(24):
        typ.append(1)
        pos.append([rng.uniform(0, 12), rng.uniform(0, 12), 5.0 + 0.3 * rng.normal()])
    coord = np.asarray(pos)
    typ = np.asarray(typ)
    (out / "box.raw").write_text("".join(f"{v}\n" for v in CELL))
    (out / "type.raw").write_text("".join(f"{t}\n" for t in typ))
    (out / "coord.raw").write_text("".join(f"{c[0]:.6f} {c[1]:.6f} {c[2]:.6f}\n" for c in coord))
    symbols = np.array(["O", "H", "C"], dtype=object)
    return box_to_np(), coord, typ, np.array([symbols[t] for t in typ])


def box_to_np():
    return CELL


def _make_train_set(out: Path, frames: np.ndarray, typ: np.ndarray):
    setdir = out / "set.000"
    setdir.mkdir(parents=True, exist_ok=True)
    n = frames.shape[0]
    np.save(setdir / "box.npy", np.tile(CELL, (n, 1)))
    np.save(setdir / "coord.npy", frames.reshape(n, -1))
    np.save(setdir / "energy.npy", np.full(n, E_TRAIN_PER_ATOM_EV * N_ATOMS) + np.arange(n) * 0.01)
    np.save(setdir / "force.npy", np.round(np.random.RandomState(SEED).normal(0, 0.5, (n, N_ATOMS * 3)), 6))
    (out / "type.raw").write_text("".join(f"{t}\n" for t in typ))
    (out / "type_map.raw").write_text("O\nH\nC\n")


def make_manifest(final: Path, rounds: int, model_file: str, total_frames: int) -> dict:
    manifest = {
        "model_family": "DPMP",
        "framework": "deepmd-jax",
        "structure_origin": {
            "source": "public/structures/<iface> (published initial supercell)",
            "used_interfaces": ["air-water", "graphene-water", "graphene-O12"],
            "structure_preparation_sha256": "a" * 64,
        },
        "reference_labeling": {
            "engine": "CP2K",
            "functional": "revPBE-D3",
            "total_labeled_frames": total_frames,
            "frame_count_per_source": {"aimd": N_AIMD, "al-round-1": N_AL},
        },
        "model_files": [model_file],
        "workflow_root": "workflow",
        "iterative_improvement": {
            "rounds": rounds,
            "explorer": "jax-md",
            "labeling_engine": "CP2K",
            "final_training_frames": N_AIMD + N_AL,
        },
        "validation_artifacts": ["validation/nvt.log"],
        "environment_manifest": "deepmd-jax 0.1, jax 0.4, cp2k 2025.2, ai2-kit 1.1.0",
        "status": "completed",
    }
    (final / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def build_good(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    rng = _rng()
    final = out / "final"
    (final / "models").mkdir(parents=True, exist_ok=True)
    (final / "workflow").mkdir(parents=True, exist_ok=True)
    for stage in ["01-aimd", "02-train", "03-active-learning", "04-validation"]:
        (final / "workflow" / stage).mkdir(parents=True, exist_ok=True)
        (final / "workflow" / stage / "run.sh").write_text("#!/bin/bash\necho ok\n")

    # structures (for the density interface — agent-visible anyway)
    _, coord0, typ, symbols = _make_structure(out / "structures" / "graphene-water", rng)

    labels = out / "work" / "labels"
    labels.mkdir(parents=True, exist_ok=True)
    frames_aimd = _make_frames(rng, N_AIMD)
    frames_al = _make_frames(rng, N_AL)  # NEW configs from the AL round

    e_aimd = _energies_ha(N_AIMD, base=E_HARROW_PER_ATOM_HA * N_ATOMS)
    e_al = _energies_ha(N_AL, base=E_HARROW_PER_ATOM_HA * N_ATOMS - 0.5)
    _write_xyz(frames_aimd, labels / "aimd-pos-1.xyz", "aimd", "pos", e_aimd, 0.0, symbols)
    _write_xyz(frames_aimd, labels / "aimd-frc-1.xyz", "aimd", "frc", e_aimd, 3.0, symbols)
    _write_ener(labels / "aimd-1.ener", N_AIMD, temp=300.0, base=-45.0 * N_ATOMS)
    _write_cell(labels / "aimd-1.cell", N_AIMD)

    al_dir = out / "work" / "labels" / "al-round-001"
    al_dir.mkdir(parents=True, exist_ok=True)
    _write_xyz(frames_al, al_dir / "al-001-pos-1.xyz", "al-001", "pos", e_al, 0.0, symbols)
    _write_xyz(frames_al, al_dir / "al-001-frc-1.xyz", "al-001", "frc", e_al, 3.0, symbols)
    _write_ener(al_dir / "al-001-1.ener", N_AL, temp=300.0, base=-45.0 * N_ATOMS - 0.5)
    _write_cell(al_dir / "al-001-1.cell", N_AL)

    # training sets: round-1 = AIMD subset, round-2 = AL labels (dataset grew)
    _make_train_set(out / "work" / "train" / "round-001", frames_aimd[:N_AIMD], typ)
    _make_train_set(out / "work" / "train" / "round-002", frames_al, typ)

    # AL artifacts live OUTSIDE final/ (the verifier treats the delivered
    # final/ subtree as NOT a training round). Round dirs sit at DISTINCT
    # parents (round-001/round-002) so V3's parent-dir dedup counts both.
    al_root = out / "work" / "active-learning"
    for rname in ("round-001", "round-002"):
        rdir = al_root / rname / "iter-001"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "lcurve.out").write_text("0 1.0\n100 0.4\n")
        (rdir / "input.json").write_text('{"training_iters": 100, "lr": 1e-3}\n')
    (al_root / "round-002" / "iter-001" / "model_devi.out").write_text("1.234 0.567 0.432\n")
    (al_root / "round-002" / "iter-001" / "explore-iter-002.lammpstrj").write_text(
        "ITEM: TIMESTEP\n0\nITEM: NUMBER OF ATOMS\n48\nITEM: BOX BOUNDS xy xz yz pp pp pp\n"
        "0 12.0 0\n0 12.0 0\n0 24.0 0\nITEM: ATOMS id type x y z\n" +
        "".join(f"{k+1} {int(typ[k])+1} 0 0 0\n" for k in range(N_ATOMS)))

    # model: >=1000 B placeholder (oracle replaces with a real deepmd-jax model)
    model = final / "models" / "model.pkl"
    model.write_bytes(b"P" * 1024)

    make_manifest(final, rounds=2, model_file="models/model.pkl",
                  total_frames=N_AIMD + N_AL)
    (final / "report.md").write_text("# 042 oracle synthetic fixture\n")
    print(f"built good fixture: {out}")


def copy_tampered(src: Path, dst: Path, mutate) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    mutate(dst)
    print(f"built negative fixture: {dst}")


def _write_bad_energy(path: Path) -> None:
    lines = path.read_text().splitlines()
    # inflate the FIRST frame's E header to a nonphysical value
    for i, ln in enumerate(lines):
        if ln.startswith("i ="):
            lines[i] = "i = 0, time = 0.000, E = 999999.00000000"
            break
    path.write_text("\n".join(lines) + "\n")


def build_all(out: Path) -> None:
    good = out / "positive" / "good"
    build_good(good)

    # V0 fail: manifest only, no model
    copy_tampered(good, out / "negative" / "manifest-only",
                  lambda d: (d / "final" / "models" / "model.pkl").unlink())

    # V2 fail: corrupt (tiny/empty) model
    copy_tampered(good, out / "negative" / "corrupt-model",
                  lambda d: (d / "final" / "models" / "model.pkl").write_bytes(b""))

    # V2 fail: duplicate model bytes (both listed in the manifest)
    copy_tampered(good, out / "negative" / "duplicate-models", _dup_models)

    # V1 fail: forged labels — the primary AIMD pos trajectory removed
    copy_tampered(good, out / "negative" / "forged-labels",
                  lambda d: (d / "work" / "labels" / "aimd-pos-1.xyz").unlink()
                  and (d / "work" / "labels" / "aimd-frc-1.xyz").unlink())

    # V1 fail: nonphysical energy header
    copy_tampered(good, out / "negative" / "nonphysical-energy",
                  lambda d: _write_bad_energy(d / "work" / "labels" / "aimd-pos-1.xyz"))

    # V3 fail: no iterative loop (single round, no acquisition, no growth)
    copy_tampered(good, out / "negative" / "no-loop",
                  lambda d: _kill_loop(d))

    # V3 fail: claims growth but the extra labels were never retrained into a new round
    copy_tampered(good, out / "negative" / "new-labels-unused",
                  lambda d: _kill_retrain(d))


def _dup_models(d: Path) -> None:
    (d / "final" / "models" / "model-b.pkl").write_bytes(b"P" * 1024)
    m = json.loads((d / "final" / "manifest.json").read_text())
    m["model_files"].append("models/model-b.pkl")
    (d / "final" / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")


def _kill_loop(d: Path) -> None:
    m = json.loads((d / "final" / "manifest.json").read_text())
    m["iterative_improvement"]["rounds"] = 1
    m["iterative_improvement"]["final_training_frames"] = N_AIMD
    (d / "final" / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    r2 = d / "work" / "active-learning" / "round-002"
    if r2.exists():
        shutil.rmtree(r2)


def _kill_retrain(d: Path) -> None:
    # keep round-001, remove round-002 retrain marker but keep the labels
    r2 = d / "work" / "active-learning" / "round-002" / "iter-001"
    if (r2 / "input.json").exists():
        (r2 / "input.json").unlink()
    if (r2 / "lcurve.out").exists():
        (r2 / "lcurve.out").unlink()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[0])
    args = ap.parse_args()
    build_all(args.out)
    print("done")
