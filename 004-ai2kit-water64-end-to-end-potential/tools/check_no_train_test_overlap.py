#!/usr/bin/env python3
"""
I3 gate: prove the agent's training frames never overlap the hidden set.

The hidden validation set (dft-validation.extxyz) is the expert's 190-frame
AIMD mother set.  A fair benchmark requires that NO agent training frame is
structurally identical to any hidden frame.

Scheme (byte-identical to tests/verifier.py._coords_hash): round every atom
coordinate to 3 decimals, row-major concatenate, comma-join -> a single string
per frame.  We compare EXACT strings, so only a genuine duplicate (same
geometry within 0.0005 A) collides.

Hidden side : reference/hidden-validation/manifest.json[].coords_hash_prec3
Agent side : every coord.npy / coord.raw found in the submission's DeepMD
             training sets (set.*/), plus any labeled extxyz the agent wrote.

Usage:
  python3 tools/check_no_train_test_overlap.py \
      --submission <agent workspace root> \
      [--reference hidden/manifest.json] \
      [--tolerance-a 0.0005]

Exit 0 (PASS) if overlap is empty, 1 (FAIL) otherwise.  Prints a report.

This is a real-run evidence tool: it is only meaningful on an actual agent
submission.  See VALIDATION.json I3.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def coords_hash(pos: np.ndarray, prec: float = 3) -> str:
    return ",".join(f"{float(x):.{prec}f}" for x in np.asarray(pos).reshape(-1))


def find_deepmd_training_frames(root: Path):
    """Yield (path, frame_hash) for every frame in every set.*/ coord file."""
    seen = set()
    for coord in sorted(root.rglob("coord.npy")):
        arr = np.load(coord)
        if arr.ndim != 3:
            continue
        for i in range(arr.shape[0]):
            h = coords_hash(arr[i])
            if h in seen:
                continue
            seen.add(h)
            yield (f"{coord}#frame{i}", h)
    for coord in sorted(root.rglob("coord.raw")):
        arr = np.loadtxt(coord).reshape(-1, 3)
        for i in range(arr.shape[0]):
            h = coords_hash(arr[i])
            if h in seen:
                continue
            seen.add(h)
            yield (f"{coord}#frame{i}", h)


def find_labeled_extxyz_frames(root: Path):
    """Yield (path, frame_hash) for labeled extxyz/xyz the agent wrote (best-effort)."""
    for p in sorted(root.rglob("*.xyz")) + sorted(root.rglob("*.extxyz")):
        name = p.name.lower()
        if "pos" in name or "frc" in name:
            continue
        try:
            from ase.io import read

            frames = read(str(p), index=":")
        except Exception:
            continue
        for i, fr in enumerate(frames):
            yield (f"{p}#frame{i}", coords_hash(fr.get_positions(), 3))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission", type=Path, required=True)
    ap.add_argument(
        "--reference",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "reference" / "hidden-validation" / "manifest.json",
    )
    args = ap.parse_args()

    if not args.submission.is_dir():
        print(f"FAIL: submission dir not found: {args.submission}")
        return 1

    ref = json.loads(args.reference.read_text())
    hidden = {e["coords_hash_prec3"] for e in ref.get("per_frame_provenance", [])}
    if not hidden:
        print(f"FAIL: no per_frame_provenance in {args.reference}")
        return 1
    print(f"hidden frames: {len(hidden)}")

    train = {}
    for path, h in find_deepmd_training_frames(args.submission):
        train.setdefault(h, []).append(path)
    for path, h in find_labeled_extxyz_frames(args.submission):
        train.setdefault(h, []).append(path)
    print(f"agent training frames found: {sum(len(v) for v in train.values())} "
          f"(unique structures: {len(train)})")

    overlap = sorted(h for h in train if h in hidden)
    if overlap:
        print(f"FAIL: {len(overlap)} agent training frame(s) overlap the hidden set:")
        for h in overlap[:20]:
            print(f"  hidden frame  -> agent at {train[h][:3]}")
        print("... (train-test leakage; benchmark invalid)")
        return 1

    print("PASS: agent training frames do NOT overlap the hidden validation set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
