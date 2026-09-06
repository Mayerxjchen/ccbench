#!/usr/bin/env python3
"""Generate the Case 042 hidden validation set from the published GO-water DPMP
labeled dataset (figshare 10.6084/m9.figshare.30472487 v2).

No HPC is required. This script:

  1. Hold-out: draws a frozen-seed (42) subset of frames from each of the eight
     published DeepMD raw training sets (14140 frames total) into
     hidden-frames/<interface>/ as DeepMD raw sets. These are the verifier's
     independent static-accuracy (V4) reference labels.
  2. Density reference: computes the water oxygen number-density profile across
     the graphene-water interface (z-direction) from the published labeled
     trajectory and records the first hydration-layer peak position and the
     depletion width in density-reference.json (V6).
  3. Manifest: writes per-holdout provenance (source set, source frame index,
     coord sha256 at 1e-6 precision) so overlap against a candidate submission
     can be checked.

Deterministic: the only randomness is the fixed hold-out seed. Boxes are stored
flat (nframe, 9) and coords flat (nframe, natom*3), exactly as in the published
sets; deepmd-jax reads them natively.

Usage:
    python3 generate_hidden_validation.py \
        --source /path/to/source_data/train_dataset \
        --out /path/to/reference/hidden-validation \
        [--holdout-frac 0.10]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

# Exactly the five publicly specified systems (instruction.md / system.json).
# The author dataset's extra systems (water, air-water-long,
# graphene-water-long) have no public construction spec and are out of scope.
INTERFACES = [
    "air-water",
    "graphene-water",
    "graphene-O12",
    "graphene-O25",
    "graphene-O50",
]

DENSITY_INTERFACE = "graphene-water"
DENSITY_BIN_ANGSTROM = 0.1


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _coords_hash(coords: np.ndarray, precision: int = 6) -> str:
    """Stable coord hash at fixed precision (overlap check uses the same scheme)."""
    flat = np.round(coords.astype(np.float64), precision).ravel()
    return _sha256_bytes(flat.tobytes())


def load_set(path: Path) -> dict:
    setdir = path / "set.000"
    return {
        "box": np.load(setdir / "box.npy"),
        "coord": np.load(setdir / "coord.npy"),
        "energy": np.load(setdir / "energy.npy"),
        "force": np.load(setdir / "force.npy"),
        "type": np.loadtxt(path / "type.raw", dtype=int).reshape(-1),
        "type_map": (path / "type_map.raw").read_text(encoding="utf-8"),
    }


def write_raw_set(out: Path, data: dict, idx: np.ndarray) -> int:
    setdir = out / "set.000"
    setdir.mkdir(parents=True, exist_ok=True)
    np.save(setdir / "box.npy", data["box"][idx])
    np.save(setdir / "coord.npy", data["coord"][idx])
    np.save(setdir / "energy.npy", data["energy"][idx])
    np.save(setdir / "force.npy", data["force"][idx])
    (out / "type.raw").write_text("".join(f"{t}\n" for t in data["type"]), encoding="utf-8")
    (out / "type_map.raw").write_text(data["type_map"], encoding="utf-8")
    return int(idx.size)


def compute_density_reference(data: dict, n_bins: int = 512) -> dict:
    """Oxygen number-density profile across the graphene-water interface.

    The graphene sheet sits near the periodic z boundary (C z in [-2.29, -1.72]
    -> folded to ~[26, 28]); water occupies the rest of the cell. We fold z into
    [0, Lz), define the graphite plane as the mean folded C z, and histogram the
    O atoms vs distance from that plane (minimum-image). The first peak is the
    first hydration layer; depletion width is the plane-to-first-peak distance.
    """
    box = data["box"]  # (nframe, 9)
    coord = data["coord"]  # (nframe, natom*3)
    typ = data["type"]
    nframe = coord.shape[0]
    natom = coord.shape[1] // 3
    coords = coord.reshape(nframe, natom, 3)
    boxes = box.reshape(nframe, 3, 3)
    Lz = float(boxes[0, 2, 2])
    A = float(boxes[0, 0, 0] * boxes[0, 1, 1])  # cell cross-section (xy)

    c_mask = typ == 2
    o_mask = typ == 0

    # Graphite plane z (mean folded C z over all frames).
    c_zs = coords[:, c_mask, 2] % Lz
    plane_z = float(np.mean(c_zs))

    # O distance to plane (minimum-image along z).
    o_zs = coords[:, o_mask, 2]
    dz = o_zs - plane_z
    dz = dz - Lz * np.round(dz / Lz)  # minimum-image to [-Lz/2, Lz/2]
    dist = np.abs(dz)

    hist, edges = np.histogram(
        dist, bins=np.arange(0.0, Lz / 2.0 + DENSITY_BIN_ANGSTROM, DENSITY_BIN_ANGSTROM)
    )
    centers = 0.5 * (edges[:-1] + edges[1:])
    # Number density (per nm^3): count / (nframe * A_angstrom^2 * bin_angstrom) * 1e3
    vol_per_bin_A3 = nframe * A * DENSITY_BIN_ANGSTROM
    density_per_nm3 = hist / vol_per_bin_A3 * 1e3

    # First hydration-layer peak: GLOBAL maximum of the smoothed density in the
    # physical window d in [2.0, Lz/2) A. (A naive "first local max after 1 A"
    # catches sparse-count noise: pristine graphene keeps water O out to ~2.5 A,
    # nearest O in the published trajectory is 1.62 A and the first REAL O layer
    # sits at ~3.3-3.5 A.)
    sm = np.convolve(density_per_nm3, np.ones(5) / 5.0, mode="same")
    start = int(2.0 / DENSITY_BIN_ANGSTROM)
    peak_bin = None
    if len(sm) > start + 1:
        seg = sm[start:]
        imax = int(np.argmax(seg)) + start
        if sm[imax] > 0.0:
            peak_bin = imax
    first_peak = float(centers[peak_bin]) if peak_bin is not None else float("nan")

    # Depletion width: exclusion-zone edge — distance from plane to the outward
    # rise of the first layer (first bin reaching 25% of the peak density).
    depletion_width = float("nan")
    if peak_bin is not None:
        peak_val = sm[peak_bin]
        for i in range(0, peak_bin + 1):
            if sm[i] >= 0.25 * peak_val:
                depletion_width = float(centers[i])
                break

    # 20 nearest-O z for a sanity tail (not used by the verifier).
    nearest_o = float(np.min(dist))

    return {
        "interface": DENSITY_INTERFACE,
        "cell_Lz_angstrom": Lz,
        "cell_area_angstrom2": A,
        "graphite_plane_z_angstrom": plane_z,
        "first_peak_position_angstrom": first_peak,
        "depletion_width_angstrom": depletion_width,
        "nearest_oxygen_angstrom": nearest_o,
        "bin_width_angstrom": DENSITY_BIN_ANGSTROM,
        "n_frames": nframe,
        "n_oxygen": int(np.sum(o_mask)),
        "density_units": "oxygen number density (nm^-3) vs minimum-image distance from graphite plane (angstrom)",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--holdout-frac", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    out = args.out
    hidden_frames = out / "hidden-frames"
    hidden_frames.mkdir(parents=True, exist_ok=True)

    manifest = {
        "benchmark_id": "042-go-water-dpmp",
        "role": "hidden_validation_manifest",
        "seed": args.seed,
        "holdout_frac": args.holdout_frac,
        "total_source_frames": 0,
        "total_held_out_frames": 0,
        "frames": [],
    }

    for iface in INTERFACES:
        src = args.source / iface
        if not src.is_dir():
            print(f"skip (missing): {src}", file=sys.stderr)
            continue
        data = load_set(src)
        nframe = data["coord"].shape[0]
        k = max(1, int(nframe * args.holdout_frac))
        idx = np.sort(rng.choice(nframe, size=k, replace=False))

        dst = hidden_frames / iface
        written = write_raw_set(dst, data, idx)
        manifest["total_source_frames"] += nframe
        manifest["total_held_out_frames"] += written

        for j in idx:
            manifest["frames"].append(
                {
                    "source": f"train_dataset/{iface}",
                    "source_frame": int(j),
                    "coords_hash_1e6": _coords_hash(data["coord"][j].reshape(-1, 3)),
                    "natoms": int(data["coord"].shape[1] // 3),
                }
            )

    density = compute_density_reference(load_set(args.source / DENSITY_INTERFACE))
    (out / "density-reference.json").write_text(
        json.dumps(density, indent=2) + "\n", encoding="utf-8"
    )

    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    print(json.dumps(
        {
            "total_source_frames": manifest["total_source_frames"],
            "total_held_out_frames": manifest["total_held_out_frames"],
            "density_reference": density,
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
