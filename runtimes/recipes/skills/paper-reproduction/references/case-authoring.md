# Case authoring (maintainer)

This reference absorbs the necessary rules of the retired
`build-scientific-benchmark-case` skill. It governs the *last* pipeline stage:
turning a reproduced (or equivalently reconstructed) claim into the **input
draft** of a CCBench builder case. Formal lifecycle state is never written
here — only builder input drafts, which `ccbench.builder` then transitions.

## What this stage may produce

A successful authoring export writes exactly the builder **input drafts**:

```text
reproduction/<slug>/export/
├── benchmark-export.md        (the human/LLM handoff: what was reproduced, how)
└── (when the builder consumes drafts directly)
    source/intake.json
    design/case.ir.yaml
    sources.lock.json
```

Everything else in `cases/<case-id>` — `task.md`, `input/`, `verifier/`,
`case.toml`, canonical `case.ir.yaml`, discovery classification, release
audit — is produced only by `ccbench.builder` running
`intake → design → build → validate → discovery → release-check → publish`.
This stage never forges that lifecycle state, and never writes into `cases/`.

## Reproduction → Case IR mapping

Map the frozen reproduction artifacts onto the Case IR fields
(`design/case.ir.yaml`, schema in the builder package):

| Case IR block | From the reproduction |
|---|---|
| `identity.title` / `case_id` / `category` / `version` | export metadata; category must be a production-supported category |
| `scientific_target.system` / `objective` / `observable` | contract `target_claim` + `observable` |
| `candidate.instruction` | what a future candidate is asked to do — phrased from the paper's own ask, never from hidden answers |
| `candidate.inputs` | only inputs classified PUBLIC_SOURCE or MAINTAINER_SOURCE; **never GOLD_SOURCE** |
| `submission.root` / `artifacts` | the concrete artifacts a candidate must hand back |
| `runtime.execution_class` / images / `timeout_sec` / `gpus` | honest sizing from the run records (an expensive reproduction becomes an expensive case unless re-scoped) |
| `verification.layer_model` / `layers` / `primitives` | derived from the acceptance criterion and observable — the verifier checks the same physical quantity the contract compared |
| `verification.thresholds` | the frozen acceptance tolerance with units, carried over **unchanged** from the contract |

## paper_faithful vs benchmark_adaptation

- **paper_faithful**: every observable, structure, and reference value the case
  exposes to candidates is anchored to the cited paper / SI / DOI from the
  contract's `evidence_sources`.
- **benchmark_adaptation**: any approximated, re-scaled, simulated, or proxied
  value (e.g. a reduced system, a cheaper surrogate, a re-derived unit) is
  explicitly labeled `benchmark_adaptation` in the intake/design draft and
  never presented as published evidence.
- No fabricated evidence, ever. A proxy wears its label.

## public / private / gold isolation

Source classification is per artifact (see `evidence-and-provenance.md`):

- **PUBLIC_SOURCE / MAINTAINER_SOURCE** — may appear in candidate-visible
  inputs and `task.md`.
- **GOLD_SOURCE** — reference answers and hidden verifier material. Must never
  appear in, or be derivable from, candidate-visible files. Verifier checks and
  hidden references stay strictly outside the candidate workspace.
- **REFERENCE_SOURCE** — author code/tooling; license-aware; usable as method
  reference but not silently copied into `input/` without provenance.

The validator flags a `benchmark-export.md` as **INVALID** if it leaks a
private / gold ground-truth value or file into the candidate-visible portion
(see `scripts/validate_reproduction.py export`).

## Threshold independence (freeze before you look)

Verifier thresholds are carried from the contract acceptance criterion, which
was frozen **before** any candidate (formal agent) transcript or outcome was
inspected. `threshold-freeze.json` in the builder workflow records
`formal_agent_results_seen: false`. Adapting benchmark thresholds to
accommodate a specific agent's performance is data contamination and is
fail-closed rejected.

## Failure attribution

A failed or low-scoring run in discovery is **not** automatically an agent
failure. Attribute by taxonomy before blaming anything:

- `SOURCE_BLOCKED` — missing paper data, ambiguous units, corrupted raw inputs.
- `RUNTIME_BLOCKED` — package version incompatibilities, missing libraries.
- `RESOURCE_BLOCKED` — OOM, scheduler timeouts, disk exhaustion.
- `CASE_DESIGN_BLOCKED` — ambiguous prompt, impossible threshold bounds.
- `INFRA_INVALID` — network disconnect, gateway crash, host platform error.
- `AGENT_LIMITATION` — legitimate benchmark failure, reserved for when
  environment, inputs, and verifiers were all valid.

## Reproduction verdict ≠ benchmark agent success

`reproduced` / `contradicted` (the reproduction's verdict axes) describe
whether the paper's claim holds under your runs. They do **not** predict, and
must never be used to pre-judge, how a future candidate agent will score on the
derived case. Two separate questions:

1. Did the paper's claim reproduce? (this skill, verdict in the report)
2. Can an agent, given only the case's public inputs, reach the frozen
   threshold? (the benchmark's question, answered only by formal runs)

A verdict that says "the science holds" licenses authoring a case draft; it
never licenses engineering the case so that an agent must pass or fail.

## Execution authorization boundaries

Authoring is an offline reasoning stage. External downloads/pulls, container
image builds/pushes, expensive calculations or training (>60 s), and real HPC
dispatches are not initiated from here without explicit maintainer
authorization. Computing the reproduction itself is the earlier pipeline
stages' job and is governed by the `hpc-submit` skill.
