# bench hpc resume (design spec)

**Status:** IMPLEMENTED — landed 2026-09-02 (`infra: bench hpc resume CLI
consolidation`).  Implementation notes: `--phase resume` on the trusted
driver; `_rehydrate_ownership` fills only absent adapter state
(`setdefault` — a live scheduler record is never clobbered); stamp/run ids
are auto-detected or operator-passed — no baked incident constants;
`CODE_IDENTITY_PATHS` drops the deleted file; the ported + new offline
tests (19) and the retargeted operator-scripts ordering test pass; 1307
passed with the same 5 pre-existing `case_factory`/`matclaw` failures.
**Touches:** `scripts/infra/qualify_hpc_dispatcher.py`,
`dftworld_bench/experiments/qualification_receipt.py` (delete one
`CODE_IDENTITY_PATHS` entry only), delete `recover_gpu_canary.py`,
`README.md` (two references), tests.
**Frozen base:** `1008c11` (after the capability-matrix refactor `840781e`
on `010b1d2`).

## 1. Why

The 2026-08-25 incident showed the one failure mode the canary phase cannot
self-heal: the supervisor process died between the durable GPU ``SUBMIT_INTENT
`` / ``SUBMIT_ACCEPTED`` and collection/settlement.  The scheduler job kept
queueing independently; nobody was polling it; the sentinels were never
cleaned.  `recover_gpu_canary.py` fixed *that incident* well, but it is a
root-level one-off:

- the run ids, stamp, `SINCE` window and scheduler-id hint are module
  constants baked to the incident;
- it must live at the repo root ("以所在目录为仓库根解析 sys.path") because it
  imports the driver by path;
- its bytes are *not* part of the ordinary trusted-driver anchor story — it
  is pinned into `CODE_IDENTITY_PATHS` as a separate root file, so any edit
  to it (or a second incident with a different shape) silently re-anchors
  every future receipt.

The durable `_submit_v2` marker-adoption machinery in
`dftworld_bench/hpc/gateway.py` already exists and is tested; the recovery
driver only needs to be a **genericized, CLI-reachable phase** of the one
trusted operator script, driven by durable audit facts instead of baked
constants.

**Goal:** `python scripts/infra/qualify_hpc_dispatcher.py --phase resume`
completes an interrupted dual-canary collection — no resubmission, no new
scheduler job, sentinels cleaned last — and reuses the exact evidence shape
the canary phase produces, so the sealed receipt verifies identically.

## 2. Invariants (what does NOT change)

- **No submission, ever.**  Resume adopts exactly one existing scheduler job
  per chain and refuses when zero or several candidates match.  There is no
  "blind second submit" path — not in `_submit_v2`, not here.
- **The gateway API stays frozen.**  Settlement must land as real protocol
  events (`SETTLEMENT_BEGIN`, token revoke) on the *same* hash-chained audit
  file the dead process opened.  We rehydrate the trusted-side ownership
  state from durable facts (audit marker + scheduler job id + workdir) and
  then call the stock `session.status()` / `session.settle()` — never a new
  network write protocol.  `gateway.py` / `dispatcher.py` / `slurm.py`
  bytes do NOT change.
- **Evidence shape is identical to canary.**  The receipt schema pins
  `evidence.phase = "canary"` (`const` in
  `dispatcher-qualification-receipt.schema.json`); resume emits the same
  envelope — both records in `evidence.jobs`, `phase: "canary"`, the same
  `cp2k_gate` NOT_RUN stub — so `build_receipt` + `verify` need zero changes.
- **Ordering guarantee preserved.**  settle → close session → sentinel
  cleanup, in that fixed order, so absence probes can never false-pass
  against a still-running job.
- **Duplicate-protocol guard.**  A second resume instance must never append
  duplicate events to the same audit chain: single-instance flock, fail
  closed.
- **Verdict-free receipt / all offline anchors**: untouched (this spec
  changes no verification logic).

## 3. What the resume driver does

Discovery is by **durable audit facts**, not incident constants:

1. **Identify the chain.**  `--stamp` (the 4-hex chain stamp) names the
   interrupted GPU run `run-gpu-nvidia-probe-containment-{stamp}` (the CPU
   sibling `run-cpu-echo-probe-containment-{stamp}` is derived from it).
   When `--stamp` is omitted, auto-detect: the unique `run-gpu-*` directory
   under `CANARY_ROOT` whose `audit.jsonl` carries a `SUBMIT_INTENT` and
   whose collection was never settled.  Fail closed on zero or on more than
   one candidate (never guess which chain to repair).
2. **Refuse when a receipt already exists.**  A sealed `receipt.json`
   means the canary finished; resume has nothing to complete.  Refuse
   rather than overwrite (later phases merge via `--phase cp2k`).
3. **Locate the GPU scheduler job.**  `--gpu-job-id` (the operator's
   squeue/sacct observation at crash time) is the identity anchor, verified
   by `WorkDir` containing the run id.  Stale hint → resolve by
   `squeue`+`WorkDir`, then by exactly-one `sacct` `WorkDir` match since
   `--since`.  Zero or several → `QualifyError` (fail closed, nothing
   adopted).
4. **Wait for a scheduler-terminal state** (long-queue PENDING → RUNNING →
   COMPLETED lifecycle; polls `squeue`, reads `sacct`; no session held, so
   the 36 h token lease does not bound the wait; 48 h ceiling).
5. **Rehydrate + settle the GPU chain.**  Open a fresh session on the SAME
   run id (same `audit.jsonl`).  Rebuild the adapter ownership tables from
   durable facts only:
   - marker ← the audit chain's `SUBMIT_INTENT` entry (durable);
   - `adapter._jobs["job-0001"] = {"slurm_id": <located>, "workspace": <workdir>}`
   - `adapter._markers[marker] = "job-0001"`, `_idem`/`_ops`/`_op_attempts`
     lineage, `gateway._remember` — the same facts
     `gateway._resolve_predecessor` would adopt on.
   Then `session.status("job-0001")` proves ownership, accounting is read,
   artifacts fetched (`stdout.log`/`stderr.log`), probes parsed, and
   `session.settle(cancel_pending=True)` appends real `SETTLEMENT_BEGIN` /
   token-revoke events to the existing chain.  A `FAILED`-state job dumps a
   `FAILED-<stamp>.record.json` and raises.
6. **Reconstruct the CPU chain read-only.**  Fresh `sacct` lines identify
   the completed cpu job (WorkDir discriminator; TRES-no-GPU fallback; fail
   closed on ambiguity or state drift).  Artifacts are already on disk
   (`CANARY_ROOT/fetched-cpu-echo-probe-containment-{stamp}/`); their chain
   already carries the full event set, so **no session is opened** and that
   audit is not touched.
7. **Build the evidence envelope** exactly like `canary()` (phase
   `"canary"`, `authorization` scope = the same two-job scope — resume
   completes the same two authorized jobs, it adds nothing), `sentinel_
   cleanup` measured LAST, seal via `build_receipt`, verify via `verify`.
8. **Clean the interrupted stamp's sentinels last** (`finally` previously
   never ran); fail closed on leftovers.

Closure: all scheduler facts come from either the audit chain or fresh
`_ssh` queries at resume time; nothing is hardcoded from the incident.

## 4. Fail-closed matrix

| Situation | Behavior |
|---|---|
| 0 GPU chains / >1 GPU chains to auto-detect | refuse |
| `receipt.json` already sealed | refuse ("already qualified") |
| GPU job: hint mismatch AND no unique active/accounted match | refuse |
| >1 accounted match for the GPU run dirname | refuse |
| audit chain missing `SUBMIT_INTENT` marker | refuse |
| GPU job never reaches terminal in 48 h | refuse |
| CPU chain: no completed job / state drift / artifacts missing | refuse |
| settle/cancel raises (unowned or ambiguous) | refuse, append nothing partial |
| sentinel cleanup leaves a leftover | refuse the seal |
| second resume instance holds the flock | refuse immediately |

Refusal = `QualifyError` → non-zero exit → no receipt write, no partial
append beyond the already-durable chain events of the go path itself.

## 5. CLI surface

```
python scripts/infra/qualify_hpc_dispatcher.py --phase resume \
    [--stamp <hex4>]             # auto-detect unique interrupted GPU chain if omitted
    [--gpu-job-id <slurm_id>]    # identity anchor recorded at crash time (optional)
    [--since <sacct_start>]      # sacct window for exact WorkDir lookup
    [--profile scripts/hpc/cluster_profile.toml]
    [--runtime-lock 033-…/compute-runtime.lock.json]
```

`--phase resume` joins `{preflight, canary, cp2k, verify}`; reuse of
`--profile`/`--runtime-lock` keeps the one-driver story.  `recover_gpu_canary
.py` is deleted; its flock mutates to `/tmp/bench-hpc-resume.lock`.

## 6. Code identity and the deletion

`CODE_IDENTITY_PATHS` currently pins `recover_gpu_canary.py` as a separate
root-level trusted module.  After deletion the anchor set is:

```
scripts/infra/qualify_hpc_dispatcher.py      (now contains the resume phase)
dftworld_bench/experiments/qualification_receipt.py
dftworld_bench/hpc/{dispatcher,gateway,gateway_runtime}.py
dftworld_bench/hpc/adapters/{slurm,base}.py
dftworld_bench/hpc/{site_profile,tres,audit}.py
schemas/dispatcher-qualification-receipt.schema.json
```

- `code_identity()` fails a seal if any listed file is missing, so the
  `"recover_gpu_canary.py"` entry MUST be dropped together with the file.
- Verifying an OLD receipt that still pins `recover_gpu_canary.py` will
  then report `code identity file missing` — the site-v1 receipt is already
  INVALID under the `840781e` verifier (code-identity drift by design), and
  the next real-site re-seal (Phase 2/3, separate cluster authorization)
  binds the new, narrower anchor set.  No receipt is re-sealed here.
- Strictly *better* anchoring: one trusted driver file (already the primary
  anchor) rather than two scattered ones; any future edit to the recovery
  path is caught by the same code-identity gate as the canary path.
- `docs/HANDOFF-20260825.md` is a dated incident record — left untouched.
  README's two references are updated.

## 7. Out of scope (deferred)

- Site-level containment qualification caching (needs a new durable record;
  separate spec — see capability-matrix spec §7).
- Making `canary()` itself resumable (checkpoint mid-Job-2, resubmit-free).
  Resume covers the incident shape; mid-canary self-checkpoint is a bigger
  design.
- A `bench` top-level binary; the driver stays script-reachable
  (`--phase resume`).

## 8. Tests (offline; monkeypatched `_ssh`)

Ported 1:1 from `tests/hpc/test_recover_gpu_canary_logic.py` (all pure or
fake-`_ssh`): `_probe_assertions` cpu/gpu/missing/device-marker cases,
`locate_gpu_job` hint-anchor / stale-hint-by-dirname / ambiguity,
`_wait_terminal_scheduler` long-queue lifecycle + timeout, `_find_completed
_cpu_job` exact / multiple / non-completed skip — now parameterized by run id.

New, on the `resume()` entry:
- auto-detect: unique GPU chain found; 0 / 2 chains → `QualifyError`;
- refuses when `CANARY_ROOT/receipt.json` exists;
- no-submit invariant: a fake adapter records calls; `submit` is never
  invoked anywhere on the resume path;
- rehydration helper: builds `_jobs`/`_markers`/`_idem`/`_ops`/`_op_attempts`
  exactly from (marker, slurm_id, workdir);
- real-gateway settle over a temp audit chain with a fake adapter: a
  `SETTLEMENT_BEGIN` event lands on the SAME chain, then token-revoke on
  close, and the append is hash-chained (no new submit);
- deletion & anchor: `recover_gpu_canary.py` absent; `CODE_IDENTITY_PATHS`
  no longer names it; `code_identity()` still resolves over the reduced set.

`tests/hpc/test_qualification_operator_scripts.py`: the recover-ordering
text test retargets to the resume phase (settle → close → `_cleanup_sentinels`
call ordering inside `qualify_hpc_dispatcher.py`).

## 9. Rollout

1. Write this spec.  **Done.**
2. Implement `--phase resume` + the shared helpers in
   `qualify_hpc_dispatcher.py`; reuse `_build_site_profile`,
   `_open_session`, `_wrapper_renderer`-family, `_cleanup_sentinels`,
   `build_receipt`, `verify`.  **Done** — plus the shared
   `_assert_canary_routes` guard now used by both `canary` and `resume`.
3. Delete `recover_gpu_canary.py`; drop it from `CODE_IDENTITY_PATHS`.
   **Done.**
4. Rename/retarget `tests/hpc/test_recover_gpu_canary_logic.py` →
   `test_hpc_qualification_resume.py`; update the operator-scripts test and
   README.  **Done** — 19 tests incl. the real-gateway same-chain-settle
   contract; 1307 passed, 0 new failures.
5. Run hpc + experiments + ablation suites; expect 0 new failures (the 5
   pre-existing `case_factory`/`matclaw` failures reproduce on the frozen
   base).  **Done.**
6. Real-site exercise deferred to the Phase 2/3 re-seal (no cluster
   credentials in this environment); the offline contract tests pin the
   never-resubmit and same-chain-settle properties.