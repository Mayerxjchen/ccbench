"""Luna v1 regression suite: the four documented forward-test failures,
frozen as machine-readable drift fixtures, plus the single-writer
runnable_draft guard and a positive end-to-end gate run.

Luna v1 (experiments/case-builder-luna-v1) produced a case that *looked*
complete but failed the real chain in exactly four ways:

1. semantic spec invalid — `claim_status: unknown` carrying a concrete value;
2. instruction/bundle drift — prose cited `public/CuInP2S6.cif` while the
   packaged candidate bundle had no such path;
3. no executable fixture matrix — negative fixtures were prose descriptions;
4. no verifier closure — `tests/test.sh` ran builder self-tests instead of
   grading the sealed submission, and never produced a standard result.

Every test here asserts the current tooling *blocks* the corresponding drift
and — for the `mvp-passing` fixture — that one tree clears the whole L1 gate.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PACKAGE = Path(__file__).resolve().parents[1]
SKILL = PACKAGE / "skills" / "build-scientific-benchmark-case"
COMMON_SCRIPTS = SKILL / "scripts" / "common"
MLP_SCRIPTS = SKILL / "scripts" / "categories" / "mlp"
RUNNABLE = COMMON_SCRIPTS / "check_discovery_runnable.py"
VALIDATE_CASE = COMMON_SCRIPTS / "validate_case.py"
CONSISTENCY = MLP_SCRIPTS / "check_draft_consistency.py"
VALIDATE_SPEC = MLP_SCRIPTS / "validate_spec.py"
FIXTURE_CASE = PACKAGE / "tests" / "fixtures" / "luna-regressions" / "mvp-passing"
LUNA_V1_CASE = PACKAGE.parent / "experiments" / "case-builder-luna-v1" / "generated" / "901-luna-cips-active-distillation"

sys.path.insert(0, str(PACKAGE / "tests"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


consistency = _load("luna_consistency", CONSISTENCY)


def _case_copy(tmp_path: Path) -> Path:
    assert FIXTURE_CASE.is_dir(), "mvp-passing fixture missing"
    case = tmp_path / "case"
    shutil.copytree(FIXTURE_CASE, case)
    return case


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------- class 1
# spec: unknown claim_status carrying a concrete value


def test_spec_unknown_claim_with_concrete_value_is_blocked(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    spec_path = case / "source" / "mlp-reproduction-spec.yaml"
    evidence_path = case / "source" / "source-evidence-map.yaml"
    spec = _load_yaml(spec_path)
    spec["spin_states"] = {"claim_status": "unknown", "value": [0.42]}
    _write_yaml(spec_path, spec)
    proc = subprocess.run(
        [sys.executable, str(VALIDATE_SPEC), str(spec_path), "--evidence", str(evidence_path)],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode != 0
    assert "claim_status=unknown should not carry a concrete value" in proc.stdout + proc.stderr


# ---------------------------------------------------------------- class 2
# instruction/bundle drift: public/ path the candidate never sees


def test_instruction_public_path_absent_from_bundle_is_blocked(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    instruction = case / "instruction.md"
    text = instruction.read_text(encoding="utf-8")
    instruction.write_text(
        text.replace("public/initial.xyz", "public/CuInP2S6-drift.cif"), encoding="utf-8"
    )
    errors = consistency.check_machine_layers(case)
    assert any(
        "public/CuInP2S6-drift.cif" in e and "no such path" in e for e in errors
    ), errors


def test_submission_root_disagreement_is_blocked(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    design = _load_yaml(case / "case-design.yaml")
    design["submission"]["root"] = "submission"  # task.toml keeps "."
    _write_yaml(case / "case-design.yaml", design)
    errors = consistency.check_machine_layers(case)
    assert any("submission root disagreement" in e for e in errors), errors


# ---------------------------------------------------------------- class 3
# prose-only negative fixtures do not close the matrix


def test_missing_executable_negative_fixtures_are_blocked(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    for name in ("empty", "forged-manifest", "missing-model", "broken-lineage"):
        shutil.rmtree(case / "tests" / "fixtures" / "negative" / name)
    shutil.rmtree(case / "tests" / "fixtures" / "positive" / "structural-minimal")
    runnable = _load("luna_runnable_matrix", RUNNABLE)
    verdict = runnable.Verdict()
    runnable.check_fixture_matrix(case, verdict)
    errors = verdict.checks["fixture_matrix"]["errors"]
    assert any("prose-only fixture descriptions" in e for e in errors), errors
    assert any("needs one executable structural submission" in e for e in errors), errors


# ---------------------------------------------------------------- class 4
# test.sh is a builder self-test, not a graded verifier entry


def test_verifier_closure_missing_is_blocked(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    tests_dir = case / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test.sh").write_text(
        "#!/usr/bin/env bash\n"
        "# construction sanity check masquerading as the entry (Luna v1): "
        "checks the tree, never the submission\n"
        'python3 -c "import pathlib,sys; sys.exit(0 if pathlib.Path(\'test.sh\').exists() else 1)"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (tests_dir / "test.sh").chmod(0o755)
    runnable = _load("luna_runnable_smoke", RUNNABLE)
    verdict = runnable.Verdict()
    runnable.check_mount_smoke(case, tmp_path / "work", verdict)
    errors = verdict.checks["verifier_mount_smoke"]["errors"]
    assert errors, "ungraded test.sh must not pass the mount smoke"
    assert any("result.json" in e for e in errors), errors


def test_builder_selftest_is_never_wired_into_test_sh() -> None:
    test_sh = (FIXTURE_CASE / "tests" / "test.sh").read_text(encoding="utf-8")
    code_lines = [ln for ln in test_sh.splitlines() if not ln.lstrip().startswith("#")]
    code = "\n".join(code_lines)
    assert "test_verifier_contract" not in code
    assert "pytest" not in code
    assert "verifier.py" in code
    assert "result.json" in code


# ------------------------------------------------- held-out / metric / cap drift


def test_heldout_dual_ownership_is_blocked(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    design = _load_yaml(case / "case-design.yaml")
    design["validation_sets"].append(
        {"name": "heldout-water", "owner": "candidate_generated"}
    )
    _write_yaml(case / "case-design.yaml", design)
    errors = consistency.check_machine_layers(case)
    assert any("multiple owners" in e and "heldout-water" in e for e in errors), errors


def test_metric_comparator_drift_is_blocked(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    instruction = case / "instruction.md"
    instruction.write_text(
        instruction.read_text(encoding="utf-8").replace("`< 0.10`", "`<= 0.10`"),
        encoding="utf-8",
    )
    errors = consistency.check_machine_layers(case)
    assert any("metric comparator drift" in e and "force_rmse" in e for e in errors), errors


def test_dft_capability_on_teacher_labels_is_blocked(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    design = _load_yaml(case / "case-design.yaml")
    design["workflow_capabilities"].append("dft_dynamics")
    _write_yaml(case / "case-design.yaml", design)
    assert design["label_source"] == "teacher_inference"
    errors = consistency.check_machine_layers(case)
    assert any("dft_dynamics claimed" in e for e in errors), errors


def test_contract_visibility_mismatch_is_blocked(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    design = _load_yaml(case / "case-design.yaml")
    design["contract"]["candidate_visible"] = True  # no staging rule added
    _write_yaml(case / "case-design.yaml", design)
    errors = consistency.check_machine_layers(case)
    assert any("does not stage CONTRACT.md" in e for e in errors), errors


# ------------------------------------------------- single-writer state guard


def test_runnable_draft_without_derivation_is_rejected(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    validation_path = case / "VALIDATION.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["case_status"] = "runnable_draft"
    validation_path.write_text(json.dumps(validation, indent=2, sort_keys=True), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(VALIDATE_CASE), str(case)],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode != 0
    assert "runnable_derivation" in proc.stdout + proc.stderr


def test_runnable_draft_with_foreign_derivation_is_rejected(tmp_path: Path) -> None:
    case = _case_copy(tmp_path)
    validation_path = case / "VALIDATION.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["case_status"] = "runnable_draft"
    validation["runnable_derivation"] = {"derived_by": "hand", "mvp_runnable": True}
    validation_path.write_text(json.dumps(validation, indent=2, sort_keys=True), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(VALIDATE_CASE), str(case)],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode != 0
    assert "runnable_derivation" in proc.stdout + proc.stderr


# ------------------------------------------------- positive: one tree clears L1


def _find_repo_root() -> Path | None:
    for parent in [PACKAGE, *PACKAGE.parents]:
        if (parent / "dftworld_bench").is_dir():
            return parent
    return None


@pytest.mark.skipif(_find_repo_root() is None, reason="dftworld_bench repo not on disk")
def test_mvp_passing_fixture_clears_gate_and_derives_state(tmp_path: Path) -> None:
    repo_root = _find_repo_root()
    case = _case_copy(tmp_path)
    report_path = tmp_path / "MVP-READINESS.json"
    proc = subprocess.run(
        [
            sys.executable, str(RUNNABLE), str(case),
            "--repo-root", str(repo_root),
            "--output", str(report_path),
            "--derive-state",
            "--json",
        ],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mvp_runnable"] is True
    assert report["benchmark_valid"] is False
    assert report["blocking_errors"] == []
    assert set(report["checks"]) >= {
        "validate_case", "category_semantics", "verifier_plan", "fixture_matrix",
        "cross_layer_consistency", "real_packaging", "bundle_agreement",
        "verifier_mount_smoke", "pre_discovery_honesty",
    }
    validation = json.loads((case / "VALIDATION.json").read_text(encoding="utf-8"))
    assert validation["case_status"] == "runnable_draft"
    assert validation["runnable_derivation"]["derived_by"] == "check_discovery_runnable.py"
    assert validation["runnable_derivation"]["mvp_runnable"] is True
    accept = subprocess.run(
        [sys.executable, str(VALIDATE_CASE), str(case)],
        text=True, capture_output=True, check=False,
    )
    assert accept.returncode == 0, accept.stdout + accept.stderr


# ------------------------------------------------- Luna v1 itself stays blocked


@pytest.mark.skipif(not LUNA_V1_CASE.is_dir(), reason="Luna v1 experiment not on disk")
def test_frozen_luna_v1_artifact_is_not_runnable(tmp_path: Path) -> None:
    repo_root = _find_repo_root()
    report_path = tmp_path / "MVP-READINESS.json"
    args = [sys.executable, str(RUNNABLE), str(LUNA_V1_CASE), "--output", str(report_path), "--json"]
    if repo_root is not None:
        args += ["--repo-root", str(repo_root)]
    proc = subprocess.run(args, text=True, capture_output=True, check=False)
    assert proc.returncode != 0, "the frozen Luna v1 artifact must not pass L1"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mvp_runnable"] is False
    joined = "\n".join(report["blocking_errors"])
    # the four documented Luna v1 failure classes
    assert "claim_status=unknown should not carry a concrete value" in joined
    assert "public/CuInP2S6.cif" in joined
    assert "prose-only fixture descriptions" in joined
    assert "does not exist; regenerate from the case template" in joined
    # and the single-writer guard rejects its hand-asserted runnable_draft
    assert "runnable_derivation" in joined
