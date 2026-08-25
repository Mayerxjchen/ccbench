# MLP Workflow Capabilities

Catalog of workflow capabilities an MLP case may require, and the mapping from
case kind to applicable capabilities. This is category-scoped science: Common
Core never interprets these fields, and none of these capabilities are
universal requirements.

## Capability catalog

| Capability | Meaning |
|---|---|
| `structure_generation` | Produce molecular structures from system definitions |
| `dft_dynamics` | Reference labeling through DFT dynamics or trajectories |
| `model_training` | Train or fine-tune an interatomic potential |
| `iterative_improvement` | Active-learning style explore/select/label/grow/retrain cycles |
| `hidden_static_accuracy` | Hidden held-out static checks (energies, forces) |
| `hidden_dynamic_stability` | Hidden dynamics stability checks |
| `hidden_physical_observable` | Hidden physical property checks (e.g. RDF) |

## Case-kind mapping

```text
final_model_retraining
  -> dataset binding + model_training + hidden_static_accuracy

end_to_end_model_development
  -> structure_generation + dft_dynamics + model_training
     + iterative_improvement when the workflow requires it
     + hidden_static_accuracy + hidden_dynamic_stability
     + hidden_physical_observable

active_learning_workflow
  -> iterative_improvement lineage is a hard outcome
     + applicable hidden static/dynamic/property checks

published_model_execution
  -> immutable model + runtime + target observable; model_training is N/A

model_evaluation
  -> immutable or reproducibly trained model + evaluation split
     + metric implementation; model_training only when the model is
     not released
```

## Reference lineage

Required lineage for each case kind (empty lineage is only valid at `planned`):

```text
final_model_retraining
  -> dataset -> config -> model -> held-out metrics

active_learning_workflow
  -> explore -> select -> label -> dataset growth -> retrain

published_model_execution
  -> artifact -> runtime -> inference/observable

end_to_end_model_development
  -> structure -> preparation -> DFT reference -> training
  -> iterative improvement -> static/dynamic/property validation
```

`model_evaluation` records the evaluation split and metric implementation as
its lineage. Every lineage step is recorded in `reference/reference.json` as
`{capability, inputs, outputs, commands, runtime_identity, digest}`.

## Rules

- The category proposes applicable capabilities during `design`; the design is
  not valid until every required capability is present in `case-design.yaml`.
- Unknown capability names fail against this catalog.
- Iterative-workflow lineage is required only when the case kind itself
  requires an iterative workflow.
