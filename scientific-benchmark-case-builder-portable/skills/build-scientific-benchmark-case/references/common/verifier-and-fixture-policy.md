# Verifier and Fixture Policy

The category proposes applicable outcomes; Common Core requires evidence,
negative coverage, and alternative validity. Verifier layers are selectable
and tested independently of category science.

## Layers

Category layers are capability-driven and conditional:

```text
MLP-V0 system/submission identity
MLP-V1 data/label provenance
MLP-V2 model authenticity
MLP-V3 iterative workflow integrity (conditional)
MLP-V4 hidden static accuracy
MLP-V5 hidden dynamic stability (conditional)
MLP-V6 hidden physical observable (conditional)
```

Common layers are always on and named independently of category science:

```text
C-V7 resource and provenance compliance
C-V8 submission integrity, filesystem safety, and manifest contract
```

`derive_verifier_plan.py` derives the layer set from `case-design.yaml`
capabilities and writes the plan; unknown capability names fail.

## Fixture matrix closure

A hard-outcome layer (one that scores an outcome) requires:

- one positive expert fixture;
- at least one alternative-valid fixture;
- one negative fixture per hard-outcome layer.

`generate_fixture_matrix.py` verifies closure against the derived plan.
Gaming fixtures appropriate to the category supplement the matrix (see
`public-hidden-boundary.md`).

## No expert-path scoring

Alternative-valid fixtures change internal layout, valid method choice, or
stage count while preserving outcomes. Exact expert directory names and
scripts are never required by the verifier. The instruction states outcomes
abstractly; scoring must depend on outcomes, not on matching the expert
implementation's file layout.
