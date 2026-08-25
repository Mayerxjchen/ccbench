#!/bin/bash

#SBATCH -N 1 -c 16
#SBATCH --job-name=compare-rdf
#SBATCH --partition=cpu
#SBATCH --output=/public/home/<site-user>/ai2kit/ai2kit/test/rdf/output/compare_slurm.out
#SBATCH --error=/public/home/<site-user>/ai2kit/ai2kit/test/rdf/output/compare_slurm.out

set -e

PROJECT_DIR="/public/home/<site-user>/ai2kit/ai2kit"
OUTPUT_DIR="$PROJECT_DIR/test/rdf/output"
AIMD_TRAJ="$PROJECT_DIR/config/aimd.xyz"
MLP_TRAJ="$PROJECT_DIR/test/nvt/output/dump.lammpstrj"
MLP_SKIP_FRAMES="${MLP_SKIP_FRAMES:-100}"

MODE="${1:-all}"
case "$MODE" in
    all|--all)
        MODE="all"
        ;;
    --plot-only|plot-only)
        MODE="plot"
        ;;
    --compute-only|compute-only)
        MODE="compute"
        ;;
    -h|--help)
        echo "Usage: bash test/rdf/compare_rdf.sh [all|--plot-only|--compute-only]"
        echo ""
        echo "  all             重新计算 RDF 数据并绘图，默认模式"
        echo "  --plot-only     只读取已有 rdf_*.dat 绘图，不重新计算 RDF"
        echo "  --compute-only  只计算 rdf_*.dat，不绘图"
        exit 0
        ;;
    *)
        echo "ERROR: unknown mode: $MODE"
        echo "Usage: bash test/rdf/compare_rdf.sh [all|--plot-only|--compute-only]"
        exit 1
        ;;
esac

mkdir -p "$OUTPUT_DIR"

source /etc/profile.d/modules.sh
module load anaconda/2022.5
eval "$(conda shell.bash hook)"
conda activate ai2kit

export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK:-1}
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

echo "============================================"
echo "Phase 8c: AIMD vs MLP RDF 对比"
echo "模式: $MODE"
echo "AIMD: $AIMD_TRAJ"
echo "MLP:  $MLP_TRAJ"
echo "MLP skip frames: $MLP_SKIP_FRAMES"
echo "输出: $OUTPUT_DIR"
echo "============================================"

if [ "$MODE" != "plot" ]; then
export AIMD_TRAJ MLP_TRAJ OUTPUT_DIR MLP_SKIP_FRAMES
/public/home/<site-user>/.conda/envs/ai2kit/bin/python << 'PYEOF'
import os
import numpy as np
from ase.io import read

AIMD_TRAJ = os.environ["AIMD_TRAJ"]
MLP_TRAJ = os.environ["MLP_TRAJ"]
OUT = os.environ["OUTPUT_DIR"]
MLP_SKIP_FRAMES = int(os.environ.get("MLP_SKIP_FRAMES", "100"))

rmax = 6.0
nbins = 200
dr = rmax / nbins
r_axis = np.linspace(dr / 2, rmax - dr / 2, nbins)
bins = np.linspace(0.0, rmax, nbins + 1)
shell_vol = 4.0 * np.pi * r_axis**2 * dr

print("读取 AIMD 轨迹 ...")
aimd_frames = read(AIMD_TRAJ, index=":")
for atoms in aimd_frames:
    atoms.set_pbc(True)
print(f"  AIMD: {len(aimd_frames)} 帧")
print(f"  AIMD cell: {aimd_frames[0].get_cell().lengths()} A")

print("读取 MLP 轨迹 ...")
mlp_frames = read(MLP_TRAJ, index=":", format="lammps-dump-text")
if MLP_SKIP_FRAMES < 0:
    raise ValueError("MLP_SKIP_FRAMES must be >= 0")
if MLP_SKIP_FRAMES >= len(mlp_frames):
    raise ValueError(
        f"MLP_SKIP_FRAMES={MLP_SKIP_FRAMES} >= total MLP frames={len(mlp_frames)}"
    )
if MLP_SKIP_FRAMES > 0:
    print(f"  skip first {MLP_SKIP_FRAMES} MLP frames as equilibration")
    mlp_frames = mlp_frames[MLP_SKIP_FRAMES:]
for atoms in mlp_frames:
    types = atoms.arrays["type"]
    symbols = ["O" if t == 1 else "H" for t in types]
    atoms.set_chemical_symbols(symbols)
    atoms.set_atomic_numbers(np.array([8 if t == 1 else 1 for t in types]))
print(f"  MLP:  {len(mlp_frames)} 帧")
print(f"  MLP cell: {mlp_frames[0].get_cell().lengths()} A")


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
            print(f"  警告: {label} 帧 {i} 缺少元素或盒子异常")
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

        if (i + 1) % 100 == 0:
            print(f"  {label}: 已处理 {i + 1}/{len(frames)} 帧")

    if n_valid == 0:
        raise RuntimeError(f"{label} 没有任何有效 RDF 帧")

    print(f"  {label}: 有效帧 {n_valid}/{len(frames)}")
    avg_ref = total_ref / n_valid
    avg_density = total_density / n_valid
    return hist / (n_valid * avg_ref * avg_density * shell_vol)


pairs = {
    "O-O": ("O", "O"),
    "O-H": ("O", "H"),
    "H-H": ("H", "H"),
}

for name, elements in pairs.items():
    print(f"\n计算 {name} RDF ...")
    print("  AIMD:")
    g_aimd = compute_avg_rdf(aimd_frames, elements, f"AIMD {name}")
    print("  MLP:")
    g_mlp = compute_avg_rdf(mlp_frames, elements, f"MLP {name}")

    stem = name.replace("-", "_")
    np.savetxt(
        f"{OUT}/rdf_{stem}_aimd.dat",
        np.column_stack([r_axis * 100, g_aimd]),
        header="r(pm)  g(r)",
        fmt="%.6f",
    )
    np.savetxt(
        f"{OUT}/rdf_{stem}_mlp.dat",
        np.column_stack([r_axis * 100, g_mlp]),
        header="r(pm)  g(r)",
        fmt="%.6f",
    )

print("\nRDF 数据已保存")
PYEOF
fi

if [ "$MODE" != "compute" ]; then
for f in \
    "$OUTPUT_DIR/rdf_O_O_aimd.dat" "$OUTPUT_DIR/rdf_O_O_mlp.dat" \
    "$OUTPUT_DIR/rdf_O_H_aimd.dat" "$OUTPUT_DIR/rdf_O_H_mlp.dat" \
    "$OUTPUT_DIR/rdf_H_H_aimd.dat" "$OUTPUT_DIR/rdf_H_H_mlp.dat"
do
    if [ ! -f "$f" ]; then
        echo "ERROR: 缺少 RDF 数据文件: $f"
        echo "请先运行: bash test/rdf/compare_rdf.sh --compute-only"
        exit 1
    fi
done

/public/home/<site-user>/.conda/envs/ai2kit/bin/python << 'PYEOF'
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = "/public/home/<site-user>/ai2kit/ai2kit/test/rdf/output"

pairs = {
    "O-O": "O_O",
    "O-H": "O_H",
    "H-H": "H_H",
}

results = {}
for name, stem in pairs.items():
    aimd = np.loadtxt(f"{OUT}/rdf_{stem}_aimd.dat")
    mlp = np.loadtxt(f"{OUT}/rdf_{stem}_mlp.dat")
    r_aimd = aimd[:, 0]
    r_mlp = mlp[:, 0]
    if np.nanmax(r_aimd) < 20:
        r_aimd = r_aimd * 100
    if np.nanmax(r_mlp) < 20:
        r_mlp = r_mlp * 100
    results[name] = {
        "r_pm": r_aimd,
        "aimd": aimd[:, 1],
        "mlp_r_pm": r_mlp,
        "mlp": mlp[:, 1],
    }

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 12,
    "axes.linewidth": 1.4,
    "xtick.major.width": 1.4,
    "ytick.major.width": 1.4,
    "xtick.major.size": 5,
    "ytick.major.size": 5,
})


def style_axis(ax, panel_label=None, show_legend=True):
    ax.set_xlabel("Distance (pm)", fontsize=13)
    ax.set_ylabel(r"$g(r)$", fontsize=13)
    ax.set_xlim(0, 600)
    ax.set_ylim(bottom=0)
    ax.tick_params(direction="out", labelsize=11)
    for spine in ax.spines.values():
        spine.set_linewidth(1.4)
    if show_legend:
        ax.legend(frameon=False, fontsize=11, loc="upper right")
    if panel_label:
        ax.text(
            -0.13, 1.04, panel_label,
            transform=ax.transAxes,
            fontsize=18, fontweight="bold",
            va="bottom", ha="left",
        )


panel_labels = {"O-O": "a", "O-H": "b", "H-H": "c"}

fig, axes = plt.subplots(1, 3, figsize=(13.5, 3.6))
for ax, name in zip(axes, pairs):
    data = results[name]
    ax.plot(data["r_pm"], data["aimd"], color="#222222", ls="-", lw=2.0, label="AIMD")
    ax.plot(data["mlp_r_pm"], data["mlp"], color="#d62728", ls="--", lw=2.0, label="MLP")
    ax.set_title(name, fontsize=13, pad=6)
    style_axis(ax, panel_labels[name], show_legend=True)

plt.tight_layout(w_pad=2.2)
plt.savefig(f"{OUT}/compare_rdf.png", dpi=600, bbox_inches="tight")
plt.close(fig)
print(f"RDF 三联图已保存: {OUT}/compare_rdf.png")

single_names = {"O-O": "OO", "O-H": "OH", "H-H": "HH"}
for name, suffix in single_names.items():
    data = results[name]
    fig_single, ax_single = plt.subplots(figsize=(4.2, 3.2))
    ax_single.plot(data["r_pm"], data["aimd"], color="#222222", ls="-", lw=2.0, label="AIMD")
    ax_single.plot(data["mlp_r_pm"], data["mlp"], color="#d62728", ls="--", lw=2.0, label="MLP")
    style_axis(ax_single, panel_label=panel_labels[name], show_legend=True)
    plt.tight_layout()
    out_single = f"{OUT}/compare_rdf_{suffix}.png"
    plt.savefig(out_single, dpi=600, bbox_inches="tight")
    plt.close(fig_single)
    print(f"{name} RDF 单图已保存: {out_single}")

print("\n" + "=" * 50)
print("RDF 峰位对比")
print("=" * 50)
for name, data in results.items():
    mask_a = data["r_pm"] >= 50
    mask_m = data["mlp_r_pm"] >= 50
    peak_a = data["r_pm"][mask_a][np.argmax(data["aimd"][mask_a])]
    peak_m = data["mlp_r_pm"][mask_m][np.argmax(data["mlp"][mask_m])]
    print(f"  {name}  AIMD: {peak_a:.0f} pm  MLP: {peak_m:.0f} pm  Δ = {abs(peak_a - peak_m):.0f} pm")

print("\n完成")
PYEOF
fi

echo "============================================"
echo "RDF 对比完成"
echo "模式: $MODE"
echo "三联图: $OUTPUT_DIR/compare_rdf.png"
echo "O-O 单图: $OUTPUT_DIR/compare_rdf_OO.png"
echo "O-H 单图: $OUTPUT_DIR/compare_rdf_OH.png"
echo "H-H 单图: $OUTPUT_DIR/compare_rdf_HH.png"
echo "数据: $OUTPUT_DIR/rdf_*_aimd.dat / rdf_*_mlp.dat"
echo "============================================"
