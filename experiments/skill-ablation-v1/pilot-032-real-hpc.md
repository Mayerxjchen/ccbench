# Pilot 032 — real-HPC No-Skill / With-Skill pair (machinery validation)

Scope: **one** paired trial of case `032-matclaw-cips-curie-temperature` —
one No-Skill run + one With-Skill run — under experiment_id
`skill-ablation-v1-pilot`. The pair validates the *pairing machinery*, not
the science: it proves that two runs of the same HPC case whose ONLY
difference is Skill availability can be recorded through the harness and
judged comparable, verifier-independent, sealed, and lifecycle-closed.

Nothing in this pilot is a formal observation. `skill-ablation-v1-pilot` is
excluded from every formal summary (`excluded_from_formal: true` in
`experiments/skill-ablation-v1/protocol.yaml`). The release
`ablation-ready-v0` is frozen (scope 001-033; 034 excluded from v0). This
pilot remains excluded from every formal summary.

Per project rule, the pilot runs on real HPC and the **user executes
cluster-side**; this document is the package the user follows.

---

## 1. Preflight (before any job is submitted)

### 1.1 Case must be benchmark_valid and reachable

The pilot may only use a case that has already passed its formal gate
(reference run + benchmark_valid). 032 is selected per Task 14 Step 5
decision. If 032's benchmark_valid flips false, STOP — the pilot must not
run on a case whose reference is not sealed.

### 1.2 Cluster profile correctness

The controller reads site facts from `scripts/hpc/cluster_profile.toml`.
Every value is fail-closed by the controller (missing/wrong type = hard
error, no silent default). Confirm before running:

| key | expected for <site-alias> |
|---|---|
| `[ssh] host` | `<site-alias>` (SSH-config alias), `port 22`, `user ""` (agent-forwarded) |
| `[slurm] account` | `acct-blocked` |
| `[slurm] partition` | `gpu` |
| `[slurm] qos` | `normal` |
| `[slurm] gres` | `gpu:1` |
| `[slurm] mem` | `64G` |
| `[paths] remote_root` | `/public/home/<site-user>/dftworld2-runs/...` (writable, pre-created) |
| `[runtime] expected_node_arch` | `x86_64` (must match the frozen SIF platform `linux/amd64`) |

The SIF identity is NOT here by design: `scripts/matclaw_runtime_lock.py`
is the single truth for the frozen runtime's path + SHA. Verify the runtime
lock resolves before the pilot (a GPU canary `probe` job should already
have passed for this case).

### 1.3 GPU canary

The controller's probe job validates GPU + apptainer + locked SIF on the
cluster *before* any paper run. Run a probe and confirm it reports
`x86_64` and green before the pilot pair. This is the same preflight gate
the formal run uses; do not skip it because this is "only a pilot".

### 1.4 Repo / skills state

- `git status` clean on the branch that will produce the runs.
- `base-env-build/.skill-image.json` skills_sha matches the protocol's
  frozen with-skill digest
  (`sha256:984bcc8d4b2775c7f8d0f4a475e46c7e9b2e03b348bd14e4a42c90c0c12668e9`).
  Drift is caught by `tests/experiments/test_ablation_protocol.py`.

---

## 2. Run the pilot pair

Two independent `eval.py` invocations, same case, same experiment, same
replicate=1, differing ONLY in the skill flags. No-Skill first.

### 2.1 No-Skill arm

```bash
uv run python eval.py 032-matclaw-cips-curie-temperature \
    --experiment skill-ablation-v1-pilot \
    --condition no-skill \
    --replicate 1 \
    --no-skills
```

### 2.2 With-Skill arm

```bash
uv run python eval.py 032-matclaw-cips-curie-temperature \
    --experiment skill-ablation-v1-pilot \
    --condition with-skill \
    --replicate 1 \
    --skills
```

Rules of the pair (from `experiments/skill-ablation-v1/protocol.yaml`):

- Run No-Skill and With-Skill on the **same site** (`same_site: true`) and
  **same profile** (`same_profile: true`). Do not interleave another job,
  a profile edit, or a cluster-side reconfiguration between the two arms.
- Do NOT change `--benchmark-commit`, model, image, or any other flag
  between the arms. Only the skill flags differ. (`--benchmark-commit` is
  now pinned automatically: eval resolves it from the frozen release's
  `source_commit` when no explicit value is given, so both arms agree.)
- `--condition` must be given explicitly and match the skill flag
  (no-skill ↔ `--no-skills`, with-skill ↔ `--skills`). A mismatch is a
  pairing defect — record it, do not silently rerun.
- No re-run to "improve" a pilot outcome. `no-silent-rerun`. An
  infra-invalid attempt may be replaced by a NEW attempt with a NEW run id
  (`infra_invalid: replace-with-new-run-id`); a scientific or agent failure
  is recorded and reported, never re-run.

### 2.3 Where the records land

Each arm writes a run-record at

```
jobs/<timestamp>__032-matclaw-cips-curie-temperature/run-record.json
```

with sibling `thread_dir` (contains `verifier-logs/result.json` and
`sealed-submission/`). Record both run ids and their thread dirs for the
post-run validation.

---

## 3. Post-run validation checklist

Run the pair validator:

```bash
python scripts/ablation/validate_pilot_pair.py \
    --no-skill   jobs/<tsA>__032-matclaw-cips-curie-temperature/run-record.json \
    --with-skill jobs/<tsB>__032-matclaw-cips-curie-temperature/run-record.json
```

Exit 0 + `"comparable": true` = the pairing machinery held. The validator
checks (see its docstring / `scripts/ablation/validate_pilot_pair.py`):

1. both records load + validate against `schemas/run-record.schema.json`
2. `ablation.comparability_errors()` is empty — every frozen identity field
   (case, experiment, image, benchmark_commit, profile, submission_root,
   verifier, platform, site_config_digest, replicate, agent_model,
   execution_class) matches across the pair
3. No-Skill arm: `skills_source == "none"`, `skills_sha == null`
4. With-Skill arm: `skills_source != "none"`, `skills_sha` == protocol's
   frozen bundle digest
5. independent verifier output exists (`thread_dir/verifier-logs/result.json`,
   result_class VALID_RESULT or classified failure)
6. submission seal exists and is non-empty (`thread_dir/sealed-submission/`)
7. lifecycle closes in a terminal phase (COMPLETED / PASS / FAIL / ...)
8. pilot is never formal: experiment_id `skill-ablation-v1-pilot` is
   recognized as a pilot (`ablation.is_pilot_experiment`)

Additional eyes, beyond the validator:

- [ ] both runs are `hpc_controller` execution_class (never infer from the
      case number; the run-record must say so)
- [ ] verifier logs show the run went through the real Slurm controller
      (submit → poll → fetch-back), not a local simulation
- [ ] the two job ids are distinct and both settled (no lingering job)
- [ ] sealed submission contents are identical in provenance handling
      between the arms (same seal mechanism, same verifier)
- [ ] both result.jsons classify the outcome (PASS / AGENT_FAILURE /
      INFRA_INVALID) with a reason — no bare "ok"

### 3.1 Expected pilot findings (not defects)

One known gap belongs in the pilot report, not the validator (it is an
infrastructure observation, documented in the Task 14 design):

1. **Pilot science is not a gate.** Agent PASS/FAIL in the pilot is a
   diagnostic, never a gate. Even a PASS is not a formal observation.

The controller transport is now resolved and NOT an open finding: the
agent reaches Slurm over HTTP to the host-side trusted gateway
(`BENCH_HPC_GATEWAY_URL` / `BENCH_HPC_RUN_TOKEN` → `GatewaySlurmTransport`),
and the gateway wraps the real `SshSlurmTransport` from the cluster profile
on the eval host. No SSH client, key, or config ships in the controller
image. The pilot should confirm in the run-record / verifier logs that the
job went through that gateway path (submit → poll → fetch-back) and that
the controller container held no SSH transport of its own.

### 3.2 If `comparable: false`

Do NOT rerun the pair to get a green check. Fix the root cause, then submit
a NEW pair (new run ids) and record the defect in the pilot notes. Report
the pairing defect as a pilot finding.

---

## 4. After the pilot

1. Record the two run ids, job ids, verifier result classes, and any pilot
   findings in this file (or a sibling `pilot-032-observations.md`).
2. Confirm both arms are excluded from formal summaries (experiment_id
   `skill-ablation-v1-pilot` is never counted).
3. Report to the project lead: pairing machinery held / broke, and what the
   real-HPC connection path was (finding 3.1.1).
4. Pilot does NOT unblock Task 14. v0 is frozen for 031-033 (034 excluded
   from v0); a later release re-freeze would still require every case it
   covers to be benchmark_valid.
