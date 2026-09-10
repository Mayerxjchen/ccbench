"""Threshold calibration verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def record_calibration(
    run_dir: Path,
    calibrated_thresholds: dict[str, float],
    passed: bool = True,
    details: dict[str, Any] | None = None,
) -> Path:
    """Record calibration results under reports/calibration-report.json."""
    rep_dir = Path(run_dir) / "reports"
    rep_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "passed": passed,
        "thresholds": calibrated_thresholds,
        "details": details or {},
    }
    target = rep_dir / "calibration-report.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return target
