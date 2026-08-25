# Evidence store operations (plan Task 9)

The durable evidence model keeps git-tracked v2 manifests on the host and the
bundle bytes in a content-addressed CAS, written twice (primary + independent
replica). This file is the operator runbook for the three operational tools:

| tool | what it does | destructive? |
|---|---|---|
| `audit_store.py` | checks every Git-tracked v2 manifest against both stores | no |
| `recovery_drill.py` | restores a run from the store and re-runs its Verifier | no |
| `gc_plan.py` | lists GC candidates (age/size/reason); `--apply` deletes | dry-run by default |

All JSON reports are suitable for attachment to a Run Record.

## Store layout

Stores are `cas+file://` directories addressed by content digest:

```text
<store-root>/sha256/<first-two>/<full-sha256>.tar.zst
```

Objects are immutable; identical bytes are reused. A v2 manifest references its
bundle by digest and carries both store URIs in `bundle.primary_uri` /
`bundle.replica_uri`.

For HPC cases (031/032) the stores live only on the cluster login node; the
host manifests point at those cluster paths. Any tool that touches the bytes
must run where the store is, or pass `--via-ssh` (audit) / a shared
`--verifier-scratch` (drill).

## Audit — `audit_store.py`

```bash
# host: HPC-case stores are cluster-side
.venv/bin/python scripts/evidence/audit_store.py --all-manifests --via-ssh <site-alias>

# local store
.venv/bin/python scripts/evidence/audit_store.py --all-manifests
```

Per-run status is `complete | degraded | corrupt | unavailable`:

- **complete** — both stores have the object with the right digest and size.
- **degraded** — exactly one store has it. Never treated as valid redundancy.
- **corrupt** — bytes present but wrong digest.
- **unavailable** — neither store has it; this **blocks benchmark validity**
  (`blocks_benchmark_valid: true`).

Exit 0 iff every audited run is `complete`.

## Recovery drill — `recovery_drill.py`

The release gate requires one full restore-and-verify drill per HPC case
version. A drill reads only — it restores the sealed bundle into a scratch dir,
byte-checks every manifest artifact, then re-runs the frozen Verifier SIF over
the restored tree.

Cluster (031-style; `--verifier-gres ""` and cpu partition, never gres):

```bash
PYTHONPATH=$PAYLOAD python3 scripts/evidence/recovery_drill.py \
  --case 031 --run run-1 \
  --evidence-root …/matclaw-031/evidence \
  --case-dir $PAYLOAD/031-matclaw-cips-active-distillation \
  --policy $PAYLOAD/031-matclaw-cips-active-distillation/reference/evidence-policy.json \
  --verifier-slurm \
  --verifier-sif …/matclaw-cips-2.2.11-cpu-amd64.sif \
  --verifier-tests $PAYLOAD/031-matclaw-cips-active-distillation/tests \
  --verifier-apptainer /public/software/apptainer/bin/apptainer \
  --verifier-scratch …/verify-scratch \
  --verifier-partition cpu --verifier-gres '' --verifier-account acct-blocked \
  --verifier-cpus 8 --verifier-mem 64G --verifier-time 04:00:00
```

> **Shared scratch, not /tmp.** The compute node binds the restored tree into
> the SIF read-only; login-node `/tmp` is per-node and invisible there. Restore
> into `--verifier-scratch` (a shared filesystem). This bit us once — a drill
> failed with `mount source …/restored doesn't exist` on the compute node.

Exit 0 iff the bundle restored with matching digests and the Verifier returned
`valid=true`.

## GC planning — `gc_plan.py`

Never deletes anything without an explicit `--apply` and user approval (Object
Lock / retention policy is the final protection). Referenced objects and
grace-protected unreferenced uploads are structurally excluded.

```bash
.venv/bin/python scripts/evidence/gc_plan.py \
  --primary cas+file://…/store/031-primary \
  --replica cas+file://…/store/031-replica \
  --evidence-root evidence/matclaw/formal \
  --grace-days 30
```

`--git-history` also protects every bundle referenced by any Git revision, not
just the working tree. `--apply` deletes only the printed candidates and is a
separate, deliberate invocation.

## Release gate

A formal run contributes to `benchmark_valid` only when:

```text
case evaluator digest valid
AND evidence policy digest valid
AND minimal curated tree passes independent verifier
AND primary object read-back SHA-256 valid
AND replica object read-back SHA-256 valid
```

Operational cadence: a full restore-and-verify drill per HPC case version at
release time, and a store audit quarterly. 031/032 as of 2026-08-18: audit
`complete` for all four runs; 031 run-1 drill `valid=true`.
