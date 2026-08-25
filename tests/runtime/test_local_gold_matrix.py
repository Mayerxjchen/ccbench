"""Simulated local gold matrix: the 37 Local cases satisfy the infra v2
contract end-to-end WITHOUT containers.

Until the authorized Docker image gate (Task 15 Step 7) qualifies concrete
digests, the runtime registry cannot resolve any Local case's compute
requirements, so build resolution must fail closed rather than guess an image.
The matrix proves the honest pre-gate state:

  * every Local manifest is strict (local_sandbox, no legacy agent fields,
    no concrete image) and declares a non-empty structural contract
  * runtime requirements are canonical (derived from the Dockerfile FROM)
  * the non-empty structural gate actually rejects an empty submission
  * the migration is idempotent (``--check`` clean after ``--write``) and
    touches only ``task.toml`` — scientific bytes never change
  * runtime resolution fails closed for every Local case
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from dftworld_bench.contracts.case import CaseSpec
from dftworld_bench.core.submission_contract import validate_submission
from dftworld_bench.runtime.registry import (
    RuntimeRegistry,
    RuntimeRegistryError,
    RuntimeRequirement as RegistryRequirement,
)

ROOT = Path(__file__).resolve().parents[2]
MIGRATE_SCRIPT = ROOT / "scripts" / "infra" / "migrate_case_contracts.py"

LOCAL_IDS = set(range(1, 31)) | set(range(35, 42))

# Cases whose agent budget exceeds the infra standard (MACE/XTB families).
_NON_STANDARD_BUDGET = {27, 28, 29, 30, 39, 40}

# Dockerfile FROM base image -> canonical requirement names.
_FROM_REQUIREMENTS = {
    "dftworld-base": (),
    "dftworld-base-cp2k": ("cp2k",),
    "dftworld-base-packmol": ("packmol",),
    "dftworld-base-packmol-src": ("packmol",),
    "dftworld-base-deepmd": ("deepmd-kit",),
    "dftworld-base-chem": ("ase-rdkit",),
    "dftworld-base-mace": ("mace",),
    "dftworld-base-xtb": ("xtb",),
}


def local_cases() -> list[Path]:
    return sorted(
        p for p in ROOT.iterdir()
        if p.is_dir() and re.fullmatch(r"\d{3}-.+", p.name)
        and int(p.name[:3]) in LOCAL_IDS
    )


def _from_line(case_dir: Path) -> str:
    dockerfile = case_dir / "Dockerfile"
    if not dockerfile.is_file():
        dockerfile = case_dir / "environment" / "Dockerfile"
    match = re.search(r"^\s*FROM\s+([^\s]+)", dockerfile.read_text(encoding="utf-8"), re.M)
    assert match is not None, f"{case_dir.name} has no FROM line"
    return match.group(1)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_local_case_is_strict_and_declares_a_gate():
    for case in local_cases():
        spec = CaseSpec.load(case)
        assert spec.execution_class == "local_sandbox", case.name
        assert spec.legacy_agent_fields == (), case.name
        assert spec.candidate_image is None, case.name
        # The migration adds a structural pre-Verifier gate that the case must
        # declare explicitly.
        assert spec.submission_contract.get("non_empty") is True, case.name
        assert spec.submission_contract.get("required") == [], case.name


def test_non_standard_agent_budgets_survive_the_migration():
    for case in local_cases():
        spec = CaseSpec.load(case)
        if int(case.name[:3]) in _NON_STANDARD_BUDGET:
            assert spec.max_agent_seconds == 1500.0, case.name
        else:
            assert spec.max_agent_seconds is None, case.name


def test_runtime_requirements_match_the_dockerfile_family():
    for case in local_cases():
        spec = CaseSpec.load(case)
        expected = _FROM_REQUIREMENTS[_from_line(case)]
        assert [r.name for r in spec.runtime_requirements] == list(expected), case.name


def test_non_empty_gate_rejects_empty_submission(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    populated = tmp_path / "populated"
    populated.mkdir()
    (populated / "out.txt").write_text("x", encoding="utf-8")

    errors = validate_submission(empty, {"required": [], "non_empty": True})
    assert [e.code for e in errors] == ["EMPTY_SUBMISSION"]
    assert validate_submission(populated, {"required": [], "non_empty": True}) == []


def test_migrated_manifests_are_idempotent_check_clean():
    proc = subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT), "--check", "--scope", "local"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DRIFT" not in proc.stdout


def test_migration_touches_only_task_toml(tmp_path):
    """Re-running the transform on a copy rewrites task.toml and nothing else:
    every scientific file stays byte-identical."""
    import importlib.util

    spec_loader = importlib.util.spec_from_file_location(
        "migrate_case_contracts", MIGRATE_SCRIPT
    )
    module = importlib.util.module_from_spec(spec_loader)
    assert spec_loader and spec_loader.loader
    spec_loader.loader.exec_module(module)

    for case in local_cases():
        copy = tmp_path / case.name
        shutil.copytree(case, copy)
        before = {
            rel: _sha256(copy / rel)
            for rel in (p.relative_to(copy) for p in copy.rglob("*") if p.is_file())
        }
        raw = tomllib.loads((copy / "task.toml").read_text(encoding="utf-8"))
        out = module._transform_local(raw, copy)
        (copy / "task.toml").write_text(module._emit_toml(out), encoding="utf-8")
        after = {
            rel: _sha256(copy / rel)
            for rel in (p.relative_to(copy) for p in copy.rglob("*") if p.is_file())
        }
        assert after == before, f"{case.name}: migration changed a non-task.toml file"
        # the rewritten copy satisfies the strict contract
        spec = CaseSpec.load(copy)
        assert spec.execution_class == "local_sandbox"
        assert spec.legacy_agent_fields == ()


def test_local_runtime_resolution_fails_closed():
    """No Local case's runtime can resolve today: the registry carries no
    legacy-family compute runtime (cp2k / packmol / deepmd-kit / ase-rdkit /
    mace / xtb), so build resolution must fail closed, never guess an image."""
    registry = RuntimeRegistry()
    for case in local_cases():
        spec = CaseSpec.load(case)
        requirements = [
            RegistryRequirement(
                name=req.name,
                family=req.family or req.name,
                version=req.version or "*",
            )
            for req in spec.runtime_requirements
        ]
        with pytest.raises(RuntimeRegistryError, match="compute runtime|no runtime requirements"):
            registry.resolve(requirements, spec.execution_class)
