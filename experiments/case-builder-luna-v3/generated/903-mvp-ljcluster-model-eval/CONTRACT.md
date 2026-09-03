# Benchmark Case Contract

- objective: Evaluate the published LC-MLP v1 checkpoint on the
  re-serialized 24-cluster Lennard-Jones descriptor table — apply the
  documented standardization, run inference per cluster, and check the
  reproduced model-level metric band against the paper's reported
  Table-1 range. No retraining is implied.
- source relationship (`paper_faithful` or `benchmark_adaptation`):
  paper_faithful.
- execution class: local_sandbox (no containers, no HPC; site_specific
  false — see runtime.yaml).
- Runnable Draft admission basis: L1 is derived solely by
  `check_discovery_runnable.py --derive-state` (the gate's own verdict,
  recorded in MVP-READINESS.json and case_status); this contract never
  hand-writes the state. At build time case_status is `draft`.
- Discovery evidence and classifier output: none yet — no Discovery run
  has been executed. Planned first run per DISCOVERY-RUNBOOK.md
  (smoke profile); failure triage via the Skill's classify_failure
  follow-up. `classify_failure` has produced no output for this case.
- public/hidden boundary: candidate-visible bundle is `public/**`
  (eval-table.csv, lc-mlp-v1.txt, eval-config.yaml, system.json,
  submission-schema.json, input-manifest.json) plus instruction.md and
  task.toml staging per [candidate].files. Hidden and never staged:
  CONTRACT.md (contract.candidate_visible=false), reference/**,
  tests/**, profiles/**, and the verifier-owned held-out set
  `lj-holdout-25-32` (owner verifier_hidden; the author-retained rows are
  restricted per the reproduction spec access record and were never read
  by the builder — leave-one-out intake, sources.lock "excluded").
  Paper-reported values (Table-1 MAE/RMSE) live only in hidden spec/
  contract material, never in the instruction or bundle.
- verifier outcome contract: sealed submission root "." carries
  manifest.json per public/submission-schema.json;
  `/logs/verifier/result.json` reports class
  VALID_RESULT | SCIENTIFIC_FAIL | INVALID_SUBMISSION | NO_SUBMISSION |
  INFRA_INVALID with layer-tagged reasons. verifier-plan.yaml selects
  MLP-V0 identity, MLP-V1 provenance, MLP-V2 model authenticity,
  MLP-V4 hidden static accuracy (mandatory, hard-outcome), C-V7 runtime
  receipts, C-V8 integrity; the packaged template verifier additionally
  executes the V3 manifest-lineage structural gate (contiguous rounds
  from 1) as part of its chain. MLP-V4's scientific comparison against
  held-out targets is explicitly deferred at Discovery MVP ("deferred"
  appears in the result reason string); MLP-V5/V6 (trajectory/property)
  are not applicable to this static model_evaluation kind.
- submission roles: `student_model` (byte-identical copy of the
  evaluated public checkpoint); `dataset` (input-table copy with a
  source provenance string and frames count); `report` (predictions
  table, metrics summary); runtime_receipts record the real local
  execution (job_id, backend, success exit_status).
- acceptance basis: technical chain passes end-to-end on the sealed
  submission; draft metric band on the provided table
  mae_ev_per_atom < 0.05, rmse_ev_per_atom < 0.075 eV/atom (status:
  draft — thresholds.json remains `draft` until calibration is executed,
  which is not authorized at this stage); hidden-set accuracy becomes a
  hard outcome only at release qualification (L2 via check_release.py).
  Open blocker carried honestly: blk-inference-serialization
  (implementation.entrypoint unknown) — must be pinned with the source
  owner before any formal, accuracy-bearing run; the blocker does not
  change the L1 technical-chain outcome.
- Reference/Solution state (`planned`, `deferred`, or evidence-backed
  state): reference.json state `planned` with empty metrics/lineage
  (valid only at `planned`); thresholds.json `draft` produced_by null;
  evaluator-manifest not frozen; solution/expert/ holds the placeholder
  README only. No reference or expert evidence has been produced yet —
  everything on this axis is planned, not executed.
- benchmark_valid.json: false — L2 is only ever derived by
  check_release.py; nothing in this draft asserts scientific validity.
