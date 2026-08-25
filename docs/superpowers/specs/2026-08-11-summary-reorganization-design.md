# dftworld Summary Reorganization Design

## Goal

Reorganize `jobs/SUMMARY.md` around completed benchmark cases while retaining
skill-use information. Completed runs from both skill-enabled and no-skill
environments remain visible. No job directory or per-run `summary.json` is
modified or deleted.

## Source of Truth

The report is derived from immutable `jobs/<run-id>/summary.json` records plus
explicit construction-status files for Cases 031–033. `jobs/SUMMARY.md` is a
generated view and must not invent a PASS, infer a PASS from artifact presence,
or convert a diagnostic result into benchmark construction success.

## Report Structure

1. **Overview**: counts of completed cases, failed latest runs, cases in
   progress, and cases awaiting construction or calibration.
2. **Completed cases**: one row per case, sorted by numeric case ID. The row
   uses the latest valid PASS and includes completion time, calls, tokens,
   duration, actual skill use, and benchmark commit.
3. **MatClaw 031–033 construction status**: construction, smoke, oracle
   calibration, and evaluated-agent outcome are displayed separately. An agent
   failure does not imply construction failure.
4. **Full run history**: every available run remains listed newest first,
   including repeated attempts and failures.
5. **Metric definitions**: concise definitions for case completion, replicate,
   attempt, skill state, and construction status.

The old top-level grouping by `ablation-canary`, `default`, and
`skill-ablation-v1` is removed from the primary view because the report is now
case-oriented. Condition data may remain in full history when present.

## Skill Display

Skill information is retained in both the completed-case table and full
history:

- list the normalized skill names when one or more skills were actually used;
- display `未调用` when skills were available but the agent invoked none;
- display `无 Skill 环境` when that run did not provide skills.

These states must not be collapsed because they represent different execution
conditions.

## Completion Rules

- A case appears in **Completed cases** only when at least one per-run summary
  explicitly records PASS.
- If several PASS runs exist, the main table uses the latest PASS; all earlier
  runs remain in full history.
- A later failure does not erase a historical completion, but the overview
  flags that the latest run failed.
- Cases 031–033 use their construction-status files for construction reporting
  and their job summaries or verifier outputs only for evaluated-agent results.
- Diagnostic artifacts without an explicit valid status are not promoted to
  completed records.

## Preservation and Verification

- Do not delete or rewrite `jobs/<run-id>/summary.json`, reward files,
  trajectories, or other job artifacts.
- Preserve traceability from each table row to its run ID.
- Verify that every completed-case row maps to an explicit PASS source record.
- Verify case ordering, Markdown table widths, skill-state normalization, and
  the absence of duplicate completed-case rows.
- Check the regenerated report with `git diff --check` and a source-to-report
  consistency script before completion.
