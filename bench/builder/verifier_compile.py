"""Verifier Compiler — Compiles declarative verifier plans into executable verifier packages.

Security invariants:
- Unknown primitives cause a compile-time error (never silently fall through).
- Missing thresholds cause a runtime hard-fail (no code-level defaults).
- Declared layers with no associated rules cause a compile-time error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bench.builder.verifier_plan import VerifierPlan


class VerifierCompileError(ValueError):
    """Raised when verifier compilation encounters an invalid plan."""


# Canonical set of known verification primitives.
# Any primitive NOT in this set will be rejected at compile time.
KNOWN_PRIMITIVES: frozenset[str] = frozenset({
    "artifact_exists",
    "numeric_threshold",
    "range",
    "json_schema",
    "mlp.energy_rmse",
    "mlp.force_rmse",
    "mlp.stability",
})


def compile_verifier(
    plan: VerifierPlan,
    target_dir: Path,
) -> dict[str, Path]:
    """Compile a VerifierPlan into executable verifier artifacts in target_dir.

    Raises VerifierCompileError if:
    - Any rule references an unknown primitive.
    - A declared layer has zero associated rules.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    # ── Compile-time validation ──────────────────────────────────────

    # 1. Reject unknown primitives
    for rule in plan.rules:
        if rule.primitive not in KNOWN_PRIMITIVES:
            raise VerifierCompileError(
                f"Unknown verifier primitive '{rule.primitive}'; "
                f"known primitives: {sorted(KNOWN_PRIMITIVES)}"
            )

    # 2. Reject declared layers with no rules
    rule_layers = {r.layer for r in plan.rules}
    for item in plan.layers:
        layer_name = item.layer if hasattr(item, "layer") else str(item)
        is_selected = (item.status.value == "selected" if hasattr(item.status, "value") else item.status == "selected") if hasattr(item, "status") else True
        if is_selected and layer_name not in rule_layers:
            raise VerifierCompileError(
                f"Selected layer '{layer_name}' has no associated rules; "
                f"every selected layer must have at least one rule"
            )

    # ── Emit plan JSON ───────────────────────────────────────────────

    plan_path = target_dir / "verifier_plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")

    # ── Emit compiled verify.py ──────────────────────────────────────

    verify_py_content = f'''"""Compiled verifier for Bench case {plan.case_id}.

Security: unknown primitives cause exit(2); missing thresholds cause exit(2).
"""

from __future__ import annotations

import json
import math
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
    """Check MLP metric against a threshold.

    The threshold MUST be explicitly provided in params; there is no default.
    Missing threshold is a fatal configuration error (exit 2).
    """
    p = submission_root / target
    if not p.is_file():
        return False
    metric_name = params.get("metric", "rmse")
    threshold = params.get("threshold")
    if threshold is None:
        print(
            f"FATAL: no threshold for metric '{{metric_name}}' on target '{{target}}'. "
            f"Thresholds must be explicitly defined in the verifier plan.",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        val = float(doc.get(metric_name, math.nan))
        return not math.isnan(val) and val <= float(threshold)
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
    import argparse
    parser = argparse.ArgumentParser(description="Bench verifier entrypoint")
    parser.add_argument("submission_root", nargs="?", default=".", help="Path to submission root")
    parser.add_argument("--profile", choices=["structural", "formal"], default="formal", help="Verification profile")
    args = parser.parse_args()

    plan_doc = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    submission_root = Path(args.submission_root)
    profile = args.profile

    results = {{}}
    all_passed = True
    raw_layers = plan_doc.get("layers", [])
    declared_layers = [
        l["layer"] if isinstance(l, dict) else str(l)
        for l in raw_layers
        if (l.get("status") == "selected" if isinstance(l, dict) else True)
    ]
    layer_results: dict[str, bool] = {{layer: True for layer in declared_layers}}

    for rule in plan_doc.get("rules", []):
        prim = rule["primitive"]
        target = rule["target"]
        params = rule.get("params", {{}})
        layer = rule.get("layer", "V1")

        # Under structural profile, defer scientific benchmark layers (V4, V5, V6)
        if profile == "structural" and layer in ("V4", "V5", "V6"):
            results[f"{{layer}}:{{prim}}:{{target}}"] = {{"passed": True, "deferred": True}}
            continue

        handler = PRIMITIVE_HANDLERS.get(prim)
        if handler is None:
            print(
                f"FATAL: unknown verifier primitive '{{prim}}'. "
                f"Known primitives: {{sorted(PRIMITIVE_HANDLERS.keys())}}. Aborting.",
                file=sys.stderr,
            )
            sys.exit(2)

        passed = handler(target, params, submission_root)
        results[f"{{layer}}:{{prim}}:{{target}}"] = passed
        if not passed:
            all_passed = False
            layer_results[layer] = False

    # Enforce mandatory layers under formal profile
    if profile == "formal":
        layers_with_rules = set()
        for rule in plan_doc.get("rules", []):
            layers_with_rules.add(rule.get("layer", "V1"))
        for layer in declared_layers:
            if layer not in layers_with_rules:
                print(
                    f"FATAL: declared layer '{{layer}}' has no associated rules. "
                    f"Mandatory layers must not be silently dropped.",
                    file=sys.stderr,
                )
                sys.exit(2)

    result_payload = {{
        "case_id": plan_doc.get("case_id"),
        "profile": profile,
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
