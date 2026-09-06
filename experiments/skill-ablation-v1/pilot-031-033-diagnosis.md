# Pilot 031/032/033 — 全部 6 臂 INFRA_INVALID 诊断（2026-08-19）

## 结论

6 臂 pilot（031/032/033 × no-skill/with-skill）全部 INFRA_INVALID/VERIFIER_FAILURE。
**不是 agent 失败**。根因：harness verifier 相对路径 volume bug —— verifier 容器从未启动。

## 三层缺陷

### #1 verifier 相对路径 volume（已修 `ccbench/core/verifier.py`）

`build_verifier_command` 用 `--volume jobs/...`（相对路径）挂载。
**docker CLI 的 `--volume` 不解析相对路径** —— 把它当命名卷名，报
`invalid characters for a local volume name`（RC=125），容器从不启动
→ 无 result.json → `_parse_verifier_output` 归 VERIFIER_FAILURE。

修复：`_host_abs(path) = str(path.expanduser().resolve())` 全部挂载路径绝对化。
手动复现验证：verifier 现在真跑容器、写 result.json + reward.txt。

### #2 verifier 容器 python 权限（待修，镜像层）

`matclaw-cips` Dockerfile：`uv venv --python 3.11.15 /opt/matclaw`。
uv venv 的 python symlink → `/root/.local/share/uv/python/...`。
verifier 容器 `--user 65532` + `--read-only` → 读不了 `/root` → Permission denied
→ test.sh fallback `python3`（3.12 无 numpy）→ pytest 收集全 ERROR。

即使 #1 修好、agent 产出完美，verifier 也会因 python 不可用误判 SCIENTIFIC_FAIL。
修法：镜像层把 uv venv 改成可访问 python（非 /root），需重建镜像 + 重新冻结。

### #3 validate_pilot_pair TERMINAL_PHASES（已修 `scripts/ablation/validate_pilot_pair.py`）

harness 终止相名是 `INVALID_INFRA` / `FAILED_AGENT`（`_TERMINAL_PHASE`），
validate 清单里只有 `INFRA_INVALID` / `AGENT_FAILURE`。修 #1 前失败臂
记录停在 INVALID_INFRA，被 validate 判"lifecycle 未闭合"。

## Agent 行为（诊断）

6 臂 agent 全在容器本地建 MD pipeline（run_command 30-45 calls，写
make_structures/run_md/build_dataset，跑 /opt/matclaw/bin/python + lmp），
**零 controller submit**，hit 32-turn 上限前未产出 result.json。

- SYSTEM prompt（`agents.py:52-56`）说"任务数据已在工作目录中"，
  instruction.md（031:15 行）无 controller/submit 指引。
- formal 证据（`evidence/matclaw/formal/031/run-1`）有完整 workspace 但
  无 slurm 产物；HPC `formal-2026081201/` 只有 workspace/ —— formal agent
  也是本地容器跑的，未提交 HPC。

→ 结论：agent 预期就是容器内本地求解（controller 携带完整 MatClaw CPU/GPU
runtime），不用走 HPC gateway。pilot 失败主因是 **32-turn 预算不足**（agent
在正确地建 pipeline，只是跑不完 active-distillation 全流程）。

## 下一步

1. #2 verifier python 权限：镜像重建（改 uv venv 路径或拷贝解释器）或
   确认是否 blocking（agent 反正 32 turns 跑不完）。
2. 重跑 6 臂前需决定 max_turns（提 64/128?）——否则即使 verifier 修好，
   agent 仍产出不了 result.json，validated 的仍是"agent 未完成"。
3. 若 agent 应走 HPC gateway（而非本地求解）：instruction.md + SYSTEM prompt
   需加 controller/submit 指引。
