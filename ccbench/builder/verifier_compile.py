"""Verifier Compiler — Compiles declarative verifier plans into executable verifier packages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ccbench.builder.verifier_plan import VerifierPlan


def compile_verifier(
    plan: VerifierPlan,
    target_dir: Path,
) -> dict[str, Path]:
    """Compile a VerifierPlan into executable verifier artifacts in target_dir."""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    plan_path = target_dir / "verifier_plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")

    verify_py_content = f'''"""Compiled verifier for CCBench case {plan.case_id}."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

PLAN_FILE = Path(__file__).parent / "verifier_plan.json"


def check_artifact_exists(target: str, params: dict[str, Any], submission_root: Path) -> bool:
    p = submission_root / target
    if params.get("is_dir", False):
        return p.is_dir()
    return p.is_file()


def check_numeric_threshold(target: str, params: dict[str, Any], submission_root: Path) -> bool:
    p = submission_root / target
    if not p.is_file():
        return False
    try:
        val = float(p.read_text(encoding="utf-8").strip())
        max_val = params.get("max_val")
        min_val = params.get("min_val")
        if max_val is not None and val > max_val:
            return False
        if min_val is not None and val < min_val:
            return False
        return True
    except Exception:
        return False


def check_range(target: str, params: dict[str, Any], submission_root: Path) -> bool:
    return check_numeric_threshold(target, params, submission_root)


def check_json_schema(target: str, params: dict[str, Any], submission_root: Path) -> bool:
    p = submission_root / target
    if not p.is_file():
        return False
    try:
        import jsonschema
        doc = json.loads(p.read_text(encoding="utf-8"))
        schema = params.get("schema", {{}})
        jsonschema.validate(instance=doc, schema=schema)
        return True
    except Exception:
        return False


def check_mlp_metric(target: str, params: dict[str, Any], submission_root: Path) -> bool:
    p = submission_root / target
    if not p.is_file():
        return False
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        metric_name = params.get("metric", "rmse")
        val = float(doc.get(metric_name, math.nan))
        threshold = float(params.get("threshold", 0.05))
        return not math.isnan(val) and val <= threshold
    except Exception:
        return False


PRIMITIVE_HANDLERS = {{
    "artifact_exists": check_artifact_exists,
    "numeric_threshold": check_numeric_threshold,
    "range": check_range,
    "json_schema": check_json_schema,
    "mlp.energy_rmse": check_mlp_metric,
    "mlp.force_rmse": check_mlp_metric,
    "mlp.stability": check_mlp_metric,
}}


def main() -> int:
    plan_doc = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    submission_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")

    results = {{}}
    all_passed = True
    layer_results: dict[str, bool] = {{layer: True for layer in plan_doc.get("layers", [])}}

    for rule in plan_doc.get("rules", []):
        prim = rule["primitive"]
        target = rule["target"]
        params = rule.get("params", {{}})
        layer = rule.get("layer", "V1")

        handler = PRIMITIVE_HANDLERS.get(prim)
        passed = False
        if handler:
            passed = handler(target, params, submission_root)
        else:
            passed = (submission_root / target).exists()

        results[f"{{layer}}:{{prim}}:{{target}}"] = passed
        if not passed:
            all_passed = False
            layer_results[layer] = False

    result_payload = {{
        "passed": all_passed,
        "layers": layer_results,
        "details": results,
    }}

    out_file = Path("verifier_result.json")
    out_file.write_text(json.dumps(result_payload, indent=2), encoding="utf-8")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
'''
    verify_py = target_dir / "verify.py"
    verify_py.write_text(verify_py_content, encoding="utf-8")

    test_sh_content = """#!/bin/bash
set -e
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "${ROOT_DIR}/verify.py" "$@"
"""
    test_sh = target_dir / "test.sh"
    test_sh.write_text(test_sh_content, encoding="utf-8")
    test_sh.chmod(0o755)

    return {
        "verifier_plan": plan_path,
        "verify_py": verify_py,
        "test_sh": test_sh,
    }
