#!/usr/bin/env python3
"""
NVT 模拟统计分析脚本
用法: python analyze_nvt.py [输出目录]
默认输出目录: test/nvt/output
"""

import sys
import numpy as np
from pathlib import Path

def read_thermo_data(filepath):
    """读取 thermo.dat 文件，返回步数、温度、势能、总能量、压力"""
    steps, temps, potengs, totengs, press = [], [], [], [], []
    header_found = False

    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('Step'):
                header_found = True
                continue
            if not header_found:
                continue
            if not line or not line[0].isdigit():
                continue

            parts = line.split()
            if len(parts) >= 6:
                try:
                    # 验证是有效的数值
                    step = int(parts[0])
                    temp = float(parts[1])
                    poteng = float(parts[2])
                    toteng = float(parts[4])
                    p = float(parts[5])
                    steps.append(step)
                    temps.append(temp)
                    potengs.append(poteng)
                    totengs.append(toteng)
                    press.append(p)
                except (ValueError, IndexError):
                    continue

    return np.array(steps), np.array(temps), np.array(potengs), np.array(totengs), np.array(press)

def read_model_devi(filepath):
    """读取 model_devi.out 文件，返回最大力偏差"""
    max_force_devi = []
    with open(filepath, 'r') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 4:
                max_force_devi.append(float(parts[3]))
    return np.array(max_force_devi)

def analyze_nvt(output_dir):
    """分析 NVT 模拟结果"""
    output_dir = Path(output_dir)

    # 检查文件是否存在
    thermo_file = output_dir / 'thermo.dat'
    model_devi_file = output_dir / 'model_devi.out'
    dump_file = output_dir / 'dump.lammpstrj'

    if not thermo_file.exists():
        print(f"错误: {thermo_file} 不存在")
        return False

    print("=" * 60)
    print("NVT 模拟统计分析")
    print("=" * 60)
    print(f"输出目录: {output_dir}")
    print()

    # 读取 thermo 数据
    steps, temps, potengs, totengs, press = read_thermo_data(thermo_file)

    if len(steps) == 0:
        print("错误: 无法读取 thermo 数据")
        return False

    # 去掉前 20000 步（平衡阶段）；若总步数不足则退化为去掉前一半
    equil_steps = 20000
    if steps[-1] <= equil_steps:
        equil_steps = max(int(steps[-1] * 0.5), 1)
    mask = steps >= equil_steps
    steps_eq = steps[mask]
    temps_eq = temps[mask]
    potengs_eq = potengs[mask]
    totengs_eq = totengs[mask]
    press_eq = press[mask]

    print(f"【基本信息】")
    print(f"  总步数: {steps[-1]}")
    print(f"  平衡阶段: 前 {equil_steps} 步")
    print(f"  统计数据点: {len(steps_eq)}")
    print()

    # 温度统计
    print(f"【温度统计】")
    print(f"  目标温度: 300 K")
    print(f"  平均温度: {np.mean(temps_eq):.2f} K")
    print(f"  标准差: {np.std(temps_eq):.2f} K")
    print(f"  相对波动: {np.std(temps_eq)/np.mean(temps_eq)*100:.2f}%")
    print(f"  最小温度: {np.min(temps_eq):.2f} K")
    print(f"  最大温度: {np.max(temps_eq):.2f} K")

    # 温度分布
    bins = [0, 250, 275, 300, 325, 350, 1000]
    labels = ['<250K', '250-275K', '275-300K', '300-325K', '325-350K', '>350K']
    print(f"  温度分布:")
    for i in range(len(bins)-1):
        count = np.sum((temps_eq >= bins[i]) & (temps_eq < bins[i+1]))
        print(f"    {labels[i]}: {count}")
    print()

    # 能量统计
    print(f"【能量统计】")
    print(f"  平均总能量: {np.mean(totengs_eq):.2f} kcal/mol")
    print(f"  标准差: {np.std(totengs_eq):.2f} kcal/mol")
    print(f"  相对波动: {abs(np.std(totengs_eq)/np.mean(totengs_eq))*100:.4f}%")
    print()

    # 压力统计
    print(f"【压力统计】")
    print(f"  平均压力: {np.mean(press_eq):.2f} atm")
    print(f"  标准差: {np.std(press_eq):.2f} atm")
    print()

    # 模型偏差统计
    if model_devi_file.exists():
        force_devi = read_model_devi(model_devi_file)
        print(f"【模型偏差统计】")
        print(f"  平均力偏差: {np.mean(force_devi):.6f} eV/Å")
        print(f"  最大力偏差: {np.max(force_devi):.6f} eV/Å")
        print(f"  最小力偏差: {np.min(force_devi):.6f} eV/Å")
        print()

    # 轨迹帧数
    if dump_file.exists():
        with open(dump_file, 'r') as f:
            frame_count = sum(1 for line in f if line.startswith('ITEM: ATOMS'))
        print(f"【轨迹信息】")
        print(f"  轨迹帧数: {frame_count}")
        print()

    # 判定标准
    print("=" * 60)
    print("【判定结果】")
    print("=" * 60)

    success = True
    issues = []

    # 温度判定
    temp_std = np.std(temps_eq)
    temp_mean = np.mean(temps_eq)
    temp_fluctuation = temp_std / temp_mean * 100

    if abs(temp_mean - 300) > 50:
        issues.append(f"平均温度偏离目标过大: {temp_mean:.2f} K")
        success = False

    if temp_fluctuation > 10:
        issues.append(f"温度波动过大: {temp_fluctuation:.2f}%")
        success = False

    # 能量判定
    energy_std = np.std(totengs_eq)
    energy_mean = np.mean(totengs_eq)
    energy_fluctuation = abs(energy_std / energy_mean) * 100

    if energy_fluctuation > 1:
        issues.append(f"能量波动过大: {energy_fluctuation:.4f}%")
        success = False

    # 模型偏差判定
    if model_devi_file.exists():
        max_devi = np.max(force_devi)
        if max_devi > 0.15:
            issues.append(f"模型偏差过大: {max_devi:.6f} eV/Å")
            success = False

    if success:
        print("✓ NVT 模拟成功!")
        print()
        print("各项指标均正常:")
        print(f"  - 温度: {temp_mean:.2f} ± {temp_std:.2f} K (波动 {temp_fluctuation:.2f}%)")
        print(f"  - 能量波动: {energy_fluctuation:.4f}%")
        if model_devi_file.exists():
            print(f"  - 模型偏差: {np.mean(force_devi):.6f} eV/Å (最大 {np.max(force_devi):.6f})")
    else:
        print("✗ NVT 模拟存在问题:")
        for issue in issues:
            print(f"  - {issue}")

    print()
    print("=" * 60)

    return success

if __name__ == '__main__':
    if len(sys.argv) > 1:
        output_dir = sys.argv[1]
    else:
        output_dir = Path(__file__).parent / 'output'

    success = analyze_nvt(output_dir)
    sys.exit(0 if success else 1)
