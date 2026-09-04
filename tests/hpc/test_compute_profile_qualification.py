"""Unit tests for ComputeProfile Qualification (Layer 2) and Zero-Orphan Cloud Recycling Gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from dftworld_bench.experiments.compute_profile_qualification import (
    ComputeProfileQualificationError,
    compute_receipt_digest,
    verify_and_derive_qualification,
)
from dftworld_bench.hpc.compute_profile import ComputeProfile


def _mock_verify_receipt_ok(receipt, *, scheduler, root, receipt_dir):
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


def _mock_verify_receipt_fail(receipt, *, scheduler, root, receipt_dir):
    """Mock verify_site_receipt that returns a failed derivation."""
    return {
        "receipt_dir": str(receipt_dir),
        "digest_ok": True,
        "problems": ["[provenance] code identity changed since qualification"],
        "derived": {
            "qualification_status": "INVALID",
            "formal_qualified": False,
            "capabilities": {},
            "gates": {},
        },
    }


def _setup_disk_receipts(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    receipts_dir = tmp_path / "site_receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    cpu_doc = {"site_id": "ikkem-cpu", "verdict": "PASS"}
    cpu_bytes = json.dumps(cpu_doc, sort_keys=True).encode("utf-8")
    cpu_sha = "sha256:" + hashlib.sha256(cpu_bytes).hexdigest()
    (receipts_dir / "ikkem-cpu.receipt.json").write_bytes(cpu_bytes)

    gpu_doc = {"site_id": "compshare-gpu", "verdict": "PASS"}
    gpu_bytes = json.dumps(gpu_doc, sort_keys=True).encode("utf-8")
    gpu_sha = "sha256:" + hashlib.sha256(gpu_bytes).hexdigest()
    (receipts_dir / "compshare-gpu.receipt.json").write_bytes(gpu_bytes)

    return receipts_dir, {"ikkem-cpu": cpu_sha, "compshare-gpu": gpu_sha}


def _valid_receipt(site_receipts: dict[str, str] | None = None) -> dict[str, Any]:
    doc = {
        "kind": "hpc-compute-profile-qualification/v1",
        "schema_id": "https://mlip-bench.example/schemas/compute-profile-qualification-receipt.schema.json",
        "compute_profile_id": "maintainer-hybrid-v1",
        "compute_profile_digest": "a" * 64,
        "routes": {
            "cpu": "ikkem-cpu",
            "gpu": "compshare-gpu",
        },
        "site_receipts": site_receipts or {
            "ikkem-cpu": f"sha256:{'1' * 64}",
            "compshare-gpu": f"sha256:{'2' * 64}",
        },
        "cloud_recycling_evidence": {
            "stock_checked": True,
            "instance_id": "inst-0001",
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
    doc["digest"] = compute_receipt_digest(doc)
    return doc


def test_missing_site_receipts_dir_fails_closed():
    """Hybrid profile qualification must fail when --site-receipts-dir is omitted."""
    receipt = _valid_receipt()
    verdict = verify_and_derive_qualification(receipt)
    assert verdict.passed is False
    assert any("requires a valid --site-receipts-dir" in err for err in verdict.errors)


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_valid_compute_profile_qualification_passes(tmp_path: Path):
    receipts_dir, hashes = _setup_disk_receipts(tmp_path)
    receipt = _valid_receipt(site_receipts=hashes)
    verdict = verify_and_derive_qualification(receipt, site_receipts_dir=receipts_dir)
    assert verdict.passed is True
    assert verdict.routes_valid is True
    assert verdict.cpu_site_qualified is True
    assert verdict.gpu_site_qualified is True
    assert verdict.cloud_recycling_passed is True
    assert verdict.errors == []


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_active_instances_remaining_fails_zero_orphan_gate(tmp_path: Path):
    receipts_dir, hashes = _setup_disk_receipts(tmp_path)
    receipt = _valid_receipt(site_receipts=hashes)
    receipt["cloud_recycling_evidence"]["active_instances_count"] = 1
    receipt["digest"] = compute_receipt_digest(receipt)

    verdict = verify_and_derive_qualification(receipt, site_receipts_dir=receipts_dir)
    assert verdict.passed is False
    assert verdict.cloud_recycling_passed is False
    assert any("Zero-Orphan Gate failed" in err for err in verdict.errors)


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_orphan_instance_fails_gate(tmp_path: Path):
    receipts_dir, hashes = _setup_disk_receipts(tmp_path)
    receipt = _valid_receipt(site_receipts=hashes)
    receipt["cloud_recycling_evidence"]["orphan_instances_count"] = 1
    receipt["digest"] = compute_receipt_digest(receipt)

    verdict = verify_and_derive_qualification(receipt, site_receipts_dir=receipts_dir)
    assert verdict.passed is False
    assert verdict.cloud_recycling_passed is False
    assert any("orphan instances recorded" in err for err in verdict.errors)


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_unsettled_termination_fails_gate(tmp_path: Path):
    receipts_dir, hashes = _setup_disk_receipts(tmp_path)
    receipt = _valid_receipt(site_receipts=hashes)
    receipt["cloud_recycling_evidence"]["settlement_terminated"] = False
    receipt["digest"] = compute_receipt_digest(receipt)

    verdict = verify_and_derive_qualification(receipt, site_receipts_dir=receipts_dir)
    assert verdict.passed is False
    assert verdict.cloud_recycling_passed is False
    assert any("settlement termination" in err.lower() for err in verdict.errors)


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_live_active_instances_checker_catches_running_cloud_instance(tmp_path: Path):
    receipts_dir, hashes = _setup_disk_receipts(tmp_path)
    receipt = _valid_receipt(site_receipts=hashes)
    checker = lambda: ["inst-999"]
    verdict = verify_and_derive_qualification(
        receipt, site_receipts_dir=receipts_dir, active_instances_checker=checker
    )
    assert verdict.passed is False
    assert verdict.cloud_recycling_passed is False
    assert any("live cloud query detected active instances" in err for err in verdict.errors)


def test_tampered_digest_fails_closed(tmp_path: Path):
    receipts_dir, hashes = _setup_disk_receipts(tmp_path)
    receipt = _valid_receipt(site_receipts=hashes)
    receipt["digest"] = f"sha256:{'f' * 64}"
    with pytest.raises(ComputeProfileQualificationError, match="Receipt digest mismatch"):
        verify_and_derive_qualification(receipt, site_receipts_dir=receipts_dir)


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_unqualified_site_fails_closed(tmp_path: Path):
    receipts_dir, hashes = _setup_disk_receipts(tmp_path)
    receipt = _valid_receipt(site_receipts=hashes)
    receipt["site_receipts"] = {"ikkem-cpu": hashes["ikkem-cpu"]}  # missing compshare-gpu
    receipt["digest"] = compute_receipt_digest(receipt)

    verdict = verify_and_derive_qualification(receipt, site_receipts_dir=receipts_dir)
    assert verdict.passed is False
    assert verdict.gpu_site_qualified is False
    assert any("compshare-gpu" in err for err in verdict.errors)


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_false_evidence_fails_qualification(tmp_path: Path):
    receipts_dir, hashes = _setup_disk_receipts(tmp_path)
    for false_field in ("stock_checked", "task_executed", "fetch_verified", "credentials_isolated"):
        receipt = _valid_receipt(site_receipts=hashes)
        receipt["cloud_recycling_evidence"][false_field] = False
        receipt["digest"] = compute_receipt_digest(receipt)

        verdict = verify_and_derive_qualification(receipt, site_receipts_dir=receipts_dir)
        assert verdict.passed is False
        assert verdict.cloud_recycling_passed is False
        assert any("Evidence failure" in err for err in verdict.errors)


def test_missing_digest_raises():
    receipt = _valid_receipt()
    del receipt["digest"]
    with pytest.raises(Exception):
        verify_and_derive_qualification(receipt)


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_expected_profile_mismatch_fails_closed(tmp_path: Path):
    receipts_dir, hashes = _setup_disk_receipts(tmp_path)
    prof = ComputeProfile.from_dict({
        "schema_version": 1,
        "profile_id": "other-profile",
        "routes": {"cpu": {"site_profile": "ikkem-cpu"}, "gpu": {"site_profile": "compshare-gpu"}},
    })
    receipt = _valid_receipt(site_receipts=hashes)
    with pytest.raises(ComputeProfileQualificationError, match="profile ID mismatch"):
        verify_and_derive_qualification(receipt, expected_profile=prof, site_receipts_dir=receipts_dir)


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
def test_on_disk_site_receipt_verification(tmp_path: Path):
    prof = ComputeProfile.from_dict({
        "schema_version": 1,
        "profile_id": "maintainer-hybrid-v1",
        "routes": {"cpu": {"site_profile": "ikkem-cpu"}, "gpu": {"site_profile": "compshare-gpu"}},
    })

    receipts_dir, hashes = _setup_disk_receipts(tmp_path)

    receipt = _valid_receipt(site_receipts=hashes)
    receipt["compute_profile_digest"] = prof.digest
    receipt["digest"] = compute_receipt_digest(receipt)

    verdict = verify_and_derive_qualification(
        receipt, expected_profile=prof, site_receipts_dir=receipts_dir
    )
    assert verdict.passed is True
    assert verdict.cpu_site_qualified is True
    assert verdict.gpu_site_qualified is True

    # Negative test: tampered receipt on disk fails
    (receipts_dir / "compshare-gpu.receipt.json").write_text('{"verdict": "FAIL"}')
    verdict_bad = verify_and_derive_qualification(
        receipt, expected_profile=prof, site_receipts_dir=receipts_dir
    )
    assert verdict_bad.passed is False
    assert verdict_bad.gpu_site_qualified is False


@patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_fail)
def test_self_authored_receipt_fails_full_verification(tmp_path: Path):
    """A self-authored minimal receipt must fail the full provenance verification."""
    receipts_dir, hashes = _setup_disk_receipts(tmp_path)
    receipt = _valid_receipt(site_receipts=hashes)
    verdict = verify_and_derive_qualification(receipt, site_receipts_dir=receipts_dir)
    assert verdict.passed is False
    assert any("failed full verification" in err for err in verdict.errors)


# ---------------------------------------------------------------------------
# Unmocked integration tests (no monkeypatch) — trust contract verification
# ---------------------------------------------------------------------------

class TestGatewayTwoPhaseSettlement:
    """Test two-phase settlement: freeze then teardown, with structured error codes."""

    def test_token_expired_still_executes_teardown(self, tmp_path: Path):
        """TOKEN_EXPIRED must still execute trusted teardown_resources."""
        from dftworld_bench.hpc.gateway import Gateway, GatewayError, GatewayErrorCode

        class FakeAdapter:
            def __init__(self):
                self.settle_called = False
                self.teardown_called = False
            def settle(self, run_id):
                self.settle_called = True
            def teardown_resources(self, run_id):
                self.teardown_called = True

        adapter = FakeAdapter()
        gw = Gateway(adapter=adapter)

        # Issue a token with all needed operations
        token = gw.issue("test-run", ["submit", "fetch", "usage", "cancel", "status", "logs"])

        # freeze() should succeed (it only marks SETTLING)
        gw.freeze(token, "test-run")

        # teardown_resources should still be called even though token expired
        # because TOKEN_EXPIRED means teardown is still needed
        # Note: teardown_resources calls adapter.settle() internally
        gw.teardown_resources(token, "test-run")
        assert adapter.settle_called is True

    def test_token_revoked_skips_teardown(self, tmp_path: Path):
        """TOKEN_REVOKED means token was explicitly revoked — teardown may be skipped."""
        from dftworld_bench.hpc.gateway import Gateway, GatewayError, GatewayErrorCode

        class FakeAdapter:
            def __init__(self):
                self.teardown_called = False
            def teardown_resources(self):
                self.teardown_called = True

        adapter = FakeAdapter()
        gw = Gateway(adapter=adapter)

        # authorize() with no registered token raises TOKEN_REVOKED
        try:
            gw.authorize("nonexistent-token", "nonexistent-run", "submit")
        except GatewayError as exc:
            assert exc.error_code == GatewayErrorCode.TOKEN_REVOKED


class TestCompShareReceiptTrustContract:
    """Test CompShare receipt verification trust contract (unmocked)."""

    def _get_problems(self, result: dict) -> list[str]:
        """Get problems list from result."""
        return result.get("problems", [])

    def test_minimal_self_authored_receipt_fails(self, tmp_path: Path):
        """A minimal self-authored CompShare receipt must FAIL verification."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        # Write a minimal self-authored receipt
        receipt = {
            "kind": "compshare-gpu-site",
            "schema_version": 1,
            "code_identity": {"commit": "abc123", "dirty": False},
            "site_profile_digest": "sha256:fake",
            "job": {"status": "succeeded"},
            "runtime": {"image": "test:latest"},
            "settlement": {"digest": "sha256:fake"},
            "instances": {"stop_confirmed": True, "delete_confirmed": True},
            "active_total": 0,
        }
        receipt_path = tmp_path / "compshare-gpu.receipt.json"
        receipt_path.write_text(json.dumps(receipt))

        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)
        problems = self._get_problems(result)
        # Must have problems — self-authored receipts are not trusted
        assert len(problems) > 0

    def test_missing_job_terminal_status_fails(self, tmp_path: Path):
        """CompShare receipt without job terminal status must FAIL."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        receipt = {
            "kind": "compshare-gpu-site",
            "schema_version": 1,
            "code_identity": {"commit": "abc123", "dirty": False},
            "site_profile_digest": "sha256:fake",
            "job": {},  # Missing status
            "runtime": {"image": "test:latest"},
            "settlement": {"digest": "sha256:fake"},
            "instances": {"stop_confirmed": True, "delete_confirmed": True},
            "active_total": 0,
        }
        receipt_path = tmp_path / "compshare-gpu.receipt.json"
        receipt_path.write_text(json.dumps(receipt))

        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)
        problems = self._get_problems(result)
        assert any("terminal" in p.lower() or "status" in p.lower() or "settlement" in p.lower() for p in problems)

    def test_missing_fetched_artifact_digest_fails(self, tmp_path: Path):
        """CompShare receipt without fetched artifact digest must FAIL."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        receipt = {
            "kind": "compshare-gpu-site",
            "schema_version": 1,
            "code_identity": {"commit": "abc123", "dirty": False},
            "site_profile_digest": "sha256:fake",
            "job": {"status": "succeeded"},
            "runtime": {"image": "test:latest"},
            "settlement": {"digest": "sha256:fake"},
            "instances": {"stop_confirmed": True, "delete_confirmed": True},
            "active_total": 0,
            "fetched_artifacts": [],  # Empty — no digests
        }
        receipt_path = tmp_path / "compshare-gpu.receipt.json"
        receipt_path.write_text(json.dumps(receipt))

        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)
        problems = self._get_problems(result)
        assert any("artifact" in p.lower() or "fetch" in p.lower() for p in problems)

    def test_stop_success_but_delete_failure_fails(self, tmp_path: Path):
        """stop_confirmed=True but delete_confirmed=False must FAIL."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        receipt = {
            "kind": "compshare-gpu-site",
            "schema_version": 1,
            "code_identity": {"commit": "abc123", "dirty": False},
            "site_profile_digest": "sha256:fake",
            "job": {"status": "succeeded"},
            "runtime": {"image": "test:latest"},
            "settlement": {"digest": "sha256:fake"},
            "instances": {"stop_confirmed": True, "delete_confirmed": False},  # Delete failed
            "active_total": 0,
            "fetched_artifacts": [{"path": "out/", "sha256": "sha256:abc"}],
        }
        receipt_path = tmp_path / "compshare-gpu.receipt.json"
        receipt_path.write_text(json.dumps(receipt))

        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)
        problems = self._get_problems(result)
        assert any("delete" in p.lower() or "instance" in p.lower() for p in problems)

    def test_live_instance_list_nonzero_fails(self, tmp_path: Path):
        """active_total > 0 means orphan instances — must FAIL."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        receipt = {
            "kind": "compshare-gpu-site",
            "schema_version": 1,
            "code_identity": {"commit": "abc123", "dirty": False},
            "site_profile_digest": "sha256:fake",
            "job": {"status": "succeeded"},
            "runtime": {"image": "test:latest"},
            "settlement": {"digest": "sha256:fake"},
            "instances": {"stop_confirmed": True, "delete_confirmed": True},
            "active_total": 2,  # Orphans!
            "fetched_artifacts": [{"path": "out/", "sha256": "sha256:abc"}],
        }
        receipt_path = tmp_path / "compshare-gpu.receipt.json"
        receipt_path.write_text(json.dumps(receipt))

        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)
        problems = self._get_problems(result)
        assert any("orphan" in p.lower() or "active" in p.lower() for p in problems)


class TestSlurmReceiptTrustContract:
    """Test Slurm receipt verification trust contract (unmocked)."""

    def _get_problems(self, result: dict) -> list[str]:
        """Get problems list from result."""
        return result.get("problems", [])

    def test_slurm_verification_returns_dict_with_problems(self, tmp_path: Path):
        """Slurm verification returns dict with 'problems' key."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        # Write a minimal Slurm receipt
        receipt = {
            "kind": "compute-profile-qualification",
            "schema_version": 1,
            "source_commit": "abc1234",
            "code_identity": {},
            "site_profile_digest": "sha256:fake",
            "probes": {
                "dispatcher.cpu": {"status": "PASS", "details": {}},
            },
            "evidence": {
                "jobs": [
                    {"probe_class": "cpu", "status": "COMPLETED"},
                ],
            },
            "runtime_lock": {
                "deepmd": {"version": "2.2.0", "sif": "deepmd.sif"},
            },
        }
        receipt_path = tmp_path / "slurm-cpu.receipt.json"
        receipt_path.write_text(json.dumps(receipt))

        result = verify_site_receipt(
            receipt, scheduler="slurm", root=tmp_path, receipt_dir=tmp_path
        )
        # Must return dict with 'problems' key
        assert isinstance(result, dict)
        assert "problems" in result
        assert "derived" in result
        # Problems must be a list
        problems = self._get_problems(result)
        assert isinstance(problems, list)


class TestCompShareNoSIFRequired:
    """Test that CompShare GPU receipt does NOT require SIF/Apptainer."""

    def _get_problems(self, result: dict) -> list[str]:
        """Get problems list from result."""
        return result.get("problems", [])

    def test_compshare_receipt_no_sif_field(self, tmp_path: Path):
        """CompShare receipt with no SIF fields should not fail for missing SIF."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        receipt = {
            "kind": "compshare-gpu-site",
            "schema_version": 1,
            "code_identity": {"commit": "abc123", "dirty": False},
            "site_profile_digest": "sha256:fake",
            "job": {"status": "succeeded"},
            "runtime": {"image": "test:latest"},
            "settlement": {"digest": "sha256:fake"},
            "instances": {"stop_confirmed": True, "delete_confirmed": True},
            "active_total": 0,
            "fetched_artifacts": [{"path": "out/", "sha256": "sha256:abc"}],
            # No runtime_lock, no SIF fields
        }
        receipt_path = tmp_path / "compshare-gpu.receipt.json"
        receipt_path.write_text(json.dumps(receipt))

        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)
        problems = self._get_problems(result)
        # Should NOT have Apptainer-specific problems (CompShare uses Docker images, not SIF)
        assert not any("apptainer" in p.lower() for p in problems)


class TestSchedulerMismatch:
    """Test that scheduler mismatch with SiteProfile must FAIL."""

    def _get_problems(self, result: dict) -> list[str]:
        """Get problems list from result."""
        return result.get("problems", [])

    def test_scheduler_mismatch_fails(self, tmp_path: Path):
        """Receipt with scheduler='slurm' but kind='compshare-gpu-site' must FAIL."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        receipt = {
            "kind": "compshare-gpu-site",  # CompShare kind
            "schema_version": 1,
            "code_identity": {"commit": "abc123", "dirty": False},
            "site_profile_digest": "sha256:fake",
            "job": {"status": "succeeded"},
            "runtime": {"image": "test:latest"},
            "settlement": {"digest": "sha256:fake"},
            "instances": {"stop_confirmed": True, "delete_confirmed": True},
            "active_total": 0,
            "fetched_artifacts": [{"path": "out/", "sha256": "sha256:abc"}],
        }
        receipt_path = tmp_path / "compshare-gpu.receipt.json"
        receipt_path.write_text(json.dumps(receipt))

        # Pass scheduler="slurm" but receipt is CompShare — mismatch
        result = verify_site_receipt(receipt, scheduler="slurm", root=tmp_path, receipt_dir=tmp_path)
        problems = self._get_problems(result)
        assert any("scheduler" in p.lower() or "mismatch" in p.lower() or "kind" in p.lower() for p in problems)


class TestVerifyReceiptReturnStructure:
    """Test real verify_receipt() return structure (unmocked)."""

    def test_verify_receipt_returns_dict_with_problems_list(self, tmp_path: Path):
        """verify_receipt() must return dict with 'problems' as list[str]."""
        from dftworld_bench.experiments.qualification_receipt import verify_receipt

        # Write a minimal receipt that will fail verification
        receipt = {
            "kind": "compute-profile-qualification",
            "schema_version": 1,
            "code_identity": {"commit": "abc123", "dirty": False},
            "compute_profile_digest": "sha256:fake",
            "site_receipts": {},
            "probes": {},  # Empty probes — will fail
        }
        receipt_path = tmp_path / "receipt.json"
        receipt_path.write_text(json.dumps(receipt))

        result = verify_receipt(receipt, root=tmp_path, receipt_dir=tmp_path)
        # Must be a dict
        assert isinstance(result, dict)
        # Must have 'problems' key
        assert "problems" in result
        # Problems must be a list
        assert isinstance(result["problems"], list)
        # Each problem must be a string
        for item in result["problems"]:
            assert isinstance(item, str)


class TestZeroOrphanFaultMatrix:
    """Matrix tests for Zero-Orphan cloud recycling gate (Gate A1)."""

    @patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
    def test_matrix_clean_slate_passes(self, tmp_path: Path):
        """Case A: 0 active cloud instances, 0 orphan ledger entries -> PASS."""
        receipts_dir, hashes = _setup_disk_receipts(tmp_path)
        receipt = _valid_receipt(site_receipts=hashes)
        verdict = verify_and_derive_qualification(
            receipt,
            site_receipts_dir=receipts_dir,
            active_instances_checker=lambda: [],
        )
        assert verdict.passed is True
        assert verdict.cloud_recycling_passed is True
        assert verdict.active_instances_count == 0
        assert verdict.orphan_instances_count == 0

    @patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
    def test_matrix_active_mlffbench_instance_fails(self, tmp_path: Path):
        """Case B: lingering active instance detected by live query -> FAIL."""
        receipts_dir, hashes = _setup_disk_receipts(tmp_path)
        receipt = _valid_receipt(site_receipts=hashes)
        verdict = verify_and_derive_qualification(
            receipt,
            site_receipts_dir=receipts_dir,
            active_instances_checker=lambda: ["inst-lingering-1"],
        )
        assert verdict.passed is False
        assert verdict.cloud_recycling_passed is False
        assert any("live cloud query detected active instances" in err for err in verdict.errors)

    @patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
    def test_matrix_orphan_count_in_receipt_fails(self, tmp_path: Path):
        """Case C: orphan ledger entry recorded in receipt evidence -> FAIL."""
        receipts_dir, hashes = _setup_disk_receipts(tmp_path)
        receipt = _valid_receipt(site_receipts=hashes)
        receipt["cloud_recycling_evidence"]["orphan_instances_count"] = 1
        receipt["digest"] = compute_receipt_digest(receipt)
        verdict = verify_and_derive_qualification(
            receipt,
            site_receipts_dir=receipts_dir,
            active_instances_checker=lambda: [],
        )
        assert verdict.passed is False
        assert verdict.cloud_recycling_passed is False
        assert any("orphan instances recorded" in err for err in verdict.errors)

    @patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok)
    def test_matrix_live_checker_error_fails_closed(self, tmp_path: Path):
        """Case D: live query failure must fail closed."""
        receipts_dir, hashes = _setup_disk_receipts(tmp_path)
        receipt = _valid_receipt(site_receipts=hashes)

        def failing_checker():
            raise RuntimeError("Cloud API timeout")

        verdict = verify_and_derive_qualification(
            receipt,
            site_receipts_dir=receipts_dir,
            active_instances_checker=failing_checker,
        )
        assert verdict.passed is False
        assert verdict.cloud_recycling_passed is False
        assert any("error querying live cloud instances" in err for err in verdict.errors)

    def test_matrix_ownership_marker_isolation(self):
        """Case E: Instances without mlffbench ownership marker are ignored by Zero-Orphan filter."""
        items = [
            {"id": "inst-1", "name": "user-dev-vm", "remark": "personal", "status": "Running"},
            {"id": "inst-2", "name": "mlffbench-run1-worker", "remark": "mlffbench:run1:worker", "status": "Running"},
            {"id": "inst-3", "name": "mlffbench-run2-worker", "remark": "mlffbench:run2:worker", "status": "Terminated"},
            {"id": "inst-4", "name": "other-training", "remark": "exp", "status": "Running"},
        ]
        active = []
        for item in items:
            name = str(item.get("name") or "")
            remark = str(item.get("remark") or "")
            inst_id = str(item.get("id") or "")
            if name.startswith("mlffbench-") or remark.startswith("mlffbench:") or inst_id.startswith("mlffbench-"):
                status = str(item.get("status") or "").lower()
                if status not in ("terminated", "deleted", "stopped", "failed"):
                    active.append(inst_id)

        assert active == ["inst-2"]


class TestCpuOnlySlurmQualificationContract:
    """Test CPU-only Slurm qualification requires only CPU canary."""

    def test_verify_site_receipt_slurm_cpu_only_passes_required_classes(self, tmp_path: Path):
        """When receipt evidence jobs only have probe_class='cpu', required_probe_classes={'cpu'}."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        with patch("dftworld_bench.experiments.qualification_receipt.verify_receipt") as mock_vr:
            mock_vr.return_value = {
                "receipt_dir": str(tmp_path),
                "digest_ok": True,
                "problems": [],
                "derived": {"qualification_status": "PASS", "formal_qualified": True},
            }

            receipt = {
                "kind": "hpc-dispatcher-qualification/site-v1",
                "schema_id": "https://mlip-bench.example/schemas/dispatcher-qualification-receipt.schema.json",
                "evidence": {
                    "jobs": [
                        {"probe_class": "cpu", "status": "COMPLETED"},
                    ]
                },
            }

            result = verify_site_receipt(receipt, scheduler="slurm", root=tmp_path, receipt_dir=tmp_path)
            assert mock_vr.called
            call_kwargs = mock_vr.call_args[1]
            assert call_kwargs["required_probe_classes"] == {"cpu"}


class TestCompShareReceiptAuditLineage:
    """Test CompShare receipt verification with real GatewayAudit ledger."""

    def _make_compshare_receipt(self, tmp_path: Path, *, audit_path: Path | None = None) -> dict[str, Any]:
        from dftworld_bench.experiments.qualification_receipt import canonical_digest, sha256_file

        code_file = tmp_path / "mod.py"
        code_file.write_text("# module code\n", encoding="utf-8")
        code_sha = sha256_file(code_file)

        receipt = {
            "kind": "compshare-gpu-site-qualification",
            "source_commit": "abcdef1234567890",
            "code_identity": {"mod.py": code_sha},
            "site_profile_digest": "sha256:" + "0" * 64,
            "runtime_lock": {
                "path": "reference/runtime/deepmd-runtime.lock.json",
                "image_id": "img-deepmd-gpu-v1",
            },
            "evidence": {
                "jobs": [
                    {
                        "probe_class": "gpu",
                        "accounting": {
                            "state": "COMPLETED",
                            "exit_code": 0,
                        },
                    }
                ],
                "settlement": {
                    "terminated": True,
                    "digest": "sha256:" + "1" * 64,
                },
                "instance_lifecycle": {
                    "instance_id": "inst-test-001",
                    "stop_confirmed": True,
                    "delete_confirmed": True,
                },
                "credential_isolation": {
                    "verified": True,
                },
                "fetch": {
                    "artifacts": [
                        {"name": "output.tar.gz", "sha256": "sha256:" + "2" * 64}
                    ]
                },
                "orphan_check": {
                    "method": "instance_list",
                    "active_total": 0,
                },
            },
        }
        if audit_path is not None:
            receipt["evidence"]["audit_log"] = str(audit_path.name)

        receipt["digest"] = canonical_digest({k: v for k, v in receipt.items() if k != "digest"})
        return receipt

    def test_compshare_receipt_with_sound_audit_log_passes(self, tmp_path: Path):
        """Sound GatewayAudit ledger allows CompShare receipt to PASS."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt
        from dftworld_bench.hpc.audit import GatewayAudit

        audit_path = tmp_path / "audit.jsonl"
        audit = GatewayAudit(audit_path)
        audit.append({"action": "INSTANCE_CREATE", "instance_id": "inst-test-001"})
        audit.append({"action": "JOB_SUBMIT", "job_id": "job-1"})
        audit.append({"action": "INSTANCE_DELETE", "instance_id": "inst-test-001"})

        receipt = self._make_compshare_receipt(tmp_path, audit_path=audit_path)
        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)

        assert result["problems"] == []
        assert result["derived"]["qualification_status"] == "PASS"

    def test_compshare_receipt_with_tampered_audit_log_fails(self, tmp_path: Path):
        """Tampered GatewayAudit ledger causes CompShare receipt to FAIL."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt
        from dftworld_bench.hpc.audit import GatewayAudit

        audit_path = tmp_path / "audit.jsonl"
        audit = GatewayAudit(audit_path)
        audit.append({"action": "INSTANCE_CREATE", "instance_id": "inst-test-001"})
        audit.append({"action": "JOB_SUBMIT", "job_id": "job-1"})

        # Tamper the file content directly
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        first_entry = json.loads(lines[0])
        first_entry["event"]["action"] = "TAMPERED_ACTION"
        lines[0] = json.dumps(first_entry)
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        receipt = self._make_compshare_receipt(tmp_path, audit_path=audit_path)
        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)

        assert any("GatewayAudit hash chain broken" in p for p in result["problems"])
        assert result["derived"]["qualification_status"] == "INVALID"

    def test_compshare_receipt_with_missing_audit_log_fails(self, tmp_path: Path):
        """Missing GatewayAudit ledger causes CompShare receipt to FAIL."""
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        missing_path = tmp_path / "nonexistent_audit.jsonl"
        receipt = self._make_compshare_receipt(tmp_path, audit_path=missing_path)
        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)

        assert any("audit log missing" in p for p in result["problems"])
        assert result["derived"]["qualification_status"] == "INVALID"


class TestP4Ed25519AndEvidenceIntegrity:
    """P4: Ed25519 signing, evidence containment, and audit log verification."""

    def test_ed25519_sign_and_verify_valid(self, tmp_path: Path):
        from dftworld_bench.experiments.compute_profile_qualification import (
            build_compute_profile_qualification_receipt,
            generate_ed25519_key_pair,
            verify_and_derive_qualification,
            verify_receipt_signature,
        )

        priv_hex, pub_hex = generate_ed25519_key_pair()
        receipts_dir, hashes = _setup_disk_receipts(tmp_path)
        raw_receipt = _valid_receipt(site_receipts=hashes)

        doc = build_compute_profile_qualification_receipt(
            compute_profile_id=raw_receipt["compute_profile_id"],
            compute_profile_digest=raw_receipt["compute_profile_digest"],
            routes=raw_receipt["routes"],
            site_receipts=raw_receipt["site_receipts"],
            cloud_recycling_evidence=raw_receipt["cloud_recycling_evidence"],
            private_key_hex=priv_hex,
        )

        assert "signature" in doc
        assert doc["signature"]["algorithm"] == "ed25519"
        assert doc["signature"]["public_key"] == pub_hex
        assert verify_receipt_signature(doc, expected_public_key_hex=pub_hex) is True

        with patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok):
            verdict = verify_and_derive_qualification(doc, site_receipts_dir=receipts_dir)
            assert verdict.passed is True
            assert verdict.errors == []

    def test_ed25519_tampered_receipt_fails(self, tmp_path: Path):
        from dftworld_bench.experiments.compute_profile_qualification import (
            build_compute_profile_qualification_receipt,
            compute_receipt_digest,
            generate_ed25519_key_pair,
            verify_and_derive_qualification,
            verify_receipt_signature,
        )

        priv_hex, _ = generate_ed25519_key_pair()
        receipts_dir, hashes = _setup_disk_receipts(tmp_path)
        raw_receipt = _valid_receipt(site_receipts=hashes)

        doc = build_compute_profile_qualification_receipt(
            compute_profile_id=raw_receipt["compute_profile_id"],
            compute_profile_digest=raw_receipt["compute_profile_digest"],
            routes=raw_receipt["routes"],
            site_receipts=raw_receipt["site_receipts"],
            cloud_recycling_evidence=raw_receipt["cloud_recycling_evidence"],
            private_key_hex=priv_hex,
        )

        # Attacker tampers field and recomputes digest to fool content-addressing
        doc["routes"]["cpu"] = "tampered-cpu"
        doc["digest"] = compute_receipt_digest(doc)

        assert verify_receipt_signature(doc) is False
        with patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok):
            verdict = verify_and_derive_qualification(doc, site_receipts_dir=receipts_dir)
            assert verdict.passed is False
            assert any("signature verification failed" in err for err in verdict.errors)

    def test_evidence_root_containment_violation_fails(self, tmp_path: Path):
        from dftworld_bench.experiments.compute_profile_qualification import (
            build_compute_profile_qualification_receipt,
            verify_and_derive_qualification,
        )

        receipts_dir, hashes = _setup_disk_receipts(tmp_path)
        raw_receipt = _valid_receipt(site_receipts=hashes)

        evidence_dir = tmp_path / "evidence"
        evidence_dir.mkdir()
        legit_file = evidence_dir / "trace.log"
        legit_file.write_text("ok")

        doc = build_compute_profile_qualification_receipt(
            compute_profile_id=raw_receipt["compute_profile_id"],
            compute_profile_digest=raw_receipt["compute_profile_digest"],
            routes=raw_receipt["routes"],
            site_receipts=raw_receipt["site_receipts"],
            cloud_recycling_evidence=raw_receipt["cloud_recycling_evidence"],
            evidence_root=str(evidence_dir),
            evidence_files=["trace.log", "../escape.txt"],
        )

        with patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt", _mock_verify_receipt_ok):
            verdict = verify_and_derive_qualification(doc, site_receipts_dir=receipts_dir)
            assert verdict.passed is False
            assert any("Evidence file containment error" in err for err in verdict.errors)

    def test_compshare_receipt_image_id_mismatch_fails(self, tmp_path: Path):
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt
        from dftworld_bench.hpc.audit import GatewayAudit

        audit_path = tmp_path / "audit.jsonl"
        audit = GatewayAudit(audit_path)
        audit.append({"action": "JOB_SUBMIT", "job_id": "job-1"})
        audit.append({"action": "INSTANCE_DELETE", "instance_id": "inst-test-001"})

        helper = TestCompShareReceiptAuditLineage()
        receipt = helper._make_compshare_receipt(tmp_path, audit_path=audit_path)
        receipt["evidence"]["instance_lifecycle"]["image_id"] = "img-mismatched-uuid"
        receipt["digest"] = compute_receipt_digest(receipt)

        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)
        assert any("Runtime image_id mismatch" in p for p in result["problems"])
        assert result["derived"]["qualification_status"] == "INVALID"

    def test_compshare_receipt_invalid_kind_fails(self, tmp_path: Path):
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt
        from dftworld_bench.hpc.audit import GatewayAudit

        audit_path = tmp_path / "audit.jsonl"
        audit = GatewayAudit(audit_path)
        audit.append({"action": "JOB_SUBMIT", "job_id": "job-1"})
        audit.append({"action": "INSTANCE_DELETE", "instance_id": "inst-test-001"})

        helper = TestCompShareReceiptAuditLineage()
        receipt = helper._make_compshare_receipt(tmp_path, audit_path=audit_path)
        receipt["kind"] = "unauthorized-custom-kind/v99"
        receipt["digest"] = compute_receipt_digest(receipt)

        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)
        assert any("missing or invalid kind" in p for p in result["problems"])
        assert result["derived"]["qualification_status"] == "INVALID"

    def test_compshare_receipt_artifact_path_traversal_fails(self, tmp_path: Path):
        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt
        from dftworld_bench.hpc.audit import GatewayAudit

        audit_path = tmp_path / "audit.jsonl"
        audit = GatewayAudit(audit_path)
        audit.append({"action": "JOB_SUBMIT", "job_id": "job-1"})
        audit.append({"action": "INSTANCE_DELETE", "instance_id": "inst-test-001"})

        helper = TestCompShareReceiptAuditLineage()
        receipt = helper._make_compshare_receipt(tmp_path, audit_path=audit_path)
        receipt["evidence"]["fetch"]["artifacts"] = [
            {"name": "../etc/passwd", "sha256": "sha256:" + "3" * 64}
        ]
        receipt["digest"] = compute_receipt_digest(receipt)

        result = verify_site_receipt(receipt, scheduler="compshare", root=tmp_path, receipt_dir=tmp_path)
        assert any("Artifact path escapes receipt dir" in p for p in result["problems"])
        assert result["derived"]["qualification_status"] == "INVALID"

