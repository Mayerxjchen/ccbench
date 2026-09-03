"""Unit tests for ComputeProfile Qualification (Layer 2) and Zero-Orphan Cloud Recycling Gate."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from dftworld_bench.experiments.compute_profile_qualification import (
    ComputeProfileQualificationError,
    compute_receipt_digest,
    verify_and_derive_qualification,
)


def _valid_receipt() -> dict[str, Any]:
    doc = {
        "kind": "hpc-compute-profile-qualification/v1",
        "schema_id": "https://mlip-bench.example/schemas/compute-profile-qualification-receipt.schema.json",
        "compute_profile_id": "maintainer-hybrid-v1",
        "compute_profile_digest": "a" * 64,
        "routes": {
            "cpu": "ikkem-cpu",
            "gpu": "compshare-gpu",
        },
        "site_receipts": {
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


def test_valid_compute_profile_qualification_passes():
    receipt = _valid_receipt()
    verdict = verify_and_derive_qualification(receipt)
    assert verdict.passed is True
    assert verdict.routes_valid is True
    assert verdict.cpu_site_qualified is True
    assert verdict.gpu_site_qualified is True
    assert verdict.cloud_recycling_passed is True
    assert verdict.errors == []


def test_active_instances_remaining_fails_zero_orphan_gate():
    receipt = _valid_receipt()
    receipt["cloud_recycling_evidence"]["active_instances_count"] = 1
    receipt["digest"] = compute_receipt_digest(receipt)

    verdict = verify_and_derive_qualification(receipt)
    assert verdict.passed is False
    assert verdict.cloud_recycling_passed is False
    assert any("Zero-Orphan Gate failed" in err for err in verdict.errors)


def test_orphan_instance_fails_gate():
    receipt = _valid_receipt()
    receipt["cloud_recycling_evidence"]["orphan_instances_count"] = 1
    receipt["digest"] = compute_receipt_digest(receipt)

    verdict = verify_and_derive_qualification(receipt)
    assert verdict.passed is False
    assert verdict.cloud_recycling_passed is False
    assert any("orphan instances recorded" in err for err in verdict.errors)


def test_unsettled_termination_fails_gate():
    receipt = _valid_receipt()
    receipt["cloud_recycling_evidence"]["settlement_terminated"] = False
    receipt["digest"] = compute_receipt_digest(receipt)

    verdict = verify_and_derive_qualification(receipt)
    assert verdict.passed is False
    assert verdict.cloud_recycling_passed is False
    assert any("Settlement termination not confirmed" in err for err in verdict.errors)


def test_live_active_instances_checker_catches_running_cloud_instance():
    receipt = _valid_receipt()
    checker = lambda: ["inst-999"]
    verdict = verify_and_derive_qualification(receipt, active_instances_checker=checker)
    assert verdict.passed is False
    assert verdict.cloud_recycling_passed is False
    assert any("Live cloud check detected active instances" in err for err in verdict.errors)


def test_tampered_digest_fails_closed():
    receipt = _valid_receipt()
    receipt["digest"] = f"sha256:{'f' * 64}"
    with pytest.raises(ComputeProfileQualificationError, match="Receipt digest mismatch"):
        verify_and_derive_qualification(receipt)


def test_unqualified_site_fails_closed():
    receipt = _valid_receipt()
    receipt["site_receipts"] = {"ikkem-cpu": f"sha256:{'1' * 64}"}  # missing compshare-gpu
    receipt["digest"] = compute_receipt_digest(receipt)

    verdict = verify_and_derive_qualification(receipt)
    assert verdict.passed is False
    assert verdict.gpu_site_qualified is False
    assert any("compshare-gpu" in err for err in verdict.errors)

