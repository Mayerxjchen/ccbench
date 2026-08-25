# Target Model and Reproduction Scope Policy

> Load this when a paper contains multiple MLIP models, training stages, committee members, pre-trained/fine-tuned models, or when the requested scope is ambiguous.

## First decide the reproduction scope

Canonical scope targets:

```text
final_model_retraining
full_data_generation_and_training
published_model_execution
model_evaluation_only
training_recipe_recovery
reproducibility_screening
```

### `final_model_retraining`

Goal: reproduce the final reported target model from the published/final labeled dataset or equivalent released training artifact.

Do not require reconstruction of active learning merely because the authors used it to create the dataset.

### `full_data_generation_and_training`

Goal: reproduce initial structures, labeling, active learning/sampling, final dataset formation, final training, and verification.

Data-generation models and exploration policy become required parts of the scope.

### `published_model_execution`

Goal: run an already released trained model. Model artifact identity, implementation compatibility, and inference/MD settings matter more than training hyperparameters.

### `model_evaluation_only`

Goal: reproduce reported test metrics or validation behavior. Exact test split, metric definitions, units, and model artifact identity are critical.

### `training_recipe_recovery`

Goal: recover the training recipe even when execution is not yet requested. Execution readiness may remain `recoverable` or `blocked` without being a failure of the extraction task.

### `reproducibility_screening`

Goal: determine whether a paper has enough public evidence/artifacts to be worth prioritizing for reproduction.

## Target-role decision tree

For final-model reproduction, ask in order:

1. Which model generated the final production MD/MC/optimization/scientific results?
2. Which model do the authors call final, production, best, selected, deployed, or equivalent?
3. Which model is evaluated against the headline held-out/physical validation?
4. Which models were used only for active learning, exploration, committee uncertainty, baselines, ablations, or comparisons?

Prefer the answer to 1–3 as the primary target. Record 4 as related models.

## Canonical roles

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

## Multi-stage example

```yaml
target:
  primary:
    reported_name: Model-B
    model_family: example_family
    role: production

  related_models:
    - reported_name: Model-A
      model_family: example_family
      role: data_generation
      required_for_scope: false
```

If the scope changes to `full_data_generation_and_training`, the same related model may become `required_for_scope: true`.

## Committee models

A committee of N models is not necessarily the final deliverable. Distinguish:

- committee members used to estimate model deviation;
- the final selected model used for production;
- an ensemble used directly for production predictions.

If production itself uses the ensemble, the target may legitimately be an ensemble/committee.

## Pre-trained and fine-tuned models

Record both the base model identity and fine-tuned target when required:

```yaml
target:
  primary:
    role: fine_tuned
  related_models:
    - role: pretrained
      required_for_scope: true
```

A fine-tuning reproduction is blocked if the base checkpoint is required but unavailable and cannot be recreated from released information.

## Ambiguous target

When the target cannot be determined:

```yaml
target:
  primary:
    role: unknown
```

Add a blocker and do not select the model based on familiarity or tool availability.

## Scope changes

A downstream user may later expand from final-model retraining to full workflow reproduction. Do not overwrite the earlier spec silently. Record a new scope/version and reassess readiness because previously optional fields may become required.
