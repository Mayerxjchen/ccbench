"""Tests for bench.portfolio module."""

from __future__ import annotations

from bench.contracts.case import CoverageTags
from bench.portfolio.registry import generate_portfolio_report, scan_cases
from bench.portfolio.coverage import marginal_coverage_value
from bench.portfolio.representativeness import calculate_representativeness
from bench.portfolio.leakage import check_case_leakage
from bench.paths import CASES_DIR


def test_scan_active_cases():
    cases, errors = scan_cases()
    assert len(cases) == 5
    assert len(errors) == 0


def test_generate_portfolio_report():
    report = generate_portfolio_report()
    assert report["total_cases"] == 5
    assert len(report["cases"]) == 5
    assert "scientific_domain" in report["dimension_distribution"]


def test_marginal_coverage():
    cand = CoverageTags(
        scientific_domain="test_domain",
        method_family="model_evaluation",
        material_class="molecular_crystal",
        computation_type="single_shot_md",
    )
    existing = [
        CoverageTags(
            scientific_domain="other_domain",
            method_family="end_to_end_potential",
            material_class="inorganic_2d",
            computation_type="iterative_training",
        )
    ]
    score = marginal_coverage_value(cand, existing)
    assert score > 0.0


def test_representativeness_metrics():
    metrics = calculate_representativeness()
    assert "overall_balance_score" in metrics
    assert "dimension_entropy" in metrics
    assert metrics["overall_balance_score"] >= 0.0


def test_leakage_check_on_active_cases():
    for case_dir in CASES_DIR.iterdir():
        if case_dir.is_dir() and (case_dir / "case.toml").is_file():
            passed, violations = check_case_leakage(case_dir)
            assert passed is True
            assert len(violations) == 0
