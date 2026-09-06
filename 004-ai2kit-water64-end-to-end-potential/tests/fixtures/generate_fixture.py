#!/usr/bin/env python3
"""
Local smoke fixture builder for the 034 verifier.

Builds a plausible "good" agent workspace from REAL reference CP2K outputs, so
the verifier's parsers and structural checks (L1-L4, L6) can be exercised on
genuine data shapes before the container image exists. L5/L7/L8/L9 need deepmd
+lmp and are therefore only testable inside dftworld-base-ai2kit.

The fixture is a stand-in for what solution/expert produces. It deliberately:
  - uses the REAL AIMD pos/frc/ener/cell files (214-frame raw, Hartree headers),
  - uses the REAL labeled 190-frame aimd.xyz to build DeepMD set dirs,
  - fabricates "active-learning" label runs (perturbed frames written as new
    CP2K pos/frc/ener outputs + new training sets), so L6's "new configurations
    trace to a real CP2K output" check passes.

Output layout (tests/fixtures/good):
  final/manifest.json
  final/workflow/            (copies of the four stage scripts, as the oracle does)
  final/models/final.pb      (placeholder — not deepmd-loadable; L5 needs container)
  work/geopt/...            (real geopt outputs)
  work/aimd/raw/...         (real AIMD raw outputs)
  work/aimd/processed/aimd.xyz
  work/al/dp-init/set.000    (DeepMD init set from aimd.xyz subset)
  work/al/iter-001/          (train input.json + lcurve.out + label pos/frc/ener + new set)
  work/al/iter-002/          (retrained)
  work/al/explore/           (LAMMPS dump artifacts for acquisition evidence)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CASE = HERE.parent.parent
REF = CASE / "reference" / "expert-trajectory"
OUT = HERE / "good"
OUT_ROOT = CASE  # used to resolve reference-relative paths

HARTREE_TO_EV = 27.211386245988


def _load_aimd_xyz(path: Path):
    from ase.io import read
    atoms_list = read(str(path), index=":")
    return atoms_list


def _build_dp_set(out_dir: Path, atoms_list, indices, cell):
    """Write a DeepMD set dir with coord/box/energy/force/type from ase frames."""
    set_dir = out_dir / "set.000"
    set_dir.mkdir(parents=True, exist_ok=True)
    coords, boxs, energies, forces = [], [], [], []
    for k in indices:
        a = atoms_list[k]
        coords.append(a.get_positions())
        boxs.append(np.eye(3) * cell)
        try:
            energies.append(a.get_potential_energy())
        except Exception:
            energies.append(a.info.get("energy", -1000.0))
        try:
            forces.append(a.get_forces())
        except Exception:
            forces.append(np.zeros_like(a.get_positions()))
    np.save(set_dir / "coord.npy", np.array(coords))
    np.save(set_dir / "box.npy", np.array(boxs))
    np.save(set_dir / "energy.npy", np.array(energies))
    np.save(set_dir / "force.npy", np.array(forces))
    # type_map O=0, H=1 (matches the ai2kit/dpdata O-first convention)
    np.save(set_dir / "type.npy", np.array([0, 1] * 64))
    (out_dir / "type_map.raw").write_text("O\nH\n")
    (out_dir / "type.raw").write_text("0\n1\n")


def _write_cp2k_xyz(path: Path, atoms_list, indices, kind="pos"):
    """Write a CP2K-format xyz (pos or frc) from ase frames."""
    lines = []
    for k in indices:
        a = atoms_list[k]
        n = len(a)
        lines.append(f"{n}")
        try:
            e = a.get_potential_energy()
        except Exception:
            e = a.info.get("energy", -1000.0)
        e_ha = e / HARTREE_TO_EV
        lines.append(f" i = {k}, time = {k * 0.5:.3f}, E = {e_ha:.12f}")
        pos = a.get_positions()
        try:
            frc = a.get_forces()
        except Exception:
            frc = np.zeros_like(pos)
        for i in range(n):
            if kind == "pos":
                lines.append(f" {a.symbols[i]} {pos[i,0]:.10f} {pos[i,1]:.10f} {pos[i,2]:.10f}")
            else:
                lines.append(f" {a.symbols[i]} {frc[i,0]:.10f} {frc[i,1]:.10f} {frc[i,2]:.10f}")
    path.write_text("\n".join(lines) + "\n")


def _write_ener(path: Path, indices):
    lines = ["# Step Nr. Time[fs] Kin.[a.u.] Temp[K] Pot.[a.u.] Cons Qty[a.u.] UsedTime[s]"]
    for k in indices:
        temp = 300 + 15 * np.sin(k / 3.0)
        lines.append(f"{k} {k*0.5:.3f} {20.0:.4f} {temp:.4f} {-1000.0:.6f} {-980.0:.6f} {0.1:.3f}")
    path.write_text("\n".join(lines) + "\n")


def _write_cell(path: Path, indices):
    lines = ["# Step Time[fs] Ax Ay Az Bx By Bz Cx Cy Cz Volume"]
    for k in indices:
        lines.append(f"{k} {k*0.5:.3f} 12.4 0.0 0.0 0.0 12.4 0.0 0.0 0.0 12.4 1906.624")
    path.write_text("\n".join(lines) + "\n")


def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    aimd_xyz = REF / "aimd" / "processed" / "aimd.xyz"
    if not aimd_xyz.is_file():
        print(f"ERROR: {aimd_xyz} missing; build reference lineage first")
        sys.exit(1)
    atoms = _load_aimd_xyz(aimd_xyz)
    nframes = len(atoms)
    cell = float(np.linalg.norm(atoms[0].get_cell()[0]))

    # --- final/ contract ----------------------------------------------------
    (OUT / "final" / "models").mkdir(parents=True, exist_ok=True)
    (OUT / "final" / "workflow").mkdir(parents=True, exist_ok=True)
    # copy the oracle stage scripts (if present) as the workflow snapshot
    src_workflow = CASE / "solution" / "expert"
    for item in src_workflow.rglob("*"):
        if item.is_file():
            rel = item.relative_to(src_workflow)
            dest = OUT / "final" / "workflow" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
    (OUT / "final" / "models" / "final.pb").write_bytes(b"\x00\x01 PLACEHOLDER not deepmd-loadable")
    manifest = {
        "model_family": "DeePMD",
        "model_files": ["final/models/final.pb"],
        "workflow_root": "final/workflow",
        "training_data_provenance": "CP2K AIMD generated from the supplied structure (190 frames) + 2 active-learning label sets",
        "validation_artifacts": [],
        "environment_manifest": "ai2-kit 1.1.0, dp 2.2.11, lammps 2023.8, cp2k 2024.3",
        "status": "completed",
        "profile": "paper",
    }
    (OUT / "final" / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (OUT / "final" / "report.md").write_text("# Fixture report (local smoke)\n")

    # --- work/geopt (real geopt outputs) ------------------------------------
    geopt_src = REF / "geopt" / "output"
    if geopt_src.is_dir():
        dest = OUT / "work" / "geopt" / "output"
        dest.mkdir(parents=True, exist_ok=True)
        for f in geopt_src.iterdir():
            if f.is_file():
                shutil.copy2(f, dest / f.name)
        (OUT / "work" / "geopt" / "geopt.done").write_text("done\n")

    # --- work/aimd/raw (REAL 214-frame raw outputs, trimmed to 214) ----------
    raw_src = REF / "aimd" / "raw"
    raw_dest = OUT / "work" / "aimd" / "raw"
    raw_dest.mkdir(parents=True, exist_ok=True)
    for f in raw_src.iterdir():
        if f.is_file() and f.name in ("water64_aimd-pos-1.xyz", "water64_aimd-frc-1.xyz",
                                      "water64_aimd-1.ener", "water64_aimd-1.cell"):
            shutil.copy2(f, raw_dest / f.name)
    # --- work/aimd/processed/aimd.xyz (REAL labeled set) ----------------------
    proc_dest = OUT / "work" / "aimd" / "processed"
    proc_dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(aimd_xyz, proc_dest / "aimd.xyz")
    (OUT / "work" / "aimd" / "aimd.done").write_text("done\n")

    # --- work/al: init set from aimd.xyz subset ------------------------------
    al = OUT / "work" / "al"
    init_idx = [i for i in range(0, 60)]
    _build_dp_set(al / "dp-init", atoms, init_idx, cell)
    # LAMMPS explore artifacts (acquisition evidence for L6)
    explore = al / "explore"
    explore.mkdir(parents=True, exist_ok=True)
    dump_lines = ["ITEM: TIMESTEP", "1000", "ITEM: NUMBER OF ATOMS", "192",
                  "ITEM: BOX BOUNDS pp pp pp", "0 12.4", "0 12.4", "0 12.4",
                  "ITEM: ATOMS id type x y z"]
    for i in range(192):
        dump_lines.append(f"{i+1} {1 if i % 2 == 0 else 2} 1.0 2.0 3.0")
    (explore / "iter-001.lammpstrj").write_text("\n".join(dump_lines) + "\n")
    (explore / "model_devi.out").write_text("max_devi_f_0 0.3 0.5\nmax_devi_f_1 0.4 0.6\n")

    # --- work/al: iter-001 + iter-002 training rounds ------------------------
    # new "AL" labels: perturbed frames written as fresh CP2K outputs (they do
    # NOT appear in the primary AIMD, so L6 sees them as new; they ARE real
    # CP2K-format outputs in the workspace, so L6's trace check passes).
    rng = np.random.RandomState(7)
    n_new = 12
    new_idx_base = list(range(60, min(60 + n_new, nframes)))
    # perturbed copies: build perturbed atoms list by copying + shifting
    from ase import Atoms as AseAtoms
    perturbed = []
    for k in new_idx_base:
        a = atoms[k]
        pos = a.get_positions().copy() + rng.normal(0, 0.08, a.get_positions().shape)
        na = AseAtoms(symbols=a.get_chemical_symbols(), positions=pos, cell=a.get_cell(), pbc=True)
        try:
            na.set_potential_energy(a.get_potential_energy() + rng.normal(0, 0.5))
            na.arrays["forces"] = a.get_forces().copy() + rng.normal(0, 0.1, a.get_forces().shape)
        except Exception:
            pass
        perturbed.append(na)

    for it, base in ((1, perturbed[:6]), (2, perturbed[6:])):
        it_dir = al / f"iter-{it:03d}"
        label_dir = it_dir / "label"
        label_dir.mkdir(parents=True, exist_ok=True)
        _write_cp2k_xyz(label_dir / "label-pos-1.xyz", base, range(len(base)), kind="pos")
        _write_cp2k_xyz(label_dir / "label-frc-1.xyz", base, range(len(base)), kind="frc")
        _write_ener(label_dir / "label-1.ener", range(len(base)))
        _write_cell(label_dir / "label-1.cell", range(len(base)))
        # the new training set = init set + this iter's new labels
        all_atoms = [atoms[i] for i in init_idx] + base
        _build_dp_set(it_dir / "train", all_atoms, range(len(all_atoms)), cell)
        (it_dir / "input.json").write_text(json.dumps({
            "model": {"type_map": ["O", "H"]},
            "training": {"numb_steps": 40000},
        }))
        (it_dir / "lcurve.out").write_text("step loss lr\n")
        (it_dir / "checkpoint").write_text("model checkpoint placeholder\n")

    # model files for rounds
    for it in (1, 2):
        (al / f"iter-{it:03d}" / f"model-{it}.pb").write_bytes(b"\x00\x01placeholder")
    # final model copy that manifest points at is the placeholder (not loadable
    # locally; L5 needs deepmd in the container).

    print(f"fixture built at {OUT}")
    print(f"  aimd.xyz frames: {nframes}, cell: {cell:.4f}")
    print("  NOTE: final/models/final.pb is a placeholder; L5/L7/L8/L9 require deepmd+lmp (container)")


if __name__ == "__main__":
    build()
