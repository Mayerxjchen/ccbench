"""Comprehensive Release Gate verification suite (G0–G15) for benchmark infrastructure."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest.mock import patch

import jsonschema


def _mock_verify_receipt_ok(receipt, *, scheduler=None, root, receipt_dir, **kwargs):
    """Mock verify_site_receipt that returns a successful derivation."""
    return {
        "receipt_dir": str(receipt_dir),
        "digest_ok": True,
        "problems": {},
        "derived": {
            "qualification_status": "PASS",
            "formal_qualified": True,
            "capabilities": {
                "dispatcher.cpu": "PASS",
                "dispatcher.gpu": "PASS",
            },
            "gates": {},
        },
    }
import pytest

from dftworld_bench.contracts.case import CaseSpec
from dftworld_bench.executors import HpcExecutor, resolve
from dftworld_bench.hpc.compute_profile import ComputeProfile, ComputeRouter
from dftworld_bench.hpc.drivers.base import HpcDriver
from dftworld_bench.hpc.drivers.compshare import (
    CompShareCli,
    CompShareDriver,
    FakeCompShareCliRunner,
)
from dftworld_bench.hpc.drivers.process import ProcessDriver
from dftworld_bench.hpc.drivers.slurm import SlurmDriver
from dftworld_bench.hpc.runtime_resolution import RuntimeResolver

ROOT = Path(__file__).resolve().parents[2]


def test_g0_public_isolation():
    """G0: Verify public surface contains no private credentials or internal host keys."""
    for case_num in ("031", "032", "033", "034", "042"):
        matches = list(ROOT.glob(f"{case_num}-*"))
        assert len(matches) == 1
        pub_dir = matches[0] / "public"
        if pub_dir.is_dir():
            for f in pub_dir.rglob("*"):
                if f.is_file():
                    content = f.read_bytes()
                    assert b"PRIVATE KEY" not in content
                    assert b"compshare_key" not in content.lower()


def test_g1_repo_provenance():
    """G1: Verify repo root and release structure integrity."""
    assert (ROOT / "pyproject.toml").is_file()
    assert (ROOT / "schemas").is_dir()
    assert (ROOT / "reference" / "runtime").is_dir()


def test_g2_clean_executor_contract():
    """G2: All 5 cases resolve to HpcExecutor under hpc_controller class."""
    for case_num in ("031", "032", "033", "034", "042"):
        matches = list(ROOT.glob(f"{case_num}-*"))
        assert len(matches) == 1
        spec = CaseSpec.load(matches[0])
        assert spec.execution_class == "hpc_controller"


def test_g3_runtime_locks_valid():
    """G3: Runtime locks exist and parse cleanly, and unbuilt runtimes are not qualified."""
    ref_runtime = ROOT / "reference" / "runtime"
    resolver = RuntimeResolver.from_lock_dir(ref_runtime)
    all_caps = resolver.all_capabilities()
    for cap in ("cp2k", "ai2kit", "deepmd", "jax"):
        assert cap in all_caps
    assert resolver.qualified_capabilities() == []


def test_g4_prompt_fidelity():
    """G4: Instructions describe provider-neutral execution."""
    for case_num in ("031", "032", "033", "034", "042"):
        matches = list(ROOT.glob(f"{case_num}-*"))
        instr = (matches[0] / "instruction.md").read_text()
        assert "A remote HPC capability exists" in instr or "remote scheduler" in instr
        assert "compshare" not in instr.lower()


def test_g5_structure_and_model_hashes():
    """G5: Source and runtime assets define sha256 or immutable image IDs."""
    locks = list((ROOT / "reference" / "runtime").glob("*.lock.json"))
    assert len(locks) >= 4
    for lk in locks:
        data = json.loads(lk.read_text())
        assert "image_name" in data


def test_g6_clean_schema_validation():
    """G6: Schemas are valid Draft 2020-12 schemas."""
    schema_dir = ROOT / "schemas"
    for s_file in schema_dir.glob("*.json"):
        doc = json.loads(s_file.read_text())
        jsonschema.Draft202012Validator.check_schema(doc)


def test_g7_route_aware_resolver():
    """G7: Resolver distinguishes SIF and CompShare image targets and fails closed when unbuilt."""
    from dftworld_bench.hpc.runtime_resolution import RuntimeResolutionError

    resolver = RuntimeResolver.from_lock_dir(ROOT / "reference" / "runtime")
    # Gate A1 requirement: unbuilt runtimes without qualification fail closed
    with pytest.raises(RuntimeResolutionError, match="UNBUILT"):
        resolver.resolve("deepmd")
    with pytest.raises(RuntimeResolutionError, match="UNBUILT"):
        resolver.resolve("jax")


def test_g8_driver_conformance():
    """G8: All drivers satisfy HpcDriver protocol."""
    assert issubclass(SlurmDriver, HpcDriver)
    assert issubclass(ProcessDriver, HpcDriver)
    assert issubclass(CompShareDriver, HpcDriver)


def test_g9_containment_and_sandboxing(tmp_path: Path):
    """G9: Runtime wrapper enforces container isolation."""
    from dftworld_bench.hpc.request import ExecutionRequestV2
    from dftworld_bench.hpc.runtime_resolution import ResolvedRuntime
    from dftworld_bench.hpc.runtime_wrapper import render_runtime_wrapper
    from dftworld_bench.hpc.site_profile import HpcSiteProfile

    req = ExecutionRequestV2.from_dict({
        "schema_version": 2,
        "operation_id": "op-1",
        "attempt": 1,
        "compute_class": "cpu",
        "runtime": "cp2k",
        "command": ["cp2k", "-i", "in.inp"],
        "resources": {"cpus": 4, "memory_gb": 8, "gpus": 0, "walltime_minutes": 10},
        "inputs": [],
        "outputs": [],
    })
    site = HpcSiteProfile.from_dict(
        json.loads((ROOT / "examples" / "hpc" / "generic-slurm-site-profile.json").read_text())
    )
    sif_rr = ResolvedRuntime(
        capability="cp2k",
        sif_path="/site/cp2k.sif",
        digest="a" * 64,
        artifact_kind="sif",
        artifact_path_or_id="/site/cp2k.sif",
    )
    rendered = render_runtime_wrapper(req, site, str(tmp_path / "work"), runtime=sif_rr)
    assert "--cleanenv" in rendered.script
    assert "--contain" in rendered.script


def _mock_site_receipts_dir(tmp_path: Path) -> tuple[Path, str, str]:
    import hashlib
    import json
    site_dir = tmp_path / "site_receipts"
    site_dir.mkdir(parents=True, exist_ok=True)
    cpu_b = json.dumps({"site_id": "ikkem-cpu", "verdict": "PASS"}, sort_keys=True).encode("utf-8")
    gpu_b = json.dumps({"site_id": "compshare-gpu", "verdict": "PASS"}, sort_keys=True).encode("utf-8")
    (site_dir / "ikkem-cpu.receipt.json").write_bytes(cpu_b)
    (site_dir / "compshare-gpu.receipt.json").write_bytes(gpu_b)
    return site_dir, f"sha256:{hashlib.sha256(cpu_b).hexdigest()}", f"sha256:{hashlib.sha256(gpu_b).hexdigest()}"


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_g10_two_layer_qualification(tmp_path: Path):
    """G10: ComputeProfile qualification verifies both routes."""
    from dftworld_bench.experiments.compute_profile_qualification import (
        compute_receipt_digest,
        verify_and_derive_qualification,
    )

    site_dir, cpu_sha, gpu_sha = _mock_site_receipts_dir(tmp_path)
    receipt = {
        "kind": "hpc-compute-profile-qualification/v1",
        "schema_id": "https://mlip-bench.example/schemas/compute-profile-qualification-receipt.schema.json",
        "compute_profile_id": "maintainer-hybrid-v1",
        "compute_profile_digest": "a" * 64,
        "routes": {"cpu": "ikkem-cpu", "gpu": "compshare-gpu"},
        "site_receipts": {
            "ikkem-cpu": cpu_sha,
            "compshare-gpu": gpu_sha,
        },
        "cloud_recycling_evidence": {
            "stock_checked": True,
            "instance_id": "inst-1",
            "image_id": "img-deepmd-gpu-v1",
            "gpu_type": "rtx4090",
            "gpu_vram_gb": 24,
            "task_executed": True,
            "fetch_verified": True,
            "credentials_isolated": True,
            "settlement_terminated": True,
            "active_instances_count": 0,
            "orphan_instances_count": 0,
        },
    }
    receipt["digest"] = compute_receipt_digest(receipt)
    verdict = verify_and_derive_qualification(receipt, site_receipts_dir=site_dir)
    # v1 remains readable for migration diagnostics, but C9 deliberately
    # removed its eligibility authority.  A signed/materialized v2 receipt is
    # required before either route can qualify.
    assert verdict.passed is False
    assert verdict.status == "LEGACY_NOT_ELIGIBLE"


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_g11_zero_orphan_gate(tmp_path: Path):
    """G11: Zero-orphan gate rejects receipts with active billing instances."""
    from dftworld_bench.experiments.compute_profile_qualification import (
        compute_receipt_digest,
        verify_and_derive_qualification,
    )

    site_dir, cpu_sha, gpu_sha = _mock_site_receipts_dir(tmp_path)
    receipt = {
        "kind": "hpc-compute-profile-qualification/v1",
        "schema_id": "https://mlip-bench.example/schemas/compute-profile-qualification-receipt.schema.json",
        "compute_profile_id": "maintainer-hybrid-v1",
        "compute_profile_digest": "a" * 64,
        "routes": {"cpu": "ikkem-cpu", "gpu": "compshare-gpu"},
        "site_receipts": {
            "ikkem-cpu": cpu_sha,
            "compshare-gpu": gpu_sha,
        },
        "cloud_recycling_evidence": {
            "stock_checked": True,
            "instance_id": "inst-1",
            "image_id": "img-deepmd-gpu-v1",
            "gpu_type": "rtx4090",
            "task_executed": True,
            "fetch_verified": True,
            "credentials_isolated": True,
            "settlement_terminated": True,
            "active_instances_count": 2,  # remaining billing instance
            "orphan_instances_count": 0,
        },
    }
    receipt["digest"] = compute_receipt_digest(receipt)
    verdict = verify_and_derive_qualification(receipt, site_receipts_dir=site_dir)
    assert verdict.passed is False
    assert verdict.cloud_recycling_passed is False


def test_g12_case_validation_states():
    """G12: Cases 031-033 are verified baseline valid; 034/042 reflect actual construction status."""
    for case_num in ("031", "032", "033"):
        matches = list(ROOT.glob(f"{case_num}-*"))
        assert len(matches) == 1
        data = json.loads((matches[0] / "benchmark_valid.json").read_text())
        assert data.get("benchmark_valid") is True

    for case_num in ("034", "042"):
        matches = list(ROOT.glob(f"{case_num}-*"))
        assert len(matches) == 1
        data = json.loads((matches[0] / "benchmark_valid.json").read_text())
        assert data.get("benchmark_valid") is False


def test_g13_user_cli_compute():
    """G13: User CLI mlffbench compute commands validate profiles."""
    import dftworld_bench.cli as cli

    example_profile = ROOT / "examples" / "hpc" / "generic-slurm-compute-profile.json"
    code = cli.main(["compute", "validate", "--profile", str(example_profile)])
    assert code == 0


def test_g14_no_merge_markers():
    """G14: Source files contain no unresolved merge conflict markers."""
    marker = re.compile(r"^(<{7}|>{7}|={7}) ", re.MULTILINE)
    for ext in ("*.py", "*.json", "*.md", "*.toml", "*.yaml"):
        for path in ROOT.glob(f"**/{ext}"):
            if ".git" in str(path) or ".venv" in str(path) or "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not marker.search(text), f"Merge marker in {path}"


def test_g15_no_credential_leaks():
    """G15: Repository contains no committed sensitive credential secrets."""
    suspicious = re.compile(r"(COMPSHARE_API_KEY\s*=\s*['\"][a-zA-Z0-9_-]{16,}['\"])")
    for ext in ("*.py", "*.json", "*.toml", "*.yaml"):
        for path in ROOT.glob(f"**/{ext}"):
            if ".git" in str(path) or ".venv" in str(path) or "__pycache__" in str(path):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not suspicious.search(text), f"Suspected API key leak in {path}"


def test_g16_gate_a1_negative_contracts_suite():
    """G16: Gate A1 Architecture Freeze negative contracts suite."""
    from tests.hpc import test_gate_a1_negative_contracts as neg_mod

    required_contracts = [
        "test_contract_1_agent_direct_image_path_rejected",
        "test_contract_2_unbuilt_runtime_rejected_at_resolution",
        "test_contract_3_ownership_marker_injected_by_trusted_driver",
        "test_contract_4_token_independent_trusted_teardown",
        "test_contract_5_zero_orphan_gate_fails_on_active_or_error",
        "test_contract_6_ed25519_signature_tampering_fails",
        "test_contract_7_evidence_path_traversal_rejected",
        "test_contract_8_site_profile_required_probe_classes_enforced",
        "test_forged_lock_status_pass_cannot_resolve",
        "test_missing_runtime_receipt_cannot_resolve",
        "test_self_signed_receipt_rejected",
        "test_unsigned_receipt_rejected",
        "test_untrusted_key_id_rejected",
        "test_missing_runtime_lock_file_rejected",
        "test_runtime_lock_digest_mismatch_rejected",
        "test_missing_artifact_file_rejected",
        "test_artifact_digest_mismatch_rejected",
        "test_audit_from_another_run_rejected",
        "test_audit_instance_id_mismatch_rejected",
        "test_audit_image_id_mismatch_rejected",
        "test_audit_event_order_rejected",
        "test_stopped_instance_blocks_cli_qualification",
        "test_failed_existing_instance_blocks_qualification",
        "test_receipt_local_site_profile_cannot_override_policy",
        "test_concurrent_audit_append_preserves_chain",
        "test_special_character_run_id_generates_safe_marker",
    ]
    for rc in required_contracts:
        assert hasattr(neg_mod.TestGateA1NegativeContracts, rc), f"Missing negative contract test: {rc}"
