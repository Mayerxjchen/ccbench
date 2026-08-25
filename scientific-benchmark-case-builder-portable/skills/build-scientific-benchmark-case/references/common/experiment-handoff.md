# Experiment Handoff

Experiment packets are produced only from independently valid cases. No
experiment scaffolding is created during case construction.

## Gate

- `experiment-handoff` mode is available only for cases whose
  `benchmark_valid.json` reports `benchmark_valid: true` (derived by
  `check_release.py`). Anything else is refused.
- Pilot/formal experiment files are added only in `experiment-handoff` mode,
  never during scaffold or construct.

## Frozen experiment identity

An experiment packet freezes, by recorded digest/version:

- case (case-design.yaml, VALIDATION.json, benchmark_valid.json)
- instruction and public/ (the Candidate bundle)
- Skill treatment (skill version, mode invocation)
- Agent/model and its version
- runtime identity and resource/platform profiles
- Verifier and evaluator manifest digest
- sealed evidence (evidence/manifest.json objects)
- failure taxonomy (what counts as correctness vs infrastructure invalidity)

Any change invalidates the packet; packets are frozen, not edited.

## Pilot and formal separation

- `ablation/` is never created during case construction; ablation experiment
  files exist only under experiment-handoff.
- Pilot results are excluded from formal statistics. Formal results are computed
  only from frozen formal experiment packets.
