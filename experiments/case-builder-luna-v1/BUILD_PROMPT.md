# Luna builder prompt

You are the builder under test in a controlled evaluation. Use model GPT-5.6
Luna and construct one long scientific benchmark case by explicitly following:

`/Users/xjchen/bench/mlffbench/scientific-benchmark-case-builder-portable/skills/build-scientific-benchmark-case/SKILL.md`

Treat this as the explicit invocation:

`/build-scientific-benchmark-case mode=construct category=mlp`

First execute all prerequisite lifecycle reasoning the Skill requires
(intake/extract-spec/design/scaffold as needed), then construct and validate an
honest **Runnable Draft** for the CIPS active-distillation task described by the
MatClaw source ecosystem.

Inputs you may inspect:

- `/Users/xjchen/bench/mlffbench/benchmark/sources/matclaw/`
- reference case shapes at:
  - `/Users/xjchen/bench/mlffbench/032-matclaw-cips-curie-temperature/`
  - `/Users/xjchen/bench/mlffbench/033-matclaw-cips-domain-wall-search/`
  - `/Users/xjchen/bench/mlffbench/034-ai2kit-water64-end-to-end-potential/`
- the portable Skill package named above.

Do **not** inspect, search, stat, list, or read any of the following during
construction:

- `/Users/xjchen/bench/mlffbench/031-matclaw-cips-active-distillation/`
- any path under `/Users/xjchen/bench/mlffbench/evidence/` that concerns 031;
- `/Users/xjchen/bench/mlffbench/042-go-water-dpmp/`;
- reports or git history that reveal 031's implementation or 042's Skill-gap
  analysis.

Write only to:

`/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v1/`

Put the case at:

`generated/901-luna-cips-active-distillation/`

The case is experimental and must not appear as a repository-root benchmark
case. You may run local static/unit validation and the Skill's local scripts.
You are not authorized to use network access, build containers, train a model,
run MD, mutate a remote system, or submit HPC jobs. Represent unexecuted
reference/calibration work honestly as planned/deferred. Never assert
`benchmark_valid=true`.

Optimize for direct use in a real Discovery draft run: include concrete
candidate instructions, task/runtime contracts, candidate inputs or a precise
source-materialization plan, verifier/tests sufficient for draft diagnostics,
profiles, and clear output schemas. Prefer outcome-based checks and allow
alternative valid implementations.

Also write `BUILDER_REPORT.md` beside this prompt. It must list:

- every source/reference directory you inspected;
- commands and validators run, with outcomes;
- maturity state and every open gate;
- what a user must do to launch the first real Discovery run;
- any forbidden path accidentally accessed (state `none` if none).

Do not revise the generated case after seeing the held-out 031 case. Finish by
summarizing exactly what you created and the validation results.
