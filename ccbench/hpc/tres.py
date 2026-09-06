"""TRES parsing for deterministic GPU/MIG gates (P0-E).

Slurm TRES (Track-able RESource) strings encode GPU allocation as:

    ReqTRES:  gpu=a100:1          — requesting 1 full A100 GPU
    AllocTRES: gpu=a100:1         — allocated 1 full A100 GPU
    AllocTRES: gpu:a100:1g.5g=1   — allocated 1 MIG slice (not full GPU)

The format is:

    <type>[:<型号>][:<大小>] = <数量>

where ``<大小>`` (e.g. ``1g.5g``, ``3g.10g``) indicates MIG slicing.
A full GPU has no ``<大小>`` component, or the component equals the full
GPU size (e.g. ``8g.40g`` on an A100-80GB).

Reference: <site-alias>-gres-mig-false-positive (memory) — Slurm accounting
defaults gpu:1 to a MIG classification; the real allocation must be
determined by parsing AllocTRES, not by ReqTRES alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GpuAllocation:
    """Parsed GPU allocation from a TRES string."""

    count: int  # total GPU units (full or MIG slices)
    model: str  # GPU model name (e.g. "a100", "h100", "")
    is_mig: bool  # True if any allocated GPU is a MIG slice
    mig_slices: tuple[str, ...] = ()  # MIG size labels (e.g. "1g.5g")

    @property
    def full_gpu_count(self) -> int:
        """Number of full (non-MIG) GPUs allocated."""
        return 0 if self.is_mig else self.count


# Regex: gpu[:model][:<mig_size>] = count
# Examples:
#   gpu=1                  → model="", mig=False, count=1
#   gpu:a100=1             → model="a100", mig=False, count=1
#   gpu:a100:1g.5g=1       → model="a100", mig=True, count=1, slices=("1g.5g",)
#   gpu:a100:1g.5g=2       → model="a100", mig=True, count=2
_GPU_RE = re.compile(
    r"gpu"                     # type
    r"(?::([a-zA-Z0-9._-]+))?" # optional model
    r"(?::([0-9]+g\.[0-9]+g))?" # optional MIG size (e.g. 1g.5g)
    r"=(\d+)"                  # count
)


def parse_tres(tres_str: str) -> GpuAllocation:
    """Parse a Slurm TRES string and extract GPU allocation.

    >>> parse_tres("gpu=1")
    GpuAllocation(count=1, model='', is_mig=False, mig_slices=())

    >>> parse_tres("gpu:a100=2")
    GpuAllocation(count=2, model='a100', is_mig=False, mig_slices=())

    >>> parse_tres("gpu:a100:1g.5g=1")
    GpuAllocation(count=1, model='a100', is_mig=True, mig_slices=('1g.5g',))

    Returns a zero-count allocation if no GPU entry is found.
    """
    count = 0
    model = ""
    is_mig = False
    slices: list[str] = []

    for m in _GPU_RE.finditer(tres_str):
        count += int(m.group(3))
        if m.group(1):
            model = m.group(1)
        if m.group(2):
            is_mig = True
            slices.append(m.group(2))

    return GpuAllocation(
        count=count,
        model=model,
        is_mig=is_mig,
        mig_slices=tuple(slices),
    )


def verify_full_gpu(
    req_tres: str,
    alloc_tres: str,
    *,
    require_full: bool = False,
) -> tuple[bool, str]:
    """Verify that the allocation grants full GPUs, not MIG slices.

    Args:
        req_tres: The requested TRES string.
        alloc_tres: The allocated TRES string.
        require_full: If True, a full GPU is mandatory; any MIG allocation
            is a failure. If False, MIG is allowed but flagged.

    Returns:
        (passed, reason) — passed is True when the gate is satisfied.
    """
    req = parse_tres(req_tres)
    alloc = parse_tres(alloc_tres)

    # No GPUs requested — gate is irrelevant
    if req.count == 0 and alloc.count == 0:
        return True, "no GPUs requested or allocated"

    # Nothing allocated but something was requested — infra failure
    if alloc.count == 0 and req.count > 0:
        return False, f"0 GPUs allocated but {req.count} requested (alloc={alloc_tres!r})"

    # MIG detected in allocation
    if alloc.is_mig:
        if require_full:
            return (
                False,
                f"MIG slices allocated ({alloc.mig_slices}) but full GPU required; "
                f"alloc={alloc_tres!r}",
            )
        return (
            True,
            f"MIG slices allocated ({alloc.mig_slices}); full GPU not required",
        )

    # Full GPU allocated
    if alloc.full_gpu_count < req.count:
        return (
            False,
            f"only {alloc.full_gpu_count} full GPU(s) allocated but {req.count} requested",
        )

    return True, f"{alloc.full_gpu_count} full GPU(s) allocated (model={alloc.model!r})"
