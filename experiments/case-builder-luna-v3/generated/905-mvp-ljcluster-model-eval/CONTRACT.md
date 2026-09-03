# Benchmark Case Contract — LC-MLP v1 model evaluation

> Builders/harness view (`candidate_visible: false`). Draft contract,
> populated from the validated reproduction spec and the case design. Facts
> not evidenced in the source ecosystem are left unknown, not defaulted.

- objective: >-
    Evaluate the published LC-MLP v1 fixed-feature MLP checkpoint (per-atom
    cohesive energy of Lennard-Jones clusters) over the re-serialized
    24-cluster descriptor table and reproduce the reported metric band
    (MAE ~0.031 eV/atom, RMSE ~0.045 eV/atom). No retraining is involved.
- source relationship: `paper_faithful` (allowed: paper_faithful,
  benchmark_adaptation).
- execution class: `local_sandbox` — no site-bound resource claims.
- Runnable Draft admission basis: >-
    The first Discovery run is authorized only after the L1 gate
    (`check_discovery_runnable.py`) derives `runnable_draft` and records a
    `runnable_derivation` record in `VALIDATION.json`. Draft admission means
    the technical chain is sound; it is not a scientific verdict and never
    asserts `benchmark_valid`.
- Discovery evidence and classifier output: >-
    The first real Discovery run (smoke-class profile) packages the candidate
    bundle in the local sandbox, mounts the sealed verifier
    (`/tests` + sealed root + `/logs/verifier/result.json`), records the run
    record as durable evidence (source commit, bundle digest, runtime/site
    identity, logs, artifacts, `result.json`), and classifies with the
    repository failure taxonomy: SOURCE_BLOCKED → REJECT,
    INFRA/CASE_DESIGN/RUNTIME/RESOURCE_BLOCKED → REFINE, else → PROMOTE.
- public/hidden boundary: >-
    Visible: `public/**`, this instruction, `task.toml`, this contract.
    Hidden: case design, validation state, verifier plan, reference and
    solution artifacts, tests/tools, evidence, profiles, evaluator manifest.
    The author-reported held-out set (LJ-25..LJ-32) is not part of the
    provided table and its assembly/threshold freeze is deferred release work.
- verifier outcome contract: >-
    Sealed verifier emits `VALID_RESULT`/`PASS`, `AGENT_FAILURE`
    (`NO_SUBMISSION` | `INVALID_SUBMISSION` | `SCIENTIFIC_FAIL`), or
    `INFRA_INVALID` (`HARNESS_FAILURE` | `VERIFIER_FAILURE`), each against the
    common result schema, with V-layer attribution in `reason`. MLP-V4
    (hidden static accuracy) is mandatory for this case kind and selected
    now; its science checks are deferred at the Discovery MVP, so admission
    grades the technical chain plus honest metric reporting only.
- submission roles: `evaluator`.
- acceptance basis: >-
    Design metric bounds `mae_ev_atom` `< 0.10` eV/atom and `rmse_ev_atom`
    `< 0.10` eV/atom, to be double-threshold-calibrated against an executed
    reference before any scientific verdict is attached. Current
    `benchmark_valid=false`.
- Reference/Solution state: `planned` (reference JSON state `planned`,
  thresholds `draft`). No executed reference exists yet.