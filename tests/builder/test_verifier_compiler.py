"""Tests for Verifier Compiler, layer explicitness, and threshold SSOT binding."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import pytest

from ccbench.builder.verifier_plan import LayerDeclaration, LayerStatus, VerifierPlan, VerifierPlanError, VerifierRule
from ccbench.builder.verifier_compile import VerifierCompileError, compile_verifier


def test_compile_and_run_verifier_smoke(tmp_path: Path):
    plan = VerifierPlan(
        case_id="toy-001",
        layers=["V0", "V1", "V4"],
        rules=[
            VerifierRule(
                primitive="artifact_exists",
                target="final",
                layer="V0",
                params={"is_dir": True},
            ),
            VerifierRule(
                primitive="artifact_exists",
                target="final/result.json",
                layer="V1",
            ),
            VerifierRule(
                primitive="mlp.energy_rmse",
                target="final/result.json",
                threshold_ref="rmse_threshold",
                params={"metric": "rmse"},
                layer="V4",
            ),
        ],
        thresholds={"rmse_threshold": 0.05},
    )

    verifier_dir = tmp_path / "verifier"
    compiled = compile_verifier(plan, verifier_dir)

    assert compiled["verify_py"].is_file()
    assert compiled["test_sh"].is_file()
    assert compiled["verifier_plan"].is_file()

    # Create dummy submission workspace
    sub_dir = tmp_path / "submission"
    final_dir = sub_dir / "final"
    final_dir.mkdir(parents=True)

    # 1. Failing run (missing result.json)
    proc1 = subprocess.run(
        [sys.executable, str(compiled["verify_py"]), str(sub_dir)],
        cwd=tmp_path,
        capture_output=True,
    )
    assert proc1.returncode == 1

    # 2. Failing run (RMSE above threshold)
    (final_dir / "result.json").write_text(json.dumps({"rmse": 0.12}), encoding="utf-8")
    proc2 = subprocess.run(
        [sys.executable, str(compiled["verify_py"]), str(sub_dir)],
        cwd=tmp_path,
        capture_output=True,
    )
    assert proc2.returncode == 1

    # 3. Passing run (RMSE below threshold)
    (final_dir / "result.json").write_text(json.dumps({"rmse": 0.03}), encoding="utf-8")
    proc3 = subprocess.run(
        [sys.executable, str(compiled["verify_py"]), str(sub_dir)],
        cwd=tmp_path,
        capture_output=True,
    )
    assert proc3.returncode == 0


def test_unknown_primitive_fails_at_compile_time(tmp_path: Path):
    plan = VerifierPlan(
        case_id="toy-002",
        layers=["V0"],
        rules=[
            VerifierRule(
                primitive="mlp.energy_rms",  # typo!
                target="final",
                layer="V0",
            )
        ],
    )
    with pytest.raises(VerifierCompileError, match="Unknown verifier primitive 'mlp.energy_rms'"):
        compile_verifier(plan, tmp_path / "verifier")


def test_silent_layer_drop_rejected(tmp_path: Path):
    with pytest.raises(VerifierPlanError, match="no associated verification rules"):
        VerifierPlan(
            case_id="toy-003",
            layers=["V0", "V1", "V4"],
            rules=[
                VerifierRule(primitive="artifact_exists", target="final", layer="V0"),
                VerifierRule(primitive="artifact_exists", target="final/a.txt", layer="V1"),
                # V4 has NO rules!
            ],
        )


def test_unresolved_threshold_ref_rejected():
    with pytest.raises(VerifierPlanError, match="Unresolved threshold_ref 'missing_ref'"):
        VerifierPlan(
            case_id="toy-004",
            layers=["V0"],
            rules=[
                VerifierRule(
                    primitive="mlp.energy_rmse",
                    target="final/res.json",
                    threshold_ref="missing_ref",
                    layer="V0",
                )
            ],
            thresholds={"other_ref": 0.05},
        )
