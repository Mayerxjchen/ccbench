# HPC 清理战役 2026-08-22

目标：按清理清单把 <site-alias>:/public/home/<site-user> 从 ~126GB 降到 25–40G。
HPC 持久层最终只保留：私有 SiteProfile/SSH/Slurm 配置、`.cluster-agents.md`、
digest-pinned 合格 SIF、当前 HpcDispatcher 活动 run、待迁移的历史 evidence 字节。

## 硬规则（每批执行前重读）

1. `squeue -u <site-user>` 为空才动手（2026-08-22 13:48 首次确认 ✓，每批复查）。
2. 不执行广域 `rm -rf ~/0??-*`；一切删除走 manifest → 校验 → 批内执行。
3. 删除对象先进 manifest：绝对路径、大小、类型、理由。
4. hidden/reference/verifier 先迁回 Trusted 本地（本目录），逐文件 SHA-256 一致
   + read-back + hidden leak audit + relocation receipt，然后才删远端副本。
5. 旧 immutable manifest 不修改。evidence 迁址只通过 relocation receipt/locator
   overlay 表达；尚未支持 relocation receipt 的 artifact **暂缓删除**。
6. 删 runtime 前查 release/runtime lock；删 run 前做 settlement 确认无遗留 job。
7. 分批执行，每批后重新统计磁盘。

## 目录

- `batch0-seal/` —— 清理前封存的唯一数据（远端 git 状态、invalid-runs.json、
  /jobs 元数据、evidence manifests、SIF 清单、磁盘统计）。
- `manifest/` —— 清理 manifest 与每批删除 receipt。
- `relocation-receipts/` —— Batch 1 hidden 资产迁址回执。
- `hidden-assets/` —— 迁回的 hidden/reference/solution/verifier 字节。

## 战役日志

- **2026-08-22 Batch 0 完成**：全部封存落 `batch0-seal/`。远端 git HEAD=c071344(main)、460 条
  dirty/untracked；5 个 modified 文件中 4 个与本地逐字节一致，invalid-runs.json 不同已导出。
  jobs/ 21,603 文件 8.96GB，元数据包 2.6MB（>16MB 者全是模型 ckpt 字节）。evidence manifest 包
  369KB。SIF×4 已登记（cpu/gpu matclaw 与 lock 一致；034 cpu/gpu 待 qualification 判定）。
- **发现**：远端 `~/.worktrees/*` 非 git worktree（无链接），是陈旧源码副本 ~1.4G；远端仓库
  worktree 注册表有 5 个 prunable 死条目（指本机路径），Batch 8 用 `git worktree prune` 清。
  `matclaw-033/solution` 8/8 与本地一致（=冻结 1b61084 字节）；`matclaw-032/solution` 仅多
  `032-pilot-convergence-probe.py`；`matclaw-031/_solution` 是正式 run staging，15 个本地无文件，
  整体迁移。hpc-ref-01-20260818c / hpc-ref-02-20260818b / hpc-alt-20260819a 各 ~1.2G ws/ 为
  本地唯一副本——用户拍板全量迁移（9 步协议，见 batch0-seal/phase-c2-migrate.sh）。

## 已知事实（侦察 2026-08-22）

- 配额用量 126,198,504 KB ≈ 126.2 GB；squeue 空。
- 案例源码副本：42 个目录共 231MB。
- `034/` 62G、`jobs/` 8.4G。
- Runtime locks（本地 evidence/matclaw/formal/）：
  - CPU SIF `matclaw-cips-2.2.11-cpu-amd64.sif`
    sha256 `f104256e5b41f9cdc0304fe5d5ab4184c57b507daba564cfd3328f8c60b96d29`
    @ dftworld2-runs/matclaw-031/runtime/
  - GPU SIF `matclaw-cips-2.2.11-gpu-amd64.sif`
    sha256 `99aefeff8f457cd6b4f57e1592db511f167ab493730f970bfd7baa68993250c3`
    （formal_eligible=true, qualify job 3536969）
  - arm64 SIF `matclaw-cips-2.2.11-cpu.sif` sha256 `4ed34b2d…` 已被取代，
    架构不匹配，锁内已有记录 → Batch 6 删除候选。
- v2 infra 尚无 relocation receipt 实现（scripts/evidence/ 无 relocat* 匹配）
  → 被旧 manifest 引用的 store/*-primary、*-replica、bundle.tar.zst、locked SIF
  本轮原地保留。
- 031/032/033 各 2 run 已封双 CAS 且 audit complete（docs/evidence/
  migration-report-031-033.md）；raw workspace 未删，等待 fresh restore 验证
  + 用户批准后才删。

## 停止条件

**只要旧 manifest 仍直接引用某个 HPC 字节路径，就不能删除该字节，
除非 relocation receipt 和 fresh restore 已经通过。**
