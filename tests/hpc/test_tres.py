"""Tests for ccbench.hpc.tres — TRES parsing and GPU/MIG gates (P0-E)."""

from __future__ import annotations

import pytest

from ccbench.hpc.tres import GpuAllocation, parse_tres, verify_full_gpu


# ---- parse_tres ----

class TestParseTres:
    def test_simple_gpu(self):
        r = parse_tres("gpu=1")
        assert r.count == 1
        assert r.model == ""
        assert r.is_mig is False
        assert r.mig_slices == ()

    def test_model_only(self):
        r = parse_tres("gpu:a100=2")
        assert r.count == 2
        assert r.model == "a100"
        assert r.is_mig is False

    def test_mig_slice(self):
        r = parse_tres("gpu:a100:1g.5g=1")
        assert r.count == 1
        assert r.model == "a100"
        assert r.is_mig is True
        assert r.mig_slices == ("1g.5g",)

    def test_multiple_mig_slices(self):
        r = parse_tres("gpu:h100:1g.5g=2")
        assert r.count == 2
        assert r.model == "h100"
        assert r.is_mig is True
        assert r.mig_slices == ("1g.5g",)

    def test_mixed_full_and_mig(self):
        r = parse_tres("gpu:a100:4g.20g=1, gpu:a100:1g.5g=2")
        assert r.count == 3
        assert r.is_mig is True

    def test_no_gpu(self):
        r = parse_tres("cpu=64, mem=256G")
        assert r.count == 0
        assert r.is_mig is False

    def test_full_gpu_mig_size(self):
        """Full A100-80GB has size 8g.40g — should be treated as full GPU."""
        r = parse_tres("gpu:a100:8g.40g=1")
        assert r.count == 1
        assert r.model == "a100"
        assert r.is_mig is True  # technically a MIG slice, even if full

    def test_full_gpu_count(self):
        r = parse_tres("gpu:a100=1")
        assert r.full_gpu_count == 1

    def test_mig_full_gpu_count_is_zero(self):
        r = parse_tres("gpu:a100:1g.5g=1")
        assert r.full_gpu_count == 0


# ---- verify_full_gpu ----

class TestVerifyFullGpu:
    def test_no_gpu_requested(self):
        ok, reason = verify_full_gpu("", "")
        assert ok is True
        assert "no GPUs" in reason

    def test_full_gpu_allocated(self):
        ok, reason = verify_full_gpu("gpu:a100=1", "gpu:a100=1")
        assert ok is True
        assert "1 full GPU" in reason

    def test_mig_allocated_when_full_required(self):
        ok, reason = verify_full_gpu(
            "gpu:a100=1", "gpu:a100:1g.5g=1", require_full=True
        )
        assert ok is False
        assert "MIG" in reason

    def test_mig_allocated_when_full_not_required(self):
        ok, reason = verify_full_gpu(
            "gpu:a100=1", "gpu:a100:1g.5g=1", require_full=False
        )
        assert ok is True
        assert "MIG" in reason

    def test_no_allocation_when_requested(self):
        ok, reason = verify_full_gpu("gpu:a100=1", "")
        assert ok is False
        assert "0 GPUs allocated" in reason

    def test_partial_allocation(self):
        ok, reason = verify_full_gpu("gpu:a100=2", "gpu:a100=1")
        assert ok is False
        assert "only 1 full GPU" in reason

    def test_overallocation(self):
        ok, reason = verify_full_gpu("gpu:a100=1", "gpu:a100=2")
        assert ok is True
        assert "2 full GPU" in reason

    def test_site_false_positive(self):
        """Simulate the <site-alias> false positive: ReqTRES=full, AllocTRES=MIG classification."""
        # The actual allocation is full GPU but Slurm accounting shows MIG
        ok, reason = verify_full_gpu(
            "gpu:tesla:1", "gpu:tesla:1g.5g=1", require_full=True
        )
        assert ok is False  # fail-closed: ambiguous MIG → reject
        assert "MIG" in reason


# ---- fake Slurm fixtures integration ----

class TestFakeSlurmFixtures:
    """Verify TRES parsing against the fake Slurm fixture suite."""

    def test_full_a100_fixture(self):
        from tests.hpc.fake_slurm import FIXTURE_FULL_A100
        alloc = parse_tres(FIXTURE_FULL_A100.alloc_tres)
        assert alloc.count == 1
        assert alloc.model == "a100"
        assert alloc.is_mig is False
        ok, _ = verify_full_gpu(FIXTURE_FULL_A100.req_tres, FIXTURE_FULL_A100.alloc_tres)
        assert ok is True

    def test_mig_fixture(self):
        from tests.hpc.fake_slurm import FIXTURE_MIG_A100
        alloc = parse_tres(FIXTURE_MIG_A100.alloc_tres)
        assert alloc.is_mig is True
        assert alloc.mig_slices == ("1g.5g",)

    def test_site_fixture_rejects_full_required(self):
        from tests.hpc.fake_slurm import FIXTURE_IKKEMHPC_FALSE_POSITIVE
        ok, reason = verify_full_gpu(
            FIXTURE_IKKEMHPC_FALSE_POSITIVE.req_tres,
            FIXTURE_IKKEMHPC_FALSE_POSITIVE.alloc_tres,
            require_full=True,
        )
        assert ok is False
        assert "MIG" in reason

    def test_pending_fixture_has_no_alloc(self):
        from tests.hpc.fake_slurm import FIXTURE_PENDING_NO_ALLOC
        ok, reason = verify_full_gpu(
            FIXTURE_PENDING_NO_ALLOC.req_tres,
            FIXTURE_PENDING_NO_ALLOC.alloc_tres,
        )
        assert ok is False
        assert "0 GPUs allocated" in reason

    def test_cpu_only_fixture(self):
        from tests.hpc.fake_slurm import FIXTURE_CPU_ONLY
        ok, _ = verify_full_gpu(
            FIXTURE_CPU_ONLY.req_tres, FIXTURE_CPU_ONLY.alloc_tres
        )
        assert ok is True

    def test_partial_alloc_fixture(self):
        from tests.hpc.fake_slurm import FIXTURE_PARTIAL_ALLOC
        ok, reason = verify_full_gpu(
            FIXTURE_PARTIAL_ALLOC.req_tres,
            FIXTURE_PARTIAL_ALLOC.alloc_tres,
        )
        assert ok is False
        assert "only 1 full GPU" in reason

    def test_scontrol_render(self):
        """Verify fixtures render valid scontrol output."""
        from tests.hpc.fake_slurm import FIXTURE_FULL_A100
        out = FIXTURE_FULL_A100.to_scontrol()
        assert "JobId=12345" in out
        assert "JobState=COMPLETED" in out
        assert "AllocTRES=gpu:a100=1" in out
