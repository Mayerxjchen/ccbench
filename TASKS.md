# dftworld 任务总览

本仓库 `dftworld` 是一个面向计算化学任务的 AI agent benchmark。任务按 **001–041** 统一编号（由易到难，含知识题与论文衍生案例），目录名为 `NNN-slug`。每个任务目录包含 `task.toml`（任务配置）和 `instruction.md`（agent 指令），并在 Docker 镜像中运行。

运行方式见 [README.md](README.md)：

```bash
uv run python eval.py <task-dir> -v
uv run python eval.py --all
```

---

## 任务清单

共 **41** 个任务。

### 入门 / 环境检查（easy）

| 任务 | 输出文件 | 描述 |
|------|----------|------|
| [001-hello](001-hello/) | `hello.txt` | 创建内容为 `hello world`（无尾换行）的文件 |
| [002-arithmetic](002-arithmetic/) | `answers.txt` | 解 5 道四则运算题（加减乘除），每行一个结果 |
| [003-uv-version](003-uv-version/) | `out.txt` | 查询 `uv` 版本 |
| [004-python-version](004-python-version/) | `out.txt` | 查询 `python3` 版本（仅数字，`<answer>` 包裹） |
| [005-cp2k-version](005-cp2k-version/) | `out.txt` | 查询 `cp2k` 版本 |
| [006-packmol-version](006-packmol-version/) | `out.txt` | 查询 `packmol` 版本（仅数字，`<answer>` 包裹） |
| [007-pip-install](007-pip-install/) | — | 用 `pip3` 全局安装 `requests` 库 |
| [008-packmol-build](008-packmol-build/) | `packmol_version.txt` | 从源码 `packmol-21.2.1.tar.gz` 编译安装 packmol |
| [009-cp2k-run](009-cp2k-run/) | `out.txt` | 直接运行给定的 `H2O.inp`，提取最终分子总能量 |
| [010-deepmd-train](010-deepmd-train/) | — | 数据与 `input.json` 已就绪，执行 `dp train` / `freeze` / `test` |

### 中等（medium）

| 任务 | 描述 |
|------|------|
| [011-cp2k-template](011-cp2k-template/) | 填充模板 `H2O_template.inp` 的占位符，运行并提取最终总能量 |
| [012-ase-methane](012-ase-methane/) | 用 ASE 构建甲烷分子，写出标准 XYZ 到 `/app/CH4.xyz` |
| [013-rdkit-volume](013-rdkit-volume/) | 用 RDKit 计算咖啡因的范德华体积（约 164–165 Å³，2 位小数写入 `out.txt`） |
| [014-deepmd-template](014-deepmd-template/) | 填充 `input_template.json` 占位符，再训练评估 |
| [017-cp2k-cutoff](017-cp2k-cutoff/) | 读收敛表，选最小满足精度的 GPW `CUTOFF`，写 `params.txt` 并算 H₂O 能量 |
| [018-deepmd-rcut](018-deepmd-rcut/) | 读数据集几何报告，按规则选 `rcut`，训练并输出 RMSE |
| [019-cp2k-eps-scf](019-cp2k-eps-scf/) | 读 SCF 收敛表，选最松且达标的 `EPS_SCF`，算 H₂O 能量 |

### 困难（hard）

| 任务 | 描述 |
|------|------|
| [015-cp2k-scratch](015-cp2k-scratch/) | 从零编写 CP2K 输入文件（DFT/PBE + GPW），提取总能量写入 `out.txt` |
| [016-deepmd-pipeline](016-deepmd-pipeline/) | 完整流水线：`dpdata` 转换 ABACUS MD 数据 → 自写 `input.json` → 训练/冻结/测试 |

### 知识（knowledge）

不跑重型计算，考查单位、电子结构常识、DeePMD/CP2K 约定（多数只需 `dftworld-base`）。

| 任务 | 描述 |
|------|------|
| [020-hartree-to-ev](020-hartree-to-ev/) | 用给定 Ha→eV 常数换算总能量 |
| [021-gth-valence](021-gth-valence/) | `GTH-PBE-q6` 表示的价电子数 |
| [022-spin-multiplicity](022-spin-multiplicity/) | 中性 CH₄ 基态自旋多重度 |
| [023-deepmd-type-map](023-deepmd-type-map/) | C/H 体系的 `type_map` 字母序 |
| [024-xc-gth-match](024-xc-gth-match/) | 与 GTH-PBE 赝势匹配的 XC 泛函 |

### 分子工具链（medium，025–026）

RDKit 工具 drill，无论文出处，v2 root 布局（任务根 `Dockerfile` + `public/`）。

| 任务 | 引擎 | 描述 |
|------|------|------|
| [025-name2smi](025-name2smi/) | rdkit | 分子名 → 规范 SMILES（查表 + RDKit canonical） |
| [026-name2coord](026-name2coord/) | rdkit | 分子名 → 3D XYZ 坐标（RDKit embed, seed=42） |

### 论文衍生（paper，027–030、039–040）

来自 ChemGraph 论文（DOI `10.1038/s42004-025-01776-9`）的 40 条 ground-truth 实例，
只用 MACE 与 TBLite/GFN2-xTB 两种引擎（引擎镜像见 [README](README.md#新案例镜像配置025030)）。
v2 root 布局：任务根 `Dockerfile` + `public/`（`COPY public/ /app/`），`reference/`
`solution/` `tests/` 不进入 agent 工作区。

| 任务 | 引擎 | 描述 |
|------|------|------|
| [027-name2opt-so2](027-name2opt-so2/) | MACE-MP-0 | SO₂ 几何优化，报告优化后能量 |
| [028-name2vib-water](028-name2vib-water/) | MACE-MP-0 | 水的振动频率（meV 与 cm⁻¹） |
| [029-name2gibbs-co2](029-name2gibbs-co2/) | GFN2-xTB | CO₂ 在 800 K 的热力学量（H/G/S） |
| [030-name2file-so2](030-name2file-so2/) | MACE-MP-0 | SO₂ 优化 + 保存优化后结构 XYZ |
| [039-react2enthalpy-methane](039-react2enthalpy-methane/) | GFN2-xTB | 甲烷燃烧 400 K 的反应焓 ΔH |
| [040-react2gibbs-ammonia](040-react2gibbs-ammonia/) | GFN2-xTB | 合成氨 400 K 的反应吉布斯自由能 ΔG |

### SMILES 工具链（medium，035–038、041）

SMILES 输入 → 结构/性质任务（RDKit + GFN2-xTB），v2 root 布局，无论文出处。

| 任务 | 引擎 | 描述 |
|------|------|------|
| [035-smiles2opt](035-smiles2opt/) | GFN2-xTB | SMILES → 几何优化（RDKit + tblite） |
| [036-smiles2vib](036-smiles2vib/) | GFN2-xTB | SMILES → 振动频率（数值 Hessian） |
| [037-smiles2gibbs](037-smiles2gibbs/) | GFN2-xTB | SMILES → 吉布斯自由能（IdealGasThermo） |
| [038-smiles2file](038-smiles2file/) | GFN2-xTB | SMILES → 优化结构存为分子名 XYZ |
| [041-smiles2coord](041-smiles2coord/) | rdkit | SMILES → 3D XYZ 坐标（RDKit embed, seed=42） |

### 论文衍生 / MatClaw（hard，031–033，GPU）

031–033 来自 MatClaw 论文（CIPS = CuInP₂S₆ 单层，DeePMD 势函数）。
用 DeePMD-kit 2.2.11 + Apptainer + 1×A100 GPU 跑正式运行，两层验证（论文流程 + 合成夹具）。

| 任务 | 方法 | 描述 |
|------|------|------|
| [031-matclaw-cips-active-distillation](031-matclaw-cips-active-distillation/) | DeePMD 主动蒸馏 | 从 teacher 势主动蒸馏快速 CIPS DeePMD 势（✅ `benchmark_valid=true`） |
| [032-matclaw-cips-curie-temperature](032-matclaw-cips-curie-temperature/) | DeePMD MD | 从收敛 MD 估算 CIPS 居里温度 Tc（✅ `benchmark_valid=true`） |
| [033-matclaw-cips-domain-wall-search](033-matclaw-cips-domain-wall-search/) | 自适应搜索 | 电场/温度自适应搜索 CIPS 畴壁传播（✅ `benchmark_valid=true`） |

### 端到端势函数流水线（hard，034，GPU）

[034-ai2kit-water64-end-to-end-potential](034-ai2kit-water64-end-to-end-potential/)
从 64-H₂O 结构出发，CP2K AIMD → 主动学习 → DeePMD 势 → 验证的端到端流水线。
`real_hpc_controller` 后端，agent 只做控制层（HPC 由 harness 提供），
与 matclaw 同属 HPC 控制层案例（案例本体在 `.worktrees/034-hpc-controller/` 开发，主仓为恢复副本）。

## 任务总表

| 编号 | 任务 | 难度 | 描述 |
|------|------|------|------|
| 001 | [001-hello](001-hello/) | easy | 创建 `hello world`（无尾换行） |
| 002 | [002-arithmetic](002-arithmetic/) | easy | 解 5 道四则运算题 |
| 003 | [003-uv-version](003-uv-version/) | easy | 查询 `uv` 版本 |
| 004 | [004-python-version](004-python-version/) | easy | 查询 `python3` 版本 |
| 005 | [005-cp2k-version](005-cp2k-version/) | easy | 查询 `cp2k` 版本 |
| 006 | [006-packmol-version](006-packmol-version/) | easy | 查询 `packmol` 版本 |
| 007 | [007-pip-install](007-pip-install/) | easy | `pip3` 全局安装 `requests` |
| 008 | [008-packmol-build](008-packmol-build/) | easy | 源码编译 packmol |
| 009 | [009-cp2k-run](009-cp2k-run/) | easy | 运行 `H2O.inp` 提取总能量 |
| 010 | [010-deepmd-train](010-deepmd-train/) | easy | `dp train` / `freeze` / `test` |
| 011 | [011-cp2k-template](011-cp2k-template/) | medium | 填充 H₂O 模板并运行 |
| 012 | [012-ase-methane](012-ase-methane/) | medium | ASE 构建甲烷 XYZ |
| 013 | [013-rdkit-volume](013-rdkit-volume/) | medium | RDKit 范德华体积 |
| 014 | [014-deepmd-template](014-deepmd-template/) | medium | 填充 DeePMD 模板 |
| 015 | [015-cp2k-scratch](015-cp2k-scratch/) | hard | 从零写 CP2K 输入 |
| 016 | [016-deepmd-pipeline](016-deepmd-pipeline/) | hard | 完整 DeePMD 流水线 |
| 017 | [017-cp2k-cutoff](017-cp2k-cutoff/) | medium | 选 GPW `CUTOFF` |
| 018 | [018-deepmd-rcut](018-deepmd-rcut/) | medium | 选 `rcut` 训练 |
| 019 | [019-cp2k-eps-scf](019-cp2k-eps-scf/) | medium | 选 `EPS_SCF` |
| 020 | [020-hartree-to-ev](020-hartree-to-ev/) | easy | Ha→eV 换算 |
| 021 | [021-gth-valence](021-gth-valence/) | easy | `GTH-PBE-q6` 价电子数 |
| 022 | [022-spin-multiplicity](022-spin-multiplicity/) | easy | CH₄ 基态自旋多重度 |
| 023 | [023-deepmd-type-map](023-deepmd-type-map/) | easy | `type_map` 字母序 |
| 024 | [024-xc-gth-match](024-xc-gth-match/) | easy | GTH-PBE 匹配 XC 泛函 |
| 025 | [025-name2smi](025-name2smi/) | medium | name→SMILES |
| 026 | [026-name2coord](026-name2coord/) | medium | name→3D 坐标 |
| 027 | [027-name2opt-so2](027-name2opt-so2/) | medium | SO₂ 几何优化 |
| 028 | [028-name2vib-water](028-name2vib-water/) | medium | 水振动频率 |
| 029 | [029-name2gibbs-co2](029-name2gibbs-co2/) | medium | CO₂ 800 K 热力学量 |
| 030 | [030-name2file-so2](030-name2file-so2/) | medium | SO₂ 优化 + XYZ |
| 031 | [031-matclaw-cips-active-distillation](031-matclaw-cips-active-distillation/) | hard | CIPS 势主动蒸馏（✅） |
| 032 | [032-matclaw-cips-curie-temperature](032-matclaw-cips-curie-temperature/) | hard | CIPS 居里温度（✅） |
| 033 | [033-matclaw-cips-domain-wall-search](033-matclaw-cips-domain-wall-search/) | hard | CIPS 畴壁搜索（✅） |
| 034 | [034-ai2kit-water64-end-to-end-potential](034-ai2kit-water64-end-to-end-potential/) | hard | 64-H₂O 端到端 DeePMD 势 |
| 035 | [035-smiles2opt](035-smiles2opt/) | medium | SMILES→几何优化 |
| 036 | [036-smiles2vib](036-smiles2vib/) | medium | SMILES→振动频率 |
| 037 | [037-smiles2gibbs](037-smiles2gibbs/) | medium | SMILES→吉布斯自由能 |
| 038 | [038-smiles2file](038-smiles2file/) | medium | SMILES→优化结构 XYZ |
| 039 | [039-react2enthalpy-methane](039-react2enthalpy-methane/) | medium | 甲烷燃烧 ΔH |
| 040 | [040-react2gibbs-ammonia](040-react2gibbs-ammonia/) | medium | 合成氨 ΔG |
| 041 | [041-smiles2coord](041-smiles2coord/) | medium | SMILES→3D 坐标 |

## 按难度汇总

- **easy**：001–010、020–024
- **medium**：011–014、017–019、025–030、035–038、041
- **hard**：015–016、031–034

## 运行环境

- 任务在 Docker 中执行，基础镜像见 [base-env-build/](base-env-build/)。任务数据放在各自 `environment/`（001–024，v1 布局）或 `public/`（025 起，v2 root 布局）下。
- 资源：CPU 任务多用 1–2 核、2–4 GB 内存；计算类（cp2k/deepmd）为 2 核 4 GB。所有任务 `allow_internet = true`，超时均为 600s。
- 完整结构说明见 [README.md 的 Project Structure](README.md#project-structure)。
