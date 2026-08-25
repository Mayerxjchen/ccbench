# MLIP Reproduction Schema

> Load this when constructing or reviewing `mlp-reproduction-spec.yaml`.

The schema is intentionally model-family-neutral. It describes the information needed to reproduce a machine-learning interatomic potential without assuming DeepMD, MACE, NequIP, Allegro, GAP, ACE, or any other specific implementation.

## Root contract

A v1 spec has these root fields:

```yaml
schema_version: 1
paper: {}
reproduction_scope: {}
target: {}
system: {}
dataset: {}
labels: {}
reference_method: {}
implementation: {}
model: {}
training: {}
validation: {}
access: {}
licenses: {}
```

`null` and `unknown` are preferable to guessed values. Reproduction-critical values should use the field-value shape below when provenance matters.

## Field-value shape

Use this shape for values that require evidence, units, or uncertainty tracking:

```yaml
cutoff:
  value: 6.0
  unit: angstrom
  claim_status: observed
  evidence:
    - ev-model-cutoff
```

Canonical `claim_status` values:

```text
observed
# directly stated or directly present in a version-matched artifact

derived
# deterministic computation/transformation from observed values

inferred
# interpretation that is not directly stated

unknown
# unresolved or unavailable

conflicting
# multiple relevant sources disagree and no resolution is justified
```

A plain scalar is allowed for low-risk metadata such as `schema_version`, but critical scientific or execution values should normally be evidence-bearing objects.

## `paper`

```yaml
paper:
  title:
  doi:
  arxiv:
  journal:
  year:
  authors:
  source_id:
```

At least one stable paper identity should be present when available.

## `reproduction_scope`

```yaml
reproduction_scope:
  target: final_model_retraining
  includes:
    - published labeled training data
    - target production-model training
    - model-level verification
  excludes:
    - regenerate active-learning dataset
    - reproduce downstream spectroscopy
  interpretation:
    claim_status: inferred
    evidence: []
```

Canonical targets:

```text
final_model_retraining
full_data_generation_and_training
published_model_execution
model_evaluation_only
training_recipe_recovery
reproducibility_screening
```

## `target`

Use a primary target plus related models.

```yaml
target:
  primary:
    reported_name:
    model_family:
    variant:
    role: production
    intended_use:
    evidence: []

  related_models:
    - reported_name:
      model_family:
      variant:
      role: data_generation
      required_for_scope: false
      evidence: []
```

Canonical roles:

```text
production
data_generation
committee
baseline
teacher
student
pretrained
fine_tuned
evaluation_only
unknown
```

## `system`

Describe the scientific applicability domain, not filesystem paths.

```yaml
system:
  elements: [C, H, O]
  compositions: []
  phases: []
  interfaces: []
  charge_states: []
  spin_states: []
  thermodynamic_conditions: {}
  chemical_scope:
    - bulk_water
    - graphene_water
```

Do not infer element types from model filenames alone when a dataset/config can be inspected directly.

## `dataset`

Separate scientific composition from physical files.

```yaml
dataset:
  scientific_scope:
    systems: []
    generation_summary: null

  inventory:
    total_frames:
      value: null
      claim_status: unknown
      evidence: []
    components: []

  splits:
    training: null
    validation: null
    test: null

  artifact:
    source_id: null
    format: null
    manifest: null
    paths: []
    content_hash: null

  data_generation:
    required_for_scope: false
    method: null
    active_learning: null
```

When a total is computed from reported components, mark it `derived` unless the source explicitly reports the total.

## `labels`

Represent every supervised target independently.

```yaml
labels:
  energy:
    present: true
    unit: eV
    claim_status: observed
    evidence: []
  forces:
    present: true
    unit: eV/angstrom
    claim_status: observed
    evidence: []
  virial:
    present: null
    claim_status: unknown
    evidence: []
  stress:
    present: null
    claim_status: unknown
    evidence: []
  charges:
    present: null
    claim_status: unknown
    evidence: []
  dipoles:
    present: null
    claim_status: unknown
    evidence: []
  other: []
```

Do not assume virials/stress merely because an MD ensemble used pressure control.

## `reference_method`

This is the label-generating electronic-structure or reference-theory fingerprint.

```yaml
reference_method:
  software:
  version:
  theory_level:
  xc_functional:
  dispersion:
  basis:
  pseudopotential:
  plane_wave_cutoff:
  k_points:
  charge:
  spin:
  scf:
  smearing:
  convergence:
  corrections:
  evidence: []
```

A method change that materially changes the labels should not be silently merged into one dataset fingerprint.

## `implementation`

Keep implementation identity separate from abstract model architecture.

```yaml
implementation:
  framework:
  backend:
  version:
  repository:
  commit:
  release:
  fork:
  entrypoint:
  environment:
  evidence: []
```

Examples include `deepmd-kit`, `mace-torch`, `nequip`, an author fork, or a custom JAX implementation.

## `model`

Keep the common layer generic.

```yaml
model:
  family:
  variant:
  architecture:
    cutoff:
    parameters: {}
  precision:
  initialization:
  evidence: []
```

Framework-specific parameters belong under `architecture.parameters`.

Deep-Potential-style example:

```yaml
model:
  family: deep_potential
  variant: message_passing
  architecture:
    cutoff:
      value: 6.0
      unit: angstrom
      claim_status: observed
      evidence: [ev-rcut]
    parameters:
      embedding_network: [48, 48, 96]
      message_passing_network: [96, 96, 96]
      fitting_network: [96, 96, 96]
```

MACE-style example:

```yaml
model:
  family: MACE
  architecture:
    cutoff:
      value: 5.0
      unit: angstrom
      claim_status: observed
      evidence: [ev-cutoff]
    parameters:
      num_interactions: 2
      hidden_irreps: 128x0e+128x1o
      correlation: 3
```

The schema does not require either model to expose the other's parameter names.

## `training`

```yaml
training:
  steps:
  epochs:
  optimizer:
  learning_rate:
    initial:
    final:
    schedule:
  batch_size:
  loss: {}
  seed:
  precision:
  checkpointing:
  initialization:
  data_binding:
    train_systems: []
    validation_systems: []
    test_systems: []
    sampling_weights: null
    shuffle: null
  evidence: []
```

If the original config is available, preserve the executable artifact path/identity in evidence rather than translating away important implementation-specific options.

## `validation`

Separate model-level numerical verification from trajectory and scientific-property validation.

```yaml
validation:
  model_level:
    dataset_or_split:
    metrics: []
    reference_values: []
    procedure:

  trajectory_level:
    conditions:
    metrics: []

  property_level:
    observables: []
    metrics: []
```

Typical model-level metrics include energy RMSE/MAE, force RMSE/MAE, virial/stress error, and correlation statistics. Do not require a specific metric universally.

For `final_model_retraining`, at least one reproducible model-level verifier is normally required for `verification_readiness: ready`.

## `access`

Availability and identity are distinct from license.

```yaml
access:
  paper:
    status: available
    source: null
  supporting_information:
    status: unknown
    source: null
  repository:
    status: unknown
    source: null
  dataset:
    status: unknown
    source: null
  pretrained_model:
    status: unknown
    source: null
```

Canonical statuses:

```text
available
restricted
unavailable
unknown
```

## `licenses`

```yaml
licenses:
  code:
    license: unknown
    redistribution: unknown
    evidence: []
  dataset:
    license: unknown
    redistribution: unknown
    evidence: []
  model:
    license: unknown
    redistribution: unknown
    evidence: []
  paper:
    license: unknown
    redistribution: unknown
    evidence: []
```

Unknown license information need not block private/internal reproduction, but it may prevent a public benchmark package from being release-ready.

## Anti-patterns

Do not encode:

```yaml
batch_size: 1  # guessed because it is common
```

Use:

```yaml
batch_size:
  value: null
  claim_status: unknown
  evidence: []
```

Do not encode:

```yaml
total_frames:
  value: 14140
  claim_status: observed
```

when the paper only reports 4782 and 9358 separately. If the sum is useful:

```yaml
total_frames:
  value: 14140
  claim_status: derived
  evidence: [ev-frames-a, ev-frames-b]
```
