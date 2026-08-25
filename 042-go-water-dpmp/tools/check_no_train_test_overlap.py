#!/usr/bin/env python3
"""Case 042 train/test overlap check.

Asserts that NO training frame in a candidate workspace shares a coordinate hash
with any hidden held-out frame (reference/hidden-validation/manifest.json). A
candidate that somehow used a hidden frame fails. Uses the same coord-hash
scheme (round to 1e-6, sha256 of the raw bytes) as the hidden-set generator.

Usage:
    python3 check_no_train_test_overlap.py <candidate_workspace>
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

CASE = Path(__file__).resolve().parents[1]
MANIFEST = CASE / "reference" / "hidden-validation" / "manifest.json"


def _coords_hash(coords: np.ndarray, precision: int = 6) -> str:
    flat = np.round(coords.astype(np.float64), precision).ravel()
    return hashlib.sha256(flat.tobytes()).hexdigest()


def load_hidden_hashes() -> set[str]:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {f["coords_hash_1e6"] for f in m["frames"]}


def scan_training_sets(root: Path) -> set[str]:
    out: set[str] = set()
    for p in root.rglob("set.*"):
        coord = p / "coord.npy"
        if not coord.is_file():
            continue
        c = np.load(str(coord))
        for i in range(c.shape[0]):
            out.add(_coords_hash(c[i].reshape(-1, 3)))
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check_no_train_test_overlap.py <candidate_workspace>", file=sys.stderr)
        return 2
    ws = Path(sys.argv[1])
    hidden = load_hidden_hashes()
    train = scan_training_sets(ws)
    overlap = hidden & train
    if overlap:
        print(f"OVERLAP FAIL: {len(overlap)} training frame(s) match hidden held-out frames")
        return 1
    print(f"OVERLAP PASS: {len(train)} training frames, 0 overlap with {len(hidden)} hidden frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
