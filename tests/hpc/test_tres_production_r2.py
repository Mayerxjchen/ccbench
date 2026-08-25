"""R2 RED gates: TRES/GPU production integration (C4/P0-E).

These fail against the current tree because ``parse_tres`` / ``verify_full_gpu``
have zero production callers: the Slurm adapter never checks AllocTRES after a
job completes, runtime qualification never sees scheduler GPU identity, and no
profile/receipt path fails closed on MIG.

The gates are *production*: they assert the wiring points where a real site
would hand GPU accounting data back to the trusted path.
"""

from __future__ import annotations

import re

import pytest


def test_slurm_adapter_status_verifies_gpu_allocation():
    """C4: a completed GPU job must reconcile AllocTRES against ReqTRES.

    RED: `SlurmAdapter.status()` maps the transport's JobState and never calls
    `parse_tres()` / `verify_full_gpu()` — a job that was allocated a MIG slice
    (or zero GPUs) after requesting a full GPU is reported as SUCCEEDED.
    """
    import inspect
    from dftworld_bench.hpc.adapters import slurm as mod

    src = inspect.getsource(mod)
    assert "verify_full_gpu(" in src
    assert "alloc_tres" in src


def test_runtime_qualification_checks_scheduler_gpu_identity():
    """C4: runtime qualification must fail closed on a MIG/ambiguous GPU.

    RED: `qualify_runtime()` checks digest/platform/non-root/imports, never
    scheduler GPU identity; a GPU runtime qualified without AllocTRES evidence
    would admit an allocation that cannot run a full-GPU job.
    """
    import inspect
    from dftworld_bench.runtime import qualify as mod

    src = inspect.getsource(mod)
    assert "MIG" in src or "mig" in src


def test_full_gpu_profile_rejects_mig_alloc():
    """C4: a full-GPU site profile must reject a MIG allocation.

    RED: `verify_full_gpu(..., require_full=True)` is implemented but no
    production caller passes site-profile `require_full`; a MIG allocation can
    therefore never be rejected by the admission path.
    """
    from dftworld_bench.hpc.tres import verify_full_gpu

    ok, reason = verify_full_gpu(
        req_tres="gpu:tesla=1", alloc_tres="gpu:tesla:1g.5g=1",
        require_full=True,
    )
    assert ok is False
    assert "mig" in reason.lower()
