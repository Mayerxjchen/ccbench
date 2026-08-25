# HPC 清理战役最终报告 — 2026-08-22

**结果：126.20 GB → 27.48 GB，释放 98.72 GB（目标 25–40G ✓）**
`squeue` 全程空载；零 manifest 破坏；全部删除有封存哈希或 relocation receipt。

## 验收门对照

| 门 | 结果 |
|---|---|
| squeue 无孤儿作业 | ✓ 0 |
| 无 001–042 源码目录 | ✓ 0 个 |
| 无 reference/solution/verifier hidden 资产 | ✓（hpc-ref-*/hidden-score/_solution/payload 全清；余下命中均为 repo 内容，属 Batch 7 范围）|
| 无旧 /jobs workspace | ✓ 整目录删（元数据 236 文件先封存+验证：7 run-record.json 解析全通过）|
| 无 smoke/probe/diag/bench 目录 | ✓（仅剩 evidence/case-factory-smoke=正式 evidence、benchmark/=repo 内容）|
| 保留 SIF 有 digest+qualification | ✓ cpu `f104256e…`(smoke job 3536012)、gpu `99aefeff…`(qualify job 3536969, formal_eligible)；arm64 已淘汰 |
| 保留 evidence 可重算 SHA | ✓ 当日 verify_evidence --restore always：3 案例 × 2 run 全部 valid=True restored=True failure=None |
| 历史 reader 可解析 | ✓ 同上（verify_evidence 实跑成功）|
| manifest/receipt 存 Trusted 本地 | ✓ 本目录 + ../hpc-cleanup-20260822-archive/ |
| 磁盘 25–40G | ✓ **27.48G** |
| Candidate 无法访问 HOME / Dispatcher root 空 | ◻ 属新 Dispatcher infra 属性，待 canary 验证（与 Batch 7 同门）|

## HPC 最终保留层

- `dftworld2-runs/matclaw-03{1,2,3}/`：evidence/ + store/*-{primary,replica} + formal-* 记录
  （被旧 v2 manifest 引用且 infra 尚无 relocation receipt → 按停止条件原地保留，21G）
- `dftworld2-runs/matclaw-031/runtime/`：locked CPU+GPU SIF + locked/qualify_gpu.py
- `.cluster-agents.md`、`site-configs/`（含 site-config-034-<site-login-node>.json）
- `.conda/envs/ai2kit`（3.2G）、当前 `.vscode-server`
- git 仓库工作树（scripts/benchmark/schemas 等）——Batch 7 legacy 清理待新 Dispatcher façade/canary/Pilot 通过后执行

## 迁移产物（本地 Trusted 存储）

`案例测试/hpc-cleanup-20260822-archive/`（Git 外）：

| 归档 | SHA-256 前缀 | 内容 | 验证 |
|---|---|---|---|
| batch1-hidden-assets.tar.zst (2.7G) | 6c6bfa05 | hpc-ref-01/02/ws + hpc-alt + matclaw-031/_solution + 032 探针脚本 | PASS 2441/2441 |
| batch1-old-refs.tar.zst (18M) | b550ce69 | 旧版 hpc-ref ×6 | PASS 350/350 |
| batch1-hidden-score.tar.zst | a162878d | 034/hidden-score（冻结阈值所在树） | PASS 26/26 |
| batch1-gate-assets.tar.zst | 275bcce8 | **FROZEN thresholds.json**（当时仅存 HPC！）+ verifier out + qualification assets/evidence + SiteProfile | PASS 79/79 |
| batch5-small-assets.tar.zst | ac68dcca | 034 eval 目录、032/assets、031 元数据小件等 | PASS |

⚠️ **跟进项**：FROZEN thresholds.json（cf875d25…）尚未提交回本地 git 的 034 案例树——本地 HEAD 仍是 draft 版。需要一次构造层提交把它落回 `034-ai2kit-water64-end-to-end-potential/tests/hidden/thresholds.json`。

## 关键决策记录

1. hpc-ref 三工作区（本地唯一副本）：用户拍板全量迁移（9 步协议），未裁剪。
2. 034 未 qualification SIF ×2（4.6G）：用户拍板删除；重建走正式 qualification。
3. GO–water interfaces（470M）：69/69 文件与本地逐字节一致 → 删远端副本。
4. verifier-staging 33G 中 32G 是 pytest-of-<site-user> 测试临时目录。
5. formal workspace 删除前用集群权威 verify_evidence --restore always 重验（经 ~/verify-tmp 符号链接重定位 ROOT，验毕即清）。

分批 receipt 见本目录各 JSON；逐批 quota 轨迹：126.20 → 123.75(B1) → 65.82(B2) → 61.12(B3) → 53.02(B4) → 48.60(B5a) → ~46.7(B5b) → 30.87(B8) → **27.48G(终)**。
