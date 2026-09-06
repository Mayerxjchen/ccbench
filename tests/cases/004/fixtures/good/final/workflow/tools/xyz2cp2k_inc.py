#!/usr/bin/env python3
"""
将 XYZ 结构转换为包含 &CELL 和 &COORD 的 CP2K include 文件。

用于 PACKMOL -> CP2K GEOPT/AIMD 步骤。

支持单帧 XYZ 文件和多帧 XYZ 轨迹。
多帧 XYZ 中，每帧以原子数行和注释行开始。
例如，64 水分子轨迹每帧有 192 个原子，文件可能包含多帧，
因此总行数可能远大于 194。准备 AIMD 的最终 GEO_OPT 结构时使用 --frame last。

示例
----

1. 将 64 水分子 PACKMOL 结构转换为 CP2K include 文件：

   python tools/xyz2cp2k_inc.py \\
       data/packmol/water64_pbc.xyz \\
       data/cp2k-geopt/input/coord_n_cell.inc \\
       --cell 12.4


2. 将 CP2K GEO_OPT 轨迹的最后一帧用于 AIMD：

   python tools/xyz2cp2k_inc.py \\
       data/cp2k-geopt/output/water64_geopt-pos-1.xyz \\
       data/cp2k-aimd/input/coord_n_cell.inc \\
       --cell 12.4 \\
       --frame last

   然后在 AIMD 输入中包含：

      &SUBSYS
      @include coord_n_cell.inc
        ...
      &END SUBSYS

3. 使用非立方正交晶胞：

   python tools/xyz2cp2k_inc.py input.xyz coord_n_cell.inc \\
       --cell 12.4 12.4 20.0

4. 使用三斜晶胞，指定 a, b, c, alpha, beta, gamma：

   python tools/xyz2cp2k_inc.py input.xyz coord_n_cell.inc \\
       --cell 12.4 12.4 12.4 90 90 90

5. 打印帮助和示例：

   python tools/xyz2cp2k_inc.py --example

重要说明
--------

本脚本生成 CP2K &CELL 和 &COORD 块，不是普通单帧 XYZ 文件。
如果 CP2K 输入使用 @include coord_n_cell.inc，这是正确的工具。
如果 CP2K 输入使用 &TOPOLOGY / COORD_FILE_NAME，请提供单帧 XYZ 文件。

输出格式
--------

生成的文件可在 CP2K 输入中这样包含：

   &SUBSYS
   @include coord_n_cell.inc
     &KIND O
       BASIS_SET TZV2P-GTH
       POTENTIAL GTH-BLYP-q6
     &END KIND
     &KIND H
       BASIS_SET TZV2P-GTH
       POTENTIAL GTH-BLYP-q1
     &END KIND
   &END SUBSYS
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


Atom = Tuple[str, float, float, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将 XYZ 坐标转换为 CP2K coord_n_cell.inc 文件。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("xyz", nargs="?", help="输入 XYZ 文件。")
    parser.add_argument("output", nargs="?", help="输出 CP2K include 文件。")
    parser.add_argument(
        "--cell",
        nargs="+",
        type=float,
        default=[12.4],
        metavar="VALUE",
        help="晶胞参数：一个值 a，三个值 a b c，或六个值 a b c alpha beta gamma。默认：12.4",
    )
    parser.add_argument(
        "--periodic",
        default="XYZ",
        help="CP2K PERIODIC 设置。默认：XYZ",
    )
    parser.add_argument(
        "--frame",
        default="0",
        help="多帧 XYZ 的帧索引，从 0 开始，或 'last'。默认：0",
    )
    parser.add_argument(
        "--example",
        action="store_true",
        help="打印示例并退出。",
    )
    return parser.parse_args()


def parse_cell(values: Sequence[float]) -> Tuple[float, float, float, float, float, float]:
    if len(values) == 1:
        a = values[0]
        return a, a, a, 90.0, 90.0, 90.0
    if len(values) == 3:
        a, b, c = values
        return a, b, c, 90.0, 90.0, 90.0
    if len(values) == 6:
        a, b, c, alpha, beta, gamma = values
        return a, b, c, alpha, beta, gamma
    raise SystemExit("--cell 需要 1、3 或 6 个数值。")


def read_xyz_frames(path: Path) -> Iterable[List[Atom]]:
    with path.open("r", encoding="utf-8") as handle:
        while True:
            first = handle.readline()
            if not first:
                return
            if not first.strip():
                continue

            try:
                natoms = int(first.strip())
            except ValueError as exc:
                raise SystemExit(f"XYZ 文件中无效的原子数行 {path}: {first!r}") from exc

            comment = handle.readline()
            if not comment:
                raise SystemExit(f"文件在原子数行后意外结束：{path}")

            atoms: List[Atom] = []
            for atom_index in range(natoms):
                line = handle.readline()
                if not line:
                    raise SystemExit(f"读取原子 {atom_index + 1}/{natoms} 时文件意外结束。")
                parts = line.split()
                if len(parts) < 4:
                    raise SystemExit(f"XYZ 文件中无效的原子行：{line!r}")
                symbol = parts[0]
                try:
                    x, y, z = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError as exc:
                    raise SystemExit(f"XYZ 文件中无效的坐标：{line!r}") from exc
                atoms.append((symbol, x, y, z))

            yield atoms


def pick_frame(frames: List[List[Atom]], frame_arg: str) -> Tuple[int, List[Atom]]:
    if not frames:
        raise SystemExit("XYZ 文件中没有找到帧。")

    if frame_arg == "last":
        return len(frames) - 1, frames[-1]

    try:
        frame_index = int(frame_arg)
    except ValueError as exc:
        raise SystemExit("--frame 必须是整数或 'last'。") from exc

    if frame_index < 0 or frame_index >= len(frames):
        raise SystemExit(f"--frame {frame_index} 超出范围，共 {len(frames)} 帧。")

    return frame_index, frames[frame_index]


def write_cp2k_inc(
    output: Path,
    atoms: Sequence[Atom],
    cell: Tuple[float, float, float, float, float, float],
    periodic: str,
    source: Path,
    frame_index: int,
) -> None:
    a, b, c, alpha, beta, gamma = cell
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", encoding="utf-8") as handle:
        handle.write(f"# 由 xyz2cp2k_inc.py 从 {source} 生成\n")
        handle.write(f"# 帧：{frame_index}，原子数：{len(atoms)}\n")
        handle.write("&CELL\n")
        handle.write(f"  ABC [angstrom] {a:.10f} {b:.10f} {c:.10f}\n")
        handle.write(f"  ALPHA_BETA_GAMMA {alpha:.10f} {beta:.10f} {gamma:.10f}\n")
        handle.write(f"  PERIODIC {periodic}\n")
        handle.write("&END CELL\n\n")
        handle.write("&COORD\n")
        handle.write("  UNIT angstrom\n")
        for symbol, x, y, z in atoms:
            handle.write(f"  {symbol:<2s} {x:18.10f} {y:18.10f} {z:18.10f}\n")
        handle.write("&END COORD\n")


def main() -> None:
    args = parse_args()

    if args.example:
        print(__doc__.strip())
        return

    if not args.xyz or not args.output:
        raise SystemExit("需要输入 XYZ 和输出路径。使用 --example 查看使用示例。")

    xyz_path = Path(args.xyz)
    output_path = Path(args.output)

    if not xyz_path.is_file():
        raise SystemExit(f"输入 XYZ 文件未找到：{xyz_path}")

    cell = parse_cell(args.cell)
    frames = list(read_xyz_frames(xyz_path))
    frame_index, atoms = pick_frame(frames, args.frame)
    write_cp2k_inc(output_path, atoms, cell, args.periodic, xyz_path, frame_index)

    print(f"已将帧 {frame_index} 的 {len(atoms)} 个原子写入 {output_path}")
    print(f"晶胞：a={cell[0]} b={cell[1]} c={cell[2]} alpha={cell[3]} beta={cell[4]} gamma={cell[5]}")


if __name__ == "__main__":
    main()
