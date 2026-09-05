#!/usr/bin/env python3
"""从 jobs/ 原始运行直接生成科学化汇总。

设计原则:
    1. 真相源 = jobs/<run-id>/run-record.json(canonical,不可变)。每次运行永不
       覆盖,都是独立观测;无 run-record 时回退 legacy summary.json。
    2. 已完成案例取每个任务最新的明确 PASS,完整历史不去重。
    3. 保留 condition、Skill 调用和 commit provenance。
    4. MatClaw 构造状态与被测 Agent 结果分开展示。

用法:
    uv run python summarize.py                       # 生成 jobs/SUMMARY.md
    uv run python summarize.py --all-runs            # 仅打印运行历史(不去重)
    uv run python summarize.py --experiment <id>     # 只看某实验
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JOBS = ROOT / "jobs"

TASK_ORDER = [
    "031-matclaw-cips-active-distillation",
    "032-matclaw-cips-curie-temperature",
    "033-matclaw-cips-domain-wall-search",
    "034-ai2kit-water64-end-to-end-potential",
    "042-go-water-dpmp",
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _task_key(name: str) -> tuple:
    m = re.match(r"(\d+)", name)
    return (int(m.group(1)) if m else 0, name)


def sort_tasks(tasks) -> list[str]:
    tset = set(tasks)
    known = [t for t in TASK_ORDER if t in tset]
    unknown = sorted((t for t in tset if t not in TASK_ORDER), key=_task_key)
    return known + unknown


def load_runs(jobs_dir: Path) -> list[dict]:
    """扫描 jobs/*/,优先只读的 run-record.json,回退 legacy summary.json。

    返回每个原始 run(带 _run_ts)。run-record 是 canonical record;summary.json
    只是兼容视图,仅在无 run-record 时兜底。
    """
    runs = []
    if not jobs_dir.is_dir():
        return runs
    # canonical run-record 目录名如 <stamp>__<task>;若某 stamp 已有 per-task
    # 记录,则跳过该 stamp 的 legacy summary.json,避免同一 attempt 双计。
    record_names = {
        run_dir.name for run_dir in jobs_dir.glob("*/")
        if (run_dir / "run-record.json").is_file()
    }

    def _has_record_for(stamp: str) -> bool:
        prefix = stamp + "__"
        return any(name.startswith(prefix) for name in record_names)

    for run_dir in sorted(jobs_dir.glob("*/")):
        record_path = run_dir / "run-record.json"
        if record_path.is_file():
            try:
                data = _record_to_run(record_path)
            except (ValueError, KeyError, TypeError, OSError):
                continue
        else:
            summary_path = run_dir / "summary.json"
            if not summary_path.is_file():
                continue
            if _has_record_for(run_dir.name):
                continue
            try:
                data = json.loads(summary_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
        data["_run_ts"] = run_dir.name
        runs.append(data)
    return runs


def _record_to_run(record_path: Path) -> dict:
    """把一条 RunRecord 转成与 summary.json 兼容的 run dict(单条 results)。"""
    from dftworld_bench.contracts.result import FailureCode
    from dftworld_bench.contracts.run_record import RunRecord

    record = RunRecord.from_dict(json.loads(record_path.read_text(encoding="utf-8")))
    result = record.result
    ok = result.is_counted_scientifically and result.failure_code is FailureCode.PASS
    skill_label = "with-skill" if record.condition_id == "with-skill" else "No Skill"
    return {
        "experiment_id": record.experiment_id,
        "condition_id": record.condition_id,
        "skill": skill_label,
        "skills_source": record.skills_source,
        "benchmark_commit": record.benchmark_commit,
        "results": [
            {
                "task": record.case_id,
                "experiment_id": record.experiment_id,
                "condition_id": record.condition_id,
                "replicate": record.replicate,
                "attempt": record.attempt,
                "ok": ok,
                "success": ok,
                "reward": 1.0 if result.failure_code is FailureCode.PASS else 0.0,
                "tool_calls": record.usage.get("tool_calls", 0),
                "tokens": record.usage.get("tokens", 0),
                "elapsed_sec": record.usage.get("elapsed_sec", 0.0),
                "skill": skill_label,
                "skills_source": record.skills_source,
                "skills_invoked": [],
                "benchmark_commit": record.benchmark_commit,
                "error": "" if ok else result.reason,
                "result_class": result.result_class.value,
                "failure_code": (
                    None if result.failure_code is None else result.failure_code.value
                ),
                "retryable": result.retryable,
                "platform": record.platform,
                "job_id": record.job_id,
            }
        ],
    }


def flatten_runs(runs: list[dict]) -> list[dict]:
    """把每个 run 展开成一条条 task 执行,补全 experiment/condition 字段。

    每次执行都是独立观测,不做去重。
    """
    execs = []
    for run in runs:
        exp = run.get("experiment_id") or "default"
        cond = run.get("condition_id") or (
            "no-skill" if str(run.get("skill", "")).lower() in ("", "no skill")
            else "with-skill"
        )
        for r in run.get("results", []):
            # skills_invoked 新字段;旧 run 的 summary 缺此字段时从 messages.jsonl 补算
            invoked = list(r.get("skills_invoked") or [])
            if not invoked:
                tdir = r.get("thread_dir")
                if tdir and Path(tdir).is_dir():
                    try:
                        from eval import collect_skill_invocations
                        invoked = collect_skill_invocations(tdir)
                    except Exception:
                        invoked = []
            execs.append({
                "run_ts": run["_run_ts"],
                "experiment_id": r.get("experiment_id", exp),
                "condition_id": r.get("condition_id", cond),
                "replicate": int(r.get("replicate", 1) or 1),
                "attempt": int(r.get("attempt", 1) or 1),
                "task": r["task"],
                "ok": bool(r.get("ok", r.get("success", False))),
                "reward": float(r.get("reward", 0.0) or 0.0),
                "tool_calls": int(r.get("tool_calls", 0) or 0),
                "tokens": int(r.get("tokens", 0) or 0),
                "elapsed_sec": float(r.get("elapsed_sec", 0.0) or 0.0),
                "skill": r.get("skill") or run.get("skill") or "No Skill",
                "skills_source": r.get("skills_source") or run.get("skills_source") or "none",
                "skills_invoked": invoked,
                "benchmark_commit": r.get("benchmark_commit")
                                   or run.get("benchmark_commit") or "unknown",
                "error": r.get("error", ""),
                "result_class": r.get("result_class"),
                "failure_code": r.get("failure_code"),
                "retryable": bool(r.get("retryable", False)),
                "platform": r.get("platform"),
                "job_id": r.get("job_id"),
            })
    return execs


def format_skill_state(execution: dict) -> str:
    """将 Skill 状态规范化为报表文本,不混淆"未调用"与"无 Skill 环境"。"""
    invoked = execution.get("skills_invoked") or []
    if invoked:
        return ", ".join(sorted(set(invoked)))
    if execution.get("condition_id") == "with-skill":
        return "未调用"
    return "无 Skill 环境"


def select_completed_cases(
    executions: list[dict], condition_id: str | None = None
) -> list[dict]:
    """每个案例只选最新明确 PASS,同时标记其后是否有更新失败。"""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for execution in executions:
        if condition_id is not None and execution["condition_id"] != condition_id:
            continue
        grouped[execution["task"]].append(execution)

    completed = []
    for task, rows in grouped.items():
        rows.sort(
            key=lambda row: (row["run_ts"], row["replicate"], row["attempt"])
        )
        passing = [row for row in rows if row["ok"]]
        if not passing:
            continue
        selected = dict(passing[-1])
        selected["latest_run_failed"] = not rows[-1]["ok"]
        completed.append(selected)
    return sorted(completed, key=lambda row: _task_key(row["task"]))


def _summary_execution(execution: dict) -> dict:
    """Return a stable JSON-facing execution record."""
    return {
        "task": execution["task"],
        "run_id": execution["run_ts"],
        "experiment_id": execution["experiment_id"],
        "condition_id": execution["condition_id"],
        "replicate": execution["replicate"],
        "attempt": execution["attempt"],
        "ok": execution["ok"],
        "reward": execution["reward"],
        "tool_calls": execution["tool_calls"],
        "tokens": execution["tokens"],
        "elapsed_sec": execution["elapsed_sec"],
        "skill_state": format_skill_state(execution),
        "skills_invoked": sorted(set(execution.get("skills_invoked") or [])),
        "benchmark_commit": execution["benchmark_commit"],
        "error": execution["error"],
        "latest_run_failed": execution.get("latest_run_failed", False),
    }


def build_comparison(executions: list[dict]) -> list[dict]:
    """Build one sorted row per case with with-skill minus no-skill deltas."""
    with_skill = {
        row["task"]: row
        for row in select_completed_cases(executions, "with-skill")
    }
    no_skill = {
        row["task"]: row
        for row in select_completed_cases(executions, "no-skill")
    }
    rows = []
    for task in sorted(set(with_skill) | set(no_skill), key=_task_key):
        with_row = with_skill.get(task)
        no_row = no_skill.get(task)
        delta = None
        if with_row is not None and no_row is not None:
            delta = {
                "tool_calls": with_row["tool_calls"] - no_row["tool_calls"],
                "tokens": with_row["tokens"] - no_row["tokens"],
                "elapsed_sec": round(
                    with_row["elapsed_sec"] - no_row["elapsed_sec"], 3
                ),
            }
        strictly_comparable = None
        if with_row is not None and no_row is not None:
            strictly_comparable = (
                with_row["experiment_id"] == no_row["experiment_id"]
                and with_row["benchmark_commit"] == no_row["benchmark_commit"]
            )
        rows.append(
            {
                "task": task,
                "with_skill": _summary_execution(with_row) if with_row else None,
                "no_skill": _summary_execution(no_row) if no_row else None,
                "delta_with_minus_no": delta,
                "strictly_comparable": strictly_comparable,
            }
        )
    return rows


def _is_pilot(execution: dict) -> bool:
    """Pilot trials carry a distinct experiment ID and never enter the formal
    summary (completed cases / comparison).  They remain viewable through
    `--experiment skill-ablation-v1-pilot` and stay in the raw history."""
    return "pilot" in (execution.get("experiment_id") or "")


def load_release(root: Path | None) -> dict | None:
    """Frozen release reference for the summary header.  None when the release
    manifest is absent (e.g. unit-test fixtures), so views degrade gracefully."""
    if root is None:
        return None
    manifest = _read_json(root / "releases" / "ablation-ready-v0.json")
    if not manifest:
        return None
    return {
        "name": manifest.get("name"),
        "source_commit": manifest.get("source_commit"),
        "release_digest": manifest.get("release_digest"),
    }


def build_summary_data(
    executions: list[dict], root: Path | None = ROOT
) -> dict:
    """Build the single serializable model used by JSON and Markdown views.

    The formal view (completed cases + comparison) excludes pilot trials; the
    raw history keeps every observation for traceability.
    """
    formal_execs = [e for e in executions if not _is_pilot(e)]
    completed_with = select_completed_cases(formal_execs, "with-skill")
    completed_no = select_completed_cases(formal_execs, "no-skill")

    def history(condition_id: str) -> list[dict]:
        rows = [row for row in executions if row["condition_id"] == condition_id]
        rows.sort(
            key=lambda row: (row["run_ts"], row["replicate"], row["attempt"]),
            reverse=True,
        )
        return [_summary_execution(row) for row in rows]

    return {
        "generated_at": _now(),
        "source": "jobs/<run-id>/run-record.json (fallback summary.json)",
        "release": load_release(root),
        "with_skill": {
            "completed_cases": [_summary_execution(row) for row in completed_with]
        },
        "no_skill": {
            "completed_cases": [_summary_execution(row) for row in completed_no]
        },
        "comparison": build_comparison(formal_execs),
        "matclaw": load_matclaw_status(root),
        "history": {
            "with_skill": history("with-skill"),
            "no_skill": history("no-skill"),
        },
    }


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def load_matclaw_status(root: Path | None) -> list[dict]:
    """读取 031–033 显式状态;缺少字段时保持未记录,不从产物存在性推断。"""
    if root is None:
        return []

    rows = []
    case_dirs = []
    for prefix in ("031-", "032-", "033-"):
        case_dirs.extend(root.glob(f"{prefix}*"))
    for case_dir in sorted(case_dirs, key=lambda path: _task_key(path.name)):
        if not case_dir.is_dir():
            continue
        validation = _read_json(case_dir / "VALIDATION.json")
        benchmark = _read_json(case_dir / "benchmark_valid.json")
        if not validation and not benchmark:
            continue
        smoke_profile = validation.get("smoke_profile") or {}
        paper_profile = validation.get("paper_profile") or {}
        rows.append(
            {
                "benchmark_id": validation.get("benchmark_id")
                or benchmark.get("benchmark_id")
                or case_dir.name,
                "state": validation.get("state")
                or benchmark.get("state")
                or "未记录",
                "construction_valid": validation.get("construction_valid"),
                "smoke_validated": validation.get(
                    "smoke_validated", bool(smoke_profile.get("completed", False))
                ),
                "oracle_calibrated": validation.get(
                    "oracle_calibrated", bool(paper_profile.get("completed", False))
                ),
                "agent_passed": validation.get("agent_passed", "未记录"),
                "benchmark_valid": bool(
                    benchmark.get(
                        "benchmark_valid", validation.get("benchmark_valid", False)
                    )
                ),
                "reason": benchmark.get("reason")
                or paper_profile.get("reason")
                or "",
            }
        )
    return rows


def aggregate(execs: list[dict]) -> dict:
    """按 (experiment, condition, task) 聚合,统计运行次数与成功率。

    返回:
        {
          (exp, cond, task): {
            "runs": 总运行次数,
            "passed": 成功次数,
            "infra_invalid": 基础设施无效次数(不计入成功率分母),
            "rate": 成功率,
            "latest": 最新一次执行(按 run_ts),
            "all": [每次执行],
          },
          ...
        }
    """
    cells: dict = {}
    for e in execs:
        key = (e["experiment_id"], e["condition_id"], e["task"])
        cell = cells.setdefault(key, {"runs": 0, "passed": 0, "infra_invalid": 0,
                                      "rate": 0.0, "latest": None, "all": []})
        cell["runs"] += 1
        cell["passed"] += 1 if e["ok"] else 0
        if e.get("result_class") == "INFRA_INVALID":
            cell["infra_invalid"] += 1
        cell["all"].append(e)
        if (cell["latest"] is None
                or (e["run_ts"], e["attempt"]) > (cell["latest"]["run_ts"],
                                                  cell["latest"]["attempt"])):
            cell["latest"] = e
    for cell in cells.values():
        denom = cell["runs"] - cell["infra_invalid"]
        cell["rate"] = cell["passed"] / denom if denom else 0.0
        cell["all"].sort(key=lambda x: (x["run_ts"], x["replicate"], x["attempt"]))
    return cells


def render_comparison(execs: list[dict], experiment_id: str) -> str:
    """View A:同一实验下不同 condition 的对比表(按任务行)。"""
    cells = aggregate(execs)
    # 只看该实验
    cell = {k: v for k, v in cells.items() if k[0] == experiment_id}
    if not cell:
        return "(无数据)\n"

    conds = sorted({k[1] for k in cell})
    tasks = sort_tasks({k[2] for k in cell})

    # commit 一致性警告
    commits = {v["latest"]["benchmark_commit"] for v in cell.values()}
    L = []
    if len(commits) > 1:
        L.append(f"> ⚠️ **condition 之间 benchmark_commit 不一致** "
                 f"({', '.join(sorted(commits))}),不能视为严格 A/B。")
        L.append("")

    def fmt_ok(c):
        """成功次数/有效运行次数 + 成功率;基础设施无效单列,不进分母。"""
        text = f"{c['passed']}/{c['runs'] - c['infra_invalid']} ({c['rate']:.0%})"
        if c["infra_invalid"]:
            text += f" +{c['infra_invalid']} infra"
        return text

    if len(conds) == 2:
        c0, c1 = conds[0], conds[1]
        L.append(f"| 任务 | {c0} 成功 | {c1} 成功 | {c0} Calls | {c1} Calls | "
                 f"Δ Calls | {c0} Tokens | {c1} Tokens | Δ Tokens |")
        L.append("|------|:---:|:---:|---:|---:|---:|---:|---:|---:|")
        for t in tasks:
            a, b = cell.get((experiment_id, c0, t)), cell.get((experiment_id, c1, t))
            s0 = fmt_ok(a) if a else "–"
            s1 = fmt_ok(b) if b else "–"
            ca0 = a["latest"]["tool_calls"] if a else "–"
            ca1 = b["latest"]["tool_calls"] if b else "–"
            tok0 = a["latest"]["tokens"] if a else "–"
            tok1 = b["latest"]["tokens"] if b else "–"
            dc = (f"{b['latest']['tool_calls']-a['latest']['tool_calls']:+.0f}"
                  if a and b else "–")
            dt = (f"{b['latest']['tokens']-a['latest']['tokens']:+,.0f}"
                  if a and b else "–")
            L.append(f"| `{t}` | {s0} | {s1} | {ca0} | {ca1} | {dc} | "
                     f"{tok0} | {tok1} | {dt} |")

        # 汇总行:按任务成功率(不是运行次数,避免跑得多的任务权重偏大)
        def task_rate(c):
            seen = {}
            for (e, cond, t) in cell:
                if cond != c:
                    continue
                v = cell[(e, cond, t)]
                if v["runs"] - v["infra_invalid"] > 0:
                    seen[t] = v
            ok = sum(1 for v in seen.values() if v["passed"] > 0)
            return ok, len(seen)

        ok0, n0 = task_rate(c0)
        ok1, n1 = task_rate(c1)
        avg_calls0 = (sum(v["latest"]["tool_calls"] for v in cell.values()
                          if v["latest"]["condition_id"] == c0) / n0 if n0 else 0)
        avg_calls1 = (sum(v["latest"]["tool_calls"] for v in cell.values()
                          if v["latest"]["condition_id"] == c1) / n1 if n1 else 0)
        avg_tok0 = (sum(v["latest"]["tokens"] for v in cell.values()
                        if v["latest"]["condition_id"] == c0) / n0 if n0 else 0)
        avg_tok1 = (sum(v["latest"]["tokens"] for v in cell.values()
                        if v["latest"]["condition_id"] == c1) / n1 if n1 else 0)
        L.append(f"| **Total** | {ok0}/{n0} | {ok1}/{n1} | "
                 f"{avg_calls0:.0f} | {avg_calls1:.0f} | "
                 f"{avg_calls1-avg_calls0:+.1f} | "
                 f"{avg_tok0:.0f} | {avg_tok1:.0f} | "
                 f"{avg_tok1-avg_tok0:+,.0f} |")
    else:
        # 通用布局:1 或 ≥3 个 condition
        L.append("| 任务 | " + " | ".join(f"{c} 成功" for c in conds)
                 + " | " + " | ".join(f"{c} Calls" for c in conds)
                 + " | " + " | ".join(f"{c} Tokens" for c in conds) + " |")
        L.append("|------" + "|---" * (1 + len(conds) * 3) + "|")
        for t in tasks:
            row = [f"| `{t}`"]
            for c in conds:
                v = cell.get((experiment_id, c, t))
                row.append(fmt_ok(v) if v else "–")
            for c in conds:
                v = cell.get((experiment_id, c, t))
                row.append(str(v["latest"]["tool_calls"]) if v else "–")
            for c in conds:
                v = cell.get((experiment_id, c, t))
                row.append(str(v["latest"]["tokens"]) if v else "–")
            L.append(" | ".join(row) + " |")

    return "\n".join(L) + "\n"


def render_history(execs: list[dict], experiment_id: str | None = None,
                   limit: int | None = None) -> str:
    """View B:运行历史,每次运行一行,不去重。"""
    filtered = [e for e in execs
                if experiment_id is None or e["experiment_id"] == experiment_id]
    filtered.sort(key=lambda e: (e["run_ts"], e["replicate"], e["attempt"]),
                  reverse=True)
    if limit:
        filtered = filtered[:limit]

    L = []
    if not filtered:
        L.append("(无数据)")
        return "\n".join(L) + "\n"

    L.append("| 运行时间 | 条件 | 任务 | 复现 | 尝试 | 状态 | Calls | Tokens | 用时(s) | Skill 调用 | Commit | 错误 |")
    L.append("|------|------|------|----:|----:|:----:|-----:|------:|--------:|------|-------|------|")
    for e in filtered:
        if e.get("result_class") == "INFRA_INVALID":
            status = "**INFRA**"
        else:
            status = "**PASS**" if e["ok"] else "FAIL"
        err = (e["error"] or "").replace("|", "\\|")[:30]
        invoked = format_skill_state(e)
        L.append(f"| {e['run_ts']} | {e['condition_id']} | `{e['task']}` | "
                 f"{e['replicate']} | {e['attempt']} | {status} | "
                 f"{e['tool_calls']} | {e['tokens']} | {e['elapsed_sec']:.0f} | "
                 f"{invoked} | {e['benchmark_commit']} | {err} |")
    return "\n".join(L) + "\n"


def _fmt_fact(value, *, pass_label: str = "通过", fail_label: str = "未通过") -> str:
    if value is True:
        return pass_label
    if value is False:
        return fail_label
    return "未单独记录"


def render_completed_cases(
    execs: list[dict], condition_id: str | None = None
) -> str:
    rows = select_completed_cases(execs, condition_id)
    if not rows:
        return "(无明确 PASS 记录)\n"

    lines = [
        "| 案例 | 最新有效 PASS | 完成状态 | Calls | Tokens | 用时(s) | Skill 调用 | 条件 | Commit |",
        "|------|------|------|------:|-------:|--------:|------|------|------|",
    ]
    for row in rows:
        status = "已完成（最新运行失败）" if row["latest_run_failed"] else "已完成"
        lines.append(
            f"| `{row['task']}` | {row['run_ts']} | {status} | "
            f"{row['tool_calls']} | {row['tokens']} | {row['elapsed_sec']:.0f} | "
            f"{format_skill_state(row)} | {row['condition_id']} | "
            f"{row['benchmark_commit']} |"
        )
    return "\n".join(lines) + "\n"


def render_completed_records(rows: list[dict]) -> str:
    if not rows:
        return "(无明确 PASS 记录)\n"
    lines = [
        "| 案例 | 实验 | 最新有效 PASS | 完成状态 | Calls | Tokens | 用时(s) | Skill 调用 | Commit |",
        "|------|------|------|------|------:|-------:|--------:|------|------|",
    ]
    for row in rows:
        status = "已完成（最新运行失败）" if row["latest_run_failed"] else "已完成"
        lines.append(
            f"| `{row['task']}` | {row['experiment_id']} | {row['run_id']} | {status} | "
            f"{row['tool_calls']} | {row['tokens']} | {row['elapsed_sec']:.0f} | "
            f"{row['skill_state']} | {row['benchmark_commit']} |"
        )
    return "\n".join(lines) + "\n"


def render_comparison_records(rows: list[dict]) -> str:
    if not rows:
        return "(无可对比记录)\n"
    lines = [
        "| 案例 | 可比性 | No Skill Calls | With Skill Calls | Δ Calls | No Skill Tokens | With Skill Tokens | Δ Tokens | No Skill 用时(s) | With Skill 用时(s) | Δ 用时(s) | Skill 调用 |",
        "|------|------|------:|------:|------:|------:|------:|------:|------:|------:|------:|------|",
    ]

    def value(row, key, fmt=str):
        return fmt(row[key]) if row is not None else "–"

    for row in rows:
        no_skill = row["no_skill"]
        with_skill = row["with_skill"]
        delta = row["delta_with_minus_no"]
        delta_calls = f"{delta['tool_calls']:+d}" if delta else "–"
        delta_tokens = f"{delta['tokens']:+d}" if delta else "–"
        delta_elapsed = f"{delta['elapsed_sec']:+.0f}" if delta else "–"
        skill_state = with_skill["skill_state"] if with_skill else "–"
        comparable = row["strictly_comparable"]
        if comparable is True:
            comparable_text = "严格可比"
        elif comparable is False:
            comparable_text = "⚠️ 实验/Commit 不同"
        else:
            comparable_text = "–"
        lines.append(
            f"| `{row['task']}` | {comparable_text} | {value(no_skill, 'tool_calls')} | "
            f"{value(with_skill, 'tool_calls')} | {delta_calls} | "
            f"{value(no_skill, 'tokens')} | {value(with_skill, 'tokens')} | "
            f"{delta_tokens} | {value(no_skill, 'elapsed_sec', lambda x: f'{x:.0f}')} | "
            f"{value(with_skill, 'elapsed_sec', lambda x: f'{x:.0f}')} | "
            f"{delta_elapsed} | {skill_state} |"
        )
    return "\n".join(lines) + "\n"


def render_history_records(rows: list[dict]) -> str:
    if not rows:
        return "(无数据)\n"
    lines = [
        "| 运行时间 | 任务 | 复现 | 尝试 | 状态 | Calls | Tokens | 用时(s) | Skill 调用 | Commit | 错误 |",
        "|------|------|----:|----:|:----:|-----:|------:|--------:|------|------|------|",
    ]
    for row in rows:
        status = "**PASS**" if row["ok"] else "FAIL"
        error = (row["error"] or "").replace("|", "\\|")[:30]
        lines.append(
            f"| {row['run_id']} | `{row['task']}` | {row['replicate']} | "
            f"{row['attempt']} | {status} | {row['tool_calls']} | {row['tokens']} | "
            f"{row['elapsed_sec']:.0f} | {row['skill_state']} | "
            f"{row['benchmark_commit']} | {error} |"
        )
    return "\n".join(lines) + "\n"


def render_matclaw_status(rows: list[dict]) -> str:
    if not rows:
        return "(未找到 031–033 显式状态文件)\n"

    lines = [
        "| 案例 | 记录状态 | 构造 | Smoke | Oracle 标定 | Agent 结果 | Benchmark | 说明 |",
        "|------|------|------|------|------|------|------|------|",
    ]
    for row in rows:
        agent = row["agent_passed"]
        if agent is True:
            agent_text = "PASS"
        elif agent is False:
            agent_text = "FAIL"
        else:
            agent_text = "未记录"
        reason = str(row["reason"]).replace("|", "\\|")
        lines.append(
            f"| `{row['benchmark_id']}` | `{row['state']}` | "
            f"{_fmt_fact(row['construction_valid'])} | "
            f"{_fmt_fact(row['smoke_validated'])} | "
            f"{_fmt_fact(row['oracle_calibrated'])} | {agent_text} | "
            f"{_fmt_fact(row['benchmark_valid'])} | {reason} |"
        )
    return "\n".join(lines) + "\n"


def render_overview_from_data(data: dict) -> str:
    """Render Markdown from the same serializable model written to JSON."""
    L = ["# dftworld 测评结果", ""]
    L.append(f"> 更新于 {data['generated_at']}")
    release = data.get("release")
    if release and release.get("release_digest"):
        L.append(
            f"> 冻结发布:`{release['name']}` @ `{release['source_commit'][:12]}` "
            f"(digest `{release['release_digest'][:16]}`)。"
        )
    L.append("> 正式对比只统计非 pilot 观测;pilot 结果用 `--experiment <pilot-id>` 单独查看。")
    L.append("> 真相源:`jobs/<时间戳>/summary.json`(每次运行永久保留)。")
    L.append("> `jobs/summary.json` 与本文件都是可重建聚合视图。")
    L.append("> 每次运行都是独立观测;不覆盖、不删除。")
    L.append("")
    completed_with = data["with_skill"]["completed_cases"]
    completed_no = data["no_skill"]["completed_cases"]
    matclaw = data["matclaw"]
    pending_matclaw = sum(not row["benchmark_valid"] for row in matclaw)

    L.append("## 概览")
    L.append("")
    L.append(f"- With Skill 已完成案例：**{len(completed_with)}**")
    L.append(f"- No Skill 已完成案例：**{len(completed_no)}**")
    L.append(f"- 可对比案例总数：**{len(data['comparison'])}**")
    L.append(f"- MatClaw 待发布/待标定案例：**{pending_matclaw}**")
    L.append("")
    L.append("## With Skill 已完成案例")
    L.append("")
    L.append(render_completed_records(completed_with).rstrip())
    L.append("")
    L.append("## No Skill 已完成案例")
    L.append("")
    L.append(render_completed_records(completed_no).rstrip())
    L.append("")
    L.append("## With Skill / No Skill 直接对比")
    L.append("")
    L.append("> Δ = With Skill − No Skill；“–”表示某一侧没有明确 PASS。")
    L.append("> 只有 experiment ID 和 benchmark commit 都相同时才标记为“严格可比”。")
    L.append("")
    L.append(render_comparison_records(data["comparison"]).rstrip())
    L.append("")
    L.append("## MatClaw 031–033 构造状态")
    L.append("")
    L.append("> 构造状态与被测 Agent 成败分开记录；Agent 失败不等于测试集无效。")
    L.append("")
    L.append(render_matclaw_status(matclaw).rstrip())
    L.append("")
    L.append("## With Skill 运行历史")
    L.append("")
    L.append(render_history_records(data["history"]["with_skill"]).rstrip())
    L.append("")
    L.append("## No Skill 运行历史")
    L.append("")
    L.append(render_history_records(data["history"]["no_skill"]).rstrip())
    L.append("")
    L.append("### 指标口径")
    L.append("")
    L.append("- **已完成案例**:至少一条 `summary.json` 执行明确记录 PASS;主表取最新有效 PASS。")
    L.append("- **Calls/Tokens**:已完成主表取该案例最新有效 PASS 的值。")
    L.append("- **运行历史**:每次运行一行,不去重,用于溯源与复现。")
    L.append("- **复现 (replicate)**:同一 (条件×任务) 的独立重复采样,评估结果稳定性。"
             "每次复现 = 一次新样本;统计时按复现计数。")
    L.append("- **尝试 (attempt)**:同一复现内因上次失败(超时/网络/harness)而重试的次数,"
             "补偿偶发故障,不算独立样本。")
    L.append("- **Skill 调用**:列出实际调用的 Skill;`未调用` = Skill 可用但 Agent 未调用;"
             "`无 Skill 环境` = 该次运行未提供 Skill。")
    L.append("- **MatClaw 状态**:只读取显式状态字段;缺少 `agent_passed` 时显示“未记录”,不从产物存在性推断。")
    return "\n".join(L) + "\n"


def render_overview(execs: list[dict], root: Path | None = ROOT) -> str:
    return render_overview_from_data(build_summary_data(execs, root))


def write_summary_views(
    execs: list[dict], jobs_dir: Path, root: Path | None = ROOT
) -> tuple[Path, Path]:
    """Atomically replace the derived Markdown and aggregate JSON views."""
    jobs_dir.mkdir(parents=True, exist_ok=True)
    data = build_summary_data(execs, root)
    markdown = render_overview_from_data(data)
    markdown_path = jobs_dir / "SUMMARY.md"
    json_path = jobs_dir / "summary.json"
    markdown_tmp = jobs_dir / ".SUMMARY.md.tmp"
    json_tmp = jobs_dir / ".summary.json.tmp"
    markdown_tmp.write_text(markdown, encoding="utf-8")
    json_tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown_tmp.replace(markdown_path)
    json_tmp.replace(json_path)
    return markdown_path, json_path


def main() -> None:
    ap = argparse.ArgumentParser(description="dftworld 科学化汇总(jobs/ 为唯一真相源)")
    ap.add_argument("--experiment", default=None, help="只看某实验 ID(默认全部)")
    ap.add_argument("--all-runs", action="store_true", help="仅打印运行历史(不去重)")
    ap.add_argument("--limit", type=int, default=None, help="--all-runs 最多列多少条")
    ap.add_argument("--jobs-dir", type=Path, default=JOBS, help="jobs 根目录(默认 %(default)s)")
    args = ap.parse_args()

    runs = load_runs(args.jobs_dir)
    if not runs:
        print(f"没有找到任何运行:{args.jobs_dir}")
        return
    execs = flatten_runs(runs)

    if args.all_runs:
        print(render_history(execs, args.experiment, args.limit))
        return

    exps = sorted({e["experiment_id"] for e in execs}
                  if not args.experiment else [args.experiment])
    for exp in exps:
        print(f"## {exp}")
        print()
        print(render_comparison(execs, exp))

    markdown_path, json_path = write_summary_views(execs, args.jobs_dir)
    print(f"已写入 {markdown_path}")
    print(f"已写入 {json_path}")


if __name__ == "__main__":
    main()
