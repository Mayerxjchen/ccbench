#!/usr/bin/env python3
"""
Generate the HIDDEN validation set for benchmark 034 from the expert AIMD
reference trajectory.

Design (§ CONTRACT.md hidden-validation): the agent starts from the unrelaxed
PACKMOL structure and generates its OWN first-principles data, so the expert's
190-frame AIMD mother set is a fair out-of-training test. This script:

  1. reads  reference/expert-trajectory/aimd/processed/aimd.xyz  (190 labeled frames)
  2. writes dft-validation.extxyz   (the hidden E/F reference the verifier grades)
  3. computes the RDF reference (O-O / O-H / H-H first peaks + curve) from those
     frames and writes rdf-reference.json
  4. writes manifest.json (provenance + hashes + params)

No new HPC CP2K run is required: the hidden set is derived from the archived
expert trajectory. Rerun after any change to aimd.xyz; outputs are committed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from ase.io import read

# ---------------------------------------------------------------------------
# RDF computation (mirrors the expert analysis/plot_rdf.py compute_rdf)
# ---------------------------------------------------------------------------

RMAX = 6.0  # Angstrom
DR = 0.02   # Angstrom


def compute_rdf(frames, pair, rmax=RMAX, dr=DR, skip=0):
    """Return (r_A, g(r)) for a pair of elements, minimum-image PBC."""
    bins = np.arange(0, rmax + dr, dr)
    hist = np.zeros(len(bins) - 1)
    n_valid = 0
    total_ref = 0
    total_density = 0.0
    elem_a, elem_b = pair

    for atoms in frames[skip:]:
        symbols = np.array(atoms.get_chemical_symbols())
        pos = atoms.get_positions()
        cell_len = np.array(atoms.get_cell().lengths())
        volume = np.prod(cell_len)
        idx_a = np.where(symbols == elem_a)[0]
        idx_b = np.where(symbols == elem_b)[0]
        if len(idx_a) == 0 or len(idx_b) == 0:
            continue
        n_valid += 1
        total_ref += len(idx_a)
        total_density += len(idx_b) / volume
        dists = []
        for i in idx_a:
            for j in idx_b:
                if elem_a == elem_b and i == j:
                    continue
                rij = pos[j] - pos[i]
                rij = rij - cell_len * np.round(rij / cell_len)
                d = np.linalg.norm(rij)
                if d < rmax:
                    dists.append(d)
        hist += np.histogram(dists, bins=bins)[0]

    r = 0.5 * (bins[:-1] + bins[1:])
    shell_vol = 4.0 * np.pi * r**2 * dr
    rdf = hist / (n_valid * (total_ref / n_valid) * (total_density / n_valid) * shell_vol)
    return r, rdf


def first_peak(r, g, lo=0.0, hi=6.0):
    """First maximum of g(r) in [lo, hi] Angstrom (binary length peak)."""
    mask = (r >= lo) & (r <= hi)
    if not np.any(mask):
        return None
    i = np.argmax(g[mask])
    return float(r[mask][i])


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def coords_hash(pos, prec: float = 3) -> str:
    """Canonical frame fingerprint, byte-identical to tests/verifier.py._coords_hash.

    The verifier recomputes this exact string over an agent's DeepMD coord.npy
    (rounded to 3 decimals, row-major concatenation with commas).  Storing the
    same digest per hidden frame makes I3 (no-train-test-overlap) an exact
    string-set intersection instead of a tolerance-based comparison.
    """
    return ",".join(f"{float(x):.{prec}f}" for x in np.asarray(pos).reshape(-1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference-root", type=Path,
                    default=Path(__file__).resolve().parents[1])
    args = ap.parse_args()

    root: Path = args.reference_root          # reference/hidden-validation
    expert = root.parent / "expert-trajectory"
    aimd_xyz = expert / "aimd" / "processed" / "aimd.xyz"
    filter_tsv = expert / "aimd" / "processed" / "filter.tsv"

    if not aimd_xyz.is_file():
        raise SystemExit(f"missing expert mother set: {aimd_xyz}")

    # 1. Load frames
    frames = read(str(aimd_xyz), index=":")
    n = len(frames)
    natoms = len(frames[0])
    symbols = frames[0].get_chemical_symbols()
    print(f"loaded {n} frames x {natoms} atoms; types {set(symbols)}")

    # 2. dft-validation.extxyz  (byte-identical normalized copy)
    out_ext = root / "dft-validation.extxyz"
    shutil.copyfile(aimd_xyz, out_ext)
    print(f"wrote {out_ext} ({out_ext.stat().st_size} bytes)")

    # 3. RDF reference from all 190 frames (equilibration is already removed
    #    by the 250-350K filter; no extra skip).
    cell = frames[0].get_cell().lengths()
    for a in frames:
        a.set_pbc(True)
        a.set_cell(cell)
    pairs = [("O", "O"), ("O", "H"), ("H", "H")]
    labels = ["OO", "OH", "HH"]
    rdf = {}
    for pair, label in zip(pairs, labels):
        r, g = compute_rdf(frames, pair)
        peak = first_peak(r, g)
        rdf[label] = {
            "first_peak_angstrom": peak,
            "r_angstrom": [round(float(x), 4) for x in r],
            "g_r": [round(float(x), 4) for x in g],
        }
        print(f"  RDF {label}: first peak {peak:.3f} A")

    rdf_path = root / "rdf-reference.json"
    rdf_path.write_text(json.dumps(rdf, indent=2) + "\n")
    print(f"wrote {rdf_path}")

    # 4. manifest
    temp_band = None
    if filter_tsv.is_file():
        temps = []
        with open(filter_tsv) as fh:
            header = fh.readline()
            for line in fh:
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.split("\t")
                try:
                    temps.append(float(parts[2]))
                except (ValueError, IndexError):
                    continue
        temp_band = {"min_K": min(temps), "max_K": max(temps)} if temps else None

    # 3b. Per-frame provenance: raw CP2K AIMD step, time, temperature (from the
    #     filter, which is computed from the CP2K ener output), energy, and a
    #     verifier-compatible coordinate hash.  Ordered to match frame order in
    #     dft-validation.extxyz so an independent I3 check is a 1:1 mapping.
    # filter.tsv may list a step TWICE: once `kept` (selected=1, it IS in the
    # mother set) and once `duplicate_step` (selected=0, a secondary
    # near-duplicate annotation on a frame that was still kept).  Prefer the
    # selected=1 row for temp/selected, and record the duplicate annotation
    # separately so per-frame provenance stays truthful.
    filter_map = {}
    if filter_tsv.is_file():
        with open(filter_tsv) as fh:
            header = fh.readline()
            for line in fh:
                if not line.strip() or line.startswith("#"):
                    continue
                p = line.split("\t")
                try:
                    step = int(p[0])
                    row = {
                        "time_fs": float(p[1]),
                        "temperature_K": float(p[2]),
                        "selected": int(p[3]),
                        "reason": p[4].strip(),
                    }
                except (ValueError, IndexError):
                    continue
                prev = filter_map.get(step)
                if prev is None or row["selected"] == 1:
                    filter_map[step] = row
                if prev is not None and row["selected"] == 0:
                    filter_map[step]["annotated_duplicate_step"] = True
    per_frame = []
    for f in frames:
        # ASE's extxyz reader splits the header: `i=`/`time=` land in .info,
        # `energy=` becomes the SinglePointCalculator.  `i` is the RAW CP2K AIMD
        # step (ener/pos cadence: 0,50,100,...), which joins to filter.tsv.
        info = dict(f.info or {})
        step = int(info["i"]) if "i" in info else None
        time_fs = float(info["time"]) if "time" in info else None
        e_hartree = float(f.get_potential_energy()) if f.calc is not None else None
        filt = filter_map.get(step, {}) if step is not None else {}
        per_frame.append({
            "index_in_extxyz": len(per_frame),
            "raw_aimd_step": step,
            "time_fs": time_fs,
            "temperature_K": filt.get("temperature_K") or None,
            "energy_hartree": e_hartree,
            "selected": bool(filt.get("selected")) if filt else True,
            "filter_reason": filt.get("reason"),
            "annotated_duplicate_step": bool(filt.get("annotated_duplicate_step")),
            "coords_hash_prec3": coords_hash(f.get_positions(), 3),
        })
    n_selected = sum(1 for x in per_frame if x["selected"])

    manifest = {
        "benchmark_id": "034-ai2kit-water64-end-to-end-potential",
        "role": "hidden_validation_set",
        "generated_by": str(Path(__file__).name),
        "generated_from": {
            "aimd.xyz": str(aimd_xyz),
            "aimd.xyz_sha256": sha256(aimd_xyz),
            "filter.tsv": str(filter_tsv) if filter_tsv.is_file() else None,
            "filter.tsv_sha256": sha256(filter_tsv) if filter_tsv.is_file() else None,
            "raw_aimd_pos": str(expert / "aimd" / "raw" / "water64_aimd-pos-1.xyz"),
            "raw_aimd_frc": str(expert / "aimd" / "raw" / "water64_aimd-frc-1.xyz"),
            "raw_aimd_ener": str(expert / "aimd" / "raw" / "water64_aimd-1.ener"),
        },
        "system": {
            "molecule": "64 H2O",
            "natoms": natoms,
            "cell_angstrom": [float(x) for x in cell],
            "type_map": ["O", "H"],
            "dft": "CP2K BLYP-D3 / TZV2P-GTH (AIMD, 300 K NVT)",
        },
        "frames": n,
        "frames_selected_kept": n_selected,
        "temperature_window_K_from_filter": temp_band,
        "per_frame_provenance": per_frame,
        "energy_unit": "eV",
        "force_unit": "eV/A",
        "usage": {
            "L7": "hidden E/F: deepmd inference on dft-validation.extxyz",
            "L9": "hidden RDF reference: first peaks in rdf-reference.json",
            "I3": "no-train-test overlap: intersect per_frame_provenance[].coords_hash_prec3 with agent training frame hashes (same string scheme as verifier._coords_hash)",
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {root / 'manifest.json'}")
    print("done.")


if __name__ == "__main__":
    main()
