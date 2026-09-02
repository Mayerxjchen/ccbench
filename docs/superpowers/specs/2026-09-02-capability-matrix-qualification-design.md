# Capability-matrix qualification (design spec)

**Status:** IMPLEMENTED — landed 2026-09-02 as `infra: capability-matrix
qualification derivation`.  Implementation notes: §4 (option A + dropped
`site_acl_blocked`), §5a (aggregate is INVALID|PASS only), §5b (`[hpc]`
`qual_requires`, not `[execution]`), §5c (old receipt held INVALID by
code-identity until a fresh re-seal), §8 (BLOCKED_SITE_ACL branch removed).
**Touches (trust-anchor):** `schemas/dispatcher-qualification-receipt.schema.json`, `dftworld_bench/experiments/qualification_receipt.py` (`verify_receipt` derivation).
**Frozen base:** `010b1d2` (`qualification-candidate-site-v1`).

## 1. Why

The current receipt derives one global `qualification_status ∈ {PARTIAL, PASS, INVALID}` that **blocks on CP2K**: a `cp2k_gate` of `NOT_RUN` forces `PARTIAL`, so no case can be `formal_qualified` until CP2K is qualified site-wide. This couples unrelated cases to a runtime they may not use.

034 does use CP2K (`task.toml: required = ["ai2kit","cp2k"]`; `dft.method = "CP2K BLYP-D3 / TZV2P-GTH"`; smoke runs `dft_label_runs: 2`). But a case that only needs the GPU dispatcher + a MatClaw/DeePMD runtime should not be gated on `runtime.cp2k`.

**Goal:** split the single verdict into a per-capability matrix. A case declares `requires:` and may run as soon as *its* capabilities are PASS — CP2K absent no longer blocks the dispatcher.

## 2. What does NOT change (invariants)

- The receipt stays **verdict-free**. `qualification_status`, `formal_qualified`, per-gate labels, and the new capability statuses are all **derived by the verifier** from bound evidence — never stored on the receipt. This is the structural anti-forgery property and must not be weakened.
- Evidence collection is unchanged: the canary still places the 7 sentinels, the two jobs (cpu echo + gpu nvidia-smi) and the optional cp2k job each carry their own audit/settlement/accounting/probe/fetch blocks. The evidence is already per-capability-shaped; only the **derivation mapping** changes.
- All offline anchors survive: code-identity digests, SiteProfile digest, runtime-lock SIF digest, audit chain replay, sacct accounting re-parse, TRES reconciliation, settlement-digest recompute, artifact re-hash.
- `memory_gb = memory per allocated node`, independent CPU/GPU ceilings, cancel-before-sentinel ordering — all preserved (they are contract tests, not part of this change).

## 3. Capability namespace

A capability is a `(domain, name)` pair, derived from exactly one evidence block:

| Capability | Derived from | NOT_RUN when |
|---|---|---|
| `dispatcher.cpu` | the cpu-class canary job (`probe_class="cpu"`) | cpu job absent |
| `dispatcher.gpu` | the gpu-class canary job (`probe_class="gpu"`) | gpu job absent |
| `runtime.matclaw-gpu` | the same gpu job's `runtime_decl` SIF digest bound to the runtime lock | (shares the gpu job; cannot be NOT_RUN if `dispatcher.gpu` PASS) |
| `runtime.cp2k` | `evidence.cp2k_gate.evidence` | `cp2k_gate.evidence` is `null` (the current `NOT_RUN`) |

A capability status is `PASS` | `FAIL` | `NOT_RUN`:
- `PASS` — the evidence block is present and every sub-check re-derives clean.
- `FAIL` — the evidence block is present but a sub-check breaks.
- `NOT_RUN` — the evidence block is absent (only valid for capabilities whose evidence is optional, e.g. `runtime.cp2k`; `dispatcher.cpu`/`dispatcher.gpu` absent is `FAIL` because the canary coverage gate requires both classes).

## 4. Derivation mapping (the only logic change)

`verify_receipt` today builds `gates: dict[str, list[str]]` (gate→problems) then maps to a single status. The change keeps `gates` (the raw problem ledger) and **adds a capability projection** in the returned `derived`:

```
derived = {
  "qualification_status": <aggregate, see §5>,
  "formal_qualified": bool,
  "capabilities": {
     "dispatcher.cpu":    "PASS"|"FAIL",
     "dispatcher.gpu":    "PASS"|"FAIL",
     "runtime.matclaw-gpu": "PASS"|"FAIL",
     "runtime.cp2k":      "PASS"|"FAIL"|"NOT_RUN",
  },
  "gates": <unchanged gate→PASS/FAIL map>,
}
```

**IMPL note:** `site_acl_blocked` is **removed** from the derived dict, not kept
as a constant False.  The old BLOCKED_SITE_ACL branch was `status == "PARTIAL"
∧ cpu→gpu ACL mapping`; there is no PARTIAL under the matrix and the ACL facts
are enforced elsewhere: the collector hard-refuses a profile that maps cpu
workloads off the native queue (`QualifyError` up front), and the provenance
gate re-checks `native_cpu_partition_accessible` against the rebuilt
SiteProfile — a contradictory ACL claim fails closed as INVALID via
`provenance`.  `release_builder` no longer emits BLOCKED_SITE_ACL.

**Mapping design (revised after code audit 2026-09-02 — see review note below).**

> **Review finding:** `_derive_job` has **no per-job sub-gates**. Its `problem()` appends every job's problems into *shared* gate buckets (`scheduler_facts`, `tres_reconciliation`, `containment_probe`, `gpu_device_probe`, `settlement_integrity`, `audit_ledger`, `artifact_manifest`, `provenance`), distinguishing attribution only by message-label prefix. `_derive_cp2k` additionally calls `_derive_job(label="cp2k-job")`, so a broken cp2k job pollutes the shared buckets as well as `cp2k_gate`. An earlier draft of this section mapped `dispatcher.cpu/gpu` to "the cpu/gpu job's sub-gates" — that mapping is **not implementable without re-plumbing `_derive_job`**, and would let a cp2k failure mark `dispatcher.cpu/gpu` FAIL, defeating the matrix. Per-job bucket splitting (option B) was rejected: it changes trust-anchor plumbing and double-counts the shared anchors (schema, provenance, SiteProfile rebuild, code identity) that legitimately belong to every capability.

**Adopted mapping (option A — capabilities map to the EXISTING gate buckets wholesale):**

- `dispatcher.cpu` ← canary coverage + every shared gate, restricted to the cpu-class job's needs: PASS iff the shared gates carry no problem attributable to canary jobs (`canary_ok` in current code) **and** a cpu-class job exists with `probe_class="cpu"` deriving clean.
- `dispatcher.gpu` ← same shared-gate set, with the gpu-class job present and its `gpu_device_probe` clean.
- `runtime.matclaw-gpu` ← the gpu job's `runtime_decl@sha256` binding to the runtime-lock `sif_sha256` (already enforced in the shared `provenance` gate by `_derive_job`).
- `runtime.cp2k` ← the dedicated `cp2k_gate` bucket only (self-contained in `_derive_cp2k`): input binding, output re-parse, runtime-lock bind, job derivation. `NOT_RUN` when `evidence.cp2k_gate.evidence is None`.

Since cpu and gpu canary jobs both flow through the same shared buckets, `dispatcher.cpu` and `dispatcher.gpu` share fate in the shared gates — that is correct and intended: a broken scheduler-facts anchor means the whole dispatcher evidence is untrustworthy. What the matrix buys is **`runtime.cp2k` independence**: cp2k FAIL leaves `dispatcher.cpu/gpu` PASS and the aggregate INVALID, exactly as today; cp2k NOT_RUN leaves the aggregate PASS, which is the behavior change we want.

No gate's PASS/FAIL computation changes — this is a **re-projection** at the end of `verify_receipt`, not a re-derivation. `_derive_job` and `_derive_cp2k` are untouched. The only semantic change is the aggregation (§5).

## 5. Aggregation & backward compatibility

Two questions to resolve in review (I have a recommendation, not a foregone conclusion):

### 5a. What is the aggregate `qualification_status`?

The old single status was the only thing formal drivers keyed on. To avoid churning every driver, `verify_receipt` keeps returning a top-level `qualification_status`, now defined as:

```
INVALID  if any PRESENT capability is FAIL   (schema-broken ⇒ dispatcher.* FAIL)
PASS     if every PRESENT capability is PASS  (NOT_RUN does not block)
```

There is **no PARTIAL** any more: the capability matrix covers every state a
receipt can derive, so the third bucket disappears.  The implementation folds
the aggregate directly off the matrix — `INVALID if any(cap == "FAIL") else
PASS` — since `dispatcher.*`/`runtime.matclaw-gpu` are never NOT_RUN (an
absent canary class is FAIL; the coverage gate requires both).

vs. today: `PARTIAL` whenever cp2k is `NOT_RUN`. **This is the semantic change:** a site with cpu+gpu canary clean and cp2k not yet run now derives `PASS` (cp2k is simply `NOT_RUN`, not a blocker). The old `formal_qualified = (status==PASS)` then means "dispatcher is formally qualified"; cp2k qualification is a separate, case-gated check.

### 5b. How does a case gate on capabilities?

A new, tiny function (not on the receipt):

```
def case_requirements_satisfied(derived, requires: list[str]) -> bool:
    return all(derived["capabilities"].get(c) == "PASS" for c in requires)
```

**Case manifest field (decided): `[hpc].qual_requires`** — a list of capability
names under the `[hpc]` block (not `[execution]`), e.g. in `034-…/task.toml`:

```toml
[hpc]
contract_version = "hpc-execution/v1"
required_capabilities = ["batch_jobs", "gpu", "artifact_fetch"]
qual_requires = ["dispatcher.gpu", "runtime.matclaw-gpu", "runtime.cp2k"]
```

A MatClaw-only case declares `["dispatcher.gpu", "runtime.matclaw-gpu"]` and runs without cp2k.  `CaseSpec` carries the tuple (`qual_requires`), validated
against the schema pattern `^(dispatcher|runtime)\.[a-z0-9-]+$` even on the
legacy path (fail-closed: a typo'd name blocks, never reads as satisfied).

**The consumer that must actually be rewired: `release_builder.py`** (the non-test readers of the derived verdict are exactly two: `dftworld_bench/experiments/release_builder.py:181`, which releases on `qualification_status == "PASS"`, and `scripts/infra/qualify_hpc_dispatcher.py:928`, the operator print). After §5a, `release_builder`'s aggregate check alone would release a cp2k-dependent case with `runtime.cp2k = NOT_RUN` — the exact hole this section closes. The fix: `check_qualification_receipt(root, *, case_dir=…)` additionally resolves the named case's `qual_requires` (via `CaseSpec`) and requires `case_requirements_satisfied(derived, qual_requires)` before returning PASS; an unmet or unknown capability yields `BLOCKED_QUALIFICATION` detailing `unmet=[...] capabilities={...}`. A broken case manifest fails closed too. `qualify_hpc_dispatcher.py:928` stays on the aggregate (it is the site-qualification operator path, not per-case).

### 5c. Existing site-v1 receipt

The existing `receipt.json` (evidence collected at `010b1d2`) re-derives under the new rules into:
```
capabilities: {dispatcher.cpu: PASS, dispatcher.gpu: PASS,
               runtime.matclaw-gpu: PASS, runtime.cp2k: NOT_RUN}
qualification_status: PASS   (was PARTIAL)
formal_qualified: true       (was false)
```
This is the intended behavior change. The old receipt is **not re-sealed**; only the verifier's view of it changes. Evidence stays byte-identical. Old `PARTIAL` consumers must move to capability checks.

**IMPL note (reality of the trust anchor):** the receipt is content-addressed
and bound to the *verifier bytes that sealed it* — the `code_identity` anchor
(sha256 of `qualification_receipt.py`) recomputes on every verify.  Editing the
verifier therefore holds the old on-disk receipt in INVALID
(`provenance: code identity changed since qualification`) until a fresh re-seal
under the new verifier — by design, and already pinned by
`test_code_identity_drift_after_release`.  So the PARTIAL→PASS re-derivation
above is proven at the fixture level (`TestGoldenDerives`), and the next
real-site re-seal (Phase 2/3, separate authorization) will produce a fresh
receipt bound to the new verifier with `runtime.cp2k` still NOT_RUN until the
CP2K ENERGY canary runs.

## 6. Schema diff

Minimal. The receipt **evidence** schema is unchanged (it already carries `jobs` + `cp2k_gate` as separate blocks). The only schema-level consideration: `evidence.cp2k_gate` is currently `required` in the `evidence` object — but its `evidence` sub-field is optional (NOT_RUN). That stays. No new required fields on the receipt.

If we later want a site-level capability record (the §7 containment-caching idea), that is a **separate** new artifact (`site-capability-record/v1`), not a modification of this receipt.

## 7. Out of scope (explicitly deferred)

- Site-level containment qualification with expiry (the "don't re-place 7 sentinels every run" idea). This needs a new durable record + per-run lightweight probes; separate spec.
- Unified `bench hpc resume` CLI and deletion of `recover_gpu_canary.py`. The durable `_submit_v2` marker-adoption logic already supports it; this is CLI/driver consolidation only.
- `resource_class: gpu-small` alias layer on SiteProfile. `public_capabilities()` already exposes abstract classes; this is a config shorthand.

## 8. Tests

- **Update** `tests/hpc/test_qualification_receipt.py`: the derivation assertions that expect `PARTIAL` for cp2k NOT_RUN move to expecting `PASS` + `runtime.cp2k: NOT_RUN`. Add negatives: cp2k present-but-broken still → `INVALID` and `runtime.cp2k: FAIL` (with `dispatcher.cpu`/`dispatcher.gpu` still PASS — pins the §4 shared-bucket property, see `test_energy_tampered`); cpu canary broken → `dispatcher.cpu: FAIL` and aggregate `INVALID` (and, by shared fate, `dispatcher.gpu: FAIL`, see `test_cpu_canary_broken_marks_dispatcher_cpu_fail`). **Decided during implementation: the `BLOCKED_SITE_ACL` branch is removed, not repurposed** — there is no PARTIAL to key on, the collector refuses cpu→gpu mappings up front, and the provenance gate re-checks the ACL fact (spec §4 IMPL note).  `test_golden_receipt_derives_capability_matrix_pass` asserts the full four-key capabilities map and the aggregate PASS; `test_release_builder_sees_pass_without_cp2k` asserts the aggregate releases without cp2k.
- **Add** `case_requirements_satisfied` unit tests (matrix gate: PASS list, NOT_RUN hole, unknown capability name) — `TestCaseRequirementsSatisfied`.
- **Add** release-builder wiring tests: a cp2k-`qual_requires` case is BLOCKED while `runtime.cp2k` is NOT_RUN; a MatClaw-only `qual_requires` case is released once the aggregate is PASS; the same case is released once cp2k passes; a broken case manifest fails closed — `TestReleaseBuilderCaseGating`.
- **Keep** the existing verdict-free / anti-forgery adversarial tests unchanged — they prove the receipt cannot declare a status, and that still holds (one docstring now reads INVALID instead of PARTIAL).
- HPC + experiments + ablation suites green (444 passed).  Baseline reproduction on the frozen base shows the 5 unrelated `case_factory`/`matclaw` failures pre-existing — not from this change.

## 9. Rollout

1. Implement `capabilities` projection in `verify_receipt` (§4, option A) + aggregate (§5a). No evidence change; `_derive_job`/`_derive_cp2k` untouched. **Done** — aggregate folds the matrix; `site_acl_blocked` removed.
2. Add `case_requirements_satisfied`; wire `qual_requires` resolution into `release_builder` (§5b). **Done** — `CaseSpec.qual_requires`, `check_qualification_receipt(root, *, case_dir=…)`.
3. Update derivation tests + add wiring tests (§8). **Done** — `TestGoldenDerives`, `TestCaseRequirementsSatisfied`, `TestReleaseBuilderCaseGating`, cp2k-independence and cpu-broken negatives; 444 HPC+experiments+ablation pass.
4. Re-run `verify` on the existing site-v1 receipt: **see §5c IMPL note** — the live repo-root verify correctly reports `BLOCKED_QUALIFICATION` on `code identity changed` (the verifier just changed; the anchor is doing its job) plus the repo-local placeholder profile lacking `[slurm].account`.  The PARTIAL→PASS re-derivation is pinned by the fixture-level derivation tests; the next real-site re-seal under the new verifier produces the PASS+NOT_RUN receipt.
5. Commit on top of `010b1d2` as `infra: capability-matrix qualification derivation`. Do **not** re-seal the old receipt.
6. CP2K canary (next) then flips `runtime.cp2k` to PASS; 034 smoke becomes unblocked.
