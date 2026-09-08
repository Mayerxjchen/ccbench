# Resource guidance

This is the decision aid for choosing a compute class and sizing a request.
It pairs with `compute-capabilities.json`, which lists the actual ceilings and
allowed values for this run. You never name a backend; you choose an abstract
`cpu` or `gpu` shape and justify it.

## Choosing between cpu and gpu

Ask in order:

1. **Can it run in this workspace?** Parsing, plotting, small analysis of
   already-returned outputs → no request at all.
2. **Is the step CPU-shaped or GPU-shaped?** Training a neural-network
   potential, GPU molecular dynamics, or a step dominated by GPU kernels →
   `gpu`. A CPU simulation, MPI job, or verification/reproduction run → `cpu`.
3. **Would a GPU actually help enough to justify metered cost?** "Faster" is
   not a reason. "CPU training of N frames exceeds the time budget; the
   framework's training kernel is GPU-only" is a reason.
4. **When in doubt, prefer `cpu`.** It is lower-cost and may wait; correctness
   rarely needs the most expensive option.

## Sizing honestly

The validator is mechanical, not an oracle: it checks shape and internal
consistency, not whether the resources fit the science. Sizing well is your
job. Concretely:

- **Memory is per node** (`memory_gb_per_node`). Estimate what one node must
  hold (the system, the model, the batch), not a global total divided oddly.
  Round up to an allowed value from the capability manifest.
- **GPUs are per node too.** State `gpus` and a `minimum_gpu_memory_gb` floor
  that the model actually needs — not the biggest card available.
- **CPUs and nodes grow with the work.** One small serial step rarely needs
  `nodes > 1`. Scale `nodes`/`ntasks`/`cpus_per_task` to the parallel width
  your engine can actually use; unused parallelism is waste.
- **Wall-clock** is an upper bound. Estimate generously enough that the step
  finishes without a retry, but do not pad to hide a wrong class choice.
- **Do not request what the manifest does not offer.** Check
  `compute-capabilities.json` for the allowed per-node memory values, GPU
  memory values, GPU count, and ceilings, and stay inside them.

## Cost discipline

- External compute is metered or may-wait; the abstract class you pick
  controls the cost class. Prefer the smallest shape that completes the step.
- Revisions are limited: if the operator returns a request as rejected or the
  run fails, you may revise the resources **at most twice per step**. Make the
  revision count: shrink or grow based on the actual failure evidence, not on
  guessing.
- After the revision budget is spent, do not keep resubmitting the same step
  with cosmetic changes. Re-plan: is the class right? Is the batch smaller?
  Is there a checkpoint to resume from?

## What a completed request is *not*

- A scheduler finishing is not a scientific result. The operator returns the
  declared outputs under `compute-results/`; success means the engine output
  matches `validation.success_markers` and contains nothing from
  `validation.reject_if` (convergence reached, no NaN loss, model valid, MD
  stable, files complete).
- A request that was never validated is not pending — it is unwritten. If you
  have not written a request file and gotten evidence back, you have not done
  the step.
- You never submit, never check queue state, and never fetch results yourself.
  You write the descriptor and wait for the operator; all remote lifecycle is
  theirs.

## When a run comes back

- `REJECTED` means the descriptor itself was invalid or infeasible. Read the
  reason and fix the request shape or resource values (revision 1).
- `FAILED` with evidence (e.g. out-of-memory, NaN, divergence) means the step
  ran but did not succeed. Read the evidence, adjust (revision 2), resume from
  the latest checkpoint when one exists.
- Then verify the returned outputs before folding them into `final/`. A
  completed external step is an input to your scientific judgment, never a
  substitute for it.
