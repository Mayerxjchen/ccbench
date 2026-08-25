#!/usr/bin/env python3
"""Convert CP2K AIMD outputs (pos/frc/ener/cell) into a DeepMD raw labeled set.

Reads work/<iface>/go-water-pos-1.xyz, -frc-1.xyz, -1.ener, -1.cell and writes
work/<iface>/labeled/{set.000/{box,coord,energy,force}.npy, type.raw, type_map.raw}
with box flat (nframe,9) and coord flat (nframe,natom*3), matching deepmd-jax.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

TYPE_MAP = ["O", "H", "C"]
ELEM = {"O": 0, "H": 1, "C": 2}


def _parse_xyz(path: Path):
    frames = []
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        nat = int(lines[i].strip())
        atoms = []
        for k in range(nat):
            p = lines[i + 2 + k].split()
            atoms.append((p[0], float(p[1]), float(p[2]), float(p[3])))
        frames.append(atoms)
        i += 2 + nat
    return frames


def _parse_ener(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) >= 5 and p[0].lstrip("-").isdigit():
            rows.append(float(p[4]))
    return rows


def main() -> int:
    """Convert CP2K AIMD outputs into a DeepMD raw labeled set.

    Usage: convert.py <iface> [structure_dir]
      structure_dir defaults to <case-root>/structures/<iface> and must
      contain box.raw / coord.raw / type.raw from stage 00.
    """
    if len(sys.argv) < 2:
        print("[convert] ERROR: usage: convert.py <iface> [structure_dir]", file=sys.stderr)
        return 2
    iface = sys.argv[1]
    if len(sys.argv) >= 3:
        struct_dir = Path(sys.argv[2])
    else:
        # Case root is three parents up from this file (solution/expert/01-aimd)
        case_root = Path(__file__).resolve().parent.parent.parent.parent
        struct_dir = case_root / "structures" / iface
    w = Path(f"work/{iface}")
    pos = _parse_xyz(w / "go-water-pos-1.xyz")
    frc = _parse_xyz(w / "go-water-frc-1.xyz")
    ener = _parse_ener(w / "go-water-1.ener")
    if not pos:
        print(f"[convert] ERROR: no trajectory frames in {w / 'go-water-pos-1.xyz'}", file=sys.stderr)
        return 1
    n = min(len(pos), len(frc), len(ener))
    if n == 0:
        print("[convert] ERROR: empty pos/frc/ener (CP2K produced no usable output)", file=sys.stderr)
        return 1
    nat = len(pos[0])
    if len(pos) != len(frc):
        print(f"[convert] WARNING: pos frames ({len(pos)}) != frc frames ({len(frc)}) — truncating", file=sys.stderr)
    box_file = struct_dir / "box.raw"
    if not box_file.is_file():
        print(f"[convert] ERROR: structure box.raw not found: {box_file}", file=sys.stderr)
        return 1
    box = np.loadtxt(box_file).reshape(9)
    # Atom types come from the trajectory's own element symbols (the AIMD
    # trajectory is the ground truth for atom order); fall back to stage-00
    # type.raw only if the trajectory carries no parseable symbols.
    try:
        typ = np.array([ELEM[a[0]] for a in pos[0]], dtype=int)
    except KeyError:
        type_file = struct_dir / "type.raw"
        if not type_file.is_file():
            print(f"[convert] ERROR: cannot derive types from traj and {type_file} missing",
                  file=sys.stderr)
            return 1
        typ = np.loadtxt(type_file, dtype=int).reshape(-1)
    if len(typ) != nat:
        print(f"[convert] ERROR: nat mismatch — traj frame has {nat} atoms but "
              f"{len(typ)} types", file=sys.stderr)
        return 1

    coord = np.array([[x for a in fr for x in (a[1], a[2], a[3])] for fr in pos])
    force = np.array([[x for a in fr for x in (a[1], a[2], a[3])] for fr in frc])
    energy = np.array(ener[:n])
    # Convert CP2K native units to DeepMD units:
    #   energy: Hartree -> eV
    #   forces: Hartree/bohr -> eV/Å   (CP2K prints the force trajectory in
    #           atomic units; raw values would silently corrupt labels)
    HARTREE_TO_EV = 27.211386245988
    BOHR_TO_ANGSTROM = 0.529177210903
    HA_PER_BOHR_TO_EV_PER_A = HARTREE_TO_EV / BOHR_TO_ANGSTROM  # ~51.422067
    energy = energy * HARTREE_TO_EV
    force = force * HA_PER_BOHR_TO_EV_PER_A

    out = w / "labeled"
    setd = out / "set.000"
    setd.mkdir(parents=True, exist_ok=True)
    np.save(setd / "box.npy", np.tile(box, (n, 1)))
    np.save(setd / "coord.npy", coord[:n])
    np.save(setd / "energy.npy", energy)
    np.save(setd / "force.npy", force[:n])
    (out / "type.raw").write_text("".join(f"{t}\n" for t in typ))
    (out / "type_map.raw").write_text("".join(f"{s}\n" for s in TYPE_MAP))
    print(f"[convert] {n} frames, {nat} atoms -> work/{iface}/labeled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
