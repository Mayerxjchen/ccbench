# 工作区归类、去重与可维护性整理设计

## 目标

以 `dftworld2/` 作为唯一日常维护入口，完整保留当前 042、skill-ablation-v2、HPC 清理与 HPC dispatcher 资格验证工作，将仍有价值的验证资产归入主仓库，删除已确认可重建、已迁移或与当前工作无关的重复内容，并通过目录约定、说明文档和自动检查降低后续维护成本。

## 当前状态

工作区根目录 `/Users/chenxuanjie/案例测试` 不是 Git 仓库，而是多个项目与归档的容器。核心状态如下：

- `dftworld2/` 是主仓库，位于 `main`，相对 `origin/main` ahead 333、behind 4。
- 主仓库存在已修改的 `042-go-water-dpmp/instruction.md` 和 `infra/runs/skill-ablation-v2.yaml`，以及 042、HPC 清理和实验说明相关的未跟踪资产。
- `dftworld2-qualification/` 是同一 Git 仓库的 detached worktree，HEAD 与主仓库相同；其中仍有 HPC dispatcher 资格验证证据、runtime lock 和运行脚本。
- `dftworld2/tmp/` 约 107 MB，主要是构建 rootfs 和中断临时文件；`.venv` 与 Python 缓存均可重建。
- `hpc-cleanup-20260822-archive/` 约 2.7 GB，`local-cleanup-20260823/` 约 453 MB；二者包含恢复与审计材料，不能只按体积或修改时间删除。
- 根目录 `skills/` 与 `tests/` 是独立的技能源码及其测试，不因其与仓库内 skill bundle 内容相似而直接认定为重复。

## 整理原则

1. **当前工作优先。** 以 Git 修改、未跟踪成果、近期待办、验证回执和可复现实验合同共同判断是否属于当前工作，不只依赖文件时间。
2. **主仓库唯一。** 可维护源码、配置、说明和必要证据最终只在 `dftworld2/` 保留一个权威副本。
3. **证据与产物分离。** 复现所需的小型合同、清单、日志摘要和哈希进入仓库；大型可再生成产物留在外部归档或删除，不混入源码目录。
4. **删除必须有证据。** 文件只有在满足“可重建”“内容已迁移且哈希一致”“不再被引用且测试覆盖”之一，并通过对应验证后才能删除。
5. **现有工作不被覆盖。** 不重置、不改写、不丢弃主仓库和 qualification worktree 中的任何未提交内容；迁移时先复制、验证，再移除来源。
6. **范围克制。** 不在本轮重新设计 001–042 案例协议，也不为了目录美观批量改名稳定路径。

## 目标结构

工作区根目录整理后采用以下职责边界：

```text
/Users/chenxuanjie/案例测试/
├── dftworld2/                         # 唯一主仓库与日常维护入口
│   ├── 001-.../ ... 042-.../          # 案例定义，保持现有稳定路径
│   ├── dftworld_bench/                # Benchmark Python 实现
│   ├── infra/                         # 运行、隔离和调度基础设施
│   ├── scripts/                       # 可重复执行的维护/资格验证命令
│   ├── reference/                     # 跨案例运行时锁与公共合同
│   ├── evidence/                      # 精简、可审计的运行与清理证据
│   ├── docs/
│   │   ├── experiments/               # 实验约束和已知限制
│   │   ├── operations/                # 清理、恢复、资格验证操作说明
│   │   └── superpowers/               # 设计与实施计划
│   └── tests/                         # 仓库级自动验证
├── skills/                            # 独立维护的技能源
├── tests/                             # 根级技能测试
├── archives/                          # 仅保留一套经校验的恢复归档
│   └── workspace-recovery-2026-08/    # 清单、哈希、receipt 和必要压缩包
└── 计算化学案例构造与评测_031-034增补版.docx
```

`dpj_sif_pipeline.py` 若仍被当前流程引用，则迁入 `dftworld2/scripts/` 并补充入口说明；若无引用且其能力已由仓库脚本覆盖，则在验证后删除。空的 `worktrees/` 不保留。

## 内容分类规则

### 必须保留

- 主仓库所有 Git tracked 内容和全部未提交修改。
- 042 当前案例构造、验证和实验合同。
- `infra/runs/skill-ablation-v2.yaml` 及其直接引用的 schema、脚本和说明。
- `docs/experiments/pilot-known-limitations.yaml`。
- HPC 清理的最终报告、删除回执、迁移清单、校验哈希和恢复说明。
- qualification worktree 内尚未并入主仓库的 HPC dispatcher 资格验证证据、`cp2k-runtime.lock.json`、`deepmd-jax-runtime.lock.json`、`run_qualify.sh` 和 `supervise_qualify.sh`。
- 根目录 `skills/`、`tests/` 及其可验证的独立职责。

### 迁移后删除来源

- `dftworld2-qualification/` 中与主仓库 tracked 内容完全相同的文件。
- qualification 独有且属于当前工作的内容：先迁入主仓库的 `evidence/`、`reference/`、`scripts/` 或 `docs/operations/`，复验成功后移除整个 worktree。
- 根目录零散恢复材料：归并到 `archives/workspace-recovery-2026-08/`，生成统一索引和 SHA-256 清单后删除旧位置。
- 根目录脚本：确认职责后迁入主仓库或删除被替代版本。

### 可直接重建并删除

- `.pytest_cache/`、`__pycache__/`、`*.pyc`。
- `dftworld2/tmp/` 中的构建 rootfs、中断下载临时文件和临时校验副本。
- 空目录，包括空 `worktrees/`、空 `jobs/` 和空测试工作区。
- `.venv/`，前提是 `uv sync --frozen` 能依据 `pyproject.toml` 与 `uv.lock` 重建环境。

### 必须审计后决定

- `hpc-cleanup-20260822-archive/` 与 `local-cleanup-20260823/`：按归档内 manifest、receipt、哈希和 Git 可恢复性逐项判断，保留能够恢复尚未进入 Git 的唯一内容所需的最小集合。
- `dftworld2/ai2kit/`：确认它是源码、外部 checkout、构建产物还是运行快照；若为依赖副本，改为明确版本引用后删除。
- 大型二进制、tar 包和远端回读副本：只有在其内容已由 Git、对象存储或另一份校验一致归档覆盖时删除。
- 疑似死代码：必须经过静态引用检查、CLI/配置入口检查、测试覆盖确认和全量回归，不能仅凭未被 Python import 判断。

## Qualification worktree 收敛流程

1. 记录两个 worktree 的 HEAD、Git 状态和未跟踪文件清单。
2. 为 qualification 独有文件生成 SHA-256 清单，并按“证据、runtime lock、脚本、临时产物”分类。
3. 将证据迁入 `dftworld2/evidence/hpc-dispatcher/qualification/site-v1/`，保留审计日志、Slurm 脚本、必要 stdout/stderr 和一次运行说明。
4. 将 runtime lock 迁入 `dftworld2/reference/runtime/`，在 README 中说明生成来源、适用站点和消费方。
5. 将可重复运行脚本迁入 `dftworld2/scripts/qualification/`，修正硬编码的 worktree 相对路径，并为脚本增加 shell 静态检查或最小 dry-run 测试。
6. 在主仓库运行相关 HPC dispatcher、配置解析和案例回归测试。
7. 比对来源与目标哈希，确认 qualification 中没有未迁移的唯一文件。
8. 使用 Git worktree 管理命令移除 `dftworld2-qualification/`，随后执行 `git worktree prune`；不以普通递归删除代替 worktree 清理。

## 归档收敛流程

归档保留“恢复能力”，不保留任意多份历史副本。统一归档必须包含：

- `INDEX.md`：说明每个归档包恢复什么、来源路径、创建时间、是否仍需保留。
- `SHA256SUMS`：覆盖所有压缩包、bundle、patch、receipt 和 manifest。
- `RECOVERY.md`：给出 Git bundle、patch 和数据包的验证与恢复命令。
- 一份当前主仓库脏状态的可恢复表示，但不得包含 `.env`、API key 或其他秘密。
- 唯一不可从 Git 或公开来源重建的数据包。

若两个归档条目内容相同，保留命名清晰、校验完整、恢复说明更充分的一份。若大型包仅对应已经验证完成且不再需要恢复的远端垃圾，则保留删除回执和哈希，删除包本体。

## 代码与可读性整理

- 为根目录和主仓库分别提供简短 README：根目录说明各顶层目录职责；主仓库 README 保持使用说明，并链接 operations 文档。
- 在 `docs/operations/workspace-maintenance.md` 记录缓存清理、worktree 生命周期、证据归档和禁止直接删除的目录。
- 更新 `.gitignore`，覆盖已确认的缓存、临时构建目录、资格验证本地产物和运行工作区，同时不屏蔽应提交的 receipt、manifest、runtime lock 与实验配置。
- 对重复脚本先比较入口参数、输出合同和调用方；相同功能合并为单一实现，兼容入口仅在存在真实调用方时保留。
- 对超过单一职责的维护脚本按“清单生成、验证、迁移、报告”拆分，但不改动案例科学逻辑。
- 删除代码前使用 `rg` 检查 Python import、shell 调用、YAML/TOML 路径、CI/文档命令及动态入口，并运行对应测试。

## 验证与回滚

整理分为独立、可审查的提交，每个提交只处理一种职责：文档与忽略规则、qualification 资产迁移、缓存/临时产物清理、归档收敛、死代码清理。每阶段执行：

1. 保存变更前 `git status --short`、文件清单、磁盘占用和 SHA-256 清单。
2. 运行受影响模块的定向测试。
3. 运行仓库现有全量测试或项目定义的等价验证命令。
4. 检查文档、配置和脚本中是否仍引用旧路径。
5. 核对所有原有未提交修改仍存在且内容未被覆盖。
6. 记录删除路径、删除依据、释放空间和恢复方式。

源码和小型证据依靠 Git 提交回滚；未纳入 Git 的唯一数据在删除前必须进入统一恢复归档。任何哈希不一致、来源不明、测试失败或旧路径仍被引用的项目都停止删除，并留在审计清单中。

## 完成标准

- `dftworld2/` 成为唯一活跃 Git 工作区，qualification worktree 已安全收敛并从 Git worktree 列表移除。
- 当前 042、skill-ablation-v2、HPC 清理与 dispatcher 资格验证工作无数据或上下文丢失。
- 根目录每个保留项都有明确职责，不存在空目录、缓存、构建 rootfs 或无说明的大型临时文件。
- 恢复归档只有一套权威索引和哈希清单，重复包已删除。
- README、operations 文档和 `.gitignore` 能指导后续维护。
- 所有旧路径引用已处理，定向测试与全量回归通过。
- 最终报告列出保留、迁移、删除、待审计项以及实际释放的磁盘空间。

## 非目标

- 不执行远端 HPC 文件清理或删除远端作业。
- 不重写 Git 历史，不强制同步当前 ahead/behind 的 `main`。
- 不改变正式 benchmark 的科学阈值、评分协议或案例身份。
- 不删除 `.env`，但会确保它未被归档或提交，并在最终敏感信息检查中验证。
