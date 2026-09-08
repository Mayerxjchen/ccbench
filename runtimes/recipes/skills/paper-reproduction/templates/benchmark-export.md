# Benchmark export — <slug>

Builder **input draft**. This document is what a maintainer hands to
`ccbench.builder`; it never writes lifecycle state and never enters `cases/`.
Deterministic validation: `uv run scripts/validate_reproduction.py export <path>`
— an export that leaks private/gold ground truth into a candidate-visible
section is INVALID.

## 1. Source classification summary

| artifact | classification | candidate-visible? | sha256 |
|---|---|---|---|
|  | PUBLIC_SOURCE / MAINTAINER_SOURCE / GOLD_SOURCE / REFERENCE_SOURCE | yes/no |  |

GOLD_SOURCE / private entries listed here must appear ONLY in hidden verifier
/ reference sections and must NOT be referenced by candidate-visible inputs.

## 2. Claim and observable (paper_faithful anchor)

- Contract version(s) this export derives from: vN
- Claim: <from contract target_claim>
- Observable + units: 
- Reported value + evidence anchor (paper/DOI/table/fig):
- Verifier thresholds (frozen before any formal candidate result; equal to the
  contract acceptance tolerance, unchanged): 

## 3. benchmark_adaptation markers (if any)

Any approximated / re-scaled / simulated / proxied value is listed here and
explicitly labeled `benchmark_adaptation`. None of these may masquerade as
published evidence.

## 4. Candidate draft spec (maps onto Case IR)

- case title / category / version:
- candidate instruction (phrased from the paper's own ask, never from hidden
  answers):
- candidate inputs (PUBLIC/MAINTAINER only) with descriptions:
- submission artifacts (root under `final/`):
- runtime: execution_class, images, timeout_sec, gpus:
- verification primitives + layer model:
- thresholds + tolerance + units:

## 5. Builder handoff

Run, in order, under `ccbench.builder`:

```text
ccbench case intake   # generates source/intake.json + sources.lock.json
ccbench case design   # Case IR SSOT
ccbench case build    # task.md / case.toml / verifier/
ccbench case validate # RUNNABLE_DRAFT smoke
```

Failure attribution taxonomy applies to anything that fails downstream
(SOURCE_BLOCKED / RUNTIME_BLOCKED / RESOURCE_BLOCKED / CASE_DESIGN_BLOCKED /
INFRA_INVALID / AGENT_LIMITATION); a discovery failure is not automatically an
agent failure. A reproduction verdict (`reproduced`/`contradicted`) is not a
prediction of candidate agent success.
