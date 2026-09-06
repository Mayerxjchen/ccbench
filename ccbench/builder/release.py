"""Release check derivation for benchmark validity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def check_release_validity(run_dir: Path) -> dict[str, Any]:
    """Verify that case meets all benchmark validity requirements."""
    run_dir = Path(run_dir)
    draft_dir = run_dir / "draft"
    verifier_dir = run_dir / "verifier"

    errors = []
    if not (draft_dir / "task.md").is_file():
        errors.append("Missing task.md")
    if not (draft_dir / "case.toml").is_file():
        errors.append("Missing case.toml")
    if not (verifier_dir / "test.sh").is_file() and not (draft_dir / "verifier" / "test.sh").is_file():
        errors.append("Missing verifier/test.sh")

    valid = len(errors) == 0
    report = {
        "valid": valid,
        "errors": errors,
    }

    rep_dir = run_dir / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)
    target = rep_dir / "benchmark-valid.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
