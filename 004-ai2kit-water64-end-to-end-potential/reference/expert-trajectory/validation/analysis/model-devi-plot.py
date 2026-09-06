#!/usr/bin/env python3
"""
模型偏差统计可视化脚本

功能：
    读取各迭代的 stats.tsv 文件，绘制 good/decent/poor 比例的堆叠柱状图。
    用于直观展示主动学习过程中模型质量的变化趋势。

用法：
    python model-devi-plot.py "./workdir/iter-*/screening/stats.tsv" --out-file model-devi.png

输入：
    stats.tsv 文件格式（由 ai2-kit tool model_devi 生成）：
        file          total  good  decent  poor  good%  decent%  poor%  outlier  outlier%
        ./iter-001/...   91    51       6    34  56.04%  6.59%  37.36%     20    21.98%
        SUMMARY         546   243      55   248  44.51% 10.07%  45.42%    168    30.77%

输出：
    PNG 图片，展示各迭代的筛选统计：
    - Accurate (good): max_devi_f < lo，模型预测一致，可信
    - Candidate (decent): lo ≤ max_devi_f < hi，需要 DFT 标注
    - Failed (poor): max_devi_f ≥ hi，模型预测分歧大
"""

import matplotlib.pyplot as plt
import numpy as np
import os
import glob
import fire
import matplotlib
import pandas as pd
from typing import List

# 使用非交互式后端，适合服务器环境
matplotlib.use('agg')

# ============================================================
# 样式常量
# ============================================================
LW = 2       # 线宽
FS = 18      # 字体大小
TICKFS = 14  # 刻度字体大小


def parse_tsv(file_path: str):
    """
    解析单个 stats.tsv 文件，汇总所有作业的数据。

    参数：
        file_path: stats.tsv 文件路径

    返回：
        dict: 包含 total, good, decent, poor 的汇总数据
        None: 解析失败时返回

    说明：
        - 跳过 SUMMARY 行（避免重复计算）
        - 汇总所有作业的统计值
    """
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
            if not lines:
                return None

            # 解析表头，获取各列索引
            header = lines[0].split()
            try:
                idx_file = header.index('file')
                idx_total = header.index('total')
                idx_good = header.index('good')
                idx_decent = header.index('decent')
                idx_poor = header.index('poor')
            except ValueError as e:
                print(f"Missing required column in {file_path}: {e}")
                return None

            # 读取数据行（跳过 SUMMARY）
            data_rows = []
            for line in lines[1:]:
                parts = line.split()
                if not parts or parts[idx_file] == 'SUMMARY':
                    continue

                data_rows.append({
                    'total': int(parts[idx_total]),
                    'good': int(parts[idx_good]),
                    'decent': int(parts[idx_decent]),
                    'poor': int(parts[idx_poor])
                })

            if not data_rows:
                return None

            # 汇总所有作业的统计
            total = sum(r['total'] for r in data_rows)
            good = sum(r['good'] for r in data_rows)
            decent = sum(r['decent'] for r in data_rows)
            poor = sum(r['poor'] for r in data_rows)

        return {
            'file': file_path,
            'total': total,
            'good': good,
            'decent': decent,
            'poor': poor
        }
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return None


def run(*files, out_file="./model-devi-stats.png"):
    """
    绘制模型偏差统计图。

    参数：
        files: stats.tsv 文件的 glob 模式（可多个）
        out_file: 输出图片路径（默认 ./model-devi-stats.png）

    示例：
        python model-devi-plot.py "./workdir/iter-*/screening/stats.tsv"
        python model-devi-plot.py "./workdir/iter-001/screening/stats.tsv" "./workdir/iter-002/screening/stats.tsv"
    """
    # ============================================================
    # 1. 收集所有匹配的文件
    # ============================================================
    all_files = []
    for pattern in files:
        all_files.extend(glob.glob(pattern))

    # 去重并排序
    all_files = sorted(list(set(all_files)))

    if not all_files:
        print("No files found.")
        return

    # ============================================================
    # 2. 解析所有文件
    # ============================================================
    results = []
    for f in all_files:
        res = parse_tsv(f)
        if res:
            results.append(res)

    if not results:
        print("No valid data parsed.")
        return

    # ============================================================
    # 3. 准备绘图数据
    # ============================================================
    df_plot = pd.DataFrame(results)

    # 计算百分比
    df_plot['good_ratio'] = df_plot['good'] / df_plot['total'] * 100
    df_plot['decent_ratio'] = df_plot['decent'] / df_plot['total'] * 100
    df_plot['poor_ratio'] = df_plot['poor'] / df_plot['total'] * 100

    # 使用迭代编号（从 1 开始）
    indices = np.arange(len(df_plot))
    labels = [str(i + 1) for i in indices]

    # ============================================================
    # 4. 绘制堆叠柱状图
    # ============================================================
    fig, ax = plt.subplots(figsize=(10, 6), dpi=300)

    width = 0.6  # 柱宽

    # 绘制三层堆叠柱状图
    # 底层：good (Accurate) - 蓝色
    ax.bar(indices, df_plot['good_ratio'], width,
           label='Accurate', color='#8cbfde', edgecolor='gray', linewidth=0.5)

    # 中层：decent (Candidate) - 橙色
    ax.bar(indices, df_plot['decent_ratio'], width,
           bottom=df_plot['good_ratio'],
           label='Candidate', color='#fda072', edgecolor='gray', linewidth=0.5)

    # 顶层：poor (Failed) - 红色
    ax.bar(indices, df_plot['poor_ratio'], width,
           bottom=df_plot['good_ratio'] + df_plot['decent_ratio'],
           label='Failed', color='#e67d7d', edgecolor='gray', linewidth=0.5)

    # ============================================================
    # 5. 设置图表样式
    # ============================================================
    ax.set_xlabel('Iteration', fontsize=FS)
    ax.set_ylabel('Percentage (%)', fontsize=FS)
    ax.set_title('Model Deviation Statistics', fontsize=FS)
    ax.set_xticks(indices)
    ax.set_xticklabels(labels, fontsize=TICKFS)
    ax.tick_params(axis='y', labelsize=TICKFS)
    ax.set_ylim(0, 105)  # 留出顶部空间给图例
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: '{:.0f}%'.format(y)))

    ax.legend(loc='lower right', fontsize=TICKFS)

    # ============================================================
    # 6. 保存图片
    # ============================================================
    plt.tight_layout()
    plt.savefig(out_file)
    print(f"Plot saved to {out_file}")


if __name__ == "__main__":
    fire.Fire(run)
