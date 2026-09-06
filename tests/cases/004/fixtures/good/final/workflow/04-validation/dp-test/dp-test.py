"""
dp-test.py: DeepMD 模型精度验证与可视化
========================================

读取 dp test 输出的能量和力文件，计算 DFT 与 MLP 之间的 RMSE/MAE/R²，
并绘制 parity plot 和误差分布直方图。

输入文件格式（由 dp test 生成）:
  - *.e_peratom.out: 两列, [DFT能量, MLP能量], 单位 eV/atom
  - *.f.out: 六列, [fx_dft, fy_dft, fz_dft, fx_mlp, fy_mlp, fz_mlp], 单位 eV/Å

用法:
  python3 dp-test.py                              # 默认搜索 ./000.e_peratom.out
  python3 dp-test.py --result_prefix="test/*"      # glob 匹配多组结果
  python3 dp-test.py --output="my_plot.png"        # 指定输出文件名
"""

import matplotlib.pyplot as plt
import numpy as np
import matplotlib.gridspec as gridspec
from matplotlib.ticker import MaxNLocator
import os
import glob
import fire
import matplotlib
matplotlib.use('agg')  # 非交互后端，无需 GUI

# ============================================================
# 全局绘图参数
# ============================================================
LW2 = 2         # 坐标轴边框线宽
LW = 2          # 通用线宽
FS = 18         # 坐标轴标签字号
TICKFS = 14     # 刻度字号

# ============================================================
# 工具函数
# ============================================================

def calculate_metrics(y_true, y_pred):
    """计算 RMSE、MAE、R² 三个回归指标"""
    rmse = np.sqrt(np.mean((y_true - y_pred) ** 2))
    mae = np.mean(np.abs(y_true - y_pred))
    r2 = 1 - np.sum((y_true - y_pred) ** 2) / np.sum((y_true - np.mean(y_true)) ** 2)
    return rmse, mae, r2


def get_symmetric_limit(*arrays, pad_ratio=0.08, min_limit=1e-3):
    """根据数据的最大绝对值生成对称坐标范围，并留出 pad_ratio 比例的边距"""
    max_abs = 0.0
    for arr in arrays:
        if arr is None or len(arr) == 0:
            continue
        arr_max = np.nanmax(np.abs(arr))
        if np.isfinite(arr_max):
            max_abs = max(max_abs, arr_max)

    if max_abs == 0:
        max_abs = min_limit

    return max(max_abs * (1.0 + pad_ratio), min_limit)


# ============================================================
# 绑图主函数
# ============================================================

def plot_comparison_with_error_dist(all_dft_energy, all_mlp_energy,
                                     all_dft_forces, all_mlp_forces,
                                     output="val-dp-test.png"):
    """
    绑制能量和力的 parity plot，并在左下角嵌入误差分布直方图。

    布局: 1行 × 2列（左: 能量, 右: 力），第三个 subplot 占位未使用。

    参数
    ----------
    all_dft_energy, all_mlp_energy : ndarray, shape (n_frames,)
        每帧 DFT/MLP 能量（已减均值），单位 eV/atom。
    all_dft_forces, all_mlp_forces : ndarray, shape (n_atoms*3,)
        展平后的 DFT/MLP 力分量，单位 eV/Å。
    output : str
        输出图片路径。
    """
    # ---- 计算统计指标 ----
    rmse_energy, mae_energy, r2_energy = calculate_metrics(all_dft_energy, all_mlp_energy)
    rmse_forces, mae_forces, r2_forces = calculate_metrics(all_dft_forces, all_mlp_forces)

    # ---- 创建画布 ----
    fig = plt.figure(figsize=(18.5, 3.5), dpi=300)
    gs = gridspec.GridSpec(1, 3)  # 3列，第3列留空
    gs.update(wspace=0.35, hspace=0)

    # 计算误差向量
    energy_errors = all_mlp_energy - all_dft_energy   # eV/atom
    force_errors = all_mlp_forces - all_dft_forces     # eV/Å

    # ================================================================
    # 左图: 能量 parity plot + 误差分布直方图（左下角嵌入）
    # ================================================================
    ax1 = plt.subplot(gs[0])
    energy_limit = get_symmetric_limit(all_dft_energy, all_mlp_energy,
                                       pad_ratio=0.08, min_limit=0.01)
    ax1.scatter(all_dft_energy, all_mlp_energy, s=20, color='#fda072', alpha=0.7)

    # 在散点图左下角嵌入能量误差分布直方图（单位 meV/atom）
    bbox1 = ax1.get_position()
    hist_width = 0.053
    hist_height = 0.22
    hist_left = (bbox1.x0 + bbox1.x1) * 0.585  # 相对位置
    hist_bottom = (bbox1.y0 + bbox1.y1) * 0.238
    ax1_hist = fig.add_axes([hist_left, hist_bottom, hist_width, hist_height])

    # 能量误差从 eV/atom 转为 meV/atom
    hist_data_e = energy_errors * 1000
    max_err_e = np.max(np.abs(hist_data_e))
    # 归一化权重使直方图纵轴显示百分比
    weights_e = np.ones_like(hist_data_e) * (100.0 / len(hist_data_e))
    bins_e = np.linspace(-max_err_e, max_err_e, 31)

    ax1_hist.hist(hist_data_e, bins=bins_e, weights=weights_e,
                  color='#fda072', alpha=0.7, edgecolor='gray', linewidth=0.8)
    ax1_hist.tick_params(axis='y', which='both', left=True, labelleft=True,
                         labelsize=7, length=2)
    ax1_hist.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: '{:.0f}%'.format(y)))
    ax1_hist.set_xlabel('Error (meV/atom)', fontsize=9)
    ax1_hist.set_title('Error Distribution', fontsize=9)
    ax1_hist.set_xlim(-max_err_e, max_err_e)
    ax1_hist.xaxis.set_major_locator(MaxNLocator(nbins=3, symmetric=True))
    ax1_hist.tick_params(axis='x', labelsize=7, length=2)

    # ================================================================
    # 中图: 力 parity plot + 误差分布直方图（左下角嵌入）
    # ================================================================
    ax2 = plt.subplot(gs[1])
    force_limit = get_symmetric_limit(all_dft_forces, all_mlp_forces,
                                      pad_ratio=0.08, min_limit=0.5)
    ax2.scatter(all_dft_forces, all_mlp_forces, s=20, color='#8cbfde', alpha=0.7)

    # 在散点图左下角嵌入力误差分布直方图（单位 eV/Å）
    bbox2 = ax2.get_position()
    hist_left = (bbox2.x0 + bbox2.x1) * 0.54
    hist_bottom = (bbox2.y0 + bbox2.y1) * 0.239
    hist_width = 0.053
    hist_height = 0.22

    hist_data_f = force_errors
    max_err_f = np.max(np.abs(hist_data_f))
    weights_f = np.ones_like(hist_data_f) * (100.0 / len(hist_data_f))
    bins_f = np.linspace(-max_err_f, max_err_f, 41)

    ax2_hist = fig.add_axes([hist_left, hist_bottom, hist_width, hist_height])
    ax2_hist.hist(hist_data_f, bins=bins_f, weights=weights_f,
                  color='#8cbfde', alpha=0.9, edgecolor='gray', linewidth=0.8)
    ax2_hist.tick_params(axis='y', which='both', left=True, labelleft=True,
                         labelsize=7, length=2)
    ax2_hist.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: '{:.0f}%'.format(y)))
    ax2_hist.set_xlabel('Error (eV/Å)', fontsize=9)
    ax2_hist.set_title('Error Distribution', fontsize=9)
    ax2_hist.set_xlim(-max_err_f, max_err_f)
    ax2_hist.xaxis.set_major_locator(MaxNLocator(nbins=3, symmetric=True))
    ax2_hist.tick_params(axis='x', labelsize=7, length=2)

    # ---- 统一设置散点图边框和刻度样式 ----
    for ax in [ax1, ax2]:
        for spine in ax.spines.values():
            spine.set_linewidth(LW2)
        ax.tick_params(which='major', direction='out', width=LW2, labelsize=FS - 6)

    # ---- 绑制 y=x 对角线 ----
    ax1.plot([-energy_limit, energy_limit], [-energy_limit, energy_limit],
             'k--', linewidth=LW2)
    ax2.plot([-force_limit, force_limit], [-force_limit, force_limit],
             'k--', linewidth=LW2)

    # ---- 能量图坐标轴设置 ----
    ax1.set_xlim(-energy_limit, energy_limit)
    ax1.set_ylim(-energy_limit, energy_limit)
    ax1.xaxis.set_major_locator(MaxNLocator(nbins=5, symmetric=True))
    ax1.yaxis.set_major_locator(MaxNLocator(nbins=5, symmetric=True))
    ax1.set_xlabel(r'$\rm E_{PBE-D3}$ (eV/atom)', fontsize=FS, labelpad=6)
    ax1.set_ylabel(r'$\rm E_{MLP}$ (eV/atom)', fontsize=FS)

    # ---- 力图坐标轴设置 ----
    ax2.set_xlim(-force_limit, force_limit)
    ax2.set_ylim(-force_limit, force_limit)
    ax2.xaxis.set_major_locator(MaxNLocator(nbins=5, symmetric=True))
    ax2.yaxis.set_major_locator(MaxNLocator(nbins=5, symmetric=True))
    ax2.set_xlabel(r'$\rm F^i_{PBE-D3}$ (eV/Å)', fontsize=FS)
    ax2.set_ylabel(r'$\rm F^i_{MLP}$ (eV/Å)', fontsize=FS, labelpad=4)

    # ---- 在散点图左上角标注 RMSE/MAE/R² ----
    stats_text1 = (f"RMSE: {rmse_energy:.3e} eV/atom\n"
                   f"MAE: {mae_energy:.3e} eV/atom\n"
                   f"R² = {r2_energy:.3f}")
    ax1.text(0.03, 0.97, stats_text1, transform=ax1.transAxes,
             fontsize=FS - 6, va="top", color="black")

    stats_text2 = (f"RMSE: {rmse_forces:.3e} eV/Å\n"
                   f"MAE: {mae_forces:.3e} eV/Å\n"
                   f"R² = {r2_forces:.3f}")
    ax2.text(0.03, 0.97, stats_text2, transform=ax2.transAxes,
             fontsize=FS - 6, va="top", color="black")

    # ---- 保存图片 ----
    plt.savefig(output, bbox_inches='tight', dpi=300)
    plt.close()
    print(f"Results saved to {output}")


# ============================================================
# 主入口
# ============================================================

def run(result_prefix, output="val-dp-test.png"):
    """
    读取 dp test 结果并绑制 DFT vs MLP 对比图。

    数据文件约定（dp test 标准输出）:
      result_prefix + ".e_peratom.out"  → 能量文件（DFT, MLP 各一列）
      result_prefix + ".f.out"          → 力文件（DFT xyz, MLP xyz 各三列）

    :param result_prefix: 结果文件前缀，支持 glob 通配符匹配多组数据。
    :param output: 输出图片路径，默认 val-dp-test.png。
    """
    # 根据前缀查找所有能量文件
    energy_pattern = result_prefix + ".e_peratom.out"
    energy_files = sorted(glob.glob(energy_pattern, recursive=True))

    if not energy_files:
        print(f"Error: No files found matching prefix pattern: {energy_pattern}")
        return

    print(f"Found {len(energy_files)} result sets matching prefix.")

    # 累积所有帧的数据
    all_dft_energy = []
    all_mlp_energy = []
    all_dft_forces = []
    all_mlp_forces = []

    for energy_file in energy_files:
        # 从能量文件路径推导力文件路径
        prefix = energy_file.replace(".e_peratom.out", "")
        force_file = prefix + ".f.out"

        if not os.path.exists(force_file):
            print(f"Warning: Force file not found for {energy_file}, skipping.")
            continue

        try:
            # 读取能量: 第0列=DFT, 第1列=MLP, 减去 DFT 均值使能量中心化
            energy_data = np.loadtxt(energy_file)
            dft_energy = energy_data[:, 0]
            mlp_energy = energy_data[:, 1]
            all_dft_energy.append(dft_energy - np.mean(dft_energy))
            all_mlp_energy.append(mlp_energy - np.mean(dft_energy))

            # 读取力: 前3列=DFT(fx,fy,fz), 后3列=MLP(fx,fy,fz), 展平为1D
            force_data = np.loadtxt(force_file)
            dft_forces = force_data[:, :3].flatten()
            mlp_forces = force_data[:, 3:].flatten()
            all_dft_forces.append(dft_forces)
            all_mlp_forces.append(mlp_forces)
        except Exception as e:
            print(f"Error loading data from {prefix}: {e}")

    if not all_dft_energy:
        print("No data loaded.")
        return

    # 合并所有帧数据
    all_dft_energy = np.concatenate(all_dft_energy)
    all_mlp_energy = np.concatenate(all_mlp_energy)
    all_dft_forces = np.concatenate(all_dft_forces)
    all_mlp_forces = np.concatenate(all_mlp_forces)

    print(f"Total data points - Energy: {len(all_dft_energy)}, Forces: {len(all_dft_forces)}")

    # 绑图
    plot_comparison_with_error_dist(all_dft_energy, all_mlp_energy,
                                     all_dft_forces, all_mlp_forces,
                                     output=output)


if __name__ == '__main__':
    fire.Fire(run)
