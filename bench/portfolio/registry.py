"""Portfolio coverage registry — scan cases and produce coverage report."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from bench.contracts.case import CaseSpec, CoverageTags, load_coverage_vocabularies
from bench.paths import CASES_DIR

DIMENSIONS = ("scientific_domain", "method_family", "material_class", "computation_type")


def scan_cases(cases_dir: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load all cases and extract coverage information."""
    target_dir = cases_dir or CASES_DIR
    cases: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for case_dir in sorted(target_dir.iterdir()):
        if not case_dir.is_dir() or not (case_dir / "case.toml").is_file():
            continue
        try:
            spec = CaseSpec.load(case_dir)
            cov = spec.coverage
            cases.append({
                "case_id": spec.case_id,
                "case_dir": str(case_dir),
                "coverage": {dim: getattr(cov, dim, "") for dim in DIMENSIONS},
            })
        except Exception as exc:
            errors.append({"case_dir": str(case_dir), "error": str(exc)})

    return cases, errors


def generate_portfolio_report(cases_dir: Path | None = None) -> dict[str, Any]:
    """Generate portfolio summary report."""
    cases, errors = scan_cases(cases_dir)
    total = len(cases)

    dimension_counts: dict[str, dict[str, int]] = {}
    for dim in DIMENSIONS:
        c = Counter()
        for case in cases:
            val = case["coverage"].get(dim)
            if val:
                c[val] += 1
        dimension_counts[dim] = dict(c)

    return {
        "total_cases": total,
        "cases": cases,
        "errors": errors,
        "dimension_distribution": dimension_counts,
    }
