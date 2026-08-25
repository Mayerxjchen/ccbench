# MLP Full-workflow Prompt Contract

Load this when designing or validating an MLP case whose scope includes data
generation, training, active learning, or physical validation.

## Source relationship

Declare one relationship:

- `paper_faithful`: system and method claims are supported by cited evidence;
- `benchmark_adaptation`: benchmark-authored systems or scope use an explicitly
  identified method from the source.

Never present inferred topology or benchmark-authored values as paper facts.

## Required Prompt slots

For full data generation and training, state:

- every target system and binding composition, cell, chemistry, charge/spin,
  and boundary-condition constraint;
- that every target system, or each explicitly enumerated distinct chemical
  environment, contributes first-principles labels before acceptance;
- the reference-method fingerprint and that changing it starts a new dataset;
- an **executed** train -> explore -> screen -> label -> grow -> retrain cycle;
- quantitative active-learning stopping criteria recorded before convergence
  is judged;
- validation/test grouping by independent trajectory or configuration source,
  not random adjacent frames;
- energy/force metrics, stability checks, and each scored physical observable;
- whether published coordinates, labels, training datasets, and models may be
  used. A from-scratch task forbids published coordinates and scientific labels
  as workflow inputs while permitting cited methodology evidence;
- initial-structure artifacts separately from the labeled dataset, which also
  carries cells, energies, forces, types, and conversion provenance;
- required executed jobs, failures/retries, software versions, seeds, model
  lineage, retained trajectories/analysis, and a machine-readable manifest.

The Prompt defines outcomes and evidence, not an expert directory layout.
Numeric thresholds come from case evidence and calibration, not this policy.
