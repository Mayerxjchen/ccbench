# Evidence migration report: 031 / 032 / 033

Status: 031, 032, and 033 all migrated to sealed v2 bundles (both stores
verified, `benchmark_valid=true`). 033's original workspace bytes survived
nowhere, so it was rebuilt by re-running the frozen solution (1b61084) with
the original paper seeds — the re-run reproduced the v1 metrics exactly
(best Ez −0.17, slope 0.3827/0.7386), closing all ten gates. 034 is excluded
from finalize (in construction). Tracks the evidence-retention plan Task 8.

## Model

Three layers (git-tracked records / gitignored dual-located bytes / SHA-256
integrity anchor):

- Git tracks v2 `manifest.json` (identity + per-file `restored/` artifact
  digests + bundle descriptor with both store URIs + verifier metrics).
- Bytes live once per case as a deterministic `tar.zst` bundle, content
  addressed (`sha256/<xx>/<digest>.tar.zst`), stored in a primary CAS and an
  independent replica.
- Objects are immutable; every restore recomputes hashes before any scientific
  conclusion is drawn.

## Case 031

### Step 1 — dry-run curation (host, scriptable verifier runtime)

- Source: existing 456.7 MB local evidence workspace.
- Policy-resolved curated set: **95 files / 281.9 MB** (38.3% reduction;
  `du` 269 M on disk because the committee models are hardlinked).
- Bundle: `tar.zst`, deterministic (mtime 0, uid/gid 0, mode 0644, pinned zstd
  level 3). Sealed digest `c477dcee35f1…`, ~237 MB compressed.
- Stored in primary + replica `cas+file://`; restore from both verified.
- Excluded: solver scratch, `checkpoint.json`-adjacent non-scoring state, all
  per-run copies of the evaluator `private/` assets.

> Dry-run artefacts were held in temp dirs and cleaned; the numbers above are
> the measured values recorded at the time.

### Step 2 — cluster-native finalize (sealed 2026-08-18, driver `2955324`)

As for 032, finalize ran on the cluster login node with the frozen CPU
verifier SIF via `sbatch`. The host v1 workspaces were rsynced back to their
original cluster paths (`…/matclaw-031/runs/formal-<seed>/workspace`,
hardlinks `-H` preserved so the two runs share committee-model inodes and only
the unique ~564 MB crossed the ~2.9 MB/s link). Bundles stored twice in the
cluster CAS, restored, and re-verified before commit.

| run_id | bundle sha256 | size_bytes | primary | replica | verified_at |
|---|---|---|---|---|---|
| run-1 | `c477dcee35f1…` | 237,707,259 | `cas+file://…/031-primary` | `cas+file://…/031-replica` | 2026-08-18T08:24:03Z |
| run-2 | `7376a4452408…` | 237,705,140 | `cas+file://…/031-primary` | `cas+file://…/031-replica` | 2026-08-18T08:36:20Z |

Curated set: **95 files / 281.9 MB** per run — the exact policy-resolved
verifier input. Verified metrics: run-1/run-2 `final_force_mae_eV_A` 0.09683
both, `active_iterations` 2, inside the 0.10 MAE threshold.

> Re-seal note: the first seal produced schema-invalid manifests (the writer
> emitted a `null` `recomputed_estimate`, which v2 types `object`; case 031 has
> no Tc). Fixed the writer to omit the key when absent, regressed the cluster
> finalize-state `MANIFEST_COMMITTED → SEALED`, and re-ran the driver — the
> transaction resumed at the manifest step, re-authored clean manifests, and
> produced the same deterministic bundle digest (run-1 `c477dcee35f1…`).

### Authoritative validation

`verify_evidence --restore always` on the cluster login node:

```bash
PYTHONPATH=$PAYLOAD python3 scripts/ablation/verify_evidence.py \
  --case 031 --evidence-root …/matclaw-031/evidence --restore always
```

Result: `evidence_complete=true`, both runs `valid=true`, `restored=true`,
`failure_code=null`. Host/cluster manifests byte-identical (SHA-256 match).

> Host side fails closed (`EVIDENCE_UNAVAILABLE`) — bundle bytes live only in
> the cluster CAS; the host manifest is the durable git-tracked record.

### Step 3 — readiness

`readiness_audit --case all`: 031 `benchmark_valid=true`, `pilot_eligible=true`,
all ten gates pass (v2-aware derive from the sealed records).

## Case 032

The two formal re-runs were finalized cluster-native (ER8, approved): the
transaction runs on the cluster login node (python3.9 + numpy + zstd, no ase),
the frozen CPU verifier SIF runs on a compute node via `sbatch`, and only the
small manifests cross the slow host link.

### Jobs (paper reference runs)

| seed | run_id | job | node | started_at | finished_at | exit |
|---|---|---|---|---|---|---|
| 2026081206 | run-1 | 3567687 | <site-node-gpu3> | 2026-08-18T11:36:27Z | 2026-08-18T12:12:34Z | 0:0 |
| 2026081213 | run-2 | 3567688 | <site-node-gpu6> | 2026-08-18T11:38:05Z | 2026-08-18T12:14:36Z | 0:0 |

### Cluster layout

- Workspaces: `/public/home/<site-user>/dftworld2-runs/matclaw-032/runs/formal-<seed>/workspace`
- Payload (scripts + case reference/tests, ~1 MB): `…/matclaw-032/payload`
- Evidence: `…/matclaw-032/evidence/032/run-N/`
- Store primary: `…/matclaw-032/store/032-primary` (`cas+file://`)
- Store replica: `…/matclaw-032/store/032-replica` (`cas+file://`)
- Verifier jobs: frozen `matclaw-cips-2.2.11-cpu-amd64.sif` on `cpu` partition

### Finalize (sealed 2026-08-18, driver `34f81bd`)

Both runs finalized cluster-native: transaction on login node
(`finalize_run.py --verifier-slurm`), frozen CPU verifier SIF on a compute node
(`sbatch`, cpu partition, `acct-blocked`), bundles stored twice in the cluster CAS,
restored, and re-verified before commit. Each run = 3 independent verifier jobs
(original workspace / curated staging / restored tree), refusing on any metric
difference.

| run_id | bundle sha256 | size_bytes | primary | replica | verified_at |
|---|---|---|---|---|---|
| run-1 | `083cb656d5b2…21c8` | 699,986,419 | `cas+file://…/032-primary` | `cas+file://…/032-replica` | 2026-08-18T07:49:25Z |
| run-2 | `52db4a4de6a1…894e` | 699,968,710 | `cas+file://…/032-primary` | `cas+file://…/032-replica` | 2026-08-18T07:56:18Z |

Curated set: **19 files / 749,619,163 B** per run — the exact policy-resolved
verifier input (13 production trajs + `md/pilot_350K.traj` + `result.json` +
`order_parameter.csv` + `curie_temperature.png` + `report.md` +
`run_profiles.json`). Trajs are ~67 MB each and irreducibly read by the
verifier, so the bundle is ~700 MB zst, not a big reduction vs the 728 MB
workspace — there is no waste in the curated set (no `partial.traj`, no solver
scratch, no per-run evaluator copies).

Verified metrics: run-1 Tc_K 259.44 (Δ source 1.86), run-2 Tc_K 269.34 (Δ 8.04),
cross-run Δ 9.90 — all inside the ±10 tolerance from `source_Tc_K` 261.3.

### Authoritative validation

`verify_evidence --restore always` on the cluster login node (where both stores
live), evidence root `…/evidence`, payload case dir:

```bash
PYTHONPATH=$PAYLOAD python3 scripts/ablation/verify_evidence.py \
  --case 032 --evidence-root …/matclaw-032/evidence --restore always
```

Result: `evidence_complete=true`, both runs `valid=true`, `restored=true`,
`failure_code=null`. Restore → per-file SHA-256 → identity → science all pass
from the sealed bundles.

> Host side: the same command on a laptop fails closed
> (`EVIDENCE_UNAVAILABLE`) — the bundle bytes live only in the cluster CAS, and
> `cas+file://` is not reachable off-node. The host manifest is the durable
> git-tracked record; the prove-it-restorable check is the cluster run above.

## Case 033

No surviving workspace bytes on the host or cluster — the v1 manifests carried
only per-artifact sha256, never a bundle. Restore sources exhausted (git
history, `git fsck` dangling blobs, cluster filesystem, local dirs), so the
only honest path was a fresh formal re-run of the frozen code.

- Frozen solution: commit `1b61084` (run_search.py `7fbd17f7`, solve.sh
  `33f12b87`) — verified byte-identical on the cluster. The later
  `59bfc2e` HPC-contract commit added only `profiles/*` + policy, not solution
  logic; the run itself reads the staged `public/run_profiles.json`.
- Re-run seeds: **2026081301 / 2026081305** (the original paper seeds), staged
  from host `public/` (run_profiles.json `45edd88a` — identical to the v1
  workspace artifact).
- Jobs: 3574088 / 3574090, both COMPLETED 0:0 on <site-node-gpu6> (~17 min each).
- Reproduction: metrics matched v1 exactly (best Ez −0.17, slope
  0.382709…/0.738574…, 5 rounds / 10 jobs) — deterministic.
- Sealed v2 bundles: `e4d92912…` (run-1, 735 585 539 B), `3fa8096e…` (run-2,
  735 603 039 B), both stores `complete` on audit, verifier `valid=true`.
- `benchmark_valid=true`, all ten gates pass.

## Cleanup status

Nothing deleted. Per the binding constraint, raw workspace deletion waits until
dual-store upload, new-directory restore, hash verification, and independent
verifier re-verification all pass and the user approves explicitly.
