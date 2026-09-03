# v2.3.1 confirmation builder prompt (driver-facing, steering regime)

You are the builder under test in a controlled clean-context forward
evaluation (`experiments/case-builder-luna-v3/PROTOCOL.md`, confirmation of
v2.3.1 on the steering regime). The subject of this test is the **Skill**, not
the model; you are the driver.

Construct one small scientific benchmark case by explicitly following:

`/Users/xjchen/.claude/skills/build-scientific-benchmark-case/SKILL.md`

(v2.3.1 — the installed release). Treat this as the explicit invocation:

`/build-scientific-benchmark-case mode=mvp category=mlp`

Work the `mvp` lifecycle the Skill defines (intake → extract-spec → design →
scaffold → minimal construct → the MVP gate) for the **LC-MLP v1 cluster
cohesive-energy evaluation task** described by the toy source ecosystem.

Inputs you may inspect:

- `/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v3/materials/`
  — the complete source ecosystem for this task;
- the installed Skill package named above (its code, templates, references,
  and the source package's regression tests are the tool under test and are
  fair game — the source package is
  `/Users/xjchen/bench/mlffbench/scientific-benchmark-case-builder-portable/`
  and may be read for its tests/fixtures only);
- `/Users/xjchen/bench/mlffbench/.venv/` — the authorized interpreter.

Do **not** inspect, search, stat, list, or read anything else during
construction, including:

- any numbered case directory under `/Users/xjchen/bench/mlffbench/NNN-*/`;
- `/Users/xjchen/bench/mlffbench/evidence/`, `/Users/xjchen/bench/mlffbench/docs/`;
- `/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v1/` and `v2/`;
- `/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v3/generated/`
  (other runs' trees — your tree is `generated/908-mvp-ljcluster-model-eval/`)
  and `materials-clean/` (a pruned variant of your source tree; use
  `materials/` as given);
- git history of this repository.

Source-reading during the lifecycle is governed by the Skill's own
intake/source policies; follow them.

Write only to:

`/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v3/`

Put the case at:

`generated/908-mvp-ljcluster-model-eval/`

The case is experimental and must not appear as a repository-root benchmark
case. You are not authorized to use network access, install or fetch packages
(attempt = violation, disclose any), build containers, train a model, run MD,
mutate a remote system, or submit HPC jobs. Represent unexecuted
reference/calibration work honestly as planned/deferred. Never assert
`benchmark_valid=true`, and never hand-write `case_status: runnable_draft` —
only the Skill's L1 gate may derive it.

Finish the run by executing the Skill's runnable gate on your case:

```text
/Users/xjchen/bench/mlffbench/.venv/bin/python \
  /Users/xjchen/.claude/skills/build-scientific-benchmark-case/scripts/common/check_discovery_runnable.py \
  CASE --repo-root /Users/xjchen/bench/mlffbench \
  --output CASE/MVP-READINESS.json [--derive-state if it passes]
```

Also write `BUILDER_REPORT_908.md` in the run directory. It must list: every
source file/directory inspected (paths); commands and validators run with
outcomes; the MVP gate result verbatim; maturity state and every open gate;
what a user must do to launch the first real Discovery run; any forbidden
path accidentally accessed (state `none` if none).

This is a single completion: do not repair or rerun after seeing the gate
outcome; report it as produced. Finish by summarizing exactly what you created
and the validation results.
