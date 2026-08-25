"""Quality-funnel regression tests for the portable builder skill."""
from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
SKILL = PACKAGE / "skills" / "build-scientific-benchmark-case"
CLASSIFIER = SKILL / "scripts" / "common" / "classify_failure.py"
TAXONOMY = SKILL / "references" / "common" / "failure-taxonomy.yaml"
CONSISTENCY = SKILL / "scripts" / "categories" / "mlp" / "check_draft_consistency.py"
VALIDATE_CASE = SKILL / "scripts" / "common" / "validate_case.py"
DISCOVERY_POLICY = SKILL / "references" / "common" / "discovery-and-refinement.md"
CROSS_LAYER_POLICY = SKILL / "references" / "common" / "cross-layer-consistency.md"
PROMPT_POLICY = SKILL / "references" / "categories" / "mlp" / "prompt-contract.md"


def _classifier_module():
    spec = importlib.util.spec_from_file_location("discovery_classifier", CLASSIFIER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _consistency_module():
    spec = importlib.util.spec_from_file_location("draft_consistency", CONSISTENCY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_module():
    spec = importlib.util.spec_from_file_location("case_validator", VALIDATE_CASE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_quality_case(tmp_path: Path) -> Path:
    case = tmp_path / "case"
    public = case / "public"
    public.mkdir(parents=True)
    systems = {
        "interfaces": {
            "surface-a": {"composition": {"A": 2}, "cell_angstrom": [5, 5, 8]},
            "surface-b": {"composition": {"A": 2, "B": 1}, "cell_angstrom": [6, 5, 8]},
        }
    }
    (public / "system.json").write_text(json.dumps(systems), encoding="utf-8")
    (public / "recipe.py").write_text(
        'train_data_path=["../train_dataset/surface-a", "../train_dataset/surface-b"]\n',
        encoding="utf-8",
    )
    (case / "case-design.yaml").write_text(
        "category: mlp\nexecution:\n  class: local_sandbox\n",
        encoding="utf-8",
    )
    for base in (case / "reference/hidden-validation", case / "tests/hidden"):
        for name in systems["interfaces"]:
            (base / "hidden-frames" / name).mkdir(parents=True)
        manifest = {
            "systems": sorted(systems["interfaces"]),
            "total_source_frames": 20,
            "total_held_out_frames": 2,
            "frames": [
                {"source": "train_dataset/surface-a", "source_frame": 0},
                {"source": "train_dataset/surface-b", "source_frame": 0},
            ],
        }
        (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    source = case / "reference/source.lock.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        json.dumps({
            "public_input": {
                "files": {
                    "public/system.json": {"sha256": _sha(public / "system.json")},
                    "public/recipe.py": {"sha256": _sha(public / "recipe.py")},
                }
            },
            "hidden_validation": {
                "files": {
                    "reference/hidden-validation/manifest.json": {
                        "sha256": _sha(case / "reference/hidden-validation/manifest.json")
                    }
                }
            },
        }),
        encoding="utf-8",
    )
    return case


def test_discovery_resources_are_shipped() -> None:
    assert TAXONOMY.is_file()
    assert CLASSIFIER.is_file()


def test_structured_classification_priority() -> None:
    classify = _classifier_module().classify

    assert classify({"source": {"critical_conflict": True}})["decision"] == "REJECT"
    assert classify({"failure_code": "INFRA_INVALID"})["blocked_kind"] == "INFRA_INVALID"
    assert classify({"case_design": {"cross_layer_valid": False}})["blocked_kind"] == "CASE_DESIGN_BLOCKED"
    assert classify({"runtime": {"available": False}})["blocked_kind"] == "RUNTIME_BLOCKED"
    assert classify({"telemetry": {"oom": True}})["blocked_kind"] == "RESOURCE_BLOCKED"


def test_sound_structural_run_with_scientific_failure_is_agent_limitation() -> None:
    classify = _classifier_module().classify
    run = {
        "verifier": {
            "levels": {
                "V0": {"ok": True},
                "V1": {"ok": True},
                "V2": {"ok": True},
                "V3": {"ok": True},
                "V4": {"ok": False, "errors": ["accuracy below target"]},
            }
        }
    }
    result = classify(run)
    assert result["blocked_kind"] == "AGENT_LIMITATION"
    assert result["decision"] == "PROMOTE"


def test_skill_exposes_discovery_mode_and_taxonomy() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "discovery" in text
    assert "failure-taxonomy.yaml" in text
    assert "classify_failure.py" in text


def test_consistency_checker_accepts_coherent_case(tmp_path: Path) -> None:
    check_case = _consistency_module().check_case
    result = check_case(_make_quality_case(tmp_path))
    assert result["valid"], result["errors"]
    assert result["canonical_systems"] == ["surface-a", "surface-b"]


def test_consistency_checker_rejects_stale_hidden_directory(tmp_path: Path) -> None:
    check_case = _consistency_module().check_case
    case = _make_quality_case(tmp_path)
    (case / "tests/hidden/hidden-frames/surface-c").mkdir()
    result = check_case(case)
    assert not result["valid"]
    assert any("surface-c" in error and "hidden" in error for error in result["errors"])


def test_consistency_checker_rejects_recipe_extra_system(tmp_path: Path) -> None:
    check_case = _consistency_module().check_case
    case = _make_quality_case(tmp_path)
    recipe = case / "public/recipe.py"
    recipe.write_text(
        recipe.read_text()[:-2] + ', "../train_dataset/surface-c"]\n',
        encoding="utf-8",
    )
    result = check_case(case)
    assert not result["valid"]
    assert any("surface-c" in error and "recipe" in error for error in result["errors"])


def test_consistency_checker_rejects_stale_hash(tmp_path: Path) -> None:
    check_case = _consistency_module().check_case
    case = _make_quality_case(tmp_path)
    system_path = case / "public/system.json"
    system_path.write_text(
        json.dumps(json.loads(system_path.read_text()), indent=2),
        encoding="utf-8",
    )
    result = check_case(case)
    assert not result["valid"]
    assert any("hash mismatch" in error for error in result["errors"])


def test_quality_policy_resources_are_routed() -> None:
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for path in (DISCOVERY_POLICY, CROSS_LAYER_POLICY, PROMPT_POLICY):
        assert path.is_file(), path
        assert path.name in skill
    assert "check_draft_consistency.py" in skill


def test_mlp_prompt_contract_covers_full_workflow_failure_modes() -> None:
    text = PROMPT_POLICY.read_text(encoding="utf-8").lower()
    required = (
        "paper_faithful",
        "benchmark_adaptation",
        "every target system",
        "stopping criteria",
        "trajectory",
        "published coordinates",
        "labeled dataset",
        "executed",
        "machine-readable manifest",
        "provenance",
    )
    for phrase in required:
        assert phrase in text, phrase


def test_reference_policy_forbids_preflight_drift_and_false_completion() -> None:
    text = (
        SKILL / "references/common/reference-and-solution-policy.md"
    ).read_text(encoding="utf-8").lower()
    assert "production code" in text
    assert "terminal state" in text
    assert "planned" in text and "deferred" in text
    assert "completed" in text and "missing" in text


def test_validate_case_reports_category_quality_checks(tmp_path: Path) -> None:
    validate_case = _validate_module().validate_case
    case = _make_quality_case(tmp_path)
    result = validate_case(case)
    assert result["quality_checks"]["valid"] is True

    (case / "tests/hidden/hidden-frames/surface-c").mkdir()
    result = validate_case(case)
    assert result["quality_checks"]["valid"] is False
    assert any("surface-c" in error for error in result["errors"])


def test_templates_expose_quality_funnel_slots() -> None:
    template = SKILL / "assets/case-template/common"
    design = (template / "case-design.yaml").read_text(encoding="utf-8")
    instruction = (template / "instruction.md").read_text(encoding="utf-8")
    contract = (template / "CONTRACT.md").read_text(encoding="utf-8")
    validation = json.loads((template / "VALIDATION.json").read_text())

    assert "source_relationship" in design
    assert "paper_faithful" in design and "benchmark_adaptation" in design
    assert "target systems" in instruction.lower()
    assert "executed" in instruction.lower()
    assert "source relationship" in contract.lower()
    assert "discovery_prerequisites" in validation
    assert validation["benchmark_valid"] is False
