# CCBench

AI agent benchmark for computational chemistry.

Tasks focus on **scientific benchmark cases** (`001-matclaw-cips-active-distillation`, `002-matclaw-cips-curie-temperature`, `003-matclaw-cips-domain-wall-search`, `004-ai2kit-water64-end-to-end-potential`, `005-go-water-dpmp`). Directory names are `NNN-slug`. `eval.py` scans root-level `NNN-slug` directories to discover benchmark cases.

## Repository Map

仓库按「长周期核心案例 / 构造中区 / 活跃工作 / 基建」组织：

```text
ccbench/
├── 001-matclaw-cips-active-distillation   # 核心案例：MatClaw CIPS 势能主动学习蒸馏
├── 002-matclaw-cips-curie-temperature     # 核心案例：MatClaw CIPS 居里温度分子动力学
├── 003-matclaw-cips-domain-wall-search    # 核心案例：MatClaw CIPS 畴壁搜索
├── 004-ai2kit-water64-…/                  # 核心案例：ai2kit训练水的MLFF
├── 005-go-water-dpmp/                     # 核心案例：石墨烯氧化程度如何改变界面水的分子组织与振动响应
│
├── active-work/                           # 活跃工作区：不参与评测扫描的工作内容
│   └── ai2kit/                            #   water64 专家流水线工作目录
│
├── eval.py                                # Formal Runner harness（支持 ccbench / mlffbench 入口）
├── summarize.py                           # jobs/ 汇总 → jobs/SUMMARY.md
├── scripts/infra/qualify_hpc_dispatcher.py   # 双 canary 资格化驱动（--phase
│                                  #   preflight/canary/cp2k/verify/resume；
│                                  #   --phase resume 完成中断的 canary，绝不重提）
│
├── base-env-build/                # Docker 镜像构建（base/cp2k/deepmd/matclaw-cips/candidate-claude-code/skills）
│   ├── build.sh
│   ├── skills/                    # benchmark skill bundle（→ dftworld-skills:<sha>）
│   └── .skill-image.json          # skill 镜像锁（tag/commit/skills_sha）
├── benchmark/sources/             # 论文溯源（matclaw 论文与参考实现；旧 ChemGraph 已从当前版本移除、历史可由 Git 追溯）
├── scripts/                       # infra / qualification / ablation / evidence 工具
│   └── ablation/hpc/              # G9 参考运行脚手架（submit/fetch/common/template）
├── tests/                         # 架构回归测试套件（pytest testpaths=["tests"]）
├── schemas/                       # result / case / experiment-spec 等合同 schema
├── experiments/                   # 实验规格（main.toml, smoke.toml, models.toml）
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

### 第 1 步：克隆 + Python 环境

```bash
git clone https://github.com/Mayerxjchen/ccbench.git && cd ccbench

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
bash build.sh base           # 最小镜像
bash build.sh cp2k deepmd    # 指定若干引擎镜像
bash build.sh skills         # skill bundle 镜像（--skills 运行才需要）
```

镜像依赖树：`ubuntu:24.04 → dftworld-base → {cp2k, deepmd, matclaw-cips, deepmd-jax}`。国内拉不动 Docker Hub 时换源：
`DFTWORLD_BASE_IMAGE=docker.m.daocloud.io/library/ubuntu:24.04 bash build.sh base`

### 第 3 步：模型 API 凭据 `.env`

Provider/模型策略在 Run Config（`infra/runs/skill-ablation-v2.yaml`），
`.env` 只放它要求的变量值：

```bash
API_KEY=...
BASE_URL=https://...
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
- `--phase resume` 是单实例恢复路径（锁文件护栏）：确认没有别的机器正在
  对同一集群做收养，再运行；它绝不二次提交——只等待已入队作业到达终态、
  按持久化 SUBMIT_INTENT marker 收养并 settle 同一条审计链

### 跑评测

见下节 [Running eval](#running-eval)。

## Running eval

```bash
uv run python eval.py 001-matclaw-cips-active-distillation -v
uv run python eval.py --all
uv run python eval.py --all --skills      # 启用 skill bundle（需先 build.sh skills）
uv run python eval.py 001-matclaw-cips-active-distillation 002-matclaw-cips-curie-temperature   # 指定多个任务
```

Counted runs load `infra/runs/skill-ablation-v2.yaml`. The command without
`--skills` is NS; `--skills` is WS. Local smoke cases receive 64 turns and HPC
formal cases receive 1024 turns from the same frozen config. Policy overrides require
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

容器内任务根目录统一为 `/app`（instruction / tests / Dockerfile 同源）。eval 会把镜像 `/app` 拷进 `workspace/`，再把容器 `/app` 链到该目录；不继承任何宿主外部 agent skills（完全与宿主 agent 配置文件、全局技能目录及外部环境隔离解耦）。

验证永远发生在**独立 Verifier 容器**里，不共享 Candidate：Agent 轮结束后先冻结并
收集声明提交（legacy 布局排除 `.venv`/`.skills`/`_dftworld_tests`/`tests` 等运行时
名称），**销毁候选容器**，再把密封后的干净提交以只读方式挂到 `/submission` 与
legacy 兼容的 `/app`，在全新容器中跑 `tests/test.sh`（非 root `65532:65532`、
`--network none`、`--read-only`、`--cap-drop ALL`、私有 `/tmp`）。Verifier 写
`result.json`（符合 `schemas/result.schema.json`）；缺失/损坏按 `VERIFIER_FAILURE`
（基础设施无效），绝不当作科学失败。旧 test.sh 仍写 `reward.txt` 的按兼容路径映射。

## 长周期案例镜像与运行环境

5 个核心长案例覆盖 MatClaw CIPS、ai2kit + CP2K 和 GO-water DeePMD-JAX 科学工作流。计算环境支持容器化沙箱与 HPC 控制器架构：

| 案例 | 运行时引擎 | 核心组件 | 计算场景 |
|------|------------|----------|----------|
| `031-matclaw-cips-active-distillation` | `matclaw-cips` | DeePMD-kit, LAMMPS, ASE | 主动学习势函数蒸馏 |
| `032-matclaw-cips-curie-temperature` | `matclaw-cips` | DeePMD-kit, LAMMPS, ASE | 居里温度分子动力学搜索 |
| `033-matclaw-cips-domain-wall-search` | `matclaw-cips` | DeePMD-kit, LAMMPS, ASE | 外场/温度下铁电畴壁搜索 |
| `034-ai2kit-water64-end-to-end-potential` | `ai2kit` / `cp2k` | ai2kit, CP2K, DeePMD-kit | 水体系端到端势函数流水线 |
| `042-go-water-dpmp` | `deepmd-jax` | JAX, DeePMD-kit, DPMP | GO–water 界面势函数复现与隐式验证 |

### 任务目录约定（科学基准布局）

长周期案例采用规范的根级目录结构：

```text
031-matclaw-cips-active-distillation/
├── Dockerfile            # 容器环境定义（如适用）
├── public/               # COPY public/ /app/  ← 注入 agent 工作区的数据
├── reference/            # 科学参考数据与生成脚本
├── solution/             # 官方参考解与基准流程
├── tests/                # 独立 Verifier 验收测试（含 test.sh 与 test_outputs.py）
├── task.toml             # 任务元数据与资源约束
└── instruction.md        # 任务指导说明
```

要点：
- 任务数据通过 `public/` 注入 agent 工作区。
- **`reference/ solution/ tests/` 绝不进 agent 视野**——eval 在独立只读隔离沙箱中运行验证器，杜绝信息泄漏。
- `task.toml` 声明严格的超时控制、资源配额与评测门禁规范。

### skill bundle 与 .skill-image.json

```bash
cd base-env-build
bash build.sh skills
```

构建后 `base-env-build/.skill-image.json` 记录 `tag` / `commit` / `skills_sha`。
`eval.py --skills` 从 `tag` 对应镜像提取 skills，用 `skills_sha.py` 重算哈希并与
manifest 比对；不一致即本次 run 作废。**改过 `base-env-build/skills/` 后必须重跑
`bash build.sh skills`**，否则 eval 会因 sha 漂移拒绝运行。

### 从源码校验新案例（可选）

```bash
uv run python 031-matclaw-cips-active-distillation/reference/generate_reference.py   # 复现参考值
uv run pytest tests/contracts/test_hpc_cases_gold_matrix.py   # 跑长案例契约验证矩阵
```

每个长案例目录包含 `VALIDATION.json`、`benchmark_valid.json`、`task.toml`、`instruction.md`、`reference/` 以及独立验证器套件。详细科学背景、理论依据与指标说明见 [TASKS.md](TASKS.md)。

## Task Overview

| Task | Execution Class | Runtime Family | Keywords / Description |
|---|---|---|---|
| `031-matclaw-cips-active-distillation` | `hpc_controller` | `matclaw-cips` | Active distillation of a fast CIPS DeePMD potential (active-learning, cips) |
| `032-matclaw-cips-curie-temperature` | `hpc_controller` | `matclaw-cips` | Curie temperature MD search with DeePMD (cips, curie-temperature, md) |
| `033-matclaw-cips-domain-wall-search` | `hpc_controller` | `matclaw-cips` | Domain-wall search under electric field/temperature (domain-wall, ferroelectric) |
| `034-ai2kit-water64-end-to-end-potential` | `hpc_controller` | `ai2kit`, `cp2k` | End-to-end water potential (CP2K AIMD -> active learning -> validation) |
| `042-go-water-dpmp` | `hpc_controller` | `deepmd-jax` | End-to-end development of a GO–water DPMP interatomic potential |

`031`–`033` 源自 MatClaw 高性能铁电体系科学计算工作流；`034` 为 ai2kit + CP2K 端到端全自动势函数主动学习流水线；`042` 为氧化石墨烯-水界面 (GO–water) JAX/DPMP 势函数复现与隐式物理验证案例。详细规范请参阅 [TASKS.md](TASKS.md)。


## Workspace maintenance

Retention classes, protected areas, cache policy, and deletion rules live in
[docs/operations/workspace-maintenance.md](docs/operations/workspace-maintenance.md);
every deletion batch is recorded in the append-only
`evidence/local-cleanup-20260825/decision-ledger.json`.
