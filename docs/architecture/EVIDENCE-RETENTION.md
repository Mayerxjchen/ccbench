# Evidence Retention Standard

Normative design for the minimal, independently re-verifiable evidence system
(plan `2026-08-18-testset-evidence-retention`). Companion schemas live in
`schemas/`; the executable plan is the change-control source of truth.

## 1. Why this exists

The v1 model retained a full workspace copy per run. Commit `eb8bd61`
pruned 032/033 workspace bytes "as cleanup" while keeping only `manifest.json`;
the bytes had never been in git (workspaces are gitignored), and the cluster
run roots were cleaned too. A fail-closed derive engine then correctly refused
`manifest-without-bytes`, so 032/033 fell from `benchmark_valid` to
`constructed` and were unrecoverable without a full re-run.

**Lesson:** a manifest is a *record*, not evidence. Evidence is the bytes, and
it must survive independent of any one disk. This standard makes the manifest
point at content-addressed bytes stored twice, restorable on demand, and
independently re-verifiable.

## 2. Case-level vs run-level assets

| Layer | Owns | Stored where | Identity |
|---|---|---|---|
| Case evaluator bundle | `tests/`, `solution/`, hidden `reference/`, thresholds, frozen profiles | Evaluator repository (private store) | One `bundle_sha256` per case, referenced by every run |
| Run evidence bundle | Verifier-read submission bytes + reproduction/provenance | Primary CAS + independent replica | One content-addressed `tar.zst` per run |
| Git | Manifests, policies, small metadata, digests | Git repo | SHA-256 only, never large bytes |

`private/` (evaluator assets) must never be duplicated under every formal run.
Formal-run manifests reference `evaluator_bundle_sha256` instead.

## 3. Retention classes

Every asset has exactly one class:

| Class | Meaning | Retention |
|---|---|---|
| `scoring_required` | Verifier directly reads or recomputes from it | Permanent while the case version is supported |
| `reproduction_required` | Needed to reconstruct or audit how scoring evidence was generated | Permanent evidence archive |
| `operational_log` | Scheduler/stdout/stderr for incident analysis | With the formal run; compressed ok |
| `discardable` | Cache, installed software, duplicate input, scratch | Delete only after bundle restore test passes |

Per-case policies are declared in `<case>/reference/evidence-policy.json` and
validated against `schemas/evidence-policy.schema.json`. 034's policy stays
`state: construction` with `finalization_allowed: false` until the case is
constructed and `benchmark_valid`-eligible.

## 4. Content addressing

- All stored objects are addressed by lowercase SHA-256, never mutable
  `latest` paths.
- Object key: `sha256/<first-two-hex>/<full-sha256>.tar.zst`.
- A storage upload is not complete until bytes are read back and the SHA-256
  is recomputed to match.

## 5. Two-phase finalization

Order is strict and resumable (crash-safe state machine in
`scripts/evidence/finalize_run.py`):

```text
FROZEN            freeze completed run
CURATED           resolve minimal policy, stage only resolved files
VERIFIED          independent verifier passes on the curated tree,
                  scientific metrics match the original report
BUNDLED           deterministic tar.zst built (embedded manifest copy)
PRIMARY_VERIFIED  primary store put + read-back sha256 valid
REPLICA_VERIFIED  replica store put + read-back sha256 valid
SEALED            restore into a fresh temp dir, per-file hashes valid,
                  restored tree passes the independent verifier again
MANIFEST_COMMITTED  v2 manifest atomically written and committed
```

Before `SEALED`, no git-facing final manifest may claim durable evidence. A
crash resumes from the last persisted state without overwriting a sealed object.

## 6. Restore-first validation

`benchmark_valid=true` requires a restorable verified bundle, not merely a
manifest or a hand-written boolean. Validation:

1. Validate schema and identities.
2. Retrieve by digest (primary; replica on primary failure).
3. Recompute the bundle SHA-256 from read bytes.
4. Extract safely (refuse absolute paths, `..`, links, devices).
5. Recompute every per-file hash.
6. Verify evaluator + policy digests against the case manifests.
7. Run the existing scientific validation on the restored tree.

Failure codes: `EVIDENCE_UNAVAILABLE`, `EVIDENCE_HASH_MISMATCH`,
`EVALUATOR_IDENTITY_MISMATCH`, `EVIDENCE_NOT_RESTORABLE`. v1 manifests remain
readable during migration (validate adjacent workspace bytes, report
`migration_required=true`), but never synthesize a durable-storage claim.

## 7. Public/private release split

- Official public releases publish only public manifests and digests.
- Hidden evaluator bytes live in an evaluator-only repository or private
  evidence store — never in the public git tree beyond the digest.

## 8. Garbage collection

- Objects referenced by any git revision or supported release manifest are
  never deleted.
- Unreferenced uploads keep a 30-day grace period.
- GC is dry-run by default; actual deletion requires an explicit `--apply`
  plus user approval. S3 Object Lock (or ORAS over a private OCI registry)
  is the final external protection.

## 9. Release gate

A formal run contributes to `benchmark_valid` only when ALL of:

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

## 10. Migration rules

- Do not delete or prune any existing workspace until its replacement bundle
  has been built, stored twice, restored into a fresh directory,
  hash-verified, and independently re-verified.
- Migrate 031 first, then the new 032 reruns, then 033. 034 gets a policy and
  template now but no formal evidence is finalized until the case is
  constructed.
- Delete raw caches (local and HPC workspace paths) only after listing exact
  paths + bundle digests + both store URIs + successful restore timestamps,
  and obtaining explicit user approval.
