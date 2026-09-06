"""ccbench.portfolio — Coverage registry, representativeness analysis, and leakage prevention."""

from __future__ import annotations

from ccbench.portfolio.registry import generate_portfolio_report, scan_cases
from ccbench.portfolio.coverage import marginal_coverage_value
from ccbench.portfolio.representativeness import calculate_representativeness
from ccbench.portfolio.leakage import check_case_leakage

__all__ = [
    "generate_portfolio_report",
    "scan_cases",
    "marginal_coverage_value",
    "calculate_representativeness",
    "check_case_leakage",
]
