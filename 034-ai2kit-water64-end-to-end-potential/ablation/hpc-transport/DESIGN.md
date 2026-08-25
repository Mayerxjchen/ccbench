# 034 HPC Transport — design

Status: **TRANSPORT IMPLEMENTED; reference moves to HPC.** The two backends in
`scripts/ablation/transport/slurm_transport.py` are implemented (PseudoSlurm e2e
tested against a live container; SshSlurmTransport code-complete, awaiting a
cluster to test). User decision 2026-08-11: the **reference calibration runs on
real HPC** (the Mac Docker VM at 7.75GB/10CPU thrash-swaps at ~19.5 min/GEO_OPT
step and cannot carry AIMD). `scripts/ablation/hpc/g9_reference_submit.sh` +
`g9_reference_fetch.sh` drive the modules-mode reference run + sync-back; Mac
pseudo-slurm remains the
control-layer surrogate (P7/P8/P10 transport parity), not the science anchor.

## 1. Problem

Case 034 today runs the **whole stack inside one container** with
`execution_backend = "container_simulated_slurm"`: the PAgent agent, the
pseudo-slurm scheduler, and the science (CP2K / `dp train` / LAMMPS / ai2-kit) all
live in `dftworld-base-ai2kit:0.1.0-cpu-cancelfix`, and the agent calls
`sbatch`/`squeue`/`sacct`/`scancel` directly on PATH.

The target HPC deployment splits the agent from the science:

```
agent control layer (host / control container)
   └─ SSH → HPC login node
         └─ sbatch → real Slurm → compute nodes (CP2K / dp / lmp / ai2-kit)
```

The Mac test-bed is the **training surrogate**: the SAME agent behavior must be
producible by pseudo-slurm inside the case container. Therefore the agent's
Slurm-facing surface is abstracted behind a **transport interface** so the two
backends (pseudo-slurm-in-container ↔ SSH-to-real-Slurm) are swappable without
changing the agent's decision loop, the verifier, or the reward path.

## 2. Principles

1. **The agent's decision loop is transport-agnostic.** It only ever calls
   `submit` / `status` / `log` / `cancel` / workspace-sync. It never runs a
   raw scheduler binary itself.
2. **The Slurm vocabulary is the contract.** The five canonical states
   `PENDING / RUNNING / COMPLETED / FAILED / CANCELLED` are what the agent
   reasons about; each backend maps its own raw output onto them.
3. **The workspace is the shared artifact.** `final/`, provenance, models and
   training data must end up on the **control-layer** `/app`, because that is the
   tree the hidden verifier grades. On the Mac the bind-mount gives this for free;
   on HPC a shared filesystem or a per-stage sync-back must guarantee it.
4. **Only the transport + Slurm backend + workspace binding swap.** The verifier
   (L1–L9), the reward path (`/logs/verifier/reward.txt`), the hidden set, the
   output contract and the lock's grading layer are anchored and never change.
5. **The scoring-compatibility contract is pinned.** The reference calibration
   and the verifier's scoring surface run the SAME package versions —
   `deepmd-kit==2.2.11`, `ai2-kit==1.1.0`, `oh-my-batch==0.7.6`
   (`solution/expert/env-hpc.sh`, recorded verbatim in the provenance manifest).
   A reference run only counts as an anchor after its final committee passes the
   model-compatibility smoke (`ws/validation/dp-test/dp-test.done` present in
   the graded workspace) — a model that cannot be loaded/inferenced by the pinned
   scoring stack must never anchor thresholds (the launcher gates sync-back
   scoring on that marker; a missing marker is a failed reference, not a scored
   one). The hidden set itself is generated from the expert AIMD mother set and
   needs no HPC run.

## 3. Transport interface

Lives at `scripts/ablation/transport/slurm_transport.py` (python3.11, stdlib
only). Abstract base `SlurmTransport`, two impls
`PseudoSlurmTransport` and `SshSlurmTransport`.

### 3.1 `submit(script, opts=None) -> job_id`

- `script`: path (control-layer, workspace-relative or absolute) to a batch
  script. May carry `#SBATCH` headers; they are honored by both backends.
- `opts`: resource/booking options that override or supplement the headers.
  Encoded as a `SubmitOpts` dataclass (`job_name`, `ntasks`, `cpus_per_task`,
  `nodes`, `partition`, `gres`, `time`, `output`, `error`, `chdir`, `account`,
  `qos`, `dependency`, `parsable`, plus `extra` raw flags).
- **Returns** the scheduler job id as a string. It is the handle for every other
  op. Ids are opaque to the agent — no arithmetic on them.
- **Blocks until the scheduler has ACCEPTED the job** (an id is returned). It
  does NOT block until the job runs.
- Raises `TransportError` on rejection (script missing, quota, auth, scheduler
  unreachable).

### 3.2 `status(job_id) -> JobState`

- Returns the **canonical** state, one of `PENDING / RUNNING / COMPLETED /
  FAILED / CANCELLED / UNKNOWN`.
- `UNKNOWN` = scheduler has no record (unknown id) or the query itself failed. It
  is **not** terminal; callers retry.
- **Authoritative source = `sacct`** (full state names + exit codes). `squeue`
  is a fallback only for non-terminal jobs. This mirrors the
  `wait_for_job.sh` rule: a failed/empty query is "unknown, keep waiting", never
  "done".

### 3.3 `log(job_id, tail=None) -> str`

- Returns the job's merged stdout/stderr (the `--output`/`--error` files,
  `%j`-substituted), optionally tail-limited.
- Best-effort: if the file does not exist yet, return `""` — never raise on a
  missing log. `tail` is passed to the reader on the side where the file lives
  (real `tail -n` on HPC; in-memory slicing for pseudo-slurm).

### 3.4 `cancel(job_id)`

- Requests cancellation. Idempotent. After a successful cancel, `status` must
  converge to `CANCELLED`.

### 3.5 Workspace sync: `stage(...)`, `fetch(...)`, `sync_workspace(...)`

The control layer writes scripts/inputs and later reads outputs; the compute
side must see the inputs and the control layer must receive the outputs.

- `stage(local_paths, remote_dir)` — make inputs visible to the compute side.
  Pseudo-slurm: **no-op** (the `/app` bind-mount already exposes the control
  workspace inside the container). SSH: `rsync`/`scp` up to the HPC shared
  filesystem.
- `fetch(remote_paths, local_dir)` — pull job outputs back into the control-layer
  workspace (the tree the verifier grades). Pseudo-slurm: **no-op**. SSH:
  `rsync -a -c` down (content checksum, not mtime/size — same rule as
  `collect_outputs.py`), optional SHA-256 verification.
- `sync_workspace(direction, workspace)` — whole-stage convenience wrapping the
  two above.

**Sync cadence (HPC, non-shared-filesystem case):** `fetch` after every
COMPLETED output-producing stage, and always a full workspace fetch before the
agent writes `final/` and before `verify()`. A missed fetch = verifier sees an
empty workspace = reward 0 for the wrong reason (runtime break, not science).

### 3.6 Convenience: `wait(job_id, poll=30, timeout=None) -> JobState`

Polling loop over `status()` with the `wait_for_job.sh` semantics: a transient
query failure is "unknown, keep waiting"; only a terminal state returned by
`sacct` exits. Raises `TimeoutError` on deadline.

### 3.7 Job-state mapping

| Canonical (transport) | sacct full names (pseudo + real) | squeue letter | terminal? |
|---|---|---|---|
| `PENDING` | `PENDING`, `CONFIGURING`, `SUSPENDED`, `REQUEUED`, `RESIZING` | `PD`, `CF`, `SE`, `RD` | no |
| `RUNNING` | `RUNNING`, `COMPLETING` | `R`, `CG` | no |
| `COMPLETED` | `COMPLETED` | `CD` | yes |
| `FAILED` | `FAILED`, `TIMEOUT`, `NODE_FAIL`, `OUT_OF_MEMORY`, `BOOT_FAIL`, `DEADLINE`, `REVOKED`, `PREEMPTED` | `F`, `TO`, `NF`, `OOM`, `BF`, `DL`, `RV` | yes |
| `CANCELLED` | `CANCELLED` | `CA` | yes |
| `UNKNOWN` | (no row / query error) | (none) | no |

Design decisions baked into the table:

- `COMPLETING` (`CG`) stays **RUNNING** (the job is still active).
- `PREEMPTED` (`RV`) maps to **FAILED**, matching oh-my-batch 0.7.6 (which folds
  `RV→FAILED`).
- **`CANCELLED` is kept distinct from `FAILED`**, even though omb's squeue
  fallback folds `CA→FAILED`. The transport's canonical vocabulary must let the
  agent's cancel path observe a real cancellation. `sacct` already reports
  `CANCELLED` distinctly; only the squeue-letter fallback needs the extra mapping.

## 4. Backend 1 — `PseudoSlurmTransport` (Mac test-bed)

Executes the scheduler binaries via **`docker exec` into the case container** (or
runs them directly when the transport itself runs inside that container). The
pseudo-slurm implementation is `base-env-build/ai2kit/pseudo-slurm/`
(`sbatch`/`squeue`/`sacct`/`scancel`/`sinfo`, JSONL job DB, detached
`job_reaper.py`).

| op | what actually runs |
|---|---|
| `submit` | `docker exec <cid> bash -lc "sbatch --parsable [opts] <container-script>"`; parse the first integer of stdout. |
| `status` | `docker exec <cid> bash -lc "sacct -X -P --format=JobID,State -j <id>"`; fallback `squeue -h -o "%A %t"`. |
| `log` | Read the log file **directly from the host workspace** (the bind-mount is the same inode as the container's `/app`); fall back to `docker exec <cid> bash -lc "cat ..."`. |
| `cancel` | `docker exec <cid> bash -lc "scancel <id>"`. |
| `stage`/`fetch` | **no-op** — the `/app` bind-mount is the shared workspace. Paths only need control-workspace → container-`/app` translation (same inode). |

Key facts the impl relies on (all verified in the pseudo-slurm source):

- `sbatch` allocates monotonic integer ids, sets
  `SLURM_JOB_ID/SLURM_JOB_NAME/SLURM_SUBMIT_DIR/SLURM_NTASKS/
  SLURM_CPUS_PER_TASK/SLURM_JOB_NODELIST/SLURMD_NODENAME/
  SLURM_JOB_NUM_NODES/SLURM_JOB_PARTITION/PSEUDO_SLURM_JOB_ID`, and prints
  `Submitted batch job <ID>` (bare id with `--parsable`). `SLURMD_NODENAME` +
  `SLURM_JOB_NUM_NODES` are required by DeePMD's `dp train` (`slurm.py` KeyError
  history) — keep them present.
- `sacct -X -P --format=JobID,State -j <id>` prints CSV header `JobID|State` +
  one row per known job with the full state names above.
- `squeue -h -o "%A %t"` prints `JobID <letter>` for non-terminal jobs only.
- Logs land in the `#SBATCH --output`/`--error` file (relative to the script
  dir, `%j`-substituted), default `slurm-<jobid>.out`.
- `PSEUDO_SLURM_DIR` overrides the scheduler state dir; the impl must preserve it
  in the `docker exec` env so state is shared across the control layer's calls.

**Deployment shapes.**

- Shape A — control inside the container (today's single-container mode): the
  transport runs in the case container and execs the binaries directly
  (`exec_via=None`). No `docker exec`.
- Shape B — control on the host, scheduler isolated in the container (the
  faithful HPC surrogate, and the canonical Mac shape for this design): the
  transport does `docker exec <cid> bash -lc "sbatch ..."`. Workspace = the
  host's graded workspace (bind-mounted as `/app` inside the container).

## 5. Backend 2 — `SshSlurmTransport` (HPC)

Executes the scheduler binaries via **`ssh <login> "<cmd>"`** against a real
Slurm login node. Credentials are a `SshConfig` dataclass
(`host`/`user`/`key`/`port`/extra options) — no credential logic in the impl; the
actual auth comes from ssh-agent or the configured key, never echoed into
agent-visible files.

| op | what actually runs |
|---|---|
| `submit` | `ssh <login> "sbatch --parsable [opts] <remote-script>"` after the script has been staged to the remote shared filesystem. |
| `status` | `ssh <login> "sacct -X -P --format=JobID,State -j <id>"`; fallback `squeue -h -o "%A %t"`. |
| `log` | `ssh <login> "tail -n N <remote-log>"` (log lives on the shared filesystem). |
| `cancel` | `ssh <login> "scancel <id>"`. |
| `stage` | `rsync -a -c <local> <login>:<remote-workspace>/...` (only when the control workspace is not already on a cluster-shared filesystem). |
| `fetch` | `rsync -a -c <login>:<remote-path> <local-dir>/...` with optional SHA-256 check. |

**Workspace-sync strategy is configurable** (`sync="shared_fs"` vs
`sync="sync_back"`):

- `shared_fs` — the control workspace already lives on a filesystem the login /
  compute nodes also see (e.g. NFS/Lustre home mounted both sides). `stage`/`fetch`
  become path-mapping no-ops.
- `sync_back` — control layer stages inputs up per stage and pulls outputs down
  after COMPLETED. This is the contract that keeps the verifier-graded tree
  complete.

**Stateful-shell note.** One-shot `ssh host cmd` loses remote cwd/env between
calls. The impl's `_run_remote` is the single choke point; when the target
cluster provides a persistent remote runner (tmux-based, per the hpc-submit
skill), `_run_remote` should route through it instead of spawning fresh ssh per
poll. Design keeps that swap inside one method.

## 6. What is SHARED vs what SWAPS

| Layer | Mac (now) | HPC (port) | Swaps? |
|---|---|---|---|
| Agent decision loop (planning, stage orchestration, skill bundle) | same | same | **no** |
| Transport interface | `SlurmTransport` | `SlurmTransport` | no (interface) |
| Transport implementation | `PseudoSlurmTransport` | `SshSlurmTransport` | **yes** |
| Scheduler binaries | pseudo-slurm in case image | real Slurm on HPC login | **yes** |
| Workspace binding | `/app` bind-mount (control + jobs share inode) | HPC shared filesystem or per-stage rsync | **yes** |
| Env injection into jobs | pseudo-slurm sets a fixed `SLURM_*` subset | real Slurm provides the full set | yes (transparent) |
| HPC_ENVIRONMENT.md content | describes pseudo-slurm + container venv | describes the real cluster | **yes (re-freeze)** |
| Verifier (L1–L9) + hidden set + thresholds | frozen | frozen | **no** |
| Reward path (`/logs/verifier/reward.txt`), test staging (`/tests`) | unchanged | unchanged | **no** |
| Output contract (`/app/final/`, manifest, provenance) | unchanged | unchanged | **no** |
| `reference/`, `thresholds.json`, hidden files | anchored | anchored | **no** |
| Skill bundle (10 skills, incl. hpc-submit + rsess) | frozen hash | frozen hash | **no** |

The agent's **decision logic, verifier, reward path and lock grading layer are
SHARED**. Only the **transport backend, the Slurm backend and the workspace
binding** swap.

## 7. Porting steps + lock re-freeze

1. **Anchor on HPC (user decision 2026-08-11).** The reference calibration
   (`hpc-ref-01/02`, AIMD500 candidate — same profile as the Mac run-01) runs on
   a real cluster and anchors `reference/thresholds.json` + the hidden set. The
   Mac pseudo-slurm anchor was abandoned mid-GEO_OPT (~70/200 steps, crawling at
   ~19.5 min/step under a 7.75GB swap-thrashing VM); it stays as a checkpointed
   fallback only. **Thresholds anchor ONCE — to the HPC reference — and are not
   re-derived afterwards** (the compute-scale caveat below applies to the agent
   backends, not to re-running the reference).
2. **Build the transport** as a control-layer library. Wire the agent's
   Slurm-facing calls (the `hpc-submit` skill's submit/monitor/recover surface,
   and any direct `sbatch` calls) to `SlurmTransport.*`. Run on Mac in Shape B.
3. **Parity check on the Mac.** Re-run the P7/P8/P10 gates
   (sbatch→sacct→output round-trip; timeout/cancel race) *through the transport*
   and diff against raw pseudo-slurm calls. The transport must reproduce exactly
   the states, ids and log files raw calls produce.
4. **Author `SshSlurmTransport`** with `SshConfig`, `_run_remote`, state/log
   parsing, and the chosen sync strategy. Add a real-Slurm image tag variant.
5. **Port `public/HPC_ENVIRONMENT.md`** to describe the real cluster (scheduler
   commands, partitions, module/launch lines, workspace path). Re-freeze its hash.
6. **Re-probe timeouts** on the cluster (like the matclaw GPU probe) and adjust
   `task.toml` `[agent] timeout_sec` / `[verifier] timeout_sec`. Update
   `resource_policy` (`cpus`/`gpus`/`memory_mb`) to the cluster's declared
   resources. **Compute scale is NOT restored** — the benchmark grades model
   quality, not step count; thresholds stay anchored.
7. **Re-freeze the lock** via `make_common_lock.sh`. Fields that re-freeze:
   `hpc_environment.hpc_environment_sha256`, `hpc_environment.execution_backend`
   (`"container_simulated_slurm"` → e.g. `"real_remote_scheduler"`),
   `hpc_environment.image_tag` (→ real-Slurm variant),
   `resource_policy`, and the TIMEOUT fields in `task.toml` (surfacing through the
   case-tree `git_tree_sha`).
8. **Anchored, never touched:** `grading_layer` (hidden_validation,
   hidden_rdf_reference, hidden_thresholds, verifier, thresholds),
   `prompt_layer` instruction/`water64`/`system.json` hashes, `skill_bundle`
   hash, `benchmark_seeds`, the reward path and the output contract.
9. **Run the ablation on HPC:** No-Skill pilot first, then With-Skill. The run
   manifest must carry the re-frozen lock hashes. A reward 0 with a healthy
   transport (jobs actually ran, outputs fetched, workspace complete) is a
   science outcome, not a runtime failure; a broken transport (submit errors,
   missing outputs, `UNKNOWN`-forever states) is a runtime failure.

## 8. Risks

- **SSH flakiness.** A transient ssh/slurmctld failure must never be read as a
  terminal state. `status()` returns `UNKNOWN` on query failure; `wait()` keeps
  polling. This is the exact failure mode `wait_for_job.sh` was built to prevent.
- **Credential handling.** Keys/host key checking/ssh-agent must never leak into
  agent-visible files or the transcript. The impl carries only the `SshConfig`
  dataclass; auth is external. Prefer agent-forwarding or a dedicated key over
  passwords; fail closed (no fallback to insecure auth).
- **Log streaming latency.** On HPC every `log()` is an ssh round-trip; big
  `slurm-*.out` files are expensive. Always `tail -n`. For grading, the verifier
  reads **fetched** files, never live-ssh cats.
- **State polling cadence.** Polling too fast loads the login node and inflates
  cost; `sacct` also has propagation lag behind a job's real end. Default poll
  30s+ (the `wait_for_job.sh` default). Confirm terminal via `sacct`, never by a
  job's mere absence from `squeue`.
- **Workspace sync** is the largest correctness risk. A missed `fetch` makes the
  verifier grade an empty tree → reward 0 for the wrong reason. Sync-back must be
  deterministic: fetch after each COMPLETED output stage plus a full fetch before
  `final/` and before `verify()`; use `rsync -a -c`; never fetch a RUNNING job's
  partial files as final.
- **Job-state fidelity.** Real Slurm has a richer terminal vocabulary
  (`TIMEOUT`, `NODE_FAIL`, `OUT_OF_MEMORY`, `PREEMPTED`, ...) than pseudo-slurm.
  The transport normalizes them all; the agent must treat only `COMPLETED` as
  success and every other terminal as failure — never assume.
- **Slurm env-var parity.** `dp train` needs `SLURMD_NODENAME` +
  `SLURM_JOB_NUM_NODES` (historical KeyError). Real Slurm provides these
  natively; pseudo-slurm was fixed to match. The transport must not strip
  `#SBATCH` headers or the injected `SLURM_*` env.
- **`%j`/`%x` token substitution.** Pseudo-slurm substitutes a small token set;
  real Slurm substitutes more. Keep `--output`/`--error` `%j`-based so the
  transport's `resolve_log_path` is uniform across backends.
- **Compute scale.** HPC hardware differs from the 16-CPU Mac. Because
  thresholds are absolute and anchored to the reference, more compute can only
  overshoot quality — but never re-derive the thresholds on HPC or the
  comparison between the two backends breaks.
