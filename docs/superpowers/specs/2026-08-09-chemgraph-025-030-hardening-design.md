# ChemGraph 025–030 Benchmark Hardening Design

## Goal

Make cases 025–030 fair, reproducible, and auditable by aligning every public
instruction with the behavior enforced by its hidden evaluator, while preserving
the pinned ChemGraph scientific references.

## Scope

This change covers only:

- `025-name2opt-so2`
- `026-name2vib-water`
- `027-name2gibbs-co2`
- `028-name2file-so2`
- `029-react2enthalpy-methane`
- `030-react2gibbs-ammonia`
- root-level regression tests and validation metadata for those six cases

The numerical reference values, pinned image digests, public molecular inputs,
and source provenance remain unchanged.

## Public Computational Contract

### Geometry convergence

Every instruction that requires an optimized geometry will state this mandatory,
observable condition:

> The delivered optimized geometry must satisfy
> `max_i ||F_i|| < 0.01 eV/Å` under the specified calculator and model.

ASE BFGS with `fmax=0.01` and at most 1000 steps remains a recommended reference
workflow, not a mandatory algorithm. Scientifically equivalent optimizers such as
LBFGS remain valid when their delivered geometry satisfies the force criterion.

### Method identity

The documented output values are canonical:

- MACE cases: `"mace_mp / medium-mpa-0"`
- xTB cases: `"GFN2-xTB"`

Evaluators compare normalized values against these exact enumerations. Substring
matches such as `not-mace / not-medium` and `not-gfn2 / not-xtb` must fail.

### 029 fidelity exception

Case 029 continues to require `spin=0` for every species, including O2, solely to
reproduce the pinned ChemGraph workflow. The instruction explicitly distinguishes
this locked setting from the physically preferred triplet ground state of gas-phase
O2. The optimizer implementation is not locked; only the final force criterion is.

### Runtime instructions

Agent-facing instructions use `/app/.venv/bin/python` directly. They do not require
the non-POSIX `source` builtin, because pagent commands execute under `sh` by default.

### XYZ artifact format

All oracle-produced `.xyz` artifacts use strict four-column XYZ atom records:

```text
Element x y z
```

No force, charge, or calculator-result columns are emitted. Evaluators continue to
accept chemically valid standard XYZ files from alternative implementations.

### Network isolation

All six cases set `allow_internet = false`. Their calculators, model weights, and
Python dependencies are already bundled in the pinned images, so network access is
unnecessary and would create a reference-leakage path after publication.

## Evaluator Changes

The MACE evaluators for 025, 026, and 028 compute convergence with:

```python
float(np.linalg.norm(atoms.get_forces(), axis=1).max())
```

This matches ASE's atom-wise `fmax` definition. The xTB evaluators already use this
definition and remain unchanged.

All six evaluators enforce canonical method identity. Independent calculator
recomputation, exact result schemas, force checks, thermochemical identities, mode
checks, and frozen-reference comparisons remain in place.

## Regression Strategy

A root-level contract test will inspect all six cases and fail when any of these
conditions regress:

1. instructions omit the mandatory atom-wise force threshold;
2. instructions use `source /app/.venv/bin/activate`;
3. task configuration enables internet access;
4. evaluator method checks use loose substring matching;
5. a MACE evaluator uses maximum Cartesian component instead of atom-wise norm;
6. xTB oracle scripts write extended XYZ by omitting `format="xyz"`;
7. 029 requires BFGS while claiming LBFGS is a valid alternative;
8. `VALIDATION.json` and `benchmark_valid.json` disagree on smoke status.

Behavioral negative fixtures will additionally run completed oracle artifacts with
forged method strings and require evaluator failure. Existing hard-coded-answer and
wrong-geometry checks remain applicable.

## Verification

Completion requires fresh evidence from:

1. root isolation and contract tests;
2. syntax and JSON validation;
3. clean Docker oracle runs for all six cases;
4. forged-method negative fixtures for MACE and xTB;
5. a fresh pagent agent smoke run for 029 using the revised instruction;
6. validation metadata updated only from the observed results.

The six temporary audit images must be removed after verification. No reference
number is changed unless a fresh regeneration unexpectedly disproves the pinned value;
such a discrepancy would stop the work for investigation rather than widening a
tolerance.

## Success Criteria

- All six clean oracle runs receive reward 1.
- Forged method fixtures receive reward 0.
- Root tests pass with no contract violations.
- 029 receives reward 1 in the authorized fresh pagent smoke run.
- Validation files consistently reflect the new regression evidence.
- No hidden asset becomes visible in an agent workspace.
