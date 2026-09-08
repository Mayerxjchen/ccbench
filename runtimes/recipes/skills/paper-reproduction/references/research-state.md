# Research state and execution DAG

After the contract is frozen, plan the actual computation as an explicit
directed graph of steps whose nodes are reproducible commands and whose edges
are data dependencies. This reference fixes how that plan, its execution, and
its recorded state are kept deterministic and auditable.

## Workspace layout

A line of work lives under one directory, e.g. `reproduction/<slug>/`:

```text
reproduction/<slug>/
├── reproduction-contract.vN.yaml   (frozen contract per version; v1, v2, …)
├── plan.md                         (the execution DAG, one section per node)
├── input/                          (inputs pinned by hash in the contract)
├── runs/
│   ├── run-001/
│   │   ├── run-record.yaml         (bound to contract_version + freeze_hash)
│   │   └── artifacts/              (raw engine outputs, untouched)
│   └── run-002/ …
├── diagnosis/
│   └── discrepancy-log.md
├── comparison/
│   └── comparison-table.md
├── report/
│   └── final-report.md
└── export/
    └── benchmark-export.md
```

A raw artifact directory is immutable once recorded: analysis scripts read from
it, they never rewrite it. If a parse is corrected, the corrected parse is a
new derived file, not an edit to the raw output.

## The DAG

Represent the plan as stages; each stage lists:

- **inputs** (paths; for external inputs, the sha256 from the contract),
- **command** (the exact reproducible invocation, engine/container pinned by
  the method fingerprint),
- **outputs** (which files must exist before the next stage starts),
- **verification** (what makes the output trustworthy: convergence checks,
  stability windows, expected value ranges).

Typical scientific stages: structure generation → engine input assembly →
calculation → parse/converge → derive observable → compare. Compute-heavy
stages are executed through the `hpc-submit` skill (its attempt lifecycle, not
this skill's run record, owns scheduler retries).

## One scientific run = one run record

A **run** is one execution of a stage (or chain) under the frozen contract
that yields an observable to compare. It is distinct from a scheduler
*attempt*: a job that dies at the queue and is resubmitted unchanged is still
the same run, and its attempt count lives in the `hpc-submit` record. Fill
`templates/run-record.yaml` with:

- run id and the bound `contract_version` + `freeze_hash`;
- the DAG stage executed and the exact command;
- run reason (`initial` / `faithful_fix` / `gap_closure` / `sensitivity_test`);
- inputs actually used, with hashes;
- outcome status (e.g. `completed`, `failed`, `contaminated`);
- the observable parsed out, with units;
- pointer to the artifacts and the parse that produced it.

## State invariants

- **No run without a bound contract.** A record that cannot name a frozen
  contract version + hash it was executed under is invalid.
- **No rewriting.** A failed, superseded, or suspicious run keeps its record
  and artifacts; you add run-00N, you never edit run-00M.
- **State transitions are recorded, not fabricated.** If the agent is a
  candidate in a benchmark, benchmark lifecycle transitions (freeze of a
  submission, publish of a case) are owned by `ccbench.builder`; this skill's
  plan/run/verdict state is its own, and neither is forged for the other.
- **Determinism lives in modules.** Reproducible parsing/derivation logic is
  plain Python (or a pinned script), not one-off prose steps that cannot be
  re-run identically.

## From state to report

The report (`templates/final-report.md`) is written from the run records,
comparison table, and discrepancy log — never from memory. If a number in the
report cannot be traced to a run artifact and a parse step, the report is
unfinished.
