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
        if not p.is_dir():
            return ["Source directory does not exist"]

        # Expect at least one dataset or structure source file
        allowed_exts = {".xyz", ".json", ".raw", ".npy", ".npz", ".h5", ".cif", ".pdb", ".yaml"}
        files = list(p.rglob("*"))
        has_dataset = any(f.is_file() and f.suffix.lower() in allowed_exts for f in files)
        if not has_dataset:
            errors.append("MLP source directory must contain at least one dataset/structure artifact (.xyz, .json, .npy, .h5, etc.)")
        return errors

    def validate_design(self, case_ir: dict[str, Any]) -> list[str]:
        errors = []
        cat = case_ir.get("identity", {}).get("category", "")
        if cat != "mlp":
            errors.append(f"Category mismatch: expected 'mlp', got {cat!r}")

        # Check candidate inputs
        inputs = case_ir.get("candidate", {}).get("inputs", [])
        if not inputs:
            errors.append("MLP Case IR must declare at least one candidate input artifact")

        # Check thresholds
        thresholds = case_ir.get("verification", {}).get("thresholds", {})
        if not thresholds:
            errors.append("MLP Case IR must declare numeric verification thresholds")

        return errors

    def derive_verifier_layers(self, case_ir: dict[str, Any]) -> list[str]:
        # MLP requires V0 (existence), V1 (format), V2 (schema/finite), V4 (RMSE/accuracy)
        return ["V0", "V1", "V2", "V4"]

    def validate_calibration(self, calibration_data: dict[str, Any]) -> bool:
        thresholds = calibration_data.get("thresholds", {})
        if not thresholds:
            return False
        for val in thresholds.values():
            if not isinstance(val, (int, float)) or val <= 0:
                return False
        return True
