"""Task 1 — Case Factory baseline and CF dashboard contract.

The Case Factory introduces an orthogonal derived-gate vocabulary (CF0..CF12)
that must never collide with the case-internal release gates (G0..G12) or with
the Builder baseline naming.  This test pins the baseline numbers recorded
before any adapter code existed so later regression closes are honest.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_CF_IDS = [f"CF{i}" for i in range(13)]  # CF0..CF12

# Baseline recorded 2026-08-19 before adapter implementation.
EXPECTED_BASELINE = {
    "CF0": {"name": "Builder", "status": "PASS", "count": 91},
    "CF0B": {"name": "Forward smoke", "status": "PASS_REPORTED"},
    "CF0C": {"name": "Forward reproducibility", "status": "PENDING"},
    "CF1": {"name": "Infra", "status": "PASS", "count": 200, "skipped": 1},
    "CF2": {"name": "Target Adapter", "status": "PENDING"},
    "CF3": {"name": "Runnable Draft", "status": "BLOCKED"},
}


def test_dashboard_never_reuses_G0_builder_baseline_naming():
    """CF IDs must be disjoint from G0..G12 and must not be named like G0."""
    dashboard = ROOT / "docs" / "case-factory" / "ACCEPTANCE-DASHBOARD.md"
    assert dashboard.exists(), "ACCEPTANCE-DASHBOARD.md missing"

    text = dashboard.read_text(encoding="utf-8")

    for cf in EXPECTED_CF_IDS:
        assert re.search(rf"\b{cf}\b", text), f"{cf} missing from dashboard"

    # A dashboard gate labelled "G0 Builder Baseline" would be a collision.
    assert not re.search(r"\bG0\b\s*Builder\s*Baseline", text), (
        "dashboard must not reuse G0 Builder Baseline naming"
    )
    # Every gate row in the dashboard's gate-map table is CF-prefixed.
    rows = re.findall(r"^\| CF\d+\b[^|]*\|", text, flags=re.M)
    assert rows, "gate-map table rows must be CF-prefixed"


def test_baseline_values_recorded():
    """Recorded baseline command, count, skip count, commit and timestamp."""
    text = ROOT / "docs" / "case-factory" / "CASE-FACTORY.md"
    assert text.exists(), "CASE-FACTORY.md missing"
    body = text.read_text(encoding="utf-8")

    # Command evidence.
    assert "pytest" in body
    assert "unittest" in body

    # Counts.
    assert "91" in body, "builder baseline count missing"
    assert "200" in body, "infra baseline count missing"
    assert "1" in body, "infra skip count missing"

    # A commit reference and a timestamp anchor.
    assert re.search(r"\bcommit\b", body, re.IGNORECASE)
    assert "2026-08-19" in body


def test_factory_gates_are_separate_from_case_status():
    """factory_gates, case_status, CF dashboard and G release gates are distinct."""
    text = ROOT / "docs" / "case-factory" / "CASE-FACTORY.md"
    body = text.read_text(encoding="utf-8")

    for name in (
        "case_status",
        "factory_gates",
        "design_valid",
        "target_adapter_valid",
        "runtime_valid",
        "candidate_smoke_valid",
        "discovery_complete",
        "diagnosis_complete",
    ):
        assert name in body, f"{name} not documented"
