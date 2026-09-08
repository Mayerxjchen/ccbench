---
name: paper-reproduction
description: Reproduce a paper's scientific result from its evidence and provenance, freeze a reproduction contract, run and classify each attempt, diagnose discrepancies in a fixed order, reach an information-consistent verdict, and export a CCBench case authoring draft. Use for any task that asks "does this paper's claim reproduce, and can it become a benchmark case". Maintainer-side skill; deterministic lifecycle transitions are owned by ccbench.builder.
---

# paper-reproduction

Turn a paper (+ SI, linked data, author code) into an **auditable
reproduction** and, when the science holds, a **CCBench case authoring draft**.
This skill is one strict pipeline; every intermediate artifact is deterministic
and hash-bound. It never tunes toward a hidden target and it never fabricates
benchmark lifecycle state — that last step belongs to `ccbench.builder`.

```text
论文 / SI / 作者代码
        ↓
证据提取与 provenance            references/evidence-and-provenance.md
        ↓
reproduction-contract.yaml      templates/reproduction-contract.yaml
        ↓
Contract Freeze                 references/contract-freeze.md
        ↓
执行 DAG                        references/research-state.md
        ↓
run-001 / run-002 ...           templates/run-record.yaml
        ↓
技术验证与论文对比               templates/comparison-table.md
        ↓
差异诊断                        references/discrepancy-protocol.md
        ↓
最终复现裁决                    references/contract-freeze.md (verdict)
        ↓
CCBench case authoring export   references/case-authoring.md
```

## What this skill is for

Decide, from evidence, one of:

- **The claim reproduces.** Proceed to case authoring.
- **The claim reproduces only under reconstruction.** State what had to be
  inferred and why (L2/L3).
- **The claim does not reproduce.** Diagnose and record the verdict; a
  contradicted reproduction is a *finding*, not a failure to delete.
- **The reproduction is not a benchmark case.** A valid verdict does not
  automatically mean a publishable case.

## Roles and trust boundary

- The **agent** is the reasoning driver: it extracts evidence, drafts the
  contract, plans the DAG, records runs, compares, and diagnoses.
- Deterministic rules (freeze, binding, classification, verdict grammar) live
  in `scripts/validate_reproduction.py` and the schemas under `schemas/`.
  The agent does **not** hand-edit frozen state or invent lifecycle status.
- Formal case transitions (`intake → design → build → validate → discovery →
  release-check → publish`) run through `ccbench.builder` / `ccbench case …`.
  This skill only produces the **input draft** (`benchmark-export.md` →
  `source/intake.json`, `design/case.ir.yaml`, `sources.lock.json`), never a
  forged `cases/<id>` object.

## The pipeline

### 1. Evidence intake and provenance

Collect the paper, its SI, linked datasets, and author code. Record *what the
claim is* and *where each piece of evidence came from* before any computation.
See `references/evidence-and-provenance.md`. No evidence → no contract.

### 2. Write the reproduction contract

Fill `templates/reproduction-contract.yaml`. It declares scope and non-goals,
the target claim, the method fingerprint, assumptions, the observable, the
comparison rule, the acceptance criterion with tolerance and units, and every
input file with its hash. Drafts may be incomplete; **frozen** contracts must
be complete. See `references/contract-freeze.md`.

### 3. Freeze the contract

`uv run scripts/validate_reproduction.py contract --freeze reproduction-contract.yaml`
Freezing is the moment the claim, method, and acceptance rule stop changing for
a given line of work. A new assumption or a changed target is a **new contract
version**, never a silent edit to a frozen one.

### 4. Plan and run

From the frozen contract build an execution DAG (see
`references/research-state.md`). Each run writes a `run-record.yaml` bound to
the contract version and hash. Every later run declares a reason:
`initial`, `faithful_fix`, `gap_closure`, or `sensitivity_test`. There is no
`tune_to_target` route: a run whose only purpose is to make the number match
is rejected.

### 5. Compare technically

Build `comparison-table.md` (computed observable, unit, tolerance, paper
value, evidence anchor) from the actual outputs. Scheduler or engine
termination is not evidence; the comparison is on parsed physical quantities.

### 6. Diagnose discrepancies in order

When a run does not land inside the acceptance band, follow the fixed order in
`references/discrepancy-protocol.md`:

```text
extraction → convergence → provenance → method → structure
→ sampling → implementation → underspecification → contradiction
```

Skip only with a recorded reason. Diagnosis determines the *next* run reason —
it is what turns a failed attempt into `faithful_fix` or `gap_closure` instead
of blind retrying.

### 7. Reach a verdict

Assign an **information level** and an independent **final verdict**. These are
two separate axes and the validator forbids converting one into the other.
Valid values are enumerated in `scripts/validate_reproduction.py`; unknown
values are invalid.

### 8. Export to case authoring

Only a reproducible (or equivalently reconstructed) claim proceeds. Build
`benchmark-export.md`, then produce the builder intake draft exactly as
`references/case-authoring.md` prescribes. Confirm the export leaks no private
ground truth before it leaves this workspace.

## Hard guardrails

- **Never tune to target.** No run reason named `tune_to_target`; no adapting
  thresholds or inputs to make a hidden value match. Freeze before you look.
- **Never mutate a frozen contract.** Change of scope → new version; the old
  version and hash remain as audit record.
- **Never conflate the two verdict axes.** information level
  (`L1_full_reproduction`, `L2_workflow_reconstruction`,
  `L3_underspecified_reproduction`) is about what the paper supplied;
  verdict (`reproduced`, `scientifically_equivalent`, `partially_reproduced`,
  `reconstructed_result`, `inconclusive`, `contradicted`) is about what your
  runs showed. Neither implies the other.
- **Never fake lifecycle state.** A benchmark export is a *draft input*; only
  `ccbench.builder` transitions a case through intake/design/build/validate.
- **Isolate evidence.** Public inputs, private ground truth, and reference
  material are classified and separated before anything is exported.
- **A run without a bound contract is not a run.** Every `run-record.yaml`
  must reference a frozen contract version + contract hash.
- **Paper evidence stays anchored.** Approximations or proxies are labeled
  `benchmark_adaptation`, never passed off as published values.

## What is deliberately out of scope

- Actually submitting scheduler jobs or managing compute (see the `hpc-submit`
  skill for execution and run records at the job level).
- Authoring a case for a claim you have not reproduced or equivalently
  reconstructed.
- Rewriting history: past runs, frozen locks, and existing experiment evidence
  are audit records and are never edited to match this pipeline.
