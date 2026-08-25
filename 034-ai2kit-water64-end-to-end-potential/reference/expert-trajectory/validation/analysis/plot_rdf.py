#!/usr/bin/env python3
"""
AIMD vs DeepMD RDF 对比图 (论文风格)
横坐标: pm  纵坐标: g(r)
"""
import numpy as np
from ase.io import read
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ============================================================
# 配置
# ============================================================
AIMD_TRAJ = "/public/home/<site-user>/ai2kit/ai2kit/data/cp2k-aimd/raw-output/water64_aimd-pos-1.xyz"
MLP_TRAJ  = "/public/home/<site-user>/ai2kit/ai2kit/output/nvt-300K/dump.lammpstrj"
OUT_DIR   = "/public/home/<site-user>/ai2kit/ai2kit/output/rdf"
CELL      = [12.42, 12.42, 12.42]

RMAX  = 6.0      # Å
DR    = 0.02     # Å
NBINS = int(RMAX / DR)
SKIP_AIMD = 5    # AIMD 跳过前 5 帧 (equilibration)

# ============================================================
# RDF 计算函数 (手动, 最小镜像)
# ============================================================
def compute_rdf(frames, pair, rmax=RMAX, dr=DR, skip=0):
    """计算 RDF, 返回 (r_Å, g(r))"""
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

# ============================================================
# 读取轨迹
# ============================================================
print("读取 AIMD ...")
aimd_frames = read(AIMD_TRAJ, index=':')
for a in aimd_frames:
    a.set_cell(CELL)
    a.set_pbc(True)
print(f"  {len(aimd_frames)} 帧")

print("读取 MLP ...")
mlp_frames = read(MLP_TRAJ, index=':', format='lammps-dump-text')
for a in mlp_frames:
    types = a.arrays['type']
    a.set_chemical_symbols(['O' if t == 1 else 'H' for t in types])
print(f"  {len(mlp_frames)} 帧")

# ============================================================
# 计算 RDF
# ============================================================
pairs = [('O', 'O'), ('O', 'H'), ('H', 'H')]
labels = ['O-O', 'O-H', 'H-H']

results = {}
for pair, label in zip(pairs, labels):
    print(f"计算 {label} ...")
    r, g_aimd = compute_rdf(aimd_frames, pair, skip=SKIP_AIMD)
    _, g_mlp  = compute_rdf(mlp_frames, pair, skip=0)
    results[label] = (r, g_aimd, g_mlp)

    # 打印峰位
    i_a = np.argmax(g_aimd)
    i_m = np.argmax(g_mlp)
    print(f"  AIMD 峰位: {r[i_a]*100:.0f} pm  g(r)={g_aimd[i_a]:.2f}")
    print(f"  MLP  峰位: {r[i_m]*100:.0f} pm  g(r)={g_mlp[i_m]:.2f}")

# ============================================================
# 保存数据
# ============================================================
import os
os.makedirs(OUT_DIR, exist_ok=True)

for label in labels:
    r, g_a, g_m = results[label]
    np.savetxt(f"{OUT_DIR}/rdf_{label.replace('-','_')}_aimd.dat",
               np.column_stack([r * 100, g_a]),
               header="r(pm)  g(r)", fmt="%.4f")
    np.savetxt(f"{OUT_DIR}/rdf_{label.replace('-','_')}_mlp.dat",
               np.column_stack([r * 100, g_m]),
               header="r(pm)  g(r)", fmt="%.4f")

# ============================================================
# 论文风格绘图: 三子图
# ============================================================
fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))

styles = {
    'O-O': {'color': 'black', 'label_a': 'AIMD (BLYP-D3/TZV2P)', 'label_m': 'DeepMD'},
    'O-H': {'color': 'black', 'label_a': None, 'label_m': None},
    'H-H': {'color': 'black', 'label_a': None, 'label_m': None},
}

for ax, label in zip(axes, labels):
    r, g_a, g_m = results[label]
    r_pm = r * 100  # Å → pm

    ax.plot(r_pm, g_a, color='#1f77b4', ls='-', lw=1.8, label='AIMD')
    ax.plot(r_pm, g_m, color='#ff7f0e', ls='--', lw=1.8, label='MLP')

    ax.set_xlabel('Distance / pm', fontsize=13)
    ax.set_ylabel('g(r)', fontsize=13)
    ax.set_title(f'{label}', fontsize=14, fontweight='bold')
    ax.set_xlim(0, 600)
    ax.tick_params(labelsize=11)
    ax.legend(frameon=False, fontsize=10)

    # 参考线
    if label == 'O-O':
        ax.axvline(x=280, color='gray', ls=':', lw=0.8, alpha=0.6)
        ax.annotate('280 pm', xy=(285, ax.get_ylim()[1]*0.85), fontsize=9, color='gray')
    elif label == 'O-H':
        ax.axvline(x=100, color='gray', ls=':', lw=0.8, alpha=0.6)
        ax.axvline(x=180, color='gray', ls=':', lw=0.8, alpha=0.6)

plt.tight_layout()
out_png = f"{OUT_DIR}/compare_rdf_publication.png"
plt.savefig(out_png, dpi=600, bbox_inches='tight')
print(f"\n论文风格图: {out_png}")

# ============================================================
# 单独 O-O 图 (最核心)
# ============================================================
fig2, ax2 = plt.subplots(figsize=(5, 4))
r, g_a, g_m = results['O-O']
r_pm = r * 100

ax2.plot(r_pm, g_a, color='#1f77b4', ls='-', lw=2, label='AIMD')
ax2.plot(r_pm, g_m, color='#ff7f0e', ls='--', lw=2, label='MLP')
ax2.set_xlabel('Distance / pm', fontsize=14)
ax2.set_ylabel('g(r)', fontsize=14)
ax2.set_xlim(0, 600)
ax2.tick_params(labelsize=12)
ax2.legend(frameon=False, fontsize=12)
ax2.axvline(x=280, color='gray', ls=':', lw=0.8, alpha=0.6)

plt.tight_layout()
out_oo = f"{OUT_DIR}/compare_rdf_OO.png"
plt.savefig(out_oo, dpi=600, bbox_inches='tight')
print(f"O-O 单图: {out_oo}")

print("\n完成")
