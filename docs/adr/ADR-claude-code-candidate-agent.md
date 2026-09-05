# ADR: Claude Code 作为唯一正式 Candidate Agent 与独立 Sandbox 架构

- **状态**：ACCEPTED / FROZEN
- **日期**：2026-09-05
- **决策者**：MLFFBench 架构团队
- **相关组件**：`dftworld_bench.agents`, `dftworld_bench.core.harness`, `bench-hpc`, `RunLock`, `agent-profiles.toml`

---

## 1. 背景 (Context)

在早期的 MLFFBench 架构中，Candidate Agent 与 `pagent`（包括 `pagentv4` 运行时、专用 runner 及本地环境）深度绑定。该架构存在以下根本性痛点：
1. **职责耦合与安全性风险**：Agent 运行时逻辑与底层执行容器高度耦合，容易发生宿主环境变量泄漏或权限越界；
2. **科学镜像膨胀与凭据污染**：若在科学计算镜像（CP2K、DeepMD、MatClaw、JAX）中直接部署 Agent，会导致模型 API 凭据、Node/CLI 依赖污染云端生产实例；
3. **不可重现的隐式升级**：依赖无版本锁的动态包安装破坏了可复现性门禁基线；
4. **统计可比性混乱**：旧的实验（如 034 ablation）基于 PAgent 的行为和 token 统计协议，若与新模型/新代理引擎混合统计将破坏科学基准的严谨性。

---

## 2. 决策 (Decision)

1. **唯一正式 Agent 引擎**：
   - 全面确立 **`claude-code`** 为 MLFFBench 新一代基准评测的**唯一正式 Candidate Agent**；
   - `pagent` 正式退役，降级为 `legacy/read-only` 状态；生产依赖完全移除 `pagent`。

2. **镜像职责严格分离 (Strict Physical Separation)**：
   - **Candidate Agent 镜像 (`mlffbench-agent-claude-code:v1`)**：只负责推理、读写工作区 `/app`、发现本地项目级 Skills 并向 Gateway 发送抽象计算描述符；
   - **科学计算镜像 (CompShare GPU 镜像 / IKKEM CP2K SIF)**：只负责执行具体的物理与化学计算，严禁安装 Claude Code、Node 运行时或注入模型凭据。

3. **计算出口唯一性与权限最小化**：
   - Candidate Agent 严禁直接访问宿主 `~/.ssh`、`~/.claude`、CompShare API Key/CLI 配置、维护者签名私钥、真实 SiteProfile、标答与隐藏 Verifier 测试；
   - Candidate 严禁挂载 Docker socket，严禁直接执行 `ssh`, `sbatch`, `squeue`, `compshare` 等特权指令；
   - **`bench-hpc`** 为 Candidate 唯一合法计算出口，所有对 Slurm/CompShare 的调度均由宿主侧 Trusted Gateway 接管。

4. **历史数据保护与隔离原则**：
   - 历史 PAgent 评测数据（包括 034 ablation 等）完整保留为历史证据，绝不机械重写，统一标注：
     ```text
     engine=pagent
     legacy_protocol=true
     not_comparable_to_claude_code_v1
     ```
   - 新旧 Agent 的评测数据严禁在同一正式榜单或对比图中混合聚合。

---

## 3. 后果与约束 (Consequences)

- **正面收益**：
  - 云端 GPU 镜像保持极其纯净、精简与安全，计算资源与推理资源解耦；
  - Claude Code 版本、Node 版本及 Skill bundle 全哈希锁定，确保 100% 可复现；
  - 结构化事件流提供透明、真实的 Token、Walltime 与工具调用审计，拒绝虚假造零。
- **技术约束**：
  - Harness 必须基于抽象 `AgentAdapter` 协议与 Candidate 容器交互；
  - 容器执行由 Harness 统一施行 walltime 监控与 `TERM → grace → KILL` 级联清理，杜绝孤儿进程。

---

## 4. 验收标准 (Acceptance Criteria)

- [x] ADR 明确 Claude Code 为唯一新执行引擎，PAgent 标记为 legacy；
- [x] 确立科学镜像与 Agent 镜像的物理分离边界；
- [x] 确立模型凭据与云端算力凭据的隔离原则；
- [x] 历史实验数据语义得到保护，不被篡改；
- [x] 新 RunLock 与配置体系强制遵循本决策。
