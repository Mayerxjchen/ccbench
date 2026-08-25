# Test-Set Evidence Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace permanent full-workspace retention with a minimal, independently re-verifiable evidence system that preserves each case's hidden evaluator once, each formal run's scientifically necessary bytes once, and enough provenance to restore and audit every `benchmark_valid` decision.

**Architecture:** A case-level Evaluator Bundle owns hidden verifier/reference/threshold assets and is identified by one digest. A run-level Evidence Curator resolves exactly the files consumed by that case's verifier plus declared reproduction records, proves the curated tree passes the independent verifier, then writes a deterministic content-addressed bundle to an external evidence store. Git tracks only manifests, policies, small metadata, and digests; local/HPC workspaces become replaceable caches.

**Tech Stack:** Python 3.12+, JSON Schema, SHA-256, deterministic POSIX tar, zstd, pytest, existing MatClaw verifiers, pluggable filesystem/object-storage backend.

## Global Constraints

- Do not delete or prune any existing workspace until its replacement bundle has been built, stored in two locations, restored into a fresh directory, hash-verified, and independently re-verified.
- Do not alter scientific thresholds, verifier algorithms, formal seeds, runtime identities, or existing manifest metrics while changing retention.
- Preserve the untracked `ai2kit/` directory and the active `.worktrees/034-hpc-controller` worktree.
- `tests/`, `solution/`, hidden reference, and thresholds are case-level evaluator assets; never duplicate them under every formal run.
- A run-level bundle contains only submission evidence and run provenance needed to reproduce or audit that run.
- Local and HPC workspaces are caches, not authoritative storage.
- Git never contains large trajectories, model binaries, training datasets, or compressed workspace bundles.
- Official public releases publish only public manifests/digests; hidden evaluator bytes remain in an evaluator-only repository or private evidence store.
- All stored objects are addressed by lowercase SHA-256, not mutable `latest` paths.
- `benchmark_valid=true` requires a restorable verified bundle, not merely a manifest or a hand-written boolean.
- A storage upload is not complete until bytes are read back and the SHA-256 is recomputed.
- Existing `manifest.json` v1 remains readable during migration; all newly finalized or migrated runs write Evidence Manifest v2.
- 034 receives a policy/template now but no formal evidence is finalized until the case itself is constructed and `benchmark_valid`-eligible.

## Retention Classes

Every asset has exactly one class:

| Class | Meaning | Retention |
|---|---|---|
| `scoring_required` | Verifier directly reads or recomputes from it | Permanent while the case version is supported |
| `reproduction_required` | Needed to reconstruct or audit how scoring evidence was generated | Permanent evidence archive |
| `operational_log` | Scheduler/stdout/stderr useful for incident analysis | Keep with formal run; may use compressed log layer |
| `discardable` | Cache, temporary checkpoint, installed software, duplicate input, scratch | Delete only after bundle restore test passes |

For 031–034, the initial policies are:

- 031 scoring: `result.json`, `checkpoint.json`, `active_learning/history.json`,
  `teacher_model.pb`, `data/heldout/**`, every `data/iteration_*` dataset, every
  committee model and exploration trajectory referenced by `result.json`, and
  every file named in `checkpoint.json.artifact_hashes`.
- 032 scoring: `result.json`, every `trajectories[].path`,
  `md/pilot_350K.traj`, `order_parameter.csv`, `curie_temperature.png`, and
  `report.md`.
- 033 scoring: `result.json`, `teacher_model.pb`, every
  `history[].trajectory`, `best_trajectory.traj`, `search_history.csv`,
  `domino_analysis.csv`, and `search_results.png`.
- 034 scoring is derived from its final verifier contract after construction;
  its policy file remains `state: construction` and cannot finalize evidence.

Common reproduction assets include the frozen job script, case/task/instruction
digests, runtime/SIF/image identities, resource/platform profile digests,
formal seed, scheduler record, verifier report, and run record. Software install
trees, `.venv`, caches, container layers, duplicated tests/solutions, and
unreferenced scratch are discardable.

---

### Task 1: Freeze the evidence-retention standard and schemas

**Files:**
- Create: `docs/architecture/EVIDENCE-RETENTION.md`
- Create: `schemas/evidence-policy.schema.json`
- Create: `schemas/evidence-manifest-v2.schema.json`
- Create: `schemas/evaluator-bundle.schema.json`
- Create: `tests/evidence/test_evidence_schemas.py`

**Interfaces:**
- Defines `EvidencePolicy`, `EvidenceManifestV2`, `EvaluatorBundleManifest`,
  `StoredObject`, and the four retention classes.
- `StoredObject` fields are `sha256`, `size_bytes`, `media_type`, `primary_uri`,
  `primary_version`, `replica_uri`, `replica_version`, and `verified_at`.

- [ ] **Step 1: Write failing schema tests**

```python
def test_manifest_v2_requires_restorable_primary_and_replica(valid_manifest):
    validate(valid_manifest, "evidence-manifest-v2.schema.json")
    del valid_manifest["bundle"]["replica_uri"]
    with pytest.raises(jsonschema.ValidationError):
        validate(valid_manifest, "evidence-manifest-v2.schema.json")


def test_construction_case_cannot_finalize(valid_policy):
    valid_policy["state"] = "construction"
    valid_policy["finalization_allowed"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate(valid_policy, "evidence-policy.schema.json")
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/evidence/test_evidence_schemas.py -q`

Expected: FAIL because the schemas do not exist.

- [ ] **Step 3: Write the normative standard and schemas**

The standard defines the case-level/run-level split, retention classes,
two-phase finalization, content-addressed keys, restore gates, public/private
release split, and garbage-collection prohibition for referenced objects.
Manifest v2 retains all v1 scientific identity fields and adds:

```json
{
  "schema_version": "2.0",
  "evaluator_bundle_sha256": "<64 hex>",
  "artifact_policy_sha256": "<64 hex>",
  "artifacts": [
    {
      "path": "submission/result.json",
      "role": "scoring_required",
      "size_bytes": 1234,
      "sha256": "<64 hex>"
    }
  ],
  "bundle": {
    "format": "tar.zst",
    "sha256": "<64 hex>",
    "size_bytes": 123456,
    "primary_uri": "cas+file:///evidence-store/sha256/ab/abcdef",
    "primary_version": "immutable",
    "replica_uri": "cas+file:///evidence-backup/sha256/ab/abcdef",
    "replica_version": "immutable",
    "verified_at": "2026-08-18T00:00:00Z"
  }
}
```

- [ ] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/evidence/test_evidence_schemas.py -q`

Expected: valid examples pass; missing replica, digest, role, size, or version fails.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture/EVIDENCE-RETENTION.md schemas/evidence-policy.schema.json schemas/evidence-manifest-v2.schema.json schemas/evaluator-bundle.schema.json tests/evidence/test_evidence_schemas.py
git commit -m "docs: define minimal test-set evidence retention"
```

---

### Task 2: Store hidden evaluator assets once per case

**Files:**
- Create: `scripts/evidence/build_evaluator_manifest.py`
- Create: `tests/evidence/test_evaluator_manifest.py`
- Create: `031-matclaw-cips-active-distillation/evaluator-manifest.json`
- Create: `032-matclaw-cips-curie-temperature/evaluator-manifest.json`
- Create: `033-matclaw-cips-domain-wall-search/evaluator-manifest.json`
- Create: `034-ai2kit-water64-end-to-end-potential/evaluator-manifest.json`

**Interfaces:**
- Produces `build_evaluator_manifest(case_dir: Path) -> EvaluatorBundleManifest`.
- The digest covers normalized paths and SHA-256 values of `tests/`, `solution/`,
  hidden `reference/` assets, and threshold/profile files selected by the case.
- Formal-run manifests reference this digest instead of containing a private copy.

- [ ] **Step 1: Write failing determinism and visibility tests**

```python
def test_evaluator_digest_is_independent_of_mtime(case_032, tmp_path):
    first = build_evaluator_manifest(case_032)
    os.utime(case_032 / "tests/verifier.py", (1, 1))
    second = build_evaluator_manifest(case_032)
    assert first.bundle_sha256 == second.bundle_sha256


def test_public_projection_contains_digest_but_not_hidden_paths(manifest):
    public = manifest.public_projection()
    assert public["evaluator_bundle_sha256"] == manifest.bundle_sha256
    assert "files" not in public
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/evidence/test_evaluator_manifest.py -q`

Expected: missing evaluator-manifest builder.

- [ ] **Step 3: Implement normalized manifest generation**

Hash bytes without normalizing them. Normalize only path separators and sorting.
Reject symlinks, device nodes, sockets, FIFOs, absolute paths, and `..`.
Do not copy evaluator bytes into `evidence/matclaw/formal/<case>/<run>/private`.

- [ ] **Step 4: Generate and verify the four manifests**

Run:

```bash
for case in 031-matclaw-cips-active-distillation 032-matclaw-cips-curie-temperature 033-matclaw-cips-domain-wall-search 034-ai2kit-water64-end-to-end-potential; do
  .venv/bin/python scripts/evidence/build_evaluator_manifest.py "$case" --check
done
```

Expected: deterministic digests; 034 is marked construction but still has an evaluator identity.

- [ ] **Step 5: Commit**

```bash
git add scripts/evidence/build_evaluator_manifest.py tests/evidence/test_evaluator_manifest.py 031-matclaw-cips-active-distillation/evaluator-manifest.json 032-matclaw-cips-curie-temperature/evaluator-manifest.json 033-matclaw-cips-domain-wall-search/evaluator-manifest.json 034-ai2kit-water64-end-to-end-potential/evaluator-manifest.json
git commit -m "evidence: identify evaluator assets once per case"
```

---

### Task 3: Declare exact per-case evidence policies

**Files:**
- Create: `031-matclaw-cips-active-distillation/reference/evidence-policy.json`
- Create: `032-matclaw-cips-curie-temperature/reference/evidence-policy.json`
- Create: `033-matclaw-cips-domain-wall-search/reference/evidence-policy.json`
- Create: `034-ai2kit-water64-end-to-end-potential/reference/evidence-policy.json`
- Create: `scripts/evidence/resolve_required_artifacts.py`
- Create: `tests/evidence/test_required_artifacts.py`

**Interfaces:**
- Produces `resolve_required_artifacts(workspace, policy) -> list[EvidenceFile]`.
- Resolver supports static paths, restricted globs, and these JSON references:
  `history[].models[].path`, `history[].exploration_trajectories[].path`,
  `trajectories[].path`, `history[].trajectory`, and
  `checkpoint.artifact_hashes.keys()`.
- Every resolved file has one retention role and one normalized destination.

- [ ] **Step 1: Write failing case-specific resolver tests**

Use existing positive fixtures to prove that all files read by each verifier
are resolved. Remove one referenced model/trajectory/dataset and require an
explicit `EvidencePolicyError`. Add an unreferenced `scratch.bin` and require
that it is absent from the resolved set.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/evidence/test_required_artifacts.py -q`

Expected: missing policies and resolver.

- [ ] **Step 3: Implement fail-closed resolution**

Resolve references only from the approved JSON fields. Reject absolute paths,
parent traversal, symlinks, references outside the workspace, destination
collisions, missing files, and files with multiple roles. Do not use unrestricted
`workspace.rglob("*")` as the retention rule.

- [ ] **Step 4: Encode the policies listed in Retention Classes**

031 and 033 policies dynamically follow model/trajectory/checkpoint references.
032 follows its 13 production trajectories plus pilot and reports. 034 uses:

```json
{
  "schema_version": "1",
  "case_id": "034",
  "state": "construction",
  "finalization_allowed": false,
  "scoring_required": [],
  "reproduction_required": []
}
```

- [ ] **Step 5: Verify GREEN**

Run: `.venv/bin/pytest tests/evidence/test_required_artifacts.py -q`

Expected: 031–033 positive fixtures resolve completely; scratch is excluded; 034 finalization is refused.

- [ ] **Step 6: Commit**

```bash
git add 031-matclaw-cips-active-distillation/reference/evidence-policy.json 032-matclaw-cips-curie-temperature/reference/evidence-policy.json 033-matclaw-cips-domain-wall-search/reference/evidence-policy.json 034-ai2kit-water64-end-to-end-potential/reference/evidence-policy.json scripts/evidence/resolve_required_artifacts.py tests/evidence/test_required_artifacts.py
git commit -m "evidence: declare minimal verifier-driven artifact policies"
```

---

### Task 4: Build deterministic bundles and a content-addressed store

**Files:**
- Create: `scripts/evidence/bundle.py`
- Create: `scripts/evidence/store.py`
- Create: `tests/evidence/test_bundle.py`
- Create: `tests/evidence/test_store.py`
- Modify: `evidence/matclaw/.gitignore`

**Interfaces:**
- Produces `build_bundle(files, destination) -> BundleDescriptor`.
- Produces `EvidenceStore.put(bundle) -> StoredObject`, `get(digest, destination)`, and `verify(digest)`.
- Initial backends: `cas+file://` for a mounted durable store and an independent
  `cas+file://` replica; the interface permits later `s3://` or `oci://` backends.
- Object key format is `sha256/<first-two-hex>/<full-sha256>.tar.zst`.

- [ ] **Step 1: Write failing deterministic archive tests**

Build twice after changing mtimes, UID/GID-visible metadata, and source order;
require identical bundle SHA-256. Reject unsafe archive names and non-regular
files. Verify extracted files match every per-file digest.

- [ ] **Step 2: Write failing store immutability tests**

Putting identical bytes twice must return the existing object. Putting different
bytes under an existing digest must fail. Retrieval into a fresh directory must
recompute the object and per-file hashes.

- [ ] **Step 3: Verify RED**

Run: `.venv/bin/pytest tests/evidence/test_bundle.py tests/evidence/test_store.py -q`

Expected: missing bundle/store modules.

- [ ] **Step 4: Implement deterministic tar.zst creation**

Sort paths; write owner/group 0, fixed mode, and timestamp 0; store a copy of
Evidence Manifest v2 inside the archive; compress with a pinned zstd level and
record the zstd version. Extraction must refuse absolute paths, `..`, links,
devices, sockets, and FIFOs.

- [ ] **Step 5: Implement primary and replica CAS writes**

Write to a temporary object, fsync, verify, then atomically rename. A formal
manifest is not emitted unless both primary and replica stores independently
return the expected digest and size.

- [ ] **Step 6: Extend `.gitignore`**

Ignore `formal/**/bundle-staging/`, `formal/**/restored/`, and
`formal/**/*.tar.zst`. Continue ignoring raw workspaces. Do not ignore manifest files.

- [ ] **Step 7: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/evidence/test_bundle.py tests/evidence/test_store.py -q`

```bash
git add scripts/evidence/bundle.py scripts/evidence/store.py tests/evidence/test_bundle.py tests/evidence/test_store.py evidence/matclaw/.gitignore
git commit -m "evidence: store deterministic bundles by content digest"
```

---

### Task 5: Prove the curated bundle is sufficient for independent verification

**Files:**
- Create: `scripts/evidence/curate_and_verify.py`
- Create: `tests/evidence/test_curate_and_verify.py`
- Modify: `scripts/reference/write_evidence_manifest.py`

**Interfaces:**
- Produces `curate_and_verify(case_dir, workspace, policy, verifier_runtime) -> CuratedEvidence`.
- The function stages only resolved files, runs the case verifier against that
  staging directory, compares scientific metrics with the original report, and
  refuses bundling on any difference.
- Manifest writer changes from `workspace.rglob("*")` to the curated file list.

- [ ] **Step 1: Write failing sufficiency tests**

For 031–033 positive fixtures, curate then run the independent verifier. Require
the same validity and scientific metrics as verification of the original tree.
Remove a required trajectory/model/dataset and require fail-closed behavior.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/evidence/test_curate_and_verify.py -q`

Expected: missing curator and manifest-writer support.

- [ ] **Step 3: Implement staging and verification**

Copy files without following links, normalize only staging permissions, mount
the curated submission read-only into the frozen Verifier runtime, and compare:

- 031: final MAE, active iterations, and verifier validity;
- 032: `Tc_K`, temperature grid, atom count, and verifier validity;
- 033: best field, best temperature, slope, rounds/jobs, and verifier validity.

- [ ] **Step 4: Change manifest authoring to v2**

Add `role`, `size_bytes`, evaluator/policy digests, bundle descriptor, and store
locations. Preserve identity, hardware/software, seed, timestamps, and verifier
metrics from v1.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/evidence/test_curate_and_verify.py tests/test_matclaw_validation.py -q`

```bash
git add scripts/evidence/curate_and_verify.py tests/evidence/test_curate_and_verify.py scripts/reference/write_evidence_manifest.py
git commit -m "evidence: prove minimal bundles reproduce verifier outcomes"
```

---

### Task 6: Replace finalize with a two-phase evidence transaction

**Files:**
- Create: `scripts/evidence/finalize_run.py`
- Create: `tests/evidence/test_finalize_transaction.py`
- Modify: `scripts/reference/finalize_032_evidence.sh`
- Modify: `scripts/run_matclaw_reference.sh`

**Interfaces:**
- Produces states `FROZEN`, `CURATED`, `VERIFIED`, `BUNDLED`,
  `PRIMARY_VERIFIED`, `REPLICA_VERIFIED`, `SEALED`, and `MANIFEST_COMMITTED`.
- A crash can safely resume from the last state without overwriting a sealed object.

- [ ] **Step 1: Write failing transaction/recovery tests**

Inject failures after every state. Before `SEALED`, no Git-facing final manifest
may claim durable evidence. After resume, exactly one primary object, one replica
object, and one v2 manifest must exist.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/evidence/test_finalize_transaction.py -q`

Expected: missing transaction implementation.

- [ ] **Step 3: Implement two-phase finalization**

Order operations exactly:

```text
freeze completed run
resolve minimal policy
run independent verifier on curated tree
build deterministic bundle
upload and read-back primary
upload and read-back replica
restore bundle into fresh temporary directory
run independent verifier again
atomically write manifest v2
derive benchmark validity
```

- [ ] **Step 4: Rewire 032 and generic MatClaw finalizers**

Keep Slurm/SIF/verifier execution logic but delegate curation, storage, restore,
and manifest writing to `finalize_run.py`. Remove unconditional full-workspace
rsync as the permanent archive operation; it may remain a temporary staging step.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/evidence/test_finalize_transaction.py tests/test_matclaw_hpc_controller.py -q`

```bash
git add scripts/evidence/finalize_run.py tests/evidence/test_finalize_transaction.py scripts/reference/finalize_032_evidence.sh scripts/run_matclaw_reference.sh
git commit -m "evidence: finalize formal runs as recoverable transactions"
```

---

### Task 7: Make validation restore-aware and fail closed

**Files:**
- Modify: `scripts/ablation/verify_evidence.py`
- Modify: `scripts/matclaw_validation.py`
- Create: `tests/evidence/test_restore_validation.py`

**Interfaces:**
- `verify_evidence.py --case CASE --restore auto|always|never`.
- Validation accepts local curated bytes or restores the exact bundle digest into
  a private temporary directory; it never trusts remote object metadata alone.
- Adds failure codes `EVIDENCE_UNAVAILABLE`, `EVIDENCE_HASH_MISMATCH`,
  `EVALUATOR_IDENTITY_MISMATCH`, and `EVIDENCE_NOT_RESTORABLE`.

- [ ] **Step 1: Write failing restore-aware tests**

Delete local workspace bytes while retaining valid primary/replica objects;
validation must restore and pass. Corrupt primary and require replica recovery.
Corrupt both and require `benchmark_valid=false`. Change evaluator digest and
require identity mismatch before scientific verification.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/evidence/test_restore_validation.py -q`

Expected: existing validation requires files beside the manifest and cannot restore.

- [ ] **Step 3: Implement restore-first validation**

Validate schema and identities, retrieve by digest, recompute bundle SHA-256,
extract safely, recompute all per-file hashes, verify evaluator/policy digests,
then run existing scientific validation. Use a temporary directory and clean it
after the verdict.

- [ ] **Step 4: Preserve v1 compatibility**

For v1 manifests, continue validating adjacent workspace bytes. Report
`migration_required=true`; do not synthesize a durable-storage claim.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/evidence/test_restore_validation.py tests/test_matclaw_validation.py -q`

```bash
git add scripts/ablation/verify_evidence.py scripts/matclaw_validation.py tests/evidence/test_restore_validation.py
git commit -m "evidence: derive benchmark validity from restorable bundles"
```

---

### Task 8: Migrate 031, then the new 032 runs, then 033

**Files:**
- Modify: `evidence/matclaw/formal/031/run-1/manifest.json`
- Modify: `evidence/matclaw/formal/031/run-2/manifest.json`
- Modify after rerun: `evidence/matclaw/formal/032/run-1/manifest.json`
- Modify after rerun: `evidence/matclaw/formal/032/run-2/manifest.json`
- Modify after restoration: `evidence/matclaw/formal/033/run-1/manifest.json`
- Modify after restoration: `evidence/matclaw/formal/033/run-2/manifest.json`
- Create: `docs/evidence/migration-report-031-033.md`

**Interfaces:**
- Every migrated run has one v2 manifest, one primary CAS object, one replica,
  and no per-run copy of evaluator `private/` assets.

- [ ] **Step 1: Dry-run 031 curation without deletion**

Build curated bundles from the existing 539 MB local evidence. Record original
size, curated size, included files by role, excluded scratch, verifier result,
primary/replica digests, and restore result.

- [ ] **Step 2: Migrate 031 manifests and rederive validity**

Run:

```bash
.venv/bin/python scripts/ablation/verify_evidence.py --case 031 --restore always
.venv/bin/python scripts/ablation/readiness_audit.py --case 031
```

Expected: two independent restored runs remain valid and agree scientifically.

- [ ] **Step 3: Finalize the new 032 reruns directly as v2**

Do not first establish another permanent 269 MB local workspace. Use the cluster
workspace as staging, curate, verify, store twice, restore, and commit v2 manifests.

- [ ] **Step 4: Restore and migrate 033**

Retrieve the missing legacy bytes from their surviving source. If bytes cannot
be recovered and no bundle exists, keep `benchmark_valid=false`; never promote
the old manifest alone.

- [ ] **Step 5: Remove duplicated per-run evaluator copies**

Only after Tasks 1–7 pass and v2 manifests reference the case evaluator digest,
remove tracked `evidence/matclaw/formal/031/run-*/private/`. Verify the case-level
evaluator assets remain intact and digest-identical.

- [ ] **Step 6: Delete raw caches only after explicit approval**

List exact local and HPC workspace paths, their bundle digests, both store URIs,
and successful restore timestamps. Obtain user approval before deletion. Keep
cluster workspaces through one completed recovery drill even after sealing.

- [ ] **Step 7: Commit migrations separately by case**

```bash
git commit -m "evidence(031): migrate formal runs to minimal durable bundles"
git commit -m "evidence(032): finalize reruns as minimal durable bundles"
git commit -m "evidence(033): restore and migrate formal run evidence"
```

---

### Task 9: Add recovery drills, retention audits, and safe garbage collection

**Files:**
- Create: `scripts/evidence/audit_store.py`
- Create: `scripts/evidence/recovery_drill.py`
- Create: `scripts/evidence/gc_plan.py`
- Create: `tests/evidence/test_retention_operations.py`
- Create: `docs/evidence/OPERATIONS.md`

**Interfaces:**
- `audit_store.py` checks every Git-tracked v2 manifest against both stores.
- `recovery_drill.py` restores a selected run and reruns its Verifier.
- `gc_plan.py` is dry-run only by default and never deletes an object referenced
  by any Git revision or supported release manifest.

- [ ] **Step 1: Write failing audit/GC safety tests**

Referenced objects must never appear in a deletion plan. Unreferenced uploads
remain protected for a 30-day grace period. Missing primary with healthy replica
is degraded, not valid redundancy. Missing both blocks benchmark validity.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/evidence/test_retention_operations.py -q`

Expected: operational tools do not exist.

- [ ] **Step 3: Implement audits and recovery drill**

Produce JSON reports suitable for Run Record attachment. A release gate requires
one full restore-and-verify drill for each HPC case version and a quarterly store audit.

- [ ] **Step 4: Implement non-destructive GC planning**

The command prints candidate objects, age, size, and why they are unreferenced.
Actual deletion requires a separate explicit `--apply` invocation plus user
approval; Object Lock/retention policy remains the final protection.

- [ ] **Step 5: Run the complete evidence gate**

```bash
.venv/bin/pytest tests/evidence tests/test_matclaw_validation.py -q
.venv/bin/python scripts/evidence/audit_store.py --all-manifests
.venv/bin/python scripts/evidence/recovery_drill.py --case 031 --run run-1
git diff --check
```

Expected: tests pass, both stores are complete, restored 031 verifies independently,
and no tracked object is eligible for garbage collection.

- [ ] **Step 6: Commit**

```bash
git add scripts/evidence/audit_store.py scripts/evidence/recovery_drill.py scripts/evidence/gc_plan.py tests/evidence/test_retention_operations.py docs/evidence/OPERATIONS.md
git commit -m "evidence: add durable-store audit and recovery operations"
```

---

## Final Storage Layout

```text
Git repository
├── <case>/
│   ├── tests/ + solution/ + hidden reference/   # evaluator-only source
│   ├── evaluator-manifest.json                 # one identity per case
│   └── reference/evidence-policy.json          # minimal run policy
└── evidence/matclaw/formal/<case>/<run>/
    ├── manifest.json                              # tracked v2 record
    └── verifier_report.json                       # small tracked evidence

Primary evidence store
└── sha256/ab/<digest>.tar.zst                   # authoritative run bytes

Independent replica
└── sha256/ab/<digest>.tar.zst                   # disaster-recovery copy

HPC and local disk
└── workspace/                                     # staging/cache, removable after seal
```

## Release Gate

A formal run contributes to `benchmark_valid` only when:

```text
case evaluator digest valid
AND evidence policy digest valid
AND minimal curated tree passes independent verifier
AND primary object read-back SHA-256 valid
AND replica object read-back SHA-256 valid
AND fresh restore per-file hashes valid
AND restored tree passes independent verifier
AND manifest v2 is committed
```

This gate deliberately does not require the original full workspace to remain.
