#!/bin/bash
# ============================================================================
# 04-validation/rdf — O-O / O-H / H-H RDF: AIMD mother set vs MLP NVT trajectory.
#
# Input : $AI2KIT_CFG_DIR/aimd.xyz                    (filtered mother set)
#         $AI2KIT_VALIDATION_DIR/nvt/dump.lammpstrj   (NVT MLP trajectory)
# Output: $AI2KIT_VALIDATION_DIR/rdf/rdf_*_aimd.dat / rdf_*_mlp.dat
#         $AI2KIT_VALIDATION_DIR/rdf/compare_rdf*.png
#         $AI2KIT_VALIDATION_DIR/rdf/rdf.done
#
# Adapted from reference/expert-trajectory/validation/analysis/compare_rdf.sh:
# box read from the trajectories (no hard-coded 12.42 A), MLP skips the first
# AI2KIT_RDF_SKIP frames as equilibration. Pure python (ase + numpy) + matplotlib.
# ============================================================================
set -euo pipefail
source "$(dirname "$0")/../../env.sh"

RDF_DIR="$AI2KIT_VALIDATION_DIR/rdf"
mkdir -p "$RDF_DIR"

AIMD_TRAJ="$AI2KIT_CFG_DIR/aimd.xyz"
MLP_TRAJ="$AI2KIT_VALIDATION_DIR/nvt/dump.lammpstrj"
MLP_SKIP_FRAMES="${AI2KIT_RDF_SKIP:-20}"

if [ ! -f "$MLP_TRAJ" ]; then
    echo "[rdf] WARNING: $MLP_TRAJ not found -> computing AIMD-only reference RDF"
fi

export AIMD_TRAJ MLP_TRAJ MLP_SKIP_FRAMES RDF_DIR
python << 'PYEOF'
import os
import numpy as np
from ase.io import read

AIMD = os.environ["AIMD_TRAJ"]
MLP = os.environ["MLP_TRAJ"]
OUT = os.environ["RDF_DIR"]
MLP_SKIP = int(os.environ.get("MLP_SKIP_FRAMES", "20"))

rmax = 6.0
nbins = 200
dr = rmax / nbins
r_axis = np.linspace(dr / 2, rmax - dr / 2, nbins)
bins = np.linspace(0.0, rmax, nbins + 1)
shell_vol = 4.0 * np.pi * r_axis**2 * dr


def compute_avg_rdf(frames, elements, label):
    hist = np.zeros(nbins)
    n_valid = 0
    total_ref = 0
    total_density = 0.0
    elem_a, elem_b = elements
    for i, atoms in enumerate(frames):
        symbols = np.array(atoms.get_chemical_symbols())
        pos = atoms.get_positions()
        cell_len = np.array(atoms.get_cell().lengths())
        volume = float(np.prod(cell_len))
        idx_a = np.where(symbols == elem_a)[0]
        idx_b = np.where(symbols == elem_b)[0]
        if len(idx_a) == 0 or len(idx_b) == 0 or volume <= 0:
            continue
        dists = []
        for ia in idx_a:
            rij = pos[idx_b] - pos[ia]
            rij -= cell_len * np.round(rij / cell_len)
            dist = np.linalg.norm(rij, axis=1)
            if elem_a == elem_b:
                dist = dist[idx_b != ia]
            dists.extend(dist[dist < rmax])
        hist += np.histogram(dists, bins=bins)[0]
        n_valid += 1
        total_ref += len(idx_a)
        total_density += len(idx_b) / volume
    if n_valid == 0:
        raise RuntimeError(f"{label} has no valid frames")
    avg_ref = total_ref / n_valid
    avg_density = total_density / n_valid
    return hist / (n_valid * avg_ref * avg_density * shell_vol)


print(f"reading AIMD: {AIMD}")
aimd_frames = read(AIMD, index=":")
for atoms in aimd_frames:
    atoms.set_pbc(True)
print(f"  AIMD: {len(aimd_frames)} frames, cell {aimd_frames[0].get_cell().lengths()} A")

mlp_frames = []
if os.path.exists(MLP):
    print(f"reading MLP: {MLP}")
    mlp_frames = read(MLP, index=":", format="lammps-dump-text")
    if MLP_SKIP < len(mlp_frames):
        print(f"  skip first {MLP_SKIP} MLP frames as equilibration")
        mlp_frames = mlp_frames[MLP_SKIP:]
    for atoms in mlp_frames:
        # ASE's lammps-dump-text reader does NOT expose the `type` column as an
        # array; the dump's `type` ids land in `numbers` (LAMMPS type 1=O, 2=H,
        # which ASE naively maps to atomic numbers 1=H, 2=He). Remap from
        # `numbers` as LAMMPS type ids -> the true O/H symbols.
        types = np.asarray(atoms.numbers)  # LAMMPS type ids (1=O, 2=H)
        symbols = ["O" if t == 1 else "H" for t in types]
        atoms.set_chemical_symbols(symbols)
        atoms.set_atomic_numbers(np.array([8 if t == 1 else 1 for t in types]))
    print(f"  MLP: {len(mlp_frames)} frames, cell {mlp_frames[0].get_cell().lengths()} A")

pairs = {"O-O": ("O", "O"), "O-H": ("O", "H"), "H-H": ("H", "H")}
peaks = {}
for name, elements in pairs.items():
    g_aimd = compute_avg_rdf(aimd_frames, elements, f"AIMD {name}")
    stem = name.replace("-", "_")
    np.savetxt(f"{OUT}/rdf_{stem}_aimd.dat",
               np.column_stack([r_axis * 100, g_aimd]), header="r(pm)  g(r)", fmt="%.6f")
    if mlp_frames:
        g_mlp = compute_avg_rdf(mlp_frames, elements, f"MLP {name}")
        np.savetxt(f"{OUT}/rdf_{stem}_mlp.dat",
                   np.column_stack([r_axis * 100, g_mlp]), header="r(pm)  g(r)", fmt="%.6f")
        mask_a = r_axis * 100 >= 50
        mask_m = r_axis * 100 >= 50
        peak_a = (r_axis * 100)[mask_a][np.argmax(g_aimd[mask_a])]
        peak_m = (r_axis * 100)[mask_m][np.argmax(g_mlp[mask_m])]
        peaks[name] = (peak_a, peak_m)
        print(f"{name:>3s}  AIMD peak {peak_a:.0f} pm   MLP peak {peak_m:.0f} pm   d = {abs(peak_a - peak_m):.0f} pm")
    else:
        mask_a = r_axis * 100 >= 50
        peak_a = (r_axis * 100)[mask_a][np.argmax(g_aimd[mask_a])]
        peaks[name] = (peak_a, None)
        print(f"{name:>3s}  AIMD peak {peak_a:.0f} pm   (no MLP trajectory)")

# plot
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 3.6))
    for ax, name in zip(axes, pairs):
        stem = name.replace("-", "_")
        ad = np.loadtxt(f"{OUT}/rdf_{stem}_aimd.dat")
        ax.plot(ad[:, 0], ad[:, 1], color="#222222", ls="-", lw=2.0, label="AIMD")
        if os.path.exists(f"{OUT}/rdf_{stem}_mlp.dat"):
            md = np.loadtxt(f"{OUT}/rdf_{stem}_mlp.dat")
            ax.plot(md[:, 0], md[:, 1], color="#d62728", ls="--", lw=2.0, label="MLP")
        ax.set_title(name)
        ax.set_xlabel("Distance (pm)")
        ax.set_ylabel("$g(r)$")
        ax.set_xlim(0, 600)
        ax.legend(frameon=False)
    plt.tight_layout(w_pad=2.2)
    plt.savefig(f"{OUT}/compare_rdf.png", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"saved {OUT}/compare_rdf.png")
except Exception as e:
    print(f"WARNING: plotting failed: {e}")
PYEOF

touch "$RDF_DIR/rdf.done"
echo "[rdf] done -> $RDF_DIR"
