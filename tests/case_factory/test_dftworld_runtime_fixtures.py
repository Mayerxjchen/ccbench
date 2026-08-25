"""Task 8 — deterministic Local and HPC runtime fixtures.

Committed fixtures render through the Target Adapter, load through CaseSpec and
eval.load_task, package through the allowlist Packager, pass the Candidate
audit with valid=true/errors=[], and stay benchmark_valid=false so
check_release is blocked.  Fixtures are compact and never submit HPC.  No test
asserts hidden_tokens equals zero.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from dftworld_bench.case_factory.dftworld_target import DftworldTargetAdapter
from dftworld_bench.contracts.case import CaseSpec

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PORTABLE_COMMON = (
    Path(__file__).resolve().parents[2]
    / "scientific-benchmark-case-builder-portable"
    / "skills" / "build-scientific-benchmark-case" / "scripts" / "common"
)
if str(PORTABLE_COMMON) not in sys.path:
    sys.path.insert(0, str(PORTABLE_COMMON))

from audit_candidate_bundle import audit_case  # noqa: E402
from check_release import check_release  # noqa: E402

ADAPTER = DftworldTargetAdapter()

LOCAL = FIXTURES / "mlp-local-final-retraining"
HPC = FIXTURES / "mlp-hpc-end-to-end"

LOCAL_EXPECT = {
    "case_id": "benchmark/042-local-final-retraining",
    "execution_class": "local_sandbox",
    "image": "dftworld-base-mace",
    "submission_root": "final",
    "legacy_submission_layout": False,
    "gpus": 0,
    "agent_timeout_sec": 7200.0,
    "verifier_timeout_sec": 3600.0,
    "cpus": 4,
    "memory_mb": 8192,
    "storage_mb": 20480,
}

HPC_EXPECT = {
    "case_id": "benchmark/043-hpc-end-to-end",
    "execution_class": "hpc_controller",
    "image": "dftworld-base-deepmd",
    "submission_root": "final",
    "legacy_submission_layout": False,
    "gpus": 1,
    "agent_timeout_sec": 14400.0,
    "verifier_timeout_sec": 7200.0,
    "cpus": 8,
    "memory_mb": 16384,
    "storage_mb": 40960,
}


def _design(case_dir: Path) -> dict:
    value = yaml.safe_load((case_dir / "case-design.yaml").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(
    "fixture,expect",
    [(LOCAL, LOCAL_EXPECT), (HPC, HPC_EXPECT)],
    ids=["local", "hpc"],
)
def test_target_render_is_clean(fixture, expect):
    """Committed fixture bytes equal the adapter's recomputed expectation."""
    verdict = ADAPTER.validate_output(fixture, _design(fixture))
    assert verdict.valid, verdict.errors
    assert verdict.gates.target_adapter_valid is True


@pytest.mark.parametrize(
    "fixture,expect",
    [(LOCAL, LOCAL_EXPECT), (HPC, HPC_EXPECT)],
    ids=["local", "hpc"],
)
def test_casespec_load(fixture, expect):
    spec = CaseSpec.load(fixture)
    assert spec.case_id == expect["case_id"]
    assert spec.execution_class == expect["execution_class"]
    assert spec.submission_root == expect["submission_root"]
    assert spec.legacy_submission_layout is expect["legacy_submission_layout"]
    assert spec.candidate_image == expect["image"]
    assert spec.agent_timeout_sec == expect["agent_timeout_sec"]
    assert spec.verifier_timeout_sec == expect["verifier_timeout_sec"]
    assert spec.candidate_resources["cpus"] == expect["cpus"]
    assert spec.candidate_resources["memory_mb"] == expect["memory_mb"]
    assert spec.candidate_resources["storage_mb"] == expect["storage_mb"]
    assert spec.candidate_resources["gpus"] == expect["gpus"]
    assert spec.instruction_path == "instruction.md"
    assert len(spec.public_files) == 1
    assert spec.public_files[0].source == "public/**"


@pytest.mark.parametrize(
    "fixture,expect",
    [(LOCAL, LOCAL_EXPECT), (HPC, HPC_EXPECT)],
    ids=["local", "hpc"],
)
def test_eval_load_task(fixture, expect):
    import eval as evalmod  # type: ignore[import-not-found]

    tspec = evalmod.load_task(fixture)
    assert tspec.name == fixture.name
    assert tspec.image == expect["image"]
    assert tspec.gpus == expect["gpus"]
    assert tspec.submission_root == expect["submission_root"]
    assert tspec.legacy_submission_layout is expect["legacy_submission_layout"]
    assert tspec.execution_class == expect["execution_class"]
    assert tspec.agent_timeout_sec == expect["agent_timeout_sec"]
    assert tspec.verifier_timeout_sec == expect["verifier_timeout_sec"]


@pytest.mark.parametrize(
    "fixture,expect",
    [(LOCAL, LOCAL_EXPECT), (HPC, HPC_EXPECT)],
    ids=["local", "hpc"],
)
def test_package_candidate_deterministic(fixture, expect, tmp_path):
    from dftworld_bench.core.packager import package_candidate

    spec = CaseSpec.load(fixture)
    bundle_a = package_candidate(spec, tmp_path / "a")
    bundle_b = package_candidate(spec, tmp_path / "b")
    assert bundle_a.public_digest == bundle_b.public_digest
    manifest_a = (tmp_path / "a" / "bundle-manifest.json").read_bytes()
    manifest_b = (tmp_path / "b" / "bundle-manifest.json").read_bytes()
    assert manifest_a == manifest_b
    assert (tmp_path / "a" / "instruction.md").is_file()
    assert (tmp_path / "a" / "structures.xyz").is_file()
    assert (tmp_path / "a" / "seed.txt").is_file()
    assert not (tmp_path / "a" / "case-design.yaml").exists()


@pytest.mark.parametrize(
    "fixture,expect",
    [(LOCAL, LOCAL_EXPECT), (HPC, HPC_EXPECT)],
    ids=["local", "hpc"],
)
def test_candidate_audit_valid(fixture, expect):
    report = audit_case(fixture)
    assert report["valid"] is True, report["errors"]
    assert report["errors"] == []


def test_dockerfile_parse_local():
    dockerfile = (LOCAL / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.splitlines() == [
        "FROM dftworld-base-mace",
        "COPY public/ /app/",
    ]


@pytest.mark.parametrize(
    "fixture,expect",
    [(LOCAL, LOCAL_EXPECT), (HPC, HPC_EXPECT)],
    ids=["local", "hpc"],
)
def test_benchmark_valid_false(fixture, expect):
    bv = json.loads((fixture / "benchmark_valid.json").read_text(encoding="utf-8"))
    assert bv["benchmark_valid"] is False


@pytest.mark.parametrize(
    "fixture,expect",
    [(LOCAL, LOCAL_EXPECT), (HPC, HPC_EXPECT)],
    ids=["local", "hpc"],
)
def test_check_release_blocked(fixture, expect):
    report = check_release(fixture)
    assert report["valid"] is False  # release stays blocked for a draft
    assert report["errors"]
    # check_release must not have flipped the draft to valid.
    bv = json.loads((fixture / "benchmark_valid.json").read_text(encoding="utf-8"))
    assert bv["benchmark_valid"] is False


def test_hpc_explicit_controller():
    spec = CaseSpec.load(HPC)
    assert spec.execution_class == "hpc_controller"
    import tomllib
    with (HPC / "task.toml").open("rb") as fh:
        meta = tomllib.load(fh)
    assert meta["execution"]["class"] == "hpc_controller"
    hpc = meta["hpc"]
    assert hpc["contract_version"] == "hpc-v1"
    assert hpc["required_capabilities"] == ["slurm"]


def test_hpc_assets_present():
    for rel in (
        "profiles/platform.yaml",
        "profiles/resource.yaml",
        "reference/compute-runtime.lock.json",
    ):
        assert (HPC / rel).is_file(), rel
        assert (HPC / rel).read_text(encoding="utf-8").strip()


def test_hpc_has_no_site_secrets():
    """No hostname, partition, account or SSH material anywhere in the case."""
    forbidden = ("login", ".ssh", "ssh ", "partition=", "account=",
                 "sacctmgr", "scontrol", "hostname", "compute-", "gpu-00")
    for f in HPC.rglob("*"):
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace").lower()
        for token in forbidden:
            assert token not in text, f"{f.relative_to(HPC)} contains {token!r}"
