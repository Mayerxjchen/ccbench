# CIPS active distillation (Runnable Draft)

Develop a CuInP2S6 (CIPS) DeePMD student potential by distilling the supplied
published teacher. This is a paper-faithful reproduction of the source
ecosystem's teacher→student objective, adapted into an auditable benchmark
workflow. The scientific outcome is a student that is accurate on a genuinely
independent teacher-labelled trajectory and whose active-learning lineage is
complete. A script that only describes or stages the workflow is not completion.

## Fixed target and inputs

The sole scored system is bulk periodic CuInP2S6 with composition Cu9In9P18S54
in the supplied 3×3×1 supercell (90 atoms), element order `[Cu, In, P, S]` and
the cell and fractional coordinates in `public/CuInP2S6.cif`. Preserve periodic
boundary conditions and composition. The supplied `public/teacher/` files are
the published teacher model and type map; verify their hashes in your own
manifest before use. Do not substitute a different chemistry, teacher, or
element ordering.

You may use only the supplied public inputs, your own generated intermediate
files, and documented runtime software. Do not download or use published
student coordinates, labels, datasets, checkpoints, or prior run outputs as
workflow inputs; methodology citations are allowed. Do not use network access
or commercial software.

## Required executed workflow

1. Make a reproducible initial data set by running the teacher on periodic MD
   trajectories for at least two distinct temperatures spanning the intended
   CIPS regime. Record all simulation controls, seeds, job IDs, fetched raw
   trajectories, and teacher-generated energies and forces. Generate a separate
   held-out trajectory (distinct seed and trajectory identity, not a random
   adjacent-frame split) and keep it out of every training, selection, and
   retraining input. Keep initial-structure artifacts separate from labeled
   datasets; every labeled frame must carry its cell, coordinates, element
   types, energy, forces, and conversion/provenance record.
2. Train at least two independently seeded student models from the initial
   training data. Record implementation/version, configuration, seed, model
   lineage, and train/validation provenance. The committee is used for
   disagreement, not as a substitute for selecting a primary student.
3. Execute at least one complete active round: student-committee exploration
   on new trajectories → per-frame uncertainty calculation → selection of
   finite, physically admissible frames → teacher labelling of exactly those
   selected frames → append/deduplicate with provenance → retrain a new
   student committee. Each direction of this lineage must be auditable from
   artifacts and a machine-readable manifest. A claimed round with missing
   exploration, labels, data growth, or retraining fails.
4. Evaluate every final student on the untouched held-out trajectory and select
   the primary by the predeclared force metric. You may stop after the first
   active round only if the stopping outcome below is met; otherwise continue
   through at most five active rounds and report why the target was or was not
   reached. Never move held-out frames into training to meet a threshold.

## Hard outcomes and report

The final report and manifest must contain force MAE (eV/angstrom), force RMSE
(eV/angstrom), energy MAE (eV/atom with the energy-offset convention), per-frame
committee uncertainty, active-round count, and the exact held-out grouping.
The draft target is held-out force MAE ≤ 0.10 eV/angstrom after at least one
completed active round. Also report whether every selected frame was labelled,
the before/after training-frame counts, and any failed or retried job.

Deliver a self-contained submission rooted at `final/` containing:

* `manifest.json` with schema version, target identity, input hashes, software
  and runtime identity, seeds, job IDs, stage lineage, frame counts, selected
  frame IDs, metrics, and primary/committee model paths;
* `models/` with loadable final student model(s), `workflow/` scripts and logs,
  `data/` or equivalent fetched teacher/label artefacts, and `report.md`;
* raw trajectories and label files sufficient to audit every training and
  held-out frame, plus a machine-readable failure/retry record.

The verifier scores outcomes and provenance, not an expert directory layout.
Alternative valid implementations, committee sizes, and scheduler commands
are accepted when they preserve the fixed system, an independent held-out
split, a closed explore/select/label/grow/retrain loop, and the reported
outcomes. Keep credentials and hidden evaluator content out of the submission.

## Execution contract

This is an `hpc_controller` task. Discover the declared scheduler and runtime
capabilities. Long-running teacher MD, student training, committee exploration,
and teacher-label jobs must run through the scheduler; the sandbox is only the
controller and artifact-validation workspace. Submit jobs, poll for terminal
states (never fixed sleeps), fetch every artifact under `final/`, and retain
job IDs and logs. Before launching formal work, run the smoke profile and record
the site/runtime qualification receipt. Construction of this draft performed no
training, MD, labelling, downloads, container builds, remote mutation, or HPC
submission.
