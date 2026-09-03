# Luna v2 builder prompt

You are the builder under test in a controlled forward evaluation
(`experiments/case-builder-luna-v2/PROTOCOL.md`). The subject of this test is
the Skill, not the model; you are the driver. Your model identity is recorded
as metadata for the (skill, driver) pair.

Construct one long scientific benchmark case by explicitly following:

`/Users/xjchen/bench/mlffbench/scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/SKILL.md`

Treat this as the explicit invocation:

`/build-scientific-benchmark-case mode=mvp category=mlp`

Work the `mvp` lifecycle the Skill defines (intake → extract-spec → design →
scaffold → minimal construct → the MVP gate) for the **CIPS Curie-temperature
task** described by the MatClaw source ecosystem.

Inputs you may inspect:

- `/Users/xjchen/bench/mlffbench/benchmark/sources/matclaw/`
- reference case shapes at:
  - `/Users/xjchen/bench/mlffbench/031-matclaw-cips-active-distillation/`
  - `/Users/xjchen/bench/mlffbench/033-matclaw-cips-domain-wall-search/`
  - `/Users/xjchen/bench/mlffbench/034-ai2kit-water64-end-to-end-potential/`
- the portable Skill package named above.

Do **not** inspect, search, stat, list, or read any of the following during
construction:

- `/Users/xjchen/bench/mlffbench/032-matclaw-cips-curie-temperature/` (the
  held-out target);
- any path under `/Users/xjchen/bench/mlffbench/evidence/` that concerns 032;
- `/Users/xjchen/bench/mlffbench/042-go-water-dpmp/`;
- `/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v1/` (prior
  run's report and evaluation);
- `/Users/xjchen/bench/mlffbench/docs/superpowers/plans/` (the rework plan)
  and reports or git history that reveal 032's implementation or prior
  skill-gap analysis.

Skill-package code, templates, references, and its regression tests are fair
game — they are the tool under test.

Write only to:

`/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v2/`

Put the case at:

`generated/902-mvp-cips-curie-temperature/`

The case is experimental and must not appear as a repository-root benchmark
case. You may run local static/unit validation and the Skill's local scripts
(repository `.venv` interpreter is authorized; the runtime/site
authorization is outside your scope and may remain blocked). You are not
authorized to use network access, install or fetch packages (attempt =
violation, disclose any), build containers, train a model, run MD, mutate a
remote system, or submit HPC jobs. Represent unexecuted reference/calibration
work honestly as planned/deferred. Never assert `benchmark_valid=true`, and
never hand-write `case_status: runnable_draft` — only the Skill's L1 gate may
derive it.

Finish the run by executing the Skill's runnable gate on your case:

```text
python scripts/common/check_discovery_runnable.py CASE \
  --repo-root /Users/xjchen/bench/mlffbench \
  --output CASE/MVP-READINESS.json [--derive-state if it passes]
```

Also write `BUILDER_REPORT.md` beside this prompt. It must list:

- every source/reference directory you inspected;
- commands and validators run, with outcomes;
- the MVP gate result verbatim (pass or the blocking errors);
- maturity state and every open gate;
- what a user must do to launch the first real Discovery run;
- any forbidden path accidentally accessed (state `none` if none).

This is a single completion: do not repair or rerun after seeing the gate
outcome; report it as produced. Finish by summarizing exactly what you created
and the validation results.
