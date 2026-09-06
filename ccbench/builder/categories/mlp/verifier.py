"""MLP-specific verification primitives and metrics."""

from __future__ import annotations

import math
from typing import Any


def verify_mlp_metrics(
    energy_rmse: float,
    force_rmse: float,
    thresholds: dict[str, float],
) -> tuple[bool, dict[str, Any]]:
    """Check MLP energy and force RMSE against declared calibration thresholds."""
    max_e = thresholds.get("energy_rmse_max", 0.05)
    max_f = thresholds.get("force_rmse_max", 0.15)

    e_pass = not math.isnan(energy_rmse) and energy_rmse <= max_e
    f_pass = not math.isnan(force_rmse) and force_rmse <= max_f

    passed = e_pass and f_pass
    details = {
        "energy_rmse": {"value": energy_rmse, "threshold": max_e, "passed": e_pass},
        "force_rmse": {"value": force_rmse, "threshold": max_f, "passed": f_pass},
    }
    return passed, details
