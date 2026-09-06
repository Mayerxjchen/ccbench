"""MLP Category Plugin implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ccbench.builder.categories import CaseCategoryPlugin
from ccbench.builder.categories.mlp.verifier import verify_mlp_metrics


class MlpCategoryPlugin(CaseCategoryPlugin):
    """Category plugin for Machine Learning Interatomic Potentials (MLP)."""

    category_name: str = "mlp"

    def validate_source(self, source_dir: Any) -> list[str]:
        p = Path(source_dir)
        errors = []
        # Expect dataset or training structure in source
        return errors

    def validate_design(self, case_ir: dict[str, Any]) -> list[str]:
        errors = []
        cat = case_ir.get("identity", {}).get("category", "")
        if cat != "mlp":
            errors.append(f"Category mismatch: expected 'mlp', got {cat!r}")
        return errors

    def derive_verifier_layers(self, case_ir: dict[str, Any]) -> list[str]:
        # MLP requires V0 (existence), V1 (format), V2 (parity/finite), V4 (RMSE/accuracy), V7/V8 (stability)
        return ["V0", "V1", "V2", "V4", "V7", "V8"]

    def validate_calibration(self, calibration_data: dict[str, Any]) -> bool:
        thresholds = calibration_data.get("thresholds", {})
        e_max = thresholds.get("energy_rmse_max")
        f_max = thresholds.get("force_rmse_max")
        if e_max is None or f_max is None:
            return False
        return e_max > 0 and f_max > 0
