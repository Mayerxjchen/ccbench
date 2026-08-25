#!/usr/bin/env python3
"""生成 018-deepmd-rcut 的 reference。

018 的正确答案由几何规则决定,不是数值收敛表:
    d_max = 训练集最大原子间距(minimum-image PBC)
    rcut  ∈ allowed,取最小满足 rcut >= d_max + 2.0 的值

本脚本直接从 environment/training_data 重算 d_max,确保 ground truth 可复现。

用法:
    uv run python 018-deepmd-rcut/reference/generate_reference.py

输出:
    reference/reference.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent  # 018-deepmd-rcut/
TRAIN = ROOT / "environment" / "training_data"
OUT_JSON = ROOT / "reference" / "reference.json"

MARGIN = 2.0
ALLOWED = [4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0]


def max_interatomic_distance_pbc(sysdir: Path) -> float:
    """minimum-image PBC 下所有帧的最大原子间距(Angstrom)。"""
    coord = np.load(sysdir / "set.000" / "coord.npy")
    box = np.load(sysdir / "set.000" / "box.npy")
    natom = coord.shape[1] // 3
    coord = coord.reshape(coord.shape[0], natom, 3)
    box = box.reshape(box.shape[0], 3, 3)

    maxd = 0.0
    for i in range(coord.shape[0]):
        c = coord[i]
        b = box[i]
        inv = np.linalg.inv(b)
        for a in range(natom):
            for a2 in range(a + 1, natom):
                f = (c[a] - c[a2]) @ inv
                f -= np.round(f)
                dmin = f @ b
                maxd = max(maxd, float(np.linalg.norm(dmin)))
    return maxd


def main() -> None:
    if not TRAIN.is_dir():
        raise SystemExit(f"训练数据缺失:{TRAIN}")

    coord = np.load(TRAIN / "set.000" / "coord.npy")
    n_frames = coord.shape[0]
    n_atoms = coord.shape[1] // 3

    d_max = max_interatomic_distance_pbc(TRAIN)

    threshold = d_max + MARGIN
    expected = next((r for r in ALLOWED if r >= threshold), None)

    ref = {
        "distance_definition": "minimum-image PBC",
        "training_frames": n_frames,
        "validation_frames": 40,
        "atoms_per_frame": n_atoms,
        "margin": MARGIN,
        "allowed_rcut": ALLOWED,
        "d_max_training": d_max,
        "rcut_threshold": threshold,
        "expected_rcut": expected,
        "generated_by": str(ROOT / "reference" / "generate_reference.py"),
    }
    OUT_JSON.write_text(json.dumps(ref, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"reference.json → {OUT_JSON}")
    print(f"d_max(training) = {d_max:.4f} Å  →  rcut >= {threshold:.4f}  →  expected = {expected}")


if __name__ == "__main__":
    main()
