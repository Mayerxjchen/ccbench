"""Gold contract matrix for the 5 preserved end-to-end HPC scientific cases.

This test suite validates that the 5 formal benchmark cases:
- 031-matclaw-cips-active-distillation
- 032-matclaw-cips-curie-temperature
- 033-matclaw-cips-domain-wall-search
- 034-ai2kit-water64-end-to-end-potential
- 042-go-water-dpmp

satisfy all strict contract constraints, contain zero legacy candidate image bindings,
correctly declare runtime requirements, match canonical schemas, and exhibit zero drift
under the migration script.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import pytest
import jsonschema

from dftworld_bench.contracts.case import (
    CaseSpec,
    KNOWN_RUNTIME_FAMILIES,
    RUNTIME_FAMILY_CAPABILITIES,
    SCHEMA_PATH,
)
import scripts.infra.migrate_case_contracts as migrator

ROOT = Path(__file__).resolve().parents[2]

PRESERVED_HPC_CASE_IDS = (
    "031-matclaw-cips-active-distillation",
    "032-matclaw-cips-curie-temperature",
    "033-matclaw-cips-domain-wall-search",
    "034-ai2kit-water64-end-to-end-potential",
    "042-go-water-dpmp",
)

DELETED_SHORT_CASE_PREFIXES = tuple(f"{i:03d}-" for i in range(1, 31)) + tuple(
    f"{i:03d}-" for i in range(35, 42)
)


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_numbered_cases_contain_only_preserved_five_long_cases():
    numbered_cases = sorted(
        p.name for p in ROOT.iterdir() if p.is_dir() and re.fullmatch(r"\d{3}-.+", p.name)
    )
    assert numbered_cases == list(PRESERVED_HPC_CASE_IDS)
    for deleted_prefix in DELETED_SHORT_CASE_PREFIXES:
        assert not any(name.startswith(deleted_prefix) for name in numbered_cases), (
            f"Deleted short case {deleted_prefix} unexpectedly found"
        )


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_spec_loads_strictly_with_hpc_controller(case_id: str):
    case_dir = ROOT / case_id
    spec = CaseSpec.load(case_dir)
    assert spec.execution_class == "hpc_controller"
    assert spec.legacy_execution_value is None
    assert spec.legacy_agent_fields == ()
    assert spec.candidate_image is None, "Candidate agent image must not be hardcoded in task manifest"


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_manifest_satisfies_case_schema(case_id: str):
    case_dir = ROOT / case_id
    task_toml = case_dir / "task.toml"
    assert task_toml.is_file()
    schema = _load_schema()
    import tomllib
    raw = tomllib.loads(task_toml.read_text(encoding="utf-8"))
    contract_doc = {k: raw[k] for k in ("execution", "candidate", "hpc") if k in raw}
    jsonschema.validate(instance=contract_doc, schema=schema)


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_instruction_and_public_files_integrity(case_id: str):
    case_dir = ROOT / case_id
    spec = CaseSpec.load(case_dir)
    instruction = case_dir / spec.instruction_path
    assert instruction.is_file(), f"instruction file {spec.instruction_path} missing in {case_id}"
    text = instruction.read_text(encoding="utf-8").strip()
    assert len(text) > 100, f"instruction text too short in {case_id}"

    assert len(spec.public_files) >= 1
    for rule in spec.public_files:
        assert rule.source == "public/**"
        assert rule.destination == "."
        assert rule.strip_prefix == "public"


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_verifiers_exist_and_non_empty(case_id: str):
    case_dir = ROOT / case_id
    tests_dir = case_dir / "tests"
    assert tests_dir.is_dir(), f"tests directory missing in {case_id}"
    test_sh = tests_dir / "test.sh"
    assert test_sh.is_file()
    assert len(test_sh.read_text(encoding="utf-8").strip()) > 0
    test_py = tests_dir / "test_outputs.py"
    assert test_py.is_file()
    assert len(test_py.read_text(encoding="utf-8").strip()) > 0


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_runtime_requirements_matrix(case_id: str):
    case_dir = ROOT / case_id
    spec = CaseSpec.load(case_dir)
    assert len(spec.runtime_requirements) >= 1

    for req in spec.runtime_requirements:
        assert req.family in KNOWN_RUNTIME_FAMILIES
        assert req.name != ""
        assert req.version != ""

    if case_id.startswith("031") or case_id.startswith("032") or case_id.startswith("033"):
        assert len(spec.runtime_requirements) == 1
        req = spec.runtime_requirements[0]
        assert req.family == "matclaw-cips"
        assert req.name == "matclaw-cips"
        assert req.version == "==2.2.11"
        assert "dispatcher.gpu" in spec.effective_qualification_requires
        assert "runtime.matclaw-gpu" in spec.effective_qualification_requires
    elif case_id.startswith("034"):
        assert len(spec.runtime_requirements) == 2
        families = {r.family for r in spec.runtime_requirements}
        assert families == {"ai2kit", "cp2k"}
        assert "runtime.ai2kit" in spec.effective_qualification_requires
        assert "runtime.cp2k" in spec.effective_qualification_requires
    elif case_id.startswith("042"):
        assert len(spec.runtime_requirements) == 1
        req = spec.runtime_requirements[0]
        assert req.family == "deepmd-jax"
        assert req.name == "dpmp"
        assert req.version == "==0.2.1"


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_scientific_capabilities_declared(case_id: str):
    case_dir = ROOT / case_id
    spec = CaseSpec.load(case_dir)
    assert spec.scientific_capabilities is not None
    assert len(spec.scientific_capabilities.required) >= 1

    if case_id.startswith("031") or case_id.startswith("032") or case_id.startswith("033"):
        assert "matclaw-cips" in spec.scientific_capabilities.required
    elif case_id.startswith("034"):
        assert "ai2kit" in spec.scientific_capabilities.required
        assert "cp2k" in spec.scientific_capabilities.required
    elif case_id.startswith("042"):
        assert "deepmd-jax" in spec.scientific_capabilities.required


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_submission_contract_and_timeouts(case_id: str):
    case_dir = ROOT / case_id
    spec = CaseSpec.load(case_dir)
    assert spec.submission_root in (".", "final")
    assert spec.verifier_timeout_sec > 0.0


@pytest.mark.parametrize("case_id", ("031-matclaw-cips-active-distillation", "032-matclaw-cips-curie-temperature", "033-matclaw-cips-domain-wall-search", "034-ai2kit-water64-end-to-end-potential"))
def test_matclaw_and_ai2kit_profiles_exist(case_id: str):
    case_dir = ROOT / case_id
    assert (case_dir / "profiles" / "resource.yaml").is_file()
    assert (case_dir / "profiles" / "platform.yaml").is_file()
    assert (case_dir / "reference" / "compute-runtime.lock.json").is_file()


def test_hpc_cases_migration_zero_drift():
    drifted = migrator.migrate("hpc", write=False)
    assert drifted == [], f"Unexpected drift in HPC case manifests: {[str(p) for p in drifted]}"

