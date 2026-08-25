# Source Paper Lock (025–030, M1 → M2 交接)

**日期**: 2026-08-09
**状态**: **全部完成**（Source Lock + 6 案例构建 G0–G8 全过）
**上游任务**: #37 M1 source provenance freeze（completed）
**下游任务**: #39 028-name2file-so2 (MACE)、#40 029-react2enthalpy-methane (xTB)、#42 030-react2gibbs-ammonia (xTB) — 全部 completed

---

## 1. 源数据从哪来

上游官方 GitHub 仓库 `argonne-lcf/chemgraph` 原先自带 bundled dataset
`src/chemgraph/eval/data/ground_truth.json`（14-query 版），但 **2026-06-30 被删除**
（commit `2810c0e02b`，理由："stale and produced by the buggy ground-truth generator"）。

本 Source Lock 从 **删除前最后版本 commit `a42dd58dba`**（2026-04-13）经 GitHub raw
URL 完整恢复：

```
https://raw.githubusercontent.com/argonne-lcf/chemgraph/a42dd58dba/src/chemgraph/eval/data/ground_truth.json
```

## 2. 恢复数据质量评估（关键）

| 检查项 | 结果 |
|---|---|
| 条目数 | **40**（非 14，是扩展版） |
| category | 11 类：smiles_lookup×4 / optimization_from_name×4 / vibrations_from_name×2 / thermochemistry_from_name×4 / dipole_from_name×2 / energy_from_name×4 / *_from_smiles×10 / reaction_energy×10 |
| 每条目结构 | `query`(原始 prompt) + `answer`(`tool_calls` + `result` + `structured_output` 三层 reference) |
| reaction_energy 算术 | 10 条 ΔG 表达式全部独立重算 → **10/10 自洽** |
| calculator | 仅 `mace_mp(medium-mpa-0)` 与 `TBLite(GFN2-xTB)`；**无任何 DFT (NWChem/ORCA) 实例** |
| 结论 | **a42dd58dba 版数据完好，"buggy generator" 说法在该版本不成立**。数据可用。 |

**sha256**（数据冻结指纹）:
`a8e76ec8c427ec9a18de758f0492e3fdd2f01a7ea4b335249c40e8a01c47d742`

## 3. 落盘文件

| 文件 | 内容 |
|---|---|
| `original_data/ground_truth.json` | 恢复的完整 40 条源数据（冻结，含 answer 全文） |
| `source_catalog.json` | **Source Lock 目录**：40 条实例索引 + 6 案例分配 + provenance |
| `acceptance_standard.md` | G0–G8 验收规范（含 2 处 fidelity 修订） |

> 已清理（2026-08-09）：`repo/`（chemgraph 完整 clone）与
> `original_data/locked_instances_025_026_027_030.json` 已删除。前者 commit
> `8ab2d9c` 仍记录于 `source_catalog.json` 的 `repo_head_at_lock`；后者是
> `ground_truth.json` 的子集（ids 5/6/7/32），可从源数据直接恢复。

## 4. 6 案例实例分配（Source Lock 决定）

| Benchmark | 源类型 | 锁定 instance | 体系 | method | T | 引擎镜像 |
|---|---|---|---|---|---|---|
| 025 name2opt | dataset | id=5 | SO₂ | mace_mp | — | mace |
| 026 name2vib | dataset | id=6 | water | mace_mp | — | mace |
| 027 name2gibbs | dataset | id=7 | CO₂ | GFN2-xTB | 800K | xtb |
| 028 name2file | hybrid | id=5 + paper | SO₂ → XYZ | mace_mp | — | mace |
| 029 react2enthalpy | **paper_text** | 无 | CH₄+2O₂→CO₂+2H₂O | GFN2-xTB | 400K | xtb |
| 030 react2gibbs | dataset | id=32 | N₂+3H₂→2NH₃ | GFN2-xTB | 400K | xtb |

引擎覆盖：**MACE 镜像** → 025/026/028；**xTB 镜像** → 027/029/030。仅 2 个新镜像。

> **2026-08-09 编号修订**：案例编号 027–032 → **025–030**，目录/task 名去掉 "chemgraph"。
> 镜像：`dftworld-base-chemgraph-xtb` → `dftworld-base-xtb`；`dftworld-base-chemgraph-mace` → `dftworld-base-mace`。
>
> **2026-08-15 全局重排**：本批 6 个 ChemGraph 案例重排为 **027–030、039–040**
> （025-name2smi/026-name2coord、035–038/041-smiles2* 为新增 RDKit/SMILES 工具链任务，
> 031–033 MatClaw 保留，034-ai2kit 恢复原号）。下文第 4 节分配表已更新到新编号；第 5–7 节历史记录仍用旧号。

## 5. 两个 fidelity 决策（须遵守，违反即 G1 FAIL）

1. **025 用 MACE 而非 DFT**：源数据无 DFT 实例，id=5 本来就是 mace_mp。
   "论文是 DFT 却改 MACE" 是 G1 FAIL；反方向 "数据是 MACE 却改 DFT" 同样是伪造 source。
   验收标准 §025 已相应修订。

2. **029 是 paper_text 源**：源数据集 reaction_energy 全是 ΔG，无 ΔH 实例。
   甲烷燃烧 ΔH @400K xTB 取自论文正文，reference 由我们在 pinned image 重新生成，
   再与 paper ≈ -12.51 eV 合理对比。

## 6. 未决 / 后续

- 上游 **Zenodo 归档**：repo 中未见 Zenodo 引用；数据源即 GitHub 仓库 + 本恢复的
  bundled dataset。若论文补充材料另有 Supplementary Table 2 的 human prompts，
  可作为 G1 旁证（当前不阻塞）。
- 下一批：构建 `dftworld-base-xtb`（TBLite，快、离线）→ 029（母模板，最强验收）
  → 030 → 027 → 026 → 025 → 028，每个按 acceptance_standard.md G0–G8 过验。**已完成（6/6）。**
- 环境就绪：`uv sync` 通过（pagent 0.7.12）；全部基础镜像可构建
  （`dftworld-base` + `-xtb` + `-mace` + cp2k/chem/deepmd/packmol）。

## 7. 完成记录

| Benchmark | 镜像 | Δ结果 | G0–G8 | 关键证据 |
|---|---|---|---|---|
| **029 react2enthalpy-methane** | dftworld-base-xtb | ΔH(400K)=**-12.509295 eV** | ✅ 全过 | 与论文 -12.51 差 7e-4；species G 与 dataset id=38 差 <1e-5；repro 偏差 2.4e-9 |
| **030 react2gibbs-ammonia** | dftworld-base-xtb | ΔG(400K)=**-2.162500 eV** | ✅ 全过 | dataset id=32 实例；species G 与 original 差 <3e-4；ΔG 与 original 差 5e-4；repro 偏差 2.5e-7 |
| **027 name2gibbs-co2** | dftworld-base-xtb | G(800K)=**-281.988392 eV** | ✅ 全过 | dataset id=7 实例；H/G/S 与 original 差 <1.1e-5 eV；repro 偏差 1.7e-13 |
| **026 name2vib-water** | dftworld-base-mace | 3 modes @ **207.5/461.8/484.1 meV** | ✅ 全过 | dataset id=6 实例；与 original 差 <0.13 meV (<1 cm⁻¹)；MACE 确定性 repro=0 |
| **025 name2opt-so2** | dftworld-base-mace | E_opt=**-16.8158084 eV** | ✅ 全过 | dataset id=5 实例；与 original 差 3.7e-7 eV；repro bit-identical (0.0) |
| **028 name2file-so2** | dftworld-base-mace | E_opt=**-16.8158084 eV** + optimized.xyz | ✅ 全过 | hybrid id=5+paper_text；与 original 差 3.7e-7 eV；optimized.xyz 3原子 (S:1,O:2) repro bit-identical |

### 029 关键工程经验（供后续复用）

1. **tblite 无 linux/arm64 wheel** → 需 gfortran+cmake+pkg-config+git+lapack/blas 从 sdist 编译。
   编译链：`tblite 0.4.0` meson 拉 mctc-lib subproject（需 git）。
2. **pymatgen 必要**（`get_symmetry_number`，source ase_core 依赖）→ 镜像含 pymatgen。
3. **generate_reference.py 必须复刻 source ase_core 原逻辑**（传 `all_energies` 全模式给
   IdealGasThermo、BFGS fmax=0.01、P=101325 Pa、spin=0）→ regenerated ≈ original <1e-5 eV。
4. **G0 泄漏检查必须精确**：site-packages 里 numpy/matplotlib 等自带 `tests/` 目录造成 3756 条误报；
   只查 workspace 顶层 reference/solution/tests + marker 文件。
5. **六层 evaluator 的 G=H−TS 是强 anti-cheat**：伪造 H/G/S 三值难以同时满足。
6. **TBLite 有 ~1e-9 eV 数值抖动**（OpenMP 求和顺序）→ tolerance 基于实测重复性（0.05 eV），
   不要求 bit-identical。
