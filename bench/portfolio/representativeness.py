"""Portfolio representativeness and balance metrics."""

from __future__ import annotations

import math
from typing import Any

from bench.portfolio.registry import generate_portfolio_report


def calculate_representativeness(portfolio_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Calculate portfolio balance, entropy, and representativeness scores."""
    report = portfolio_report or generate_portfolio_report()
    total_cases = report.get("total_cases", 0)
    if total_cases == 0:
        return {"overall_balance_score": 0.0, "dimension_entropy": {}}

    dist = report.get("dimension_distribution", {})
    entropy_map: dict[str, float] = {}

    for dim, counts in dist.items():
        if not counts:
            entropy_map[dim] = 0.0
            continue
        ent = 0.0
        for count in counts.values():
            p = count / float(total_cases)
            if p > 0:
                ent -= p * math.log2(p)
        entropy_map[dim] = round(ent, 3)

    avg_entropy = sum(entropy_map.values()) / max(1, len(entropy_map))
    return {
        "overall_balance_score": round(avg_entropy, 3),
        "dimension_entropy": entropy_map,
    }
