# dftworld Skill Comparison Summary Implementation Plan

> [!NOTE]
> **ARCHIVED / HISTORICAL PLAN**: This implementation plan is archived for historical provenance and audit purposes. Do not treat as current operational guidelines.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate separate with-skill and no-skill result views plus a direct comparison in both `jobs/summary.json` and `jobs/SUMMARY.md`.

**Architecture:** Add condition-aware pure selectors and a serializable summary model to `summarize.py`. Render both outputs from that single model so JSON and Markdown cannot drift.

**Tech Stack:** Python 3 standard library, pytest, JSON, Markdown.

## Global Constraints

- Never modify `jobs/<run-id>/summary.json`.
- A completed row requires explicit PASS.
- Missing comparison sides and deltas are `null` in JSON and `–` in Markdown.
- Preserve actual Skill names and distinguish `未调用` from `无 Skill 环境`.

---

### Task 1: Build the Condition-Aware Summary Model

**Files:**
- Modify: `summarize.py`
- Modify: `tests/test_summarize.py`

**Interfaces:**
- Produces: `select_completed_cases(executions, condition_id=None)`,
  `build_comparison(executions)`, and `build_summary_data(executions, root=ROOT)`.

- [ ] Add failing tests proving independent latest-PASS selection per condition,
  `null` missing sides, signed deltas, distinct Skill states, and numerically
  sorted unique comparison rows.
- [ ] Run `uv run pytest tests/test_summarize.py -q` and confirm failure.
- [ ] Implement the three pure interfaces with JSON-serializable dictionaries.
- [ ] Run the focused tests and then `uv run pytest tests -q`.
- [ ] Commit `summarize.py` and `tests/test_summarize.py` with
  `feat: add condition-aware summary model`.

### Task 2: Render and Verify Both Derived Views

**Files:**
- Modify: `summarize.py`
- Modify: `tests/test_summarize.py`
- Generate: `jobs/summary.json`
- Generate: `jobs/SUMMARY.md`

**Interfaces:**
- Produces: `render_overview(executions, root=ROOT)` with separate sections and
  `write_summary_views(executions, jobs_dir, root=ROOT)` for atomic JSON and
  Markdown generation.

- [ ] Add failing tests for the required Markdown section order and matching
  JSON/Markdown case membership.
- [ ] Implement separate completed/history renderers and direct comparison.
- [ ] Generate both views with `uv run python summarize.py`.
- [ ] Hash every immutable source before and after a second regeneration and
  assert the source hash set is unchanged.
- [ ] Assert all completed rows are PASS-backed, all comparison rows are unique
  and sorted, JSON parses, and `git diff --check` succeeds.
- [ ] Run `uv run pytest tests -q` and commit tracked generator/test changes with
  `feat: separate skill summary views`. Derived `jobs/` files remain local and
  ignored by Git.
