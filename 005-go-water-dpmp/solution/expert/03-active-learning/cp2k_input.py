#!/usr/bin/env python3
"""Build CP2K single-point inputs from XYZ-labeled active-learning configs.

Extracted from 03-active-learning/run.sh so the coord.inc generation
(CP2K ``&COORD`` expects an element symbol plus three coordinates per
line) is unit-testable rather than buried in a bash heredoc.
"""
from __future__ import annotations

from pathlib import Path


def xyz_to_coord_inc(xyz_path: Path | str, out_path: Path | str) -> int:
    """Convert an XYZ frame to a CP2K ``coord.inc`` (``@INCLUDE``d by &COORD).

    CP2K ``&COORD`` rows are ``<Element> <x> <y> <z>`` — the element symbol
    (the XYZ file's first column) MUST be kept; writing only ``x y z``
    makes CP2K fail to assign Kinds.  Returns the number of coord rows
    written (== the frame's atom count).
    """
    xyz_path = Path(xyz_path)
    out_path = Path(out_path)
    n = 0
    with xyz_path.open() as fh:
        lines = fh.readlines()
    if not lines:
        raise ValueError(f"empty XYZ: {xyz_path}")
    natoms = int(lines[0].split()[0])
    with out_path.open("w") as out:
        for line in lines[2:2 + natoms]:
            parts = line.split()
            if len(parts) < 4:
                continue
            # element symbol + three coordinates
            out.write(f"  {parts[0]} {parts[1]} {parts[2]} {parts[3]}\n")
            n += 1
    return n


if __name__ == "__main__":
    import sys
    rc = xyz_to_coord_inc(sys.argv[1], sys.argv[2])
    print(f"wrote {rc} coord rows -> {sys.argv[2]}")
