#!/usr/bin/env python3
"""
将 CP2K AIMD 输出文件转换为 labeled extxyz 格式。

这个脚本的定位：
  1. 读取 CP2K AIMD 原始输出：pos/frc/cell/ener
  2. 同步检查坐标帧和力帧，确保 step、温度、盒子、能量可以对应
  3. 按规则筛选可用 AIMD 帧，默认删除 step 0 和重复 step
  4. 可按指定数量从合格候选帧中均匀抽取，但不会为了凑帧重复使用同一帧
  5. 写出 ai2kit setup 可读取的 labeled extxyz，例如 config/aimd.xyz

注意：
  - 脚本只负责从 CP2K AIMD 原始结果生成 labeled extxyz，不会自动启动 ai2-kit。
  - 温度窗口不是硬规则；对 300 K 水体系，250-350 K 是温和推荐值。
  - 如果合格帧是 190 帧，就输出 190 帧；不要为了看起来像 200 帧而重复补帧。
  - 推荐每次查看 --report 输出的 frame_filter.tsv，再决定是否放宽或收紧筛选。

用法：
  python cp2k2extxyz.py --dir <cp2k_output_dir> --prefix <prefix> --output <output>

====================================================================
推荐工作流（与 reference/tesla-h2o 一致）
====================================================================

目标：AIMD 生成 150-200 帧 → 筛选合格帧 → config/aimd.xyz 保留 100-200 帧
      → setup.sh 自动从中采样 50 帧生成 dp-init-data

步骤 1：运行 AIMD
  例如：10000 steps, timestep 0.5 fs, EACH MD 50
  这样大约输出 200 帧，时间跨度约 5 ps。

步骤 2：用本脚本筛选合格帧，写入 config/aimd.xyz

  # 推荐命令：300 K 水体系，温度窗口 250-350 K
  # 作用：删除 step 0、删除重复 step、删除明显低温/过热帧
  python cp2k2extxyz.py \\
    --dir data/cp2k-aimd/output \\
    --prefix water64_aimd \\
    --min-temp 250 --max-temp 350 \\
    --output config/aimd.xyz \\
    --report data/cp2k-aimd/processed/frame_filter.tsv

  # 宽松命令：只删除 step 0 和重复 step，不按温度筛选
  python cp2k2extxyz.py \\
    --dir data/cp2k-aimd/output \\
    --prefix water64_aimd \\
    --output config/aimd.xyz \\
    --report data/cp2k-aimd/processed/frame_filter.tsv

  # 严格命令：要求恰好抽取 150 帧；候选帧不足则报错
  # 注意：不建议用 --target-frames 200 强行凑 reference 的帧数。
  python cp2k2extxyz.py \\
    --dir data/cp2k-aimd/output \\
    --prefix water64_aimd \\
    --min-temp 250 --max-temp 350 \\
    --target-frames 150 \\
    --output config/aimd.xyz \\
    --report data/cp2k-aimd/processed/frame_filter.tsv

步骤 3：运行 setup.sh，自动从 config/aimd.xyz 采样 50 帧

  cd ai2kit && bash run.sh
  # setup.sh 内部执行：
  #   ai2-kit tool ase read config/aimd.xyz - sample 50 - to_dpdata ...

====================================================================
参数说明
====================================================================

  --min-step    剔除 step 小于该值的帧；比温度窗口更粗糙，不推荐单独依赖
  --max-step    剔除 step 大于该值的帧
  --min-temp    温度下限（K）；300 K 水体系可先用 250
  --max-temp    温度上限（K）；300 K 水体系可先用 350
  --max-frames  最多保留帧数；候选帧不足时保留全部
  --target-frames  恰好保留帧数；候选帧不足时报错
  --keep-step-zero  保留 step 0；默认删除
  --allow-duplicate-steps  保留重复 step；默认删除重复 step 的后续副本
  --report      输出帧筛选报告 TSV，记录每帧保留/剔除原因

默认行为：
  - 丢弃 step 0 初始帧，避免把未热化的初始结构混入 AIMD 数据
  - 丢弃重复 step 的后续副本，避免为了凑帧降低数据多样性
  - 如果稳定候选帧不足 200，不会补重复帧；例如 190 个唯一稳定帧也可以直接给 setup.sh 使用

====================================================================
其他示例
====================================================================

基本用法（只使用默认清理：删除 step 0 和重复 step）：
  python cp2k2extxyz.py \\
    --dir data/cp2k-aimd/output \\
    --prefix water64_aimd \\
    --output config/aimd.xyz \\
    --report data/cp2k-aimd/processed/frame_filter.tsv

只做热化剔除：
  python cp2k2extxyz.py \\
    --dir data/cp2k-aimd/output \\
    --prefix water64_aimd \\
    --min-step 750 \\
    --output config/aimd.xyz \\
    --report data/cp2k-aimd/processed/frame_filter.tsv

完整筛选（温度窗口 + 去重 + 报告，推荐用于你的 300 K 水体系）：
  python cp2k2extxyz.py \\
    --dir data/cp2k-aimd/output \\
    --prefix water64_aimd \\
    --min-temp 250 --max-temp 350 \\
    --output config/aimd.xyz \\
    --report data/cp2k-aimd/processed/frame_filter.tsv

如果你想完全复现旧行为（不删 step 0，也允许重复 step）：
  python cp2k2extxyz.py \\
    --dir data/cp2k-aimd/output \\
    --prefix water64_aimd \\
    --keep-step-zero \\
    --allow-duplicate-steps \\
    --output config/aimd.xyz \\
    --report data/cp2k-aimd/processed/frame_filter.tsv
"""

import re
import sys
import argparse
from pathlib import Path

SCRIPT_VERSION = "2026-06-14-filter-unique-stable"

# CP2K 单位转换
HARTREE_TO_EV = 27.211386245988
BOHR_TO_ANGSTROM = 0.529177210903
HARTREE_BOHR_TO_EV_ANGSTROM = HARTREE_TO_EV / BOHR_TO_ANGSTROM  # ~51.422067


def detect_files(output_dir, prefix):
    """在指定目录中自动检测 CP2K AIMD 输出文件。

    CP2K 默认输出文件命名规则：
      坐标轨迹：{prefix}-pos-1.xyz
      力轨迹：  {prefix}-frc-1.xyz
      盒子信息：{prefix}-1.cell
      能量信息：{prefix}-1.ener

    参数：
        output_dir: CP2K 输出目录
        prefix: 文件名前缀（如 water64_aimd）

    返回：
        (pos_file, frc_file, cell_file, ener_file) 的路径
    """
    d = Path(output_dir)

    # 检测坐标文件：优先使用已过滤的，否则用原始的
    pos_filtered = d / f"{prefix}-pos-1-filtered.xyz"
    pos_original = d / f"{prefix}-pos-1.xyz"
    if pos_filtered.exists():
        pos_file = pos_filtered
    elif pos_original.exists():
        pos_file = pos_original
    else:
        print(f"错误：找不到坐标文件 {pos_filtered} 或 {pos_original}")
        sys.exit(1)

    # 检测力文件：优先使用已过滤的，否则用原始的
    frc_filtered = d / f"{prefix}-frc-1-filtered.xyz"
    frc_original = d / f"{prefix}-frc-1.xyz"
    if frc_filtered.exists():
        frc_file = frc_filtered
    elif frc_original.exists():
        frc_file = frc_original
    else:
        print(f"错误：找不到力文件 {frc_filtered} 或 {frc_original}")
        sys.exit(1)

    # 检测盒子文件
    cell_file = d / f"{prefix}-1.cell"
    if not cell_file.exists():
        print(f"错误：找不到盒子文件 {cell_file}")
        sys.exit(1)

    # 检测能量文件
    ener_file = d / f"{prefix}-1.ener"
    if not ener_file.exists():
        print(f"错误：找不到能量文件 {ener_file}")
        sys.exit(1)

    return str(pos_file), str(frc_file), str(cell_file), str(ener_file)


def parse_xyz_frames(xyz_file):
    """解析 CP2K xyz 轨迹文件（坐标或力）。

    返回帧列表，每帧是一个字典：
      {
        'natoms': int,          # 原子数
        'step': int,            # MD 步数
        'time': float,          # 时间（fs）
        'energy_header': float, # xyz 头信息中的能量（Hartree）
        'atoms': [(元素, [x, y, z]), ...]
      }
    """
    frames = []
    with open(xyz_file, 'r') as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # 第一行：原子数
        natoms = int(line)
        i += 1

        # 第二行：头信息 "i = STEP, time = TIME, E = ENERGY"
        header = lines[i].strip()
        step = int(re.search(r'i\s*=\s*(\d+)', header).group(1))
        time = float(re.search(r'time\s*=\s*([\d.]+)', header).group(1))
        energy_match = re.search(r'E\s*=\s*([-\d.]+)', header)
        energy_ha = float(energy_match.group(1)) if energy_match else None
        i += 1

        # 第三行起：原子数据（元素 x y z 或 元素 fx fy fz）
        atoms = []
        for j in range(natoms):
            parts = lines[i].split()
            elem = parts[0]
            coords = [float(parts[1]), float(parts[2]), float(parts[3])]
            atoms.append((elem, coords))
            i += 1

        frames.append({
            'natoms': natoms,
            'step': step,
            'time': time,
            'energy_header': energy_ha,
            'atoms': atoms,
        })

    return frames


def parse_cell_file(cell_file):
    """解析 CP2K .cell 文件。

    返回字典：step -> (ax, ay, az, bx, by, bz, cx, cy, cz)
    其中 a/b/c 为盒子的三个矢量分量。
    """
    cell_data = {}
    with open(cell_file, 'r') as f:
        for line in f:
            line = line.strip()
            # 跳过空行和注释行
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            step = int(parts[0])
            # 列顺序：Step  Time  Ax Ay Az  Bx By Bz  Cx Cy Cz  Volume
            ax, ay, az = float(parts[2]), float(parts[3]), float(parts[4])
            bx, by, bz = float(parts[5]), float(parts[6]), float(parts[7])
            cx, cy, cz = float(parts[8]), float(parts[9]), float(parts[10])
            cell_data[step] = (ax, ay, az, bx, by, bz, cx, cy, cz)
    return cell_data


def parse_ener_file(ener_file):
    """解析 CP2K .ener 文件。

    返回字典：
      step -> {
        'time': 时间（fs）,
        'temperature': 温度（K）,
        'potential': 势能（Hartree）
      }
    """
    ener_data = {}
    with open(ener_file, 'r') as f:
        for line in f:
            line = line.strip()
            # 跳过空行和注释行
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            step = int(parts[0])
            # 列顺序：Step  Time  Kin  Temp  Pot  ConsQty  UsedTime
            ener_data[step] = {
                'time': float(parts[1]),
                'temperature': float(parts[3]),
                'potential': float(parts[4]),
            }
    return ener_data


def filter_frame_pairs(pos_frames, frc_frames, ener_data,
                       min_step=None, max_step=None,
                       min_temp=None, max_temp=None,
                       max_frames=None, target_frames=None,
                       drop_initial_step=True,
                       deduplicate_steps=True):
    """同步筛选坐标帧和力帧。

    设计原则：
      - 坐标和力必须按同一个 step 同步保留或同步剔除。
      - 默认删除 step 0，因为它通常是初始结构，不一定代表稳定 AIMD 采样。
      - 默认删除重复 step 的后续副本，避免把同一构型重复写入训练母集。
      - max_frames/target_frames 只会从已有候选帧中抽样，不会复制帧来补数量。

    参数：
        pos_frames: 坐标帧列表
        frc_frames: 力帧列表
        ener_data: 能量/温度数据
        min_step: 最小 step（包含），为 None 则不过滤下限
        max_step: 最大 step（包含），为 None 则不过滤上限
        min_temp: 最低温度（包含），为 None 则不过滤
        max_temp: 最高温度（包含），为 None 则不过滤
        max_frames: 最多保留帧数；候选帧超过该值时均匀抽取，候选帧不足时保留全部
        target_frames: 目标保留帧数；候选帧不足则报错，候选帧足够则均匀抽取
        drop_initial_step: 是否剔除 step 0
        deduplicate_steps: 是否剔除重复 step 的后续副本

    返回：
        (pos_frames, frc_frames, records)
    """
    kept = []
    records = []
    seen_steps = set()

    for pf, ff in zip(pos_frames, frc_frames):
        step = pf['step']
        reason = "kept"

        if drop_initial_step and step == 0:
            reason = "initial_step"
        elif deduplicate_steps and step in seen_steps:
            reason = "duplicate_step"
        elif min_step is not None and step < min_step:
            reason = "step_lt_min"
        elif max_step is not None and step > max_step:
            reason = "step_gt_max"
        elif step not in ener_data:
            reason = "missing_energy"
        else:
            temp = ener_data[step]['temperature']
            if min_temp is not None and temp < min_temp:
                reason = "temp_lt_min"
            elif max_temp is not None and temp > max_temp:
                reason = "temp_gt_max"

        record = {
            'step': step,
            'time': pf['time'],
            'temperature': ener_data.get(step, {}).get('temperature'),
            'reason': reason,
            'selected': reason == "kept",
        }
        records.append(record)

        if reason == "kept":
            kept.append((pf, ff))
            seen_steps.add(step)

    if target_frames is not None and max_frames is not None:
        raise SystemExit("--target-frames 和 --max-frames 只能选择一个")

    if target_frames is not None and len(kept) < target_frames:
        raise SystemExit(
            f"稳定候选帧不足：筛选后只有 {len(kept)} 帧，"
            f"但 --target-frames 要求 {target_frames} 帧。"
        )

    sample_count = None
    if target_frames is not None:
        sample_count = target_frames
    elif max_frames is not None and len(kept) > max_frames:
        sample_count = max_frames

    if sample_count is not None:
        selected_indices = evenly_spaced_indices(len(kept), sample_count)
        selected_set = set(selected_indices)
        kept = [pair for i, pair in enumerate(kept) if i in selected_set]

        kept_steps = {pf['step'] for pf, _ in kept}
        for record in records:
            if record['reason'] == "kept" and record['step'] not in kept_steps:
                record['reason'] = "not_sampled"
                record['selected'] = False

    return [pf for pf, _ in kept], [ff for _, ff in kept], records


def evenly_spaced_indices(total, count):
    """返回 count 个均匀分布的索引，包含首尾。"""
    if count <= 0:
        raise SystemExit("--max-frames 必须大于 0")
    if count >= total:
        return list(range(total))
    if count == 1:
        return [0]
    return sorted({
        round(i * (total - 1) / (count - 1))
        for i in range(count)
    })


def write_filter_report(records, report_file):
    """写出帧筛选报告，便于检查哪些帧被剔除。"""
    with open(report_file, 'w') as f:
        f.write("step\ttime_fs\ttemperature_K\tselected\treason\n")
        for record in records:
            temp = record['temperature']
            temp_str = "" if temp is None else f"{temp:.6f}"
            f.write(
                f"{record['step']}\t"
                f"{record['time']:.6f}\t"
                f"{temp_str}\t"
                f"{int(record['selected'])}\t"
                f"{record['reason']}\n"
            )


def find_cell_for_step(step, cell_data):
    """查找指定 step 的盒子信息。

    如果精确 step 未找到，则查找最近的小于等于该 step 的条目。
    """
    if step in cell_data:
        return cell_data[step]
    # 查找最近的 step <= 给定 step
    candidates = [s for s in cell_data if s <= step]
    if not candidates:
        raise ValueError(f"找不到 step {step} 的盒子信息")
    nearest = max(candidates)
    return cell_data[nearest]


def write_extxyz(frames, cell_data, ener_data, output_file, energy_unit='eV'):
    """写入 labeled extxyz 文件。

    参数：
        frames: 帧列表（来自 parse_xyz_frames）
        cell_data: 盒子数据（来自 parse_cell_file）
        ener_data: 能量数据（来自 parse_ener_file），单位 Hartree
        output_file: 输出文件路径
        energy_unit: 输出能量单位，'eV' 或 'Hartree'
    """
    with open(output_file, 'w') as f:
        for frame in frames:
            step = frame['step']
            natoms = frame['natoms']

            # 获取盒子矢量
            ax, ay, az, bx, by, bz, cx, cy, cz = find_cell_for_step(step, cell_data)

            # 获取能量：优先从 .ener 文件读取，备选从 xyz 头信息读取
            if step in ener_data:
                energy_ha = ener_data[step]['potential']
            elif frame['energy_header'] is not None:
                energy_ha = frame['energy_header']
            else:
                raise ValueError(f"找不到 step {step} 的能量数据")

            # 能量单位转换
            if energy_unit == 'eV':
                energy = energy_ha * HARTREE_TO_EV
            else:
                energy = energy_ha

            # 构建 extxyz 头信息
            # 格式：Lattice="ax ay az bx by bz cx cy cz"
            lattice_str = f"{ax:.10f} {ay:.10f} {az:.10f} {bx:.10f} {by:.10f} {bz:.10f} {cx:.10f} {cy:.10f} {cz:.10f}"
            header = (
                f'Lattice="{lattice_str}" '
                f'Properties=species:S:1:pos:R:3:forces:R:3 '
                f'i={step} time={frame["time"]:.1f} '
                f'energy={energy:.10f} pbc="T T T"'
            )

            # 写入：原子数、头信息、原子数据（元素 坐标 力）
            f.write(f"{natoms}\n")
            f.write(f"{header}\n")
            for elem, data in frame['atoms']:
                # data = [x, y, z, fx, fy, fz]
                f.write(f"  {elem:s}  {data[0]:15.10f}  {data[1]:15.10f}  {data[2]:15.10f}"
                        f"  {data[3]:15.10f}  {data[4]:15.10f}  {data[5]:15.10f}\n")

    return len(frames)


def main():
    parser = argparse.ArgumentParser(
        description='将 CP2K AIMD 输出转换为 labeled extxyz')

    parser.add_argument('--dir', required=True, help='CP2K 输出目录')
    parser.add_argument('--prefix', required=True, help='文件名前缀（如 water64_aimd）')
    parser.add_argument('--output', '-o', required=True, help='输出 extxyz 文件路径')
    parser.add_argument('--version', action='version',
                        version=f'%(prog)s {SCRIPT_VERSION}')
    parser.add_argument('--min-step', type=int, default=None,
                        help='只保留 step >= MIN_STEP 的帧；用于粗略剔除热化阶段')
    parser.add_argument('--max-step', type=int, default=None,
                        help='只保留 step <= MAX_STEP 的帧')
    parser.add_argument('--energy-unit', default='eV', choices=['eV', 'Hartree'],
                        help='输出能量单位（默认：eV）')
    parser.add_argument('--min-temp', type=float, default=None,
                        help='只保留温度 >= MIN_TEMP K 的帧；300 K 水体系可先用 250')
    parser.add_argument('--max-temp', type=float, default=None,
                        help='只保留温度 <= MAX_TEMP K 的帧；300 K 水体系可先用 350')
    parser.add_argument('--max-frames', type=int, default=None,
                        help='从候选帧中最多均匀抽取 MAX_FRAMES 帧；候选帧不足时保留全部，不重复补帧')
    parser.add_argument('--target-frames', type=int, default=None,
                        help='从候选帧中均匀抽取恰好 TARGET_FRAMES 帧；候选帧不足时直接报错')
    parser.add_argument('--keep-step-zero', action='store_true',
                        help='保留 step 0 初始帧；默认会剔除')
    parser.add_argument('--allow-duplicate-steps', action='store_true',
                        help='允许重复 step；默认会剔除重复 step 的后续副本')
    parser.add_argument('--report', default=None,
                        help='输出帧筛选报告 TSV 文件')

    args = parser.parse_args()

    print(f"cp2k2extxyz version: {SCRIPT_VERSION}")
    print("默认筛选：剔除 step 0，剔除重复 step 的后续副本，不重复补帧")

    # 自动检测输入文件
    pos_file, frc_file, cell_file, ener_file = detect_files(args.dir, args.prefix)

    # 1. 读取坐标轨迹
    print(f"读取坐标：{pos_file}")
    pos_frames = parse_xyz_frames(pos_file)
    print(f"  -> {len(pos_frames)} 帧")

    # 2. 读取力轨迹
    print(f"读取力：{frc_file}")
    frc_frames = parse_xyz_frames(frc_file)
    print(f"  -> {len(frc_frames)} 帧")

    # 检查坐标和力的帧数一致
    if len(pos_frames) != len(frc_frames):
        print(f"错误：坐标帧数 ({len(pos_frames)}) != 力帧数 ({len(frc_frames)})")
        sys.exit(1)

    # 3. 读取盒子信息
    print(f"读取盒子：{cell_file}")
    cell_data = parse_cell_file(cell_file)
    print(f"  -> {len(cell_data)} 条记录")

    # 4. 读取能量信息
    print(f"读取能量：{ener_file}")
    ener_data = parse_ener_file(ener_file)
    print(f"  -> {len(ener_data)} 条记录")

    # 5. 筛选稳定帧（坐标和力必须同步过滤）
    before = len(pos_frames)
    pos_frames, frc_frames, filter_records = filter_frame_pairs(
        pos_frames=pos_frames,
        frc_frames=frc_frames,
        ener_data=ener_data,
        min_step=args.min_step,
        max_step=args.max_step,
        min_temp=args.min_temp,
        max_temp=args.max_temp,
        max_frames=args.max_frames,
        target_frames=args.target_frames,
        drop_initial_step=not args.keep_step_zero,
        deduplicate_steps=not args.allow_duplicate_steps,
    )
    if len(pos_frames) == 0:
        print("错误：筛选后没有可用帧，请放宽 --min-step/--min-temp/--max-temp 条件")
        sys.exit(1)

    print(
        "筛选帧："
        f"step=[{args.min_step or '-∞'}, {args.max_step or '∞'}], "
        f"temp=[{args.min_temp or '-∞'}, {args.max_temp or '∞'}] K, "
        f"target_frames={args.target_frames or '不指定'}, "
        f"max_frames={args.max_frames or '不限制'}, "
        f"drop_step0={not args.keep_step_zero}, "
        f"deduplicate={not args.allow_duplicate_steps}，"
        f"{before} -> {len(pos_frames)} 帧"
    )

    if args.report:
        write_filter_report(filter_records, args.report)
        print(f"筛选报告：{args.report}")

    # 6. 合并坐标和力：将力附加到坐标帧的原子数据中
    for pf, ff in zip(pos_frames, frc_frames):
        if pf['step'] != ff['step']:
            print(f"错误：step 不匹配，pos={pf['step']} frc={ff['step']}")
            sys.exit(1)
        pf['atoms'] = [
            (pe, pc + [fx * HARTREE_BOHR_TO_EV_ANGSTROM for fx in fc])
            for (pe, pc), (_, fc) in zip(pf['atoms'], ff['atoms'])
        ]

    # 7. 写入 extxyz 文件
    print(f"写入 extxyz：{args.output}")
    n = write_extxyz(pos_frames, cell_data, ener_data, args.output, args.energy_unit)
    print(f"  -> {n} 帧已写入")
    print("完成。")


if __name__ == '__main__':
    main()
