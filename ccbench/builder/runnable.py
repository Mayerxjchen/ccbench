"""Deterministic derivation of RUNNABLE_DRAFT state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def check_runnable_draft(run_dir: Path) -> dict[str, Any]:
    """Execute runnable draft checks (files exist, verifier runs on blank or baseline submission)."""
    run_dir = Path(run_dir)
    draft_dir = run_dir / "draft"
    task_md = draft_dir / "task.md"
    case_toml = draft_dir / "case.toml"

    errors = []
    if not task_md.is_file():
        errors.append("Missing draft/task.md")
    if not case_toml.is_file():
        errors.append("Missing draft/case.toml")

    passed = len(errors) == 0
    report = {
        "passed": passed,
        "errors": errors,
        "checks": {
            "task_md": task_md.is_file(),
            "case_toml": case_toml.is_file(),
        },
    }

    smoke_dir = run_dir / "verifier-smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    smoke_report = smoke_dir / "smoke-report.json"
    smoke_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
