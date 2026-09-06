# CCBench

AI Agent Benchmark for Computational Chemistry.

CCBench 面向计算化学与材料模拟领域的前沿大模型与智能体系统，提供端到端、高度隔离、具备物理真实性校验的长周期科学评测基准。

使用者无需理解复杂历史演进，克隆仓库后即可直接回答三个核心问题：

```text
cases/       我能测试什么？       → 5 个长周期跨学科材料与分子建模科学基准案例
experiments/ 我怎么比较模型/Skill？ → 声明式 Run Config 与实验矩阵规格定义
ccbench      我怎么运行？         → 一行命令安装、自检与隔离沙箱评测执行
```

---

## 仓库结构 (Repository Architecture)

项目物理结构严格收敛为三大支柱与统一 CLI 入口：

```text
ccbench/
├── cases/                                # 评测案例集（严格四件套规范）
│   ├── 001-matclaw-cips-active-distillation  # 铁电体系主动学习势函数蒸馏
│   ├── 002-matclaw-cips-curie-temperature    # 铁电居里温度分子动力学搜索
│   ├── 003-matclaw-cips-domain-wall-search   # 外电场/温度诱导铁电畴壁动力学搜索
│   ├── 004-ai2kit-water64-end-to-end-potential # 水体系全流程第一性原理势函数流水线
│   └── 005-go-water-dpmp                 # 氧化石墨烯-水界面 JAX/DP-MP 势函数重现
│
├── experiments/                          # 实验矩阵与评测规格
│   ├── main.toml                         # 生产基线评测矩阵 (Counted runs)
│   ├── smoke.toml                        # 快速冒烟测试配置 (Uncounted smoke)
│   └── models.toml                       # 模型代号与参数映射定义
│
├── runtimes/                             # 运行时与容器镜像规范 (SSOT)
│   ├── recipes/                          # 可复现构建配方 (Dockerfile 与 build.sh)
│   ├── locks/                            # 密码学锚定的环境与镜像锁定文件
│   └── trust.toml                        # 资格化公钥信任锚点
│
├── ccbench / eval.py                     # 统一评测调度 Harness CLI 入口
├── summarize.py                          # 评测结果结构化聚合工具
├── scripts/                              # 自动化构建、资格化与分析辅助脚本
└── tests/                                # 覆盖仓库架构、合同与科学属性的回归测试套件
```

---

## 快速上手 (Quick Start)

所有步骤保证从 fresh clone 状态起 100% 直接可执行。

### 第 1 步：安装环境

推荐使用 [uv](https://astral.sh/uv) 极速包管理器：

```bash
git clone https://github.com/Mayerxjchen/ccbench.git && cd ccbench

# 一键安装生产与开发依赖（严格锁定版本）
uv sync --frozen --extra dev
```

### 第 2 步：自检与回归测试

运行架构完整性门禁与 CLI 自检：

```bash
uv run ccbench --help
uv run pytest tests/repo/ -q
```

### 第 3 步：配置通用模型 API 凭据

复制环境配置模板：

```bash
cp .env.example .env
```

在 `.env` 中填入你的大模型调用端点与密钥（支持任何兼容 OpenAI / DeepSeek 协议的 API）：

```bash
CCBENCH_API_KEY=your_api_key_here
CCBENCH_BASE_URL=https://api.deepseek.com
```

### 第 4 步：运行基准案例

你可以通过统一的 `ccbench` 命令或标准 `eval.py` 执行评测：

```bash
# 运行单个科学案例（默认在隔离沙箱内启动 Candidate Agent）
uv run ccbench run 001-matclaw-cips-active-distillation

# 或者使用 eval.py 入口
uv run python eval.py 001-matclaw-cips-active-distillation

# 启用 Benchmark Skills 辅助
uv run ccbench run 001-matclaw-cips-active-distillation --skills

# 运行完整评测矩阵（建议配置并行沙箱）
uv run ccbench run --all
```

评测产物与轨迹统一落盘至 `jobs/<run_id>/` 目录。使用以下命令生成聚合报表：

```bash
uv run python summarize.py
```

---

## 核心科学评测案例 (Benchmark Cases)

所有案例均收敛至 `cases/<case-id>/` 目录，严格包含四项标准构件（四件套）：

```text
cases/<case-name>/
├── task.md          # Agent 任务引导与科学目标描述
├── input/           # 注入到 Agent 工作区的初始物理数据与代码骨架
├── verifier/        # 独立 Verifier 验收测试（只读挂载，杜绝信息泄露）
└── case.toml        # 任务元数据、资源约束与评测门禁规范
```

| 案例目录 | 科学领域 | 计算后端引擎 | 评测目标与能力考察 |
|:---|:---|:---|:---|
| `001-matclaw-cips-active-distillation` | 铁电材料物理 | `matclaw-cips` (DeePMD+LAMMPS) | 运用主动学习闭环蒸馏高精度紧凑势函数 |
| `002-matclaw-cips-curie-temperature` | 统计力学 MD | `matclaw-cips` (DeePMD+LAMMPS) | 基于分子动力学校准相变温度与居里点 |
| `003-matclaw-cips-domain-wall-search` | 外场响应动力学 | `matclaw-cips` (DeePMD+LAMMPS) | 在外电场与温度耦合下搜索畴壁动力学机制 |
| `004-ai2kit-water64-end-to-end-potential` | 全自动势函数管线 | `ai2kit` + `cp2k` | 构建端到端基于第一性原理的主动学习流水线 |
| `005-go-water-dpmp` | 界面化学计算 | `deepmd-jax` / `jax-gpu` | 氧化石墨烯-水界面 DP-MP 势函数复现与 MD 验证 |

> **隔离与防作弊机制**：评测执行期间，Agent 只能访问注入工作区的 `input/` 内容；`verifier/` 验收脚本与测试用例运行在**独立、非特权、网络隔离（`--network none`）**的只读容器中，彻底杜绝作弊与训练集污染。

---

## 运行时与镜像构建 (Runtimes & Containers)

CCBench 针对不同科学计算任务提供经过验证的轻量化容器与环境配方：

```bash
cd runtimes/recipes

# 查看可用构建目标
bash build.sh -h

# 构建 Claude Code 候选智能体隔离沙箱
bash build.sh agent-claude-code

# 构建 MatClaw CIPS 计算镜像
bash build.sh matclaw-cips

# 按案例依赖自动解析并构建对应镜像
bash build.sh 001
```

### 运行时真实性与资格化状态说明 (Qualification Truthfulness)

CCBench 对环境与资格化证明执行严格的密码学单一真相源（SSOT）审计：

- **MatClaw CIPS Runtime** (`runtimes/locks/matclaw-cips-runtime.lock.json`): 对应已验证构建镜像 `compshareImage-1uw6sd44931i`。
- **JAX GPU Runtime** (`runtimes/locks/jax-runtime.lock.json`): 对应历史构建 JAX GPU 镜像 `compshareImage-1uyaneriamfz`。
  - **公开仓库状态**：如实标注为 `BUILT_NOT_QUALIFIED`（当前 `runtimes/recipes/jax-gpu/` 处于 `UNBUILT` 状态，待新镜像构建与物理资格化）。
  - **血统不可篡改保证**：代码严格保障不可事后篡改历史构建证据，源码归档 SHA、Recipe Digest 与 Image ID 因果链严格闭环。
  - **密码学验证与准入**：正式 Gate C 资格化收据由维护者站点使用 maintainer private key **签名**，CCBench 使用注入的 trusted public key **验证**。公开仓库绝不包含私钥，确保凭据隔离与真实性验证。

---

## 许可证与贡献指南 (License & Contributing)

本项目遵循 Apache 2.0 开源许可。详细开发者维护指南请参阅 `maintainer/` 目录。

