# dftworld

AI agent benchmark for computational chemistry tasks.

Tasks use unified IDs `001`–`042` (easy → hard, plus knowledge drills, plus paper-derived cases). Directory names are `NNN-slug`（如 `001-hello`、`015-cp2k-scratch`、`042-go-water-dpmp`）。`eval.py` 扫描**仓库根级**的 `NNN-slug` 目录自动发现任务（无需 dataset.toml）——因此案例目录必须留在根级。

## Repository Map

仓库按「冻结案例 / 构造中区 / 活跃工作 / 基建」四个区组织：

```text
dftworld/
├── 001-hello … 041-smiles2coord   # 冻结案例区：已定稿的基准任务，只进不改
├── 042-go-water-dpmp/             # 构造中区：GO-water DeePMD 势（Runnable Draft）
├── 034-ai2kit-water64-…/          # 构造中区：water64 端到端势（hidden lineage 在 reference/ 下）
│
├── active-work/                   # ★ 活跃工作区：不参与评测扫描的工作内容
│   └── ai2kit/                    #   water64 专家流水线工作目录（034 血统；
│                                  #   62 个文件与封存哈希逐字节一致的恢复副本）
│
├── eval.py                        # pagentv4 Runner harness（v1/v2 布局都支持）
├── skills_sha.py                  # skill bundle 内容哈希（eval 与 build.sh 共用）
├── summarize.py                   # jobs/ 汇总 → jobs/SUMMARY.md
├── recover_gpu_canary.py          # 双 canary 资格化恢复驱动【必须在根级：
│                                  #   以所在目录为仓库根解析 sys.path 与 gateway
│                                  #   workspace；移动会破坏重启路径】
│
├── base-env-build/                # Docker 镜像构建（base/cp2k/chem/deepmd/packmol/mace/xtb/skills）
│   ├── build.sh
│   ├── skills/                    # benchmark skill bundle（→ dftworld-skills:<sha>）
│   └── .skill-image.json          # skill 镜像锁（tag/commit/skills_sha）
├── benchmark/sources/             # 论文溯源（chemgraph 40 条 ground_truth + matclaw + 验证器）
├── scripts/                       # infra / qualification / ablation / evidence 工具
│   └── ablation/hpc/              # G9 参考运行脚手架（submit/fetch/common/template）
├── tests/                         # 架构回归测试套件（pytest testpaths=["tests"]）
├── schemas/                       # result / case 等合同 schema
├── infra/runs/                    # 实验配置（skill-ablation-v2.yaml 等）
├── reference/runtime/             # 冻结运行时锁（cp2k / deepmd-jax；数据非配置，勿就地改）
├── evidence/                      # 清理回执 / 资格化证据 / 正式证据（append-only ledger 在
│                                  #   evidence/local-cleanup-20260825/decision-ledger.json）
├── docs/                          # architecture 合同 / operations 政策 / superpowers 计划
├── jobs/                          # 本地网关审计状态（hpc-audit.jsonl）
└── paper/                         # 源论文 PDF
```

> 规则速记：案例 = 根级 `NNN-slug`；可随时重建的状态（`.venv` `tmp/` 缓存）不入 Git；
> 删除任何路径前先查 [workspace-maintenance](docs/operations/workspace-maintenance.md)
> 并记账到 decision ledger。`ai2kit/` 是用户明确要求恢复的工作目录，未经指示不得再删。

## Setup

新机器从零到能跑，四步。

### 第 1 步：克隆 + Python 环境

```bash
git clone https://github.com/Mayerxjchen/mlffbench.git && cd mlffbench

# 装 uv（已有可跳过）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 一键装齐依赖（锁定版本：运行时 + pytest/numpy/ase）
uv sync --frozen --extra dev
```

验证环境正常：

```bash
uv run python -m pytest tests/core -q          # 应全绿
uv run python eval.py --help                   # 能打印用法即 OK
```

### 第 2 步：Docker 镜像（按需，跑哪个案例装哪个，别一次 all）

```bash
cd base-env-build
bash build.sh                # 看帮助 / 已有镜像
bash build.sh base           # 最小镜像（001–004、007）
bash build.sh cp2k chem      # 指定若干引擎镜像
bash build.sh skills         # skill bundle 镜像（--skills 运行才需要）
```

镜像依赖树：`ubuntu:24.04 → dftworld-base → {cp2k, packmol, chem, deepmd,
mace, xtb}`。国内拉不动 Docker Hub 时换源：
`DFTWORLD_BASE_IMAGE=docker.m.daocloud.io/library/ubuntu:24.04 bash build.sh base`

### 第 3 步：模型 API 凭据 `.env`

Provider/模型策略在 Run Config（`infra/runs/skill-ablation-v2.yaml`），
`.env` 只放它要求的变量值：

```bash
MIMO_API_KEY=...
MIMO_BASE_URL=https://...
```

自检：`uv run python -m dftworld_bench.config.cli doctor --run-config infra/runs/skill-ablation-v2.yaml`

### 第 4 步：HPC 集群接入（可选，只有跑真实 HPC 案例才需要）

站点专属信息（地址、账号、队列、已部署的运行时镜像路径）**不入库**——
向管理员或团队内部渠道索取，然后：

```bash
# 1) 在 ~/.ssh/config 配置你的站点别名：
Host <你的hpc别名>
    HostName <内网地址>        # 通常需校园网/VPN
    User <你的账号>

ssh <你的hpc别名> hostname      # 连通性自检
```

- 计算镜像（SIF）按站点流程部署；资格化状态看
  `evidence/hpc-dispatcher/qualification/<site>/receipt.json`
  （不存在 = 未封证，fail-closed）
- `recover_gpu_canary.py` 是单实例工具（锁文件护栏）：确认没有别的机器正在
  对同一集群做收养，再运行

### 跑评测

见下节 [Running eval](#running-eval)。

## Running eval

```bash
uv run python eval.py 001-hello -v
uv run python eval.py --all
uv run python eval.py --all --skills      # 启用 skill bundle（需先 build.sh skills）
uv run python eval.py 027-name2opt-so2 028-name2vib-water   # 指定多个任务
```

Counted runs load `infra/runs/skill-ablation-v2.yaml`. The command without
`--skills` is NS; `--skills` is WS. Local cases receive 500 turns and HPC cases
receive 512 turns from the same frozen config. Policy overrides require
`--uncounted-smoke` and never enter counted results.

> `--skills`（默认关）从 `dftworld-skills:<sha>` bundle 镜像提取 skills 作为
> skill_roots，提取后哈希须与 `base-env-build/.skill-image.json` 一致，否则本次
> run 作废（防镜像/manifest 漂移）。`--experiment` / `--condition` / `--replicate`
> 控制实验分组，`jobs/SUMMARY.md` 由 `summarize.py` 汇总生成。

```text
jobs/<timestamp>/
  summary.json
  threads/<task>/
    thread.toml
    metainfo.json
    workspace/              # = 容器内 /app
    messages.jsonl
    raw-submission/         # 冻结声明提交的私有宿主副本（随线程保留）
    sealed-submission/      # 隔离密封后的干净提交（manifest.json + 文件）
    verifier-logs/          # 独立 Verifier 的输出（result.json / reward.txt）
```

容器内任务根目录统一为 `/app`（instruction / tests / Dockerfile 同源）。eval 会把镜像 `/app` 拷进 `workspace/`，再把容器 `/app` 链到该目录；不继承 `~/.pagent` skills。

验证永远发生在**独立 Verifier 容器**里，不共享 Candidate：Agent 轮结束后先冻结并
收集声明提交（legacy 布局排除 `.venv`/`.skills`/`_dftworld_tests`/`tests` 等运行时
名称），**销毁候选容器**，再把密封后的干净提交以只读方式挂到 `/submission` 与
legacy 兼容的 `/app`，在全新容器中跑 `tests/test.sh`（非 root `65532:65532`、
`--network none`、`--read-only`、`--cap-drop ALL`、私有 `/tmp`）。Verifier 写
`result.json`（符合 `schemas/result.schema.json`）；缺失/损坏按 `VERIFIER_FAILURE`
（基础设施无效），绝不当作科学失败。旧 test.sh 仍写 `reward.txt` 的按兼容路径映射。

## 新案例镜像配置（025+）

新案例分两类引擎需求：ChemGraph 论文（DOI `10.1038/s42004-025-01776-9`）的 40 条
ground-truth 实例（027–030、039–040）只用 **MACE** 与 **TBLite/GFN2-xTB**；RDKit/SMILES
工具链（025–026、035–038、041）只用 **RDKit** 或 **RDKit + GFN2-xTB**。需要的计算镜像：

| 镜像 | 构建目录 | 内容 | 用途任务 |
|------|----------|------|----------|
| `dftworld-base-chem` | `base-env-build/chem/` | ase, rdkit | 025 / 026 / 041 |
| `dftworld-base-mace` | `base-env-build/mace/` | ase 3.25.0, rdkit, pymatgen, mace-torch 0.3.13（bake 离线权重 `mace-mpa-0-medium.model`） | 027 / 028 / 030 |
| `dftworld-base-xtb` | `base-env-build/xtb/` | ase 3.25.0, rdkit, tblite 0.4.0（sdist 编译）, pymatgen | 029 / 035 / 036 / 037 / 038 / 039 / 040 |
| `dftworld-skills:<sha>` | `base-env-build/skills.Dockerfile` | `skills/` → `/opt/electromind/skills/`，非执行镜像 | `--skills` 运行时提取 |

### 构建新案例镜像

```bash
cd base-env-build
bash build.sh chem mace xtb # 引擎镜像（各自 FROM dftworld-base）
bash build.sh skills       # skill bundle + 写 .skill-image.json
```

- `build.sh chem` / `mace` / `xtb` 会先确保 `dftworld-base` 存在（`expand()` 自动补依赖）。
- `bash build.sh 027-name2opt-so2` 也能按任务 `Dockerfile` 的 `FROM dftworld-base-mace`
  反推并构建，但新案例走 **v2 root 布局**（任务根 `Dockerfile`，非 `environment/`），
  所以建议显式 `bash build.sh chem mace xtb` 更直接。
- `mace` 镜像构建期会下载 MACE-MP-0 medium 权重并验证离线可加载（build 失败即停）。

### 任务 Dockerfile 约定（v2 root 布局）

新案例（025 起）目录结构与 001–024 不同——`Dockerfile` 直接在任务根：

```
027-name2opt-so2/
├── Dockerfile            # FROM dftworld-base-mace
├── public/               # COPY public/ /app/  ← 唯一进 agent 工作区的数据
├── reference/            # 参考解（生成脚本 + original/regenerated json）
├── solution/             # 官方解
├── tests/                # 验收测试
└── task.toml
```

```dockerfile
# 027-name2opt-so2/Dockerfile
FROM dftworld-base-mace
COPY public/ /app/
```

要点：
- `FROM` 决定引擎镜像；`COPY public/ /app/` 是任务数据唯一的入口。
- **`reference/ solution/ tests/` 绝不进 agent 视野**——eval 只从 Dockerfile
  `COPY` 目标注入 workspace（G0 泄漏检查）。
- `task.toml` 里 `[environment]` 的资源/超时按任务覆盖（新案例默认 2 核 / 4 GB /
  10 GB 存储 / `allow_internet = true`）。

### skill bundle 与 .skill-image.json

```bash
bash build.sh skills
```

构建后 `base-env-build/.skill-image.json` 记录 `tag` / `commit` / `skills_sha`。
`eval.py --skills` 从 `tag` 对应镜像提取 skills，用 `skills_sha.py` 重算哈希并与
manifest 比对；不一致即本次 run 作废。**改过 `base-env-build/skills/` 后必须重跑
`bash build.sh skills`**，否则 eval 会因 sha 漂移拒绝运行。

### 从源码校验新案例（可选）

```bash
uv run python 027-name2opt-so2/reference/generate_reference.py   # 在 dftworld-base-mace 里复现参考值
uv run pytest 027-name2opt-so2/tests/                            # 跑验收测试（oracle + 负样本）
```

每个新案例目录都有 `VALIDATION.json`（L1–L6 六层验收记录）与
`reference/source.lock.json`（溯源锁定：DOI / 数据集实例 id / 生成镜像）。溯源原始
材料在 `benchmark/sources/chemgraph/`（40 条 ground_truth + 实例分配 + 验收标准），
论文 PDF 在 `paper/`。

## Task Overview

| Task | Difficulty | Description |
|------|-----------|-------------|
| 001-hello | easy | Create hello.txt |
| 002-arithmetic | easy | Arithmetic calculation |
| 003-uv-version | easy | Check uv version |
| 004-python-version | easy | Check python3 version |
| 005-cp2k-version | easy | Check cp2k version |
| 006-packmol-version | easy | Check packmol version |
| 007-pip-install | easy | Install a Python package |
| 008-packmol-build | easy | Compile packmol from source |
| 009-cp2k-run | easy | CP2K single-point energy (complete input) |
| 010-deepmd-train | easy | DeePMD train/freeze/test (complete config) |
| 011-cp2k-template | medium | CP2K fill template |
| 012-ase-methane | medium | ASE methane XYZ |
| 013-rdkit-volume | medium | RDKit VDW volume |
| 014-deepmd-template | medium | DeePMD fill template |
| 015-cp2k-scratch | hard | CP2K from scratch |
| 016-deepmd-pipeline | hard | DeePMD full pipeline |
| 017-cp2k-cutoff | medium | Choose GPW CUTOFF from convergence table |
| 018-deepmd-rcut | medium | Choose DeePMD rcut from geometry report |
| 019-cp2k-eps-scf | medium | Choose CP2K EPS_SCF from SCF table |
| 020-hartree-to-ev | knowledge | Hartree → eV unit conversion |
| 021-gth-valence | knowledge | GTH `-qN` valence electron count |
| 022-spin-multiplicity | knowledge | Ground-state spin multiplicity |
| 023-deepmd-type-map | knowledge | DeePMD `type_map` element order |
| 024-xc-gth-match | knowledge | XC functional matching GTH-PBE |
| 025-name2smi | medium | Name → canonical SMILES (RDKit) |
| 026-name2coord | medium | Name → 3D XYZ coordinates (RDKit embed) |
| 027-name2opt-so2 | paper | SO₂ geometry optimization (MACE-MP-0 medium) |
| 028-name2vib-water | paper | Water vibrational frequencies (MACE-MP-0 medium) |
| 029-name2gibbs-co2 | paper | CO₂ Gibbs free energy @800 K (GFN2-xTB) |
| 030-name2file-so2 | paper | SO₂ optimization + save optimized XYZ (MACE-MP-0) |
| 031-matclaw-cips-active-distillation | paper | CIPS DeePMD active distillation |
| 032-matclaw-cips-curie-temperature | paper | CIPS Curie temperature (DeePMD MD) |
| 033-matclaw-cips-domain-wall-search | paper | CIPS domain-wall search (adaptive) |
| 034-ai2kit-water64-end-to-end-potential | hard | Water64 end-to-end DeePMD potential (CP2K AIMD → AL → validation) |
| 035-smiles2opt | medium | SMILES → geometry optimization (GFN2-xTB) |
| 036-smiles2vib | medium | SMILES → vibrational frequencies (GFN2-xTB) |
| 037-smiles2gibbs | medium | SMILES → Gibbs free energy (GFN2-xTB) |
| 038-smiles2file | medium | SMILES → optimized structure XYZ (GFN2-xTB) |
| 039-react2enthalpy-methane | paper | Methane combustion enthalpy @400 K (GFN2-xTB) |
| 040-react2gibbs-ammonia | paper | Ammonia synthesis ΔG @400 K (GFN2-xTB) |
| 041-smiles2coord | medium | SMILES → 3D XYZ coordinates (RDKit embed) |
| 042-go-water-dpmp | hard | GO-water DeePMD potential: graphene/graphene-oxide water interfaces (construction) |

`027`–`030`、`039`–`040` 来自 ChemGraph 论文 ground-truth；`025`–`026`、`035`–`038`、`041`
为 RDKit/SMILES 工具链任务；`031`–`033` 来自 MatClaw 论文、`034` 为 ai2kit 端到端势函数
流水线、`042` 为构造中的 GO-water DeePMP 案例（见上方「新案例镜像配置」与 [TASKS.md](TASKS.md)）。

## Workspace maintenance

Retention classes, protected areas, cache policy, and deletion rules live in
[docs/operations/workspace-maintenance.md](docs/operations/workspace-maintenance.md);
every deletion batch is recorded in the append-only
`evidence/local-cleanup-20260825/decision-ledger.json`.
