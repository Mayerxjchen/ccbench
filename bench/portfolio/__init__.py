"""bench.portfolio — Coverage registry, representativeness analysis, and leakage prevention."""

from __future__ import annotations

from bench.portfolio.registry import generate_portfolio_report, scan_cases
from bench.portfolio.coverage import marginal_coverage_value
from bench.portfolio.representativeness import calculate_representativeness
from bench.portfolio.leakage import check_case_leakage

__all__ = [
    "generate_portfolio_report",
    "scan_cases",
    "marginal_coverage_value",
    "calculate_representativeness",
    "check_case_leakage",
]
