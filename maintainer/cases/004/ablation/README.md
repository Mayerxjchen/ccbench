# Ablation environment — case 034

> [!WARNING]
> **ARCHIVED / OBSOLETE HISTORICAL RECORD**
> 本文档记录的是早期的 PAgent 消融实验环境方案（已归档/废弃）。当前基准系统已全面转向基于 Claude Code 的通用 Candidate 架构与正式的发布消融协议，此文件仅作为不可变的历史追溯记录保留，**严禁作为当前操作说明或评测执行依据**。

Status: **ARCHIVED / OBSOLETE (Historical PAgent ablation design, preserved for audit only)**

## Purpose

The ablation answers: *does the frozen portable-skill bundle help an agent build
a reliable DeePMD water potential for 034?* One ablation:

| Agent      | No-Skill | With portable skill | Answer |
|------------|:--------:|:-------------------:|--------|
| **PAgent** |  ×3      |         ×3          | primary experiment |

> Claude Code ×3 external baseline is **CANCELLED** (user decision 2026-08-11) —
> not deferred, cancelled. The ablation is PAgent-only.

The two conditions share EVERYTHING except the skill bundle (COMMON_ABLATION_LOCK);
only the agent runtime differs (Agent Lock). The Agent Lock still records the
PAgent runtime so future CC comparisons (if ever reopened) are clean.

## The experiment lock (must not drift)

`locks/common_ablation_lock.json` freezes: case tree, instruction/public hashes,
hidden validation/RDF/thresholds, verifier, skill bundle hash (10 skills), HPC
environment (pseudo-slurm), resource policy, image tag
(`dftworld-base-ai2kit:0.1.0-cpu-cancelfix`).

`locks/agent_lock.json` records the PAgent runtime: commit `d4a642f`, version
`0.7.12`, model `openai/deepseek/deepseek-v4-flash`.

Regenerate with `bash scripts/ablation/make_common_lock.sh`. A result is only
comparable if the run's manifest carries the same lock hashes.

## PAGENT_ABLATION_READY (P1-P17)

`scripts/ablation/pagent_ready.sh` audits the environment and writes
`evidence/pagent_ready.json`. Current board:

| Gate | Status | What closes it |
|------|:------:|----------------|
| P1 fresh workspace | PASS | seed_workspace → clean /app |
| P2 no leakage | PASS | sandbox exposes only /app |
| P3 No-Skill bytes absent | PASS | fresh sandbox has 0 SKILL.md |
| P4 With-Skill discoverable | PASS | snapshot copies 9 skill dirs |
| P5 Skill hash | PASS | bundle hash == frozen lock |
| P6 NS/WS tool policy identical | PASS | one tool policy; flag gates only use_skill |
| P7 job transport | PASS | sbatch reachable in sandbox |
| P8 Slurm workflow | PASS | sbatch→sacct→output round-trip |
| P9 continuation | PENDING | PAgent pilot (resume) |
| P10 timeout/cancel | PASS | scancel → sacct CANCELLED (race fixed) |
| P11 artifacts recoverable | PASS | /app write visible on host bind |
| P12 transcript completeness | PENDING | pilot thread audit |
| P13 token usage | PENDING | pilot usage record |
| P14 verifier condition-blind | PASS | verifier has no condition/skill refs |
| P15 NS/WS exact-diff | PASS | identical contract; per-run diff at pilot |
| P16 NS pilot to reward | PENDING | run_pagent_pilot.sh |
| P17 WS pilot to reward | PENDING | run_pagent_pilot.sh |

reward=0 on a pilot is NOT a gate failure (science performance). A broken
runtime (SSH/slurm, skill load, artifact loss, no continuation) IS.

## Infrastructure

- `scripts/ablation/make_common_lock.sh` — COMMON_ABLATION_LOCK + Agent Lock.
- `scripts/ablation/verify_lock.sh` — hard gate: recompute all lock hashes vs the
  live case; abort if ANY drift (runs before every formal trial).
- `scripts/ablation/pagent_ready.sh` — P1-P17 board (bash-3.2 compatible).
- `scripts/ablation/run_pagent_pilot.sh` — PAgent 1+1 pilot via `eval.py`
  (`--experiment ablation-pagent-pilot`, `--no-skills` / `--skills`).
- `scripts/ablation/run_pagent_formal.sh` — PAgent 3+3 formal (seeds 34001/2/3,
  lock-verified before each run, evidence under `evidence/formal/<stamp>/`).
- `scripts/ablation/summarize_ablation.py` — result matrix from `jobs/*/summary.json`
  (per-condition × seed: status/reward/calls/tokens/walltime/skills + aggregates).
- `scripts/ablation/find_reward.py` — resolve newest trial reward from summary.json
  (run dirs are timestamp-named; experiment lives inside the JSON).
- `scripts/ablation/canary.sh` — harness regression on a cheap case
  (default `009-cp2k-run`) before the expensive 034 pilot. **Ran 2026-08-11:
  both conditions reward=1.0 (no-skill 7c/22085tok, with-skill 7c/27730tok) —
  harness has NO regression.**
- PAgent harness is the repo `eval.py` (pagentv4 Runner): fresh workspace,
  NS/WS flag, skill snapshot, thread/tool-call logs, token accounting.
- Pseudo-slurm cancel-race fixed (durable `cancelled` flag) →
  `dftworld-base-ai2kit:0.1.0-cpu-cancelfix`; 034 `Dockerfile` `FROM` updated.
- HPC control-layer transport **implemented** →
  `ablation/hpc-transport/DESIGN.md` + `scripts/ablation/transport/slurm_transport.py`
  (abstract `SlurmTransport`, `PseudoSlurmTransport` e2e-tested, `SshSlurmTransport`
  code-complete, canonical 5-state vocabulary, sacct-authoritative/squeue-fallback,
  `sync_back` strategy). `make_transport()` factory picks the backend.
  `scripts/ablation/transport/test_transport.py` runs the pseudo-backend e2e
  (isolated `PSEUDO_SLURM_DIR`; submit→COMPLETED, log/tail, cancel→CANCELLED).
- **Reference calibration MOVED TO REAL HPC (user decision 2026-08-11)** — the
  Mac Docker VM (7.75GB/10CPU) thrash-swapped the reference GEO_OPT to
  ~19.5 min/step. `scripts/ablation/hpc/g9_reference_submit.sh` +
  `g9_reference_fetch.sh` drive the **dual reference** (`hpc-ref-01` /
  `hpc-ref-02`): both runs use the SAME AIMD500
  candidate profile (paper, AIMD_STEPS=500, TRAIN_STEPS=15000, MODEL_NUM=2,
  AL_ROUNDS=2, MD_TEMP="330 430", CP2K_NP=4) with IDENTICAL fixed resources
  (SLURM_NTASKS=8, CPUS_PER_TASK=2, 48:00:00); only the declared root seed
  differs (deterministic two-realization anchor, Task 5). Sync case →
  singularity-build the `dftworld-base-ai2kit` SIF from the exported image →
  submit ONE cpu-partition compute job per run (`singularity exec` runs the whole
  expert workflow) → poll real slurm → gate sync-back scoring on the
  model-compatibility smoke (`ws/validation/dp-test/dp-test.done`) → rsync the
  graded workspace back to `reference/runtime-evidence/hpc-<run>/`.
  HPC: `<site-user>@<site-host-ip>` (<site-login-node> login, real slurm, cpu partition 389 nodes
  × 64 cores / 256 GB, unlimited time, singularity/3.5.2 module).
  **Thresholds stay DRAFT until both hpc-ref runs complete and the expert model
  is scored on the hidden set** (`reference/thresholds.json` keeps `draft:true`).

## Sequencing (user-directed)

1. 034 CASE_VALID — dual reference calibration on HPC (hpc-ref-01 + hpc-ref-02,
   AIMD500 candidate, fixed resources, distinct declared seeds; thresholds stay
   DRAFT until both complete with the dp-test model-compat smoke). Must close
   before pilots.
2. PAgent 1+1 pilot — closes P9/P12/P13/P16/P17.
3. Freeze PAgent environment.
4. PAgent 3+3 formal — primary experiment (seeds 34001/2/3).

> CC 1+1 pilot was skipped ("直接 PAgent pilot") and CC ×3 external baseline is
> cancelled (2026-08-11). The ablation is PAgent-only.

## HPC target (user direction: agent = control layer only)

On the HPC port, the agent is ONLY the control layer — it orchestrates; the
science (CP2K AIMD, DeePMD train, LAMMPS, ai2-kit AL) runs on HPC compute nodes:

```
agent control layer (host / control container)
   └─ SSH → HPC login node
         └─ sbatch → real Slurm → compute nodes (CP2K/dp/lmp/ai2-kit)
```

- `/app` must be reachable by BOTH the control layer (hidden verifier) and the
  compute nodes (job outputs) — shared filesystem or sync-back per stage.
- The Mac test-bed's pseudo-slurm is the faithful 1:1 stand-in for the agent's
  Slurm-facing behavior (`sbatch`/`squeue`/`sacct`/`scancel` + states). Keep it
  Slurm-faithful — it is the training surrogate for the HPC behavior.
- On port, only the transport (pseudo-slurm-in-container → SSH to login node)
  and the Slurm backend swap. Locks re-freeze via `make_common_lock.sh`:
  HPC_ENVIRONMENT, RESOURCE_POLICY, TIMEOUT, image tag → real-Slurm variant.
- **Anchored (never change):** reference/ + thresholds/ + hidden set + verifier
  L1-L9 + reward.txt + instruction/public. Benchmark grades model quality, not
  step count; compute scale is not restored.

## Do not run while the reference oracle is active

The Mac has 16GB; 034 reference + 032/033 oracles already consume ~6GB+.
Heavy runs (pilot, canary, full verifier suite) must wait for a low-compute
window or reference completion (OOM risk — exit 137 seen before).
