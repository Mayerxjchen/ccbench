#!/usr/bin/env python3
"""Generate the independent deterministic L0 positive fixture."""

from __future__ import annotations

import argparse
import math
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--from-cp2k", type=Path)
    args = parser.parse_args()
    cell = 12.4
    spacing = cell / 4.0
    theta = math.radians(104.5)
    atoms = []
    if args.from_cp2k:
        source = args.from_cp2k.read_text().splitlines()
        natoms = int(source[0])
        for line in source[2:2 + natoms]:
            symbol, x, y, z = line.split()[:4]
            atoms.append((symbol, float(x), float(y), float(z)))
    else:
        for index in range(64):
            ix = index % 4
            iy = (index // 4) % 4
            iz = index // 16
            ox, oy, oz = (0.25 + ix * spacing, 0.25 + iy * spacing, 0.25 + iz * spacing)
            atoms.extend([
                ("O", ox, oy, oz),
                ("H", ox + 0.96, oy, oz),
                ("H", ox + 0.96 * math.cos(theta), oy + 0.96 * math.sin(theta), oz),
            ])
    lines = [
        str(len(atoms)),
        'Lattice="12.4 0 0 0 12.4 0 0 0 12.4" Properties=species:S:1:pos:R:3 pbc="T T T"',
    ]
    lines.extend(f"{s} {x:.8f} {y:.8f} {z:.8f}" for s, x, y, z in atoms)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
