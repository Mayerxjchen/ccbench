# v3 short-regression builder prompt — ISOLATION REGIME (driver-facing)

You are the builder under test in a controlled clean-context forward
evaluation (`experiments/case-builder-luna-v3/PROTOCOL.md`, isolation
regime). The subject of this test is the **Skill**, not the model; you are
the driver. Your model identity is recorded as metadata for the
(skill, driver) pair.

Construct one small scientific benchmark case by explicitly following:

`/Users/xjchen/bench/.wt/case-builder-mvp-v3/scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/SKILL.md`

(v2.3.0, branch `case-builder/mvp-20260903`, commit `1fe2d3d`). Treat this as
the explicit invocation:

`/build-scientific-benchmark-case mode=mvp category=mlp`

Work the `mvp` lifecycle the Skill defines (intake → extract-spec → design →
scaffold → minimal construct → the MVP gate) for the **LC-MLP v1 cluster
cohesive-energy evaluation task** described by the toy source ecosystem.

Inputs you may inspect:

- `/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v3/materials-clean/`
  — the complete source ecosystem for this task, as it exists;
- the portable Skill package named above (its code, templates, references,
  and regression tests are the tool under test and are fair game);
- `/Users/xjchen/bench/mlffbench/.venv/` — the authorized interpreter for
  running local validators and the gate.

Do **not** inspect, search, stat, list, or read anything else during
construction, including:

- any numbered case directory under `/Users/xjchen/bench/mlffbench/NNN-*/`;
- `/Users/xjchen/bench/mlffbench/evidence/`, `/Users/xjchen/bench/mlffbench/docs/`;
- `/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v1/` and
  `case-builder-luna-v2/` (prior runs and evaluations);
- `/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v3/materials/`
  (a superset of your source tree that contains additional files; your
  authorized source is `materials-clean/` only — do not browse or
  reconstruct the superset);
- `/Users/xjchen/bench/mlffbench/scientific-benchmark-case-builder-portable/`
  (the stale main-checkout copy of the skill — use the worktree package only);
- git history of this repository.

Source-reading during the lifecycle is governed by the Skill's own
intake/source policies; follow them.

Write only to:

`/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v3/`

Put the case at:

`generated/906-mvp-ljcluster-model-eval/`

The case is experimental and must not appear as a repository-root benchmark
case. You may run local static/unit validation and the Skill's local scripts.
You are not authorized to use network access, install or fetch packages
(attempt = violation, disclose any), build containers, train a model, run
MD, mutate a remote system, or submit HPC jobs. Represent unexecuted
reference/calibration work honestly as planned/deferred. Never assert
`benchmark_valid=true`, and never hand-write `case_status: runnable_draft` —
only the Skill's L1 gate may derive it.

Finish the run by executing the Skill's runnable gate on your case:

```text
/Users/xjchen/bench/mlffbench/.venv/bin/python \
  /Users/xjchen/bench/.wt/case-builder-mvp-v3/scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/scripts/common/check_discovery_runnable.py \
  CASE --repo-root /Users/xjchen/bench/mlffbench \
  --output CASE/MVP-READINESS.json [--derive-state if it passes]
```

Also write `BUILDER_REPORT_ISOLATION.md` in the run directory
(`/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v3/BUILDER_REPORT_ISOLATION.md`).
It must list:

- every source file/directory you inspected (paths);
- commands and validators run, with outcomes;
- the MVP gate result verbatim (pass or the blocking errors);
- maturity state and every open gate;
- what a user must do to launch the first real Discovery run;
- any forbidden path accidentally accessed (state `none` if none).

This is a single completion: do not repair or rerun after seeing the gate
outcome; report it as produced. Finish by summarizing exactly what you
created and the validation results.
