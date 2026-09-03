# CIPS active-distillation case contract

- objective: Execute teacher-labelled CIPS DeePMD distillation with an active
  explore/select/label/grow/retrain loop and independent held-out evaluation.
- source relationship: `paper_faithful`, with benchmark-authored audit and
  runtime contracts explicitly identified as adaptation.
- execution class: `hpc_controller`; the agent controls a real scheduler and
  fetches outputs, while local checks are only preflight/diagnostic work.
- Runnable Draft admission basis: public system and teacher assets are
  recovered and hashed; instruction, task, submission, lineage, and verifier
  contracts are coherent. Expert/reference and threshold work is deferred.
- Discovery evidence and classifier output: no Discovery run was authorized or
  executed; classification is `pending` (no run record to classify).
- public/hidden boundary: only `public/`, `instruction.md`, `task.toml`, and
  this contract are candidate-visible. Reference, solution, tests, tools,
  profiles, evidence, design, and validation files are hidden and physically
  absent from a positive-allowlist candidate bundle.
- verifier outcome contract: V0 identity; V1 provenance and no train/test
  overlap; V2 loadable student/committee authenticity; V3 closed iterative
  lineage; V4 hidden held-out energy/force outcomes; C-V7 resource/provenance
  compliance; C-V8 filesystem and manifest integrity. Every hard outcome has
  positive, alternative-valid, and negative fixture coverage planned.
- submission roles: primary student, uncertainty committee, and complete
  lineage/failure manifest. Internal layout is open.
- acceptance basis: target composition and teacher identity; separate seeded
  held-out trajectory; at least one closed active round; finite selected and
  teacher-labelled frames; draft force-MAE target ≤0.10 eV/angstrom; metrics
  independently recomputed by the verifier. Numeric bounds remain provisional
  until calibration.
- Reference/Solution state: `planned`/`deferred`; no reference run, expert
  solution, threshold freeze, hidden set, or formal evidence is claimed.
