# Contract Freeze

The reproduction contract is the single frozen statement of *what a line of
work claims and how it will be judged*. Everything downstream — every run
record, comparison, diagnosis, and verdict — binds to one contract version and
its freeze hash. The authoritative behaviour described here is implemented in
`scripts/validate_reproduction.py`; this reference states the rules the agent
applies when writing and freezing a contract.

## The contract field set

Fill `templates/reproduction-contract.yaml`. Top-level fields:

| Field | Required when frozen | Meaning |
|---|---|---|
| `schema_version` | yes | contract schema version, e.g. `1.0` |
| `contract_version` | yes | monotonically increasing per line of work, starts `1` |
| `paper_identity` | yes | DOI + short citation of the reproduced paper |
| `evidence_sources` | yes | each evidence anchor with id, description, classification, sha256 (see `evidence-and-provenance.md`) |
| `information_level` | yes | `L1_full_reproduction`, `L2_workflow_reconstruction`, or `L3_underspecified_reproduction` |
| `reproduction_scope` | yes | what this line of work does reproduce |
| `non_goals` | yes | what it deliberately does not reproduce |
| `target_claim` | yes | the falsifiable claim, restated from evidence |
| `method_fingerprint` | yes | code versions, package pins, reference inputs that fix the method |
| `assumptions` | yes | every assumption made where the paper was silent |
| `observable` | yes | the measured quantity and units, e.g. `ΔE_ads(Cu(111))` in eV |
| `comparison_rule` | yes | how computed output becomes the observable (parsing/derivation) |
| `acceptance_criterion` | yes | the rule deciding reproduced vs not |
| `tolerance` | yes | numeric band with units against the paper value |
| `input_files` | yes | each input with path, role, sha256 |
| `freeze_hash` | set by freeze | SHA-256 over the frozen contract bytes |

## Validation behaviour (do not hand-argue, run the script)

`uv run scripts/validate_reproduction.py contract <path>` returns one of:

- **`VALID DRAFT`** — exit code `0`. The file is a well-formed draft that may
  be missing optional fields. Missing fields are reported as **warnings**, not
  errors. A draft may not yet be bound to runs.
- **`VALID FROZEN`** — exit code `0`, produced by an explicit `--freeze`. All
  required fields are present; the freeze hash is computed and stored.
- **`INVALID`** — nonzero exit code. Either a *freeze attempt on a draft that
  is missing a required field*, or a *frozen file whose content no longer
  matches its stored freeze hash* (tamper), or a field holding a value outside
  an enumerated domain (e.g. an unknown `information_level` or `verdict`).

Behavioural requirements enforced here:

- draft missing fields → exit `0`, `VALID DRAFT`, warnings listed.
- complete draft → exit `0`, `VALID DRAFT`.
- freeze with a missing critical field → nonzero, `INVALID`.
- complete freeze → `VALID FROZEN` with freeze hash.
- content change after freeze → hash check fails, `INVALID`. Fix by writing a
  **new contract version**, never by editing the frozen bytes.
- unknown `information_level` / `verdict` / run reason → `INVALID`.

## Versioning rule

Freezing is a commitment, not a checkpoint you revise in place. If during the
work you discover a new assumption or decide to change the target, scope, or
acceptance rule, write `contract_version: <n+1>` as a new contract file. The
old version and its freeze hash remain on disk as audit record. Runs bind to
the version that was in force when they were planned.

## Binding runs to the contract

Every `run-record.yaml` must carry the `contract_version` and `freeze_hash`
(now the contract's frozen hash) it was executed under. A run record whose
stored hash does not match the current frozen contract of that version is
`INVALID` — it cannot be compared, diagnosed, or counted toward a verdict.

## Two axes, never merged

- **information level** describes the paper's supplied material:
  - `L1_full_reproduction` — paper + SI gave enough to reproduce without
    inference;
  - `L2_workflow_reconstruction` — method steps had to be reconstructed from
    incomplete prose;
  - `L3_underspecified_reproduction` — key inputs/parameters are absent and
    had to be chosen.
- **verdict** describes what your runs showed:
  - `reproduced`
  - `scientifically_equivalent`
  - `partially_reproduced`
  - `reconstructed_result`
  - `inconclusive`
  - `contradicted`

The validator will **not** auto-convert one into the other. A full-information
paper may be `contradicted`; an underspecified one may still be `reproduced`
under stated assumptions. Assign both explicitly.

## Run reasons

Each run after the first declares why it exists:

```text
initial            first run of the bound contract
faithful_fix       fix a faithful-execution defect found in diagnosis
gap_closure        supply a missing parameter/step the paper left silent
sensitivity_test   probe sensitivity to an assumption under the contract
```

There is deliberately **no** `tune_to_target`. A run whose only justification
is making the number match a hidden value is not a scientific run and is
rejected. Retries after scheduler/engine failure belong to the `hpc-submit`
attempt lifecycle and are not new scientific runs.
