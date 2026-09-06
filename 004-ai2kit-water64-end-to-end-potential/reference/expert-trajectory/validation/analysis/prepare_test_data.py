#!/usr/bin/env python3
"""
从已筛选的 AIMD extxyz 轨迹中准备 DeepMD held-out 测试集。

这个脚本和 workflow/setup.sh 使用同一套分帧规则：
  - workflow/setup.sh 从 config/aimd.xyz 等间距抽取 50 帧作为初始训练集。
  - 本脚本用同样的等间距规则找出这 50 帧，然后把剩余帧写成测试集。
  - 因此测试集不会和初始训练集重复。

当前 water64 项目说明：
  - config/aimd.xyz 应该是已经过滤过的 AIMD labeled extxyz。
  - 如果 config/aimd.xyz 有 55 帧，默认 50 帧进入初始训练集，剩余 5 帧进入测试集。
  - 如果以后 AIMD 帧数变成 100/200，本脚本会自动保留 50 帧之外的所有帧作为测试集。
  - 如果总帧数少于 --train-frames，会直接报错，不会强行切分。

示例：
  # 使用默认项目路径（输出到 test/dp-test/test-data/O64H128）
  python test/dp-test/prepare_test_data.py

  # 如果 setup.sh 未来改成只抽 40 帧训练，这里也要对应改成 40
  python test/dp-test/prepare_test_data.py --train-frames 40

  # 写到自定义测试集目录
  python test/dp-test/prepare_test_data.py --output test/dp-test/test-data-40
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import numpy as np
from ase.io import read


PROJECT_DIR = Path("/public/home/<site-user>/ai2kit/ai2kit")
DEFAULT_AIMD_XYZ = PROJECT_DIR / "config" / "aimd.xyz"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "test" / "dp-test" / "test-data" / "O64H128"
DEFAULT_TYPE_MAP = ("O", "H")


def even_sample_indices(total: int, size: int) -> list[int]:
    """返回与 ai2-kit sample 命令一致的确定性等间距采样索引。"""
    if size <= 0:
        raise ValueError("--train-frames must be positive")
    if size > total:
        raise ValueError(f"--train-frames ({size}) cannot exceed total frames ({total})")

    interval = total / size
    return [int(i * interval) for i in range(size)]


def get_forces(atoms) -> np.ndarray:
    if atoms.calc is not None and "forces" in atoms.calc.results:
        return atoms.calc.results["forces"]
    return atoms.get_array("forces")


def get_energy(atoms) -> float:
    if atoms.calc is not None and "energy" in atoms.calc.results:
        return float(atoms.calc.results["energy"])
    if "energy" in atoms.info:
        return float(atoms.info["energy"])
    raise KeyError("Frame has no energy in atoms.calc.results or atoms.info")


def write_deepmd(frames, output_dir: Path, type_map: tuple[str, ...], overwrite: bool) -> None:
    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(f"{output_dir} already exists; use --overwrite to replace it")
        shutil.rmtree(output_dir)

    set_dir = output_dir / "set.000"
    set_dir.mkdir(parents=True, exist_ok=True)

    n_frames = len(frames)
    n_atoms = len(frames[0])

    coords = np.zeros((n_frames, n_atoms * 3))
    forces = np.zeros((n_frames, n_atoms * 3))
    energies = np.zeros(n_frames)
    boxes = np.zeros((n_frames, 9))

    type_map_reverse = {name: idx for idx, name in enumerate(type_map)}
    symbols = frames[0].get_chemical_symbols()
    try:
        type_raw = np.array([type_map_reverse[s] for s in symbols], dtype=int)
    except KeyError as exc:
        raise ValueError(f"Unknown element {exc.args[0]!r}; type_map={type_map}") from exc

    for i, atoms in enumerate(frames):
        if atoms.get_chemical_symbols() != symbols:
            raise ValueError(f"Frame {i} atom order differs from frame 0")
        coords[i] = atoms.get_positions().reshape(-1)
        forces[i] = get_forces(atoms).reshape(-1)
        energies[i] = get_energy(atoms)
        boxes[i] = atoms.get_cell().array.reshape(-1)

    np.save(set_dir / "coord.npy", coords)
    np.save(set_dir / "force.npy", forces)
    np.save(set_dir / "energy.npy", energies)
    np.save(set_dir / "box.npy", boxes)
    np.savetxt(output_dir / "type.raw", type_raw, fmt="%d")
    (output_dir / "type_map.raw").write_text("\n".join(type_map) + "\n")

    print(f"测试集已保存到: {output_dir}")
    print(f"  frames: {n_frames}, atoms/frame: {n_atoms}")
    print(f"  coord.npy:  {coords.shape}")
    print(f"  force.npy:  {forces.shape}")
    print(f"  energy.npy: {energies.shape}")
    print(f"  box.npy:    {boxes.shape}")
    print(f"  type.raw:   O={np.sum(type_raw == 0)}, H={np.sum(type_raw == 1)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a held-out DeepMD test set from labeled AIMD extxyz."
    )
    parser.add_argument("--aimd", default=str(DEFAULT_AIMD_XYZ), help="labeled AIMD extxyz 文件")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR), help="输出 DeepMD system 目录")
    parser.add_argument(
        "--train-frames",
        type=int,
        default=50,
        help="workflow/setup.sh 用于初始训练的抽样帧数",
    )
    parser.add_argument(
        "--type-map",
        nargs="+",
        default=list(DEFAULT_TYPE_MAP),
        help="DeepMD type map，必须与训练一致，默认: O H",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=True,
        help="覆盖已有输出目录；默认启用",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    aimd_xyz = Path(args.aimd)
    output_dir = Path(args.output)
    type_map = tuple(args.type_map)

    print(f"读取 AIMD 轨迹: {aimd_xyz}")
    all_frames = read(aimd_xyz, index=":")
    n_total = len(all_frames)
    print(f"总帧数: {n_total}")

    train_indices = set(even_sample_indices(n_total, args.train_frames))
    test_indices = sorted(set(range(n_total)) - train_indices)

    if not test_indices:
        raise ValueError(
            "No held-out frames remain. Use fewer --train-frames or provide a longer aimd.xyz."
        )

    print(f"训练帧数: {len(train_indices)}")
    print(f"测试帧数: {len(test_indices)}")
    print(f"测试帧索引: {test_indices}")

    test_frames = [all_frames[i] for i in test_indices]
    write_deepmd(test_frames, output_dir, type_map, args.overwrite)


if __name__ == "__main__":
    main()
