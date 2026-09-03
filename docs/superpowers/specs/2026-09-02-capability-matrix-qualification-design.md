# Capability-matrix qualification (design spec)

**Status:** APPROVED 2026-09-03 (审定).  Supersedes the 2026-09-02
landing `infra: capability-matrix qualification derivation` (`840781e`),
whose design decisions this revision reverses (§4 option A → per-job
derivation, §5a PARTIAL removed → restored, §5b bare `qual_requires` →
Infra-registry + `[hpc.qualification] requires`).  The `840781e` bytes stay
in git history as the superseded cut; the code rework landed on top of the
current HEAD after this spec was approved — spec-before-code was preserved.
**Touches (trust-anchor, rework):**
`schemas/dispatcher-qualification-receipt.schema.json`,
`schemas/case.schema.json` + `dftworld_bench/contracts/case.py` (registry,
`[hpc.qualification] requires` field), `dftworld_bench/experiments/qualification_receipt.py`,
`release_builder.py`, 034 `task.toml` (adds the `cp2k` family to
`[runtime] requirements`), 031–033 `task.toml`, `tests/hpc/test_qualification_receipt.py`.
`activate_v2.py` is intentionally **not** touched — it consumes the aggregate
(§5b).  **Frozen base:** `010b1d2` (`qualification-candidate-site-v1`).

## 1. Why

The receipt derives one global `qualification_status` that **blocks on CP2K**:
a `cp2k_gate` of `NOT_RUN` forces `PARTIAL`, so no case can be
`formal_qualified` until CP2K is qualified site-wide.  This couples unrelated
cases to a runtime they may not use.

034 does use CP2K (`task.toml: required = ["ai2kit","cp2k"]`;
`dft.method = "CP2K BLYP-D3 / TZV2P-GTH"`; smoke runs `dft_label_runs: 2`).
But a case that only needs the GPU dispatcher + a MatClaw/DeePMD runtime
should not be gated on `runtime.cp2k`.

**Goal:** split the single verdict into a per-capability matrix.  A case
declares `requires:` and may run as soon as *its* capabilities are PASS —
CP2K absent no longer blocks the MatClaw cases.  **The aggregate status must
keep its legacy meaning for old consumers** — "CP2K not run" stays `PARTIAL`,
never a false full `PASS`; new consumers gate finely on the matrix.

## 2. What does NOT change (invariants)

- The receipt stays **verdict-free**.  `qualification_status`,
  `formal_qualified`, per-gate labels, and the capability statuses are all
  **derived by the verifier** from bound evidence — never stored on the
  receipt.  This is the structural anti-forgery property and must not be
  weakened.
- **Evidence format is unchanged.**  The canary still places the 7 sentinels;
  the two jobs (cpu echo + gpu nvidia-smi) and the optional cp2k job each
  carry their own audit/settlement/accounting/probe/fetch blocks; the receipt
  bytes and the schema stay byte-compatible (one *additive optional* block,
  §6).  Only the **derivation mapping inside the verifier** changes.
- All offline anchors survive: code-identity digests, SiteProfile digest,
  runtime-lock SIF digest, audit chain replay, sacct accounting re-parse,
  TRES reconciliation, settlement-digest recompute, artifact re-hash.
- `memory_gb = memory per allocated node`, independent CPU/GPU ceilings,
  cancel-before-sentinel ordering — all preserved (contract tests, not part
  of this change).

## 3. Capability namespace

A capability is a `(domain, name)` pair, derived from exactly one evidence
block:

| Capability | Derived from | NOT_RUN when |
|---|---|---|
| `dispatcher.cpu` | the cpu-class canary job (`probe_class="cpu"`) | —— absent cpu job is `FAIL` (coverage gate), never NOT_RUN |
| `dispatcher.gpu` | the gpu-class canary job (`probe_class="gpu"`) | —— absent gpu job is `FAIL`, never NOT_RUN |
| `runtime.matclaw-gpu` | the matclaw-cips SIF digest (`matclaw-cips-2.2.11-gpu-amd64.sif`) bound to the gpu job's `runtime_decl` (per-job provenance, §4) | shares the gpu job; cannot be NOT_RUN if `dispatcher.gpu` PASS |
| `runtime.ai2kit` | `evidence.ai2kit_gate` (optional block, §6) | `ai2kit_gate.evidence` absent (site-v1 receipt: no ai2kit canary has run) |
| `runtime.cp2k` | `evidence.cp2k_gate.evidence` | `cp2k_gate.evidence` is `null` (the current `NOT_RUN`) |

A capability status is `PASS` | `FAIL` | `NOT_RUN`:
- `PASS` — the evidence block is present and every sub-check re-derives clean.
- `FAIL` — the evidence block is present but a sub-check breaks; or (for the
  dispatcher tiers) the coverage gate is not met.
- `NOT_RUN` — the evidence block is absent.  **Only valid for the optional
  runtime evidence blocks** (`runtime.cp2k`, `runtime.ai2kit`).  A missing
  canary class is `FAIL`, because the canary coverage gate requires both
  classes.

## 4. Derivation — per-job (revised; supersedes the 2026-09-02 "option A")

The 2026-09-02 review note recorded the structural problem: `_derive_job` has
**no per-job sub-gates** — every job's `problem()` appends into *shared* gate
buckets (`scheduler_facts`, `tres_reconciliation`, `containment_probe`,
`gpu_device_probe`, `settlement_integrity`, `audit_ledger`,
`artifact_manifest`, `provenance`), distinguishing attribution only by
message-label prefix.  Option A then mapped the capabilities onto those
shared buckets wholesale, so a broken gpu job marked `dispatcher.cpu` FAIL and
vice versa ("shared fate") — implementable without re-plumbing, but it cannot
accurately attribute CPU vs GPU evidence by gate name alone.

The 2026-09-03 correction makes **per-job derivation the design**, with the
evidence format unchanged.  Re-plumbed, not re-mapped:

```
def _derive_job(job, label) -> JobDerivation:
    # returns PER-JOB buckets.  A job's problems land ONLY in its own
    # buckets; the buckets keep today's names and problem strings.
    #   scheduler_facts, tres_reconciliation, containment_probe,
    #   gpu_device_probe (gpu-class jobs only; absent for cpu),   
    #   settlement_integrity, audit_ledger (per-run chain),
    #   artifact_manifest, provenance (INCLUDING the runtime_decl
    #   SIF-digest binding — now per-job, no longer injected into a
    #   shared provenance bucket).
```

Capability projection (all statuses `PASS` iff the set has no problems):

```
shared_anchors  = envelope validation ∪ schema ∪ code identity ∪
                  source commit ∪ SiteProfile digest rebuild ∪
                  native_cpu_partition_accessible ACL fact   # computed ONCE

dispatcher.cpu           ← shared_anchors ∪ cpu_derivation(passed)
dispatcher.gpu           ← shared_anchors ∪ gpu_derivation(passed)
runtime.matclaw-gpu      ← shared_anchors ∪ gpu_derivation.provenance
                           (runtime_decl↔matclaw-cips lock binding clean)
runtime.cp2k             ← shared_anchors ∪ cp2k_derivation
runtime.ai2kit           ← shared_anchors ∪ ai2kit_derivation   (# §6; absent ⇒ NOT_RUN)
```

The public `gates` ledger (`gate → list[problem]`) is the **union of all
per-job buckets + the runtime buckets**, in the same map shape — the raw
problem ledger that the offline-anchor tests and the aggregate read does not
change shape, so the "evidence format unchanged" invariant (and the receipt
bytes) hold.  The shared anchors are an explicit **overlay applied equally to
every capability**, not a per-job re-derivation — this is what makes the
per-job split affordable without double-counting (the cost the old review
note cited).

Properties the tests pin (these are the behavioral flips vs `840781e`):

- gpu device probe broken → `dispatcher.gpu: FAIL`, **`dispatcher.cpu: PASS`**
  (was: both FAIL via shared buckets).
- cpu canary broken → `dispatcher.cpu: FAIL`, `dispatcher.gpu: PASS`.
- cp2k evidence broken → `runtime.cp2k: FAIL`, `dispatcher.cpu/gpu` PASS.
- code-identity drift / schema break → every capability FAIL, aggregate
  INVALID (the overlay; anti-forgery fundamentals unchanged).

## 5. Aggregation & backward compatibility

### 5a. Aggregate `qualification_status` — `PARTIAL` restored

The old single status is the thing legacy formal drivers key on, and its
meaning must not silently become "CP2K didn't run ⇒ full PASS".  `verify_receipt`
keeps returning the top-level `qualification_status`:

```
INVALID  if the envelope is invalid (schema / code identity / digest / ACL)
         or any capability is FAIL
PARTIAL  if no FAIL but at least one capability is NOT_RUN     ← legacy
PASS     if every capability is PASS
```

- `formal_qualified = (status == PASS)` — the full pre-`840781e` meaning is
  restored: a site with `runtime.cp2k` or `runtime.ai2kit` NOT_RUN derives
  **PARTIAL**, never PASS, so an old consumer cannot misread "CP2K not run"
  as full qualification.
- `NOT_RUN` is only reachable for the optional runtime evidence blocks
  (dispatcher class absence is a coverage FAIL).  Today, site-v1's
  `runtime.cp2k = NOT_RUN` (and `runtime.ai2kit = NOT_RUN`) ⇒ aggregate
  PARTIAL — **identical to the pre-matrix value**.
- The capability matrix is what new consumers gate on (per-case §5b), not
  the aggregate.  The aggregate is demoted to the legacy summary — the exact
  split the correction asks for.

### 5b. How a case gates on capabilities — Infra registry + `[hpc.qualification] requires`

**No bare new field** (the `[hpc].qual_requires` array from the `840781e` cut
is withdrawn).  Two layers:

1. **Infra registry** — a curated, site-independent table validated against
   the frozen runtime-lock catalog, mapping declared **runtime families** to
   capability names:

   | declared runtime family | capability |
   |---|---|
   | `matclaw-cips` (031–033 locks `matclaw-cips-2.2.11-gpu-amd64.sif`) | `runtime.matclaw-gpu` |
   | `ai2kit` (034 lock `dftworld-base-ai2kit:0.1.0-cpu-controller`) | `runtime.ai2kit` |
   | `cp2k` (reference/runtime `cp2k-runtime.lock.json`) | `runtime.cp2k` |

   **Scoping rules (curated, not mechanical):**
   - `dispatcher.cpu` / `dispatcher.gpu` are **never auto-derived** from the
     coarse legacy `[hpc] required_capabilities` — 031–033 and 034 all declare
     `batch_jobs + gpu + artifact_fetch`, yet gate differently (031: GPU-only;
     034: CPU controller + GPU work).  The tier declaration is always
     explicit.
   - `[hpc.scientific_capabilities]` do **not** map to `runtime.*` gates —
     `031` lists `cp2k` there as a benchmark-domain descriptor and must not
     thereby acquire a `runtime.cp2k` qualification gate.
   - Fail closed: an unknown declared family, or a capability name outside
     `^(dispatcher|runtime)\.[a-z0-9-]+$` and the registry, is a **CaseSpec
     build error** — never silently satisfied.

2. **Explicit declaration** — for the cases where the registry default is
   incomplete or ambiguous, the authoritative per-case set lives in the
   nested `[hpc.qualification]` block:

   ```toml
   [hpc.qualification]
   requires = ["dispatcher.gpu", "runtime.matclaw-gpu"]
   ```

   The effective gate is `registry(auto, case) ∪ declared`; the declared list
   is authoritative for the dispatcher tiers (§"scoping" above), so a typing
   or registry drift cannot silently change a case's gate.

**Case table (authoritative):**

| Case | runtime families (locks) | `[hpc.qualification] requires` |
|---|---|---|
| 031–033 (MatClaw CIPS: active-distillation / curie-temperature / domain-wall-search) | `matclaw-cips` | `["dispatcher.gpu", "runtime.matclaw-gpu"]` |
| 034 (ai2kit water64 end-to-end) | `ai2kit` + **`cp2k` (add to `[runtime] requirements` — the CP2K 2025.2 AIMD runtime bound by `software.cp2k` in the 034 lock)** | `["dispatcher.cpu", "dispatcher.gpu", "runtime.ai2kit", "runtime.cp2k"]` |

Rationale for 034: the ai2kit controller is the `dftworld-base-ai2kit:0.1.0-
cpu-controller` image (CPU-resident orchestration ⇒ `dispatcher.cpu`), the
DeePMD/MatClaw-style GPU work and active-learning dispatch need the gpu
dispatcher + ai2kit runtime (`runtime.ai2kit`), and the CP2K AIMD inner step
needs the CP2K ENERGY canary (`runtime.cp2k`).  `runtime.matclaw-gpu` is
**not** on 034 — MatClaw is not a dependency of the ai2kit water pipeline.

**Consumers:** `release_builder.py` releases per case: with `case_dir` named,
it resolves the case's effective requires (`_case_qualification_requires(case_dir)`
→ `spec.effective_qualification_requires`) and gates purely on
`case_requirements_satisfied(derived, requires)` — every requires PASS.  An
unmet or unknown capability yields `BLOCKED_QUALIFICATION` with
`unmet=[…] capabilities={…}`; a broken case manifest fails closed too.
The no-`case_dir` release path, `activate_v2.py` (calls
`check_qualification_receipt(ROOT)` with no `case_dir`), and the operator
print (`qualify_hpc_dispatcher.py:928`) all stay on the **aggregate** — the
site-qualification path, where PARTIAL is the honest legacy report.  An
aggregate PARTIAL does not block a case-gated release whose requires are PASS;
it only blocks the legacy no-case release path.

### 5c. Site-v1 receipt under the revised rules

The existing `receipt.json` (evidence collected at `010b1d2`) re-derives:

```
capabilities: {dispatcher.cpu: PASS, dispatcher.gpu: PASS,
               runtime.matclaw-gpu: PASS, runtime.cp2k: NOT_RUN,
               runtime.ai2kit: NOT_RUN}
qualification_status: PARTIAL   (was PASS under 840781e; equals the legacy value)
formal_qualified: false
```

Per case:
- **031–033 releaseable now:** `requires = {dispatcher.gpu, runtime.matclaw-gpu}`
  ⊆ the PASS set ⇒ `case_requirements_satisfied` true **despite** global
  PARTIAL.  This is precisely the outcome the project wants: MatClaw flows are
  no longer blocked by a CP2K canary that hasn't run — while an old consumer
  reading the aggregate still sees the honest PARTIAL.
- **034 blocked** until both the CP2K ENERGY canary (`runtime.cp2k → PASS`)
  and an ai2kit runtime canary (`runtime.ai2kit → PASS`) run.  Unmerged:
  blocked is *correct* for 034.

The old receipt is **not re-sealed**; only the verifier's view of it changes.
**IMPL note (trust anchor):** the receipt is content-addressed and bound to
the verifier bytes that sealed it — `code_identity` recomputes on every
verify, so editing the verifier holds the on-disk receipt in INVALID
(`provenance: code identity changed since qualification`) until a fresh
re-seal under the reworked verifier.  The PARTIAL re-derivation above is
proven at the fixture level (`TestGoldenDerives`), and the next real-site
re-seal (Phase 2/3, separate authorization) produces the new receipt with
`runtime.cp2k`/`runtime.ai2kit` still NOT_RUN until those canaries run.

## 6. Schema diff

- **Receipt schema:** evidence block gains one **additive optional** member —
  `evidence.ai2kit_gate` = `{detail?, evidence}`, where `evidence` is a
  `runtimeCanaryEvidence` of `{job, runtime_lock}` — the ai2kit canary's full
  job record (own audit/settlement/accounting/probe/fetch chain) plus its
  runtime-lock SIF bind.  It mirrors the cp2k gate envelope but **without the
  CP2K-domain input/output re-parse** (an ai2kit canary has no CP2K-style
  artifact this verifier re-parses); the `evidence` sub-field is optional ⇒
  absent derives NOT_RUN.  Nothing else moves; `jobs`, `cp2k_gate` structure
  untouched.
- **Case manifest schema:** remove `[hpc].qual_requires` (bare array,
  withdrawn); add nested `[hpc.qualification].requires` (array of capability
  names, pattern `^(dispatcher|runtime)\.[a-z0-9-]+$`).
  `CaseSpec.qualification_requires` replaces the withdrawn `qual_requires`
  field; the effective gate (registry auto-derivation ∪ declared) is exposed
  as `CaseSpec.effective_qualification_requires`.

## 7. Out of scope (deferred; unchanged)

- Site-level containment qualification with expiry ("don't re-place 7
  sentinels every run"): needs a new durable record + per-run lightweight
  probes; separate spec.
- The ai2kit *runtime canary* itself (evidence for `runtime.ai2kit`):
  already authored on the CP2K-canary template, runs after an authorized
  cluster phase.  Its `ai2kit_gate` evidence block shape is pinned here (§6)
  so re-seals can carry it.
- 034 smoke and any 031–033 release on-site: cluster-bound, after the
  reworked verifier's re-seal.

## 8. Tests (reworked)

- **Update `tests/hpc/test_qualification_receipt.py`:**
  - `test_golden_receipt_derives_capability_matrix_pass` becomes
    `…_partially_qualified_without_cp2k`: the 5-key capabilities map
    (`dispatcher.cpu/gpu`, `runtime.matclaw-gpu` PASS; `runtime.cp2k`,
    `runtime.ai2kit` NOT_RUN) and aggregate **PARTIAL** (not PASS).
  - The `840781e` shared-fate assertion **flips**:
    `test_cpu_canary_broken_marks_dispatcher_cpu_only` — cpu broken ⇒
    `dispatcher.cpu: FAIL`, **`dispatcher.gpu: PASS`**; likewise gpu-probe
    broken ⇒ `dispatcher.gpu: FAIL`, `dispatcher.cpu: PASS`.
  - cp2k present-but-broken ⇒ aggregate INVALID with `runtime.cp2k: FAIL` and
    `dispatcher.cpu/gpu` still PASS (per-job isolation, see
    `test_energy_tampered`).
  - New: `runtime.ai2kit` NOT_RUN (absent block) and FAIL (tampered
    `ai2kit_gate`); overlay negatives: code-identity drift ⇒ every capability
    FAIL, aggregate INVALID.
- **Update `TestReleaseBuilderCaseGating`:** 031–033-style requires
  (`dispatcher.gpu`, `runtime.matclaw-gpu`) released while the aggregate is
  PARTIAL (proves "fine gating despite global PARTIAL"); 034-style requires
  BLOCKED with `runtime.cp2k`/`runtime.ai2kit` NOT_RUN; unknown capability
  name and unknown runtime family fail closed at CaseSpec build.
- **New `TestCapabilityRegistry`:** curated table resolution per family,
  dispatcher-tier non-derivation from legacy `required_capabilities`,
  scientific-capability non-leak (031's `cp2k` descriptor adds no gate).
- **Keep** the verdict-free / anti-forgery adversarial tests (receipt cannot
  declare a status) and the offline-anchor tests — the `gates` union shape
  keeps them green, with the PARTIAL-related docstrings restored.
- HPC + experiments + ablation suites green; the 5 pre-existing
  `case_factory`/`matclaw` failures reproduce on the frozen base and are not
  from this change.

## 9. Rollout

1. **审定 this revision** — gate.  Passed 2026-09-03; status header updated
   to APPROVED.  Steps 2–5 below landed together in the rework commit on top
   of `48a5b8e`.
2. Rework `verify_receipt`: per-job `_derive_job` (§4), shared-anchor overlay,
   PARTIAL aggregate (§5a), `runtime.ai2kit` + optional `ai2kit_gate` (§3/§6),
   `gates` union kept.  `qualify_hpc_dispatcher.py:928` unchanged (aggregate).
3. Case layer: registry + `[hpc.qualification] requires` replaces
   `qual_requires` (schema, `case.py`, `release_builder.py`); add the `cp2k`
   family to 034's `[runtime] requirements`; set the 031–033 and 034 requires
   per §5b table.  `activate_v2.py` is not part of the rework — it consumes
   the aggregate (no `case_dir`), whose legacy meaning is preserved (§5a).
4. Update tests per §8; flip the shared-fate assertions.
5. Full suite green, 0 new failures vs the frozen base; commit on top of
   `48a5b8e`.
6. Frozen-code qualification re-run (re-derive site-v1 + any re-seal under
   the reworked verifier; then CP2K ENERGY canary → `runtime.cp2k` PASS; then
   ai2kit runtime canary → `runtime.ai2kit` PASS; then 034 smoke) — **deferred
   to the authorized cluster phase**; no credentials in this environment.

## 10. Delta vs the `840781e` landing (recorded for review)

| Decision in `840781e` | Revision (this spec) |
|---|---|
| Capability projection over the shared gate buckets ("option A"); cpu/gpu share fate | Per-job `_derive_job` returning per-job derivation; shared anchors = explicit overlay; cpu and gpu isolated (§4) |
| Aggregate INVALID\|PASS; PARTIAL deleted; CP2K NOT_RUN ⇒ PASS | `PARTIAL` restored (INVALID / PARTIAL / PASS); CP2K or ai2kit NOT_RUN ⇒ PARTIAL, never false PASS (§5a) |
| Bare `[hpc].qual_requires` array | Infra-registry mapping (runtime families → `runtime.*`) + nested `[hpc.qualification] requires`; no bare field (§5b) |
| 034 requires `["dispatcher.gpu","runtime.matclaw-gpu","runtime.cp2k"]` | 034 requires `["dispatcher.cpu","dispatcher.gpu","runtime.ai2kit","runtime.cp2k"]`; 031–033 `["dispatcher.gpu","runtime.matclaw-gpu"]` (§5b) |
| No `runtime.ai2kit` capability | `runtime.ai2kit` added (optional `ai2kit_gate` evidence block, NOT_RUN absent) (§3/§6) |
| `case_requirements_satisfied` gated release on the aggregate | Release gates on the matrix; aggregate PARTIAL is the legacy summary (§5a/§5b) |

The `840781e` commit and its tests pin the superseded behavior and are
reworked in step 2–4; nothing is deleted from history.  Because every receipt
is bound to the verifier bytes that sealed it, both the `840781e` cut and the
rework hold the un-resealed site-v1 receipt INVALID until the Phase 2/3
re-seal — consistent, no re-seal here.