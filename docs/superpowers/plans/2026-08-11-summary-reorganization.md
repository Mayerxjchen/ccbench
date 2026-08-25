# dftworld Summary Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a case-oriented `jobs/SUMMARY.md` that includes every completed case, preserves actual Skill-use information, separates MatClaw construction state from evaluated-agent outcomes, and leaves raw job records untouched.

**Architecture:** Extend `summarize.py` with pure selection, normalization, MatClaw-status loading, and rendering functions. Cover those functions with fixture-based tests, then regenerate the Markdown view exclusively from `jobs/*/summary.json` and explicit 031–033 validation files.

**Tech Stack:** Python 3, standard-library `json`/`pathlib`, pytest, Markdown.

## Global Constraints

- Do not modify or delete any `jobs/<run-id>/summary.json` or job artifact.
- Keep `未调用` distinct from `无 Skill 环境`.
- A completed-case row requires an explicit PASS in a per-run summary.
- Use the latest PASS for the completed-case row and retain every execution in history.
- Do not infer 031–033 Agent success from artifact presence.

---

### Task 1: Add Case-Oriented Summary Selection and Rendering

**Files:**
- Modify: `summarize.py`
- Create: `tests/test_summarize.py`

**Interfaces:**
- Consumes: flattened execution dictionaries returned by `flatten_runs()` and case `VALIDATION.json`/`benchmark_valid.json` files.
- Produces: `format_skill_state(execution) -> str`, `select_completed_cases(executions) -> list[dict]`, `load_matclaw_status(root) -> list[dict]`, and the revised `render_overview(executions, root=ROOT) -> str`.

- [ ] **Step 1: Write failing tests for latest-PASS selection and Skill labels**

```python
def test_completed_case_uses_latest_pass_not_later_failure():
    rows = select_completed_cases([
        execution("010-deepmd-train", "2026-01-01__00-00-00", True),
        execution("010-deepmd-train", "2026-01-02__00-00-00", False),
    ])
    assert rows[0]["run_ts"] == "2026-01-01__00-00-00"
    assert rows[0]["latest_run_failed"] is True

def test_skill_states_remain_distinct():
    assert format_skill_state({"skills_invoked": ["deepmd"], "condition_id": "with-skill"}) == "deepmd"
    assert format_skill_state({"skills_invoked": [], "condition_id": "with-skill"}) == "未调用"
    assert format_skill_state({"skills_invoked": [], "condition_id": "no-skill"}) == "无 Skill 环境"
```

- [ ] **Step 2: Run the focused tests and confirm the new interfaces are absent**

Run: `uv run pytest tests/test_summarize.py -q`

Expected: FAIL during import because `format_skill_state` and `select_completed_cases` do not exist.

- [ ] **Step 3: Implement deterministic selection and Skill normalization**

```python
def format_skill_state(execution: dict) -> str:
    invoked = execution.get("skills_invoked") or []
    if invoked:
        return ", ".join(sorted(set(invoked)))
    if execution.get("condition_id") == "with-skill":
        return "未调用"
    return "无 Skill 环境"

def select_completed_cases(executions: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for execution in executions:
        grouped[execution["task"]].append(execution)
    completed = []
    for task, rows in grouped.items():
        rows.sort(key=lambda row: (row["run_ts"], row["replicate"], row["attempt"]))
        passing = [row for row in rows if row["ok"]]
        if not passing:
            continue
        selected = dict(passing[-1])
        selected["latest_run_failed"] = not rows[-1]["ok"]
        completed.append(selected)
    return sorted(completed, key=lambda row: _task_key(row["task"]))
```

- [ ] **Step 4: Add failing tests for MatClaw status and report sections**

```python
def test_matclaw_status_does_not_infer_agent_success(tmp_path):
    write_case_state(tmp_path, "032-example", smoke=True, paper=False)
    row = load_matclaw_status(tmp_path)[0]
    assert row["agent_passed"] == "未记录"
    assert row["oracle_calibrated"] is False

def test_overview_is_case_oriented():
    report = render_overview([execution("010-deepmd-train", "2026-01-01__00-00-00", True)], root=None)
    assert "## 已完成案例" in report
    assert "## 完整运行历史" in report
    assert "## default" not in report
```

- [ ] **Step 5: Implement conservative MatClaw loading and the new section order**

Read explicit fields only. When new fields are absent, display the recorded
state, use `smoke_profile.completed` for smoke, use
`paper_profile.completed` for oracle calibration, and display `未记录` for
`agent_passed`. Render overview, completed cases, MatClaw status, full history,
and metric definitions in that order.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/test_summarize.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Commit implementation and tests**

```bash
git add summarize.py tests/test_summarize.py
git commit -m "feat: reorganize benchmark summary by case"
```

### Task 2: Regenerate and Verify the Report

**Files:**
- Modify: `jobs/SUMMARY.md`

**Interfaces:**
- Consumes: `jobs/*/summary.json`, `031–033/VALIDATION.json`, and `031–033/benchmark_valid.json`.
- Produces: the regenerated human-readable `jobs/SUMMARY.md`.

- [ ] **Step 1: Regenerate the report**

Run: `uv run python summarize.py`

Expected: `已写入 .../jobs/SUMMARY.md`.

- [ ] **Step 2: Verify source preservation and report consistency**

Run: `uv run pytest tests/test_summarize.py -q`

Expected: all tests PASS.

Run: `git diff --check -- summarize.py tests/test_summarize.py jobs/SUMMARY.md`

Expected: no output and exit code 0.

Run a read-only consistency check that asserts every completed table task has
an explicit PASS source and that completed task IDs are unique and numerically
sorted.

- [ ] **Step 3: Inspect the report headings and MatClaw rows**

Run: `rg -n '^## |031-|032-|033-|Skill' jobs/SUMMARY.md`

Expected: case-oriented headings, explicit Skill columns, and separate 031–033
construction rows.

- [ ] **Step 4: Commit the generated report**

```bash
git add jobs/SUMMARY.md
git commit -m "docs: regenerate case-oriented benchmark summary"
```
