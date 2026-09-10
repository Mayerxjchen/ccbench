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

from bench.contracts.case import (
    CaseSpec,
    KNOWN_RUNTIME_FAMILIES,
    RUNTIME_FAMILY_CAPABILITIES,
    SCHEMA_PATH,
)
import scripts.infra.migrate_case_contracts as migrator

ROOT = Path(__file__).resolve().parents[2]

PRESERVED_HPC_CASE_IDS = (
    "001-matclaw-cips-active-distillation",
    "002-matclaw-cips-curie-temperature",
    "003-matclaw-cips-domain-wall-search",
    "004-ai2kit-water64-end-to-end-potential",
    "005-go-water-dpmp",
)

DELETED_SHORT_CASE_PREFIXES = tuple(f"{i:03d}-" for i in range(6, 42)) + ("042-",)


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _cases_root() -> Path:
    return ROOT / "cases" if (ROOT / "cases").is_dir() else ROOT


def _get_case_dir(case_id: str) -> Path:
    p = ROOT / "cases" / case_id
    return p if p.is_dir() else (ROOT / case_id)


def test_numbered_cases_contain_only_preserved_five_long_cases():
    numbered_cases = sorted(
        p.name for p in _cases_root().iterdir() if p.is_dir() and re.fullmatch(r"\d{3}-.+", p.name)
    )
    assert numbered_cases == list(PRESERVED_HPC_CASE_IDS)
    for deleted_prefix in DELETED_SHORT_CASE_PREFIXES:
        assert not any(name.startswith(deleted_prefix) for name in numbered_cases), (
            f"Deleted short case {deleted_prefix} unexpectedly found"
        )


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_spec_loads_strictly_with_candidate_container(case_id: str):
    case_dir = _get_case_dir(case_id)
    spec = CaseSpec.load(case_dir)
    # Compute placement is selected by the operator/dispatcher.  The case
    # owns only the portable execution contract and always runs Claude in the
    # Candidate container.
    assert spec.execution_class == "local_sandbox"
    assert spec.candidate_runner == "container_claude_code"
    assert spec.legacy_execution_value is None
    assert spec.legacy_agent_fields == ()


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_manifest_satisfies_case_schema(case_id: str):
    case_dir = _get_case_dir(case_id)
    manifest = case_dir / "case.toml" if (case_dir / "case.toml").is_file() else case_dir / "task.toml"
    assert manifest.is_file()
    schema = _load_schema()
    import tomllib
    raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
    contract_doc = {k: raw[k] for k in ("execution", "candidate", "hpc") if k in raw}
    jsonschema.validate(instance=contract_doc, schema=schema)


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_instruction_and_public_files_integrity(case_id: str):
    case_dir = _get_case_dir(case_id)
    spec = CaseSpec.load(case_dir)
    instruction = case_dir / spec.instruction_path
    assert instruction.is_file(), f"instruction file {spec.instruction_path} missing in {case_id}"
    text = instruction.read_text(encoding="utf-8").strip()
    assert len(text) > 100, f"instruction text too short in {case_id}"

    assert len(spec.public_files) >= 1
    for rule in spec.public_files:
        assert rule.source in ("public/**", "input/**")
        assert rule.destination == "."


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_verifiers_exist_and_non_empty(case_id: str):
    case_dir = _get_case_dir(case_id)
    verifier_sh = case_dir / "verifier" / "test.sh" if (case_dir / "verifier" / "test.sh").is_file() else case_dir / "tests" / "test.sh"
    assert verifier_sh.is_file(), f"verifier launcher missing in {case_id}"
    assert len(verifier_sh.read_text(encoding="utf-8").strip()) > 0
    verifier_py = case_dir / "verifier" / "verifier.py" if (case_dir / "verifier" / "verifier.py").is_file() else case_dir / "tests" / "verifier.py"
    assert verifier_py.is_file(), f"verifier.py missing in {case_id}"
    assert len(verifier_py.read_text(encoding="utf-8").strip()) > 0


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_runtime_requirements_matrix(case_id: str):
    case_dir = _get_case_dir(case_id)
    spec = CaseSpec.load(case_dir)
    assert len(spec.runtime_requirements) >= 1

    for req in spec.runtime_requirements:
        assert req.family in KNOWN_RUNTIME_FAMILIES
        assert req.name != ""
        assert req.version != ""

    if case_id.startswith("031") or case_id.startswith("032") or case_id.startswith("033") or case_id.startswith("001") or case_id.startswith("002") or case_id.startswith("003"):
        assert len(spec.runtime_requirements) == 1
        req = spec.runtime_requirements[0]
        assert req.family == "matclaw-cips"
        assert req.name == "matclaw-cips"
        assert req.version == "==2.2.11"
        assert spec.effective_qualification_requires == ()
    elif case_id.startswith("034") or case_id.startswith("004"):
        assert len(spec.runtime_requirements) == 2
        families = {r.family for r in spec.runtime_requirements}
        assert families == {"ai2kit", "cp2k"}
        assert spec.effective_qualification_requires == ()
    elif case_id.startswith("042") or case_id.startswith("005"):
        assert len(spec.runtime_requirements) == 1
        req = spec.runtime_requirements[0]
        assert req.family == "deepmd-jax"
        assert req.name == "dpmp"
        assert req.version == "==0.2.1"


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_scientific_capabilities_declared(case_id: str):
    case_dir = _get_case_dir(case_id)
    spec = CaseSpec.load(case_dir)
    # Scientific capabilities are operator/compute-profile concerns; keeping
    # them out of the case prevents a case from selecting a scheduler or
    # leaking dispatch credentials into Candidate.
    assert spec.scientific_capabilities is None
    assert len(spec.runtime_requirements) >= 1


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_submission_contract_and_timeouts(case_id: str):
    case_dir = _get_case_dir(case_id)
    spec = CaseSpec.load(case_dir)
    assert spec.submission_root in (".", "final")
    assert spec.verifier_timeout_sec > 0.0


@pytest.mark.parametrize("case_id", PRESERVED_HPC_CASE_IDS)
def test_case_profiles_and_dockerfile_removed_from_case_v2(case_id: str):
    case_dir = _get_case_dir(case_id)
    assert not (case_dir / "profiles").exists(), f"profiles/ must be deleted from case v2 root: {case_id}"
    assert not (case_dir / "Dockerfile").exists(), f"Dockerfile must be deleted from case v2 root: {case_id}"


def test_hpc_cases_migration_zero_drift():
    drifted = migrator.migrate("hpc", write=False)
    assert drifted == [], f"Unexpected drift in HPC case manifests: {[str(p) for p in drifted]}"
