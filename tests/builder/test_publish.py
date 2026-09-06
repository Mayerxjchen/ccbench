"""Tests for Atomic Case Publisher."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from ccbench.builder.publish import PublishError, publish_case
from ccbench.builder.state import CaseLifecycleState


def test_publish_enforces_benchmark_valid_and_four_objects(tmp_path: Path):
    run_dir = tmp_path / "runs" / "run-001"
    cases_dir = tmp_path / "cases"
    maintainer_dir = tmp_path / "maintainer"

    # Setup draft
    draft_dir = run_dir / "draft"
    draft_dir.mkdir(parents=True)
    (draft_dir / "task.md").write_text("# Task\n", encoding="utf-8")
    (draft_dir / "case.toml").write_text('case_id = "006-toy"\n', encoding="utf-8")

    input_dir = draft_dir / "input"
    input_dir.mkdir()
    (input_dir / "input.txt").write_text("data", encoding="utf-8")

    verifier_dir = run_dir / "verifier"
    verifier_dir.mkdir(parents=True)
    (verifier_dir / "test.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    # Source & reports
    source_dir = run_dir / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "intake.json").write_text(json.dumps({"category": "mlp"}), encoding="utf-8")
    (source_dir / "sources.lock.json").write_text("{}", encoding="utf-8")
    (run_dir / "design").mkdir()
    (run_dir / "design" / "case.ir.yaml").write_text("schema: 1\n", encoding="utf-8")
    (run_dir / "verifier-smoke").mkdir()
    (run_dir / "verifier-smoke" / "smoke-report.json").write_text(json.dumps({"passed": True}), encoding="utf-8")
    (run_dir / "discovery").mkdir()
    (run_dir / "discovery" / "classification.json").write_text(json.dumps({"decision": "PROMOTED"}), encoding="utf-8")
    (run_dir / "reports").mkdir()
    (run_dir / "reports" / "reference-ready.json").write_text("{}", encoding="utf-8")
    (run_dir / "reports" / "calibration-report.json").write_text("{}", encoding="utf-8")
    (run_dir / "reports" / "benchmark-valid.json").write_text(json.dumps({"valid": True}), encoding="utf-8")

    # Publish
    published = publish_case(
        run_dir,
        "006-toy",
        cases_dir=cases_dir,
        maintainer_dir=maintainer_dir,
    )

    assert published.is_dir()
    # Check that EXACTLY the 4 canonical objects are present
    names = {p.name for p in published.iterdir()}
    assert names == {"task.md", "case.toml", "input", "verifier"}

    # Check maintainer assets were saved separately
    maint_case = maintainer_dir / "cases" / "006-toy"
    assert maint_case.is_dir()
    assert (maint_case / "source" / "intake.json").is_file()
