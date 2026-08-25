"""Generate coord_n_cell.inc for a CP2K AIMD/GEO input from DeepMD raw data.

Single source of truth for the labeling-chain conversion: used by
01-aimd/run.sh and by tools/aimd_preflight.py so both exercise identical
path and element-symbol semantics.

Usage:
    python gen_coord_inc.py --iface NAME --struct-dir DIR \
        --case-root CASE_ROOT [--out-root work]

Writes <out-root>/<iface>/coord_n_cell.inc containing a full &CELL block and
an &COORD block whose lines carry element symbols resolved through
public/system.json's element_type_map.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", required=True)
    ap.add_argument("--struct-dir", required=True,
                    help="directory with box.raw / coord.raw / type.raw")
    ap.add_argument("--case-root", default=".",
                    help="case root holding public/system.json")
    ap.add_argument("--out-root", default="work")
    args = ap.parse_args()

    struct = Path(args.struct_dir)
    box = np.loadtxt(struct / "box.raw").reshape(3, 3)
    coord = np.loadtxt(struct / "coord.raw").reshape(-1, 3)
    typ = np.loadtxt(struct / "type.raw", dtype=int).reshape(-1)

    sysmap = json.loads(
        (Path(args.case_root) / "public/system.json").read_text()
    )["element_type_map"]
    inv = {v: k for k, v in sysmap.items()}
    symbols = [inv[int(t)] for t in typ]

    out_dir = Path(args.out_root) / args.iface
    out_dir.mkdir(parents=True, exist_ok=True)
    inc = out_dir / "coord_n_cell.inc"
    with inc.open("w") as fh:
        fh.write("&CELL\n")
        for i in range(3):
            fh.write("  " + " ".join(f"{box[i, j]:.10f}" for j in range(3)) + "\n")
        fh.write("&END CELL\n&COORD\n")
        for sym, (x, y, z) in zip(symbols, coord):
            fh.write(f"  {sym} {x:.10f} {y:.10f} {z:.10f}\n")
        fh.write("&END COORD\n")

    print(f"[gen_coord_inc] wrote {inc} ({len(symbols)} atoms with elements)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
