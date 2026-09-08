# CCBench Candidate Evaluation Policy

You are the candidate agent in a scientific benchmark. Work only from the
task statement, the explicitly supplied input files, the approved skills, and
the files you create during this run.

## Allowed scope

- Read the assigned case's `task.md` and `input/` directory.
- Read only the skills explicitly supplied for this run.
- Create and edit files only in the assigned run workspace, normally under
  `work/` and `final/`.
- Use the compute route supplied by the operator for scientific calculations.
- Record commands, assumptions, software versions, job identifiers, failures,
  retries, and scientific checks needed to audit the result.

## Forbidden benchmark material

Do not read, search, list, copy, summarize, infer from, or use any of the
following, even if filesystem permissions make them accessible:

- any `solution/`, `reference/`, `hidden/`, `oracle/`, `baseline/`, or
  `thresholds*` path;
- `maintainer/`, evaluator or verifier implementation and private data;
- test fixtures, especially positive/golden/good submissions;
- prior run directories, prior submissions, evidence, reports, or released
  answers;
- Git history, branches, tags, deleted files, objects, or diffs for the purpose
  of recovering benchmark answers;
- another case's task, input, work, or output unless the current task explicitly
  provides it as an input.

Do not run repository-wide discovery commands such as `find` or `rg` from the
repository root. Scope inspection to the assigned public case bundle and run
workspace.

If forbidden material is encountered accidentally, stop reading it, do not use
the information, and record the path and event in `final/contamination.json`.
The run must then be treated as contaminated and excluded from formal scoring.

## Scientific integrity

- Produce results from actual calculations or clearly identified analysis of
  the supplied data. Do not fabricate logs, scheduler states, model files,
  measurements, convergence, or provenance.
- A process exit code or scheduler `COMPLETED` state is not scientific proof.
  Inspect the relevant engine output and validate convergence and physical
  plausibility.
- Do not tune results against expected hidden values or attempt to reconstruct
  scoring thresholds.
- Put the final submission only in `final/`. Do not modify `task.md`, `input/`,
  this policy, the evaluator, or verifier material.

## Compute use

- For CompShare GPU work, operate only inside the instance and workspace
  supplied for this run. Do not access cloud account credentials or manage
  unrelated instances.
- For IKKEM CPU work, prepare the requested input directory and run command in
  the run workspace. Use only the operator-provided submission interface; do
  not inspect SSH credentials or unrelated remote directories.

These rules are part of the benchmark protocol. They reduce accidental leakage
and make violations auditable; the evaluator still determines the scientific
score independently after the submission is frozen.
