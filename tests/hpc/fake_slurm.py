"""Fake Slurm fixtures for deterministic GPU/MIG gate testing (P0-E).

Provides canned ``scontrol`` / ``sacct`` outputs that replicate real Slurm
responses, including edge cases like the <site-alias> MIG false positive.
These are pure data — no subprocess calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeScontrolOutput:
    """Canned scontrol show job output with TRES fields."""

    job_id: str
    job_state: str  # PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
    req_tres: str  # e.g. "gpu:a100:1g.5g=1,cpu=32,mem=256G"
    alloc_tres: str  # e.g. "gpu:a100=1,cpu=32,mem=256G" or "" when pending
    node_list: str = ""  # e.g. "gpu-node-[001-004]"
    reason: str = ""  # e.g. "Priority" for pending jobs
    exit_code: int = 0

    def to_scontrol(self) -> str:
        """Render as scontrol show job output."""
        lines = [
            f"JobId={self.job_id}",
            f"JobState={self.job_state}",
            f"ReqTRES={self.req_tres}",
            f"AllocTRES={self.alloc_tres}",
            f"NodeList={self.node_list}",
            f"ExitCode={self.exit_code}:0",
        ]
        if self.reason:
            lines.append(f"Reason={self.reason}")
        return "\n".join(lines)


# ---- Canonical fixtures ----


# Full A100 GPU — normal allocation
FIXTURE_FULL_A100 = FakeScontrolOutput(
    job_id="12345",
    job_state="COMPLETED",
    req_tres="gpu:a100=1,cpu=32,mem=256G",
    alloc_tres="gpu:a100=1,cpu=32,mem=256G",
    node_list="gpu-node-001",
    exit_code=0,
)

# MIG slice — valid but not full GPU
FIXTURE_MIG_A100 = FakeScontrolOutput(
    job_id="12346",
    job_state="COMPLETED",
    req_tres="gpu:a100:1g.5g=1,cpu=16,mem=128G",
    alloc_tres="gpu:a100:1g.5g=1,cpu=16,mem=128G",
    node_list="gpu-node-002",
    exit_code=0,
)

# <site-alias> false positive: ReqTRES=full, AllocTRES shows MIG classification
# but the actual allocation IS a full GPU (Slurm accounting artifact)
FIXTURE_IKKEMHPC_FALSE_POSITIVE = FakeScontrolOutput(
    job_id="12347",
    job_state="COMPLETED",
    req_tres="gpu:tesla:1,cpu=32,mem=256G",
    alloc_tres="gpu:tesla:1g.5g=1,cpu=32,mem=256G",
    node_list="gpu-node-003",
    exit_code=0,
)

# Pending job — no allocation yet
FIXTURE_PENDING_NO_ALLOC = FakeScontrolOutput(
    job_id="12348",
    job_state="PENDING",
    req_tres="gpu:a100=2,cpu=64,mem=512G",
    alloc_tres="",
    reason="Priority",
)

# Zero GPU — CPU-only job
FIXTURE_CPU_ONLY = FakeScontrolOutput(
    job_id="12349",
    job_state="COMPLETED",
    req_tres="cpu=32,mem=128G",
    alloc_tres="cpu=32,mem=128G",
    node_list="cpu-node-001",
    exit_code=0,
)

# Multiple GPUs — full allocation
FIXTURE_MULTI_FULL = FakeScontrolOutput(
    job_id="12350",
    job_state="COMPLETED",
    req_tres="gpu:a100=4,cpu=128,mem=1024G",
    alloc_tres="gpu:a100=4,cpu=128,mem=1024G",
    node_list="gpu-node-[001-004]",
    exit_code=0,
)

# Partial allocation — requested 2, got 1
FIXTURE_PARTIAL_ALLOC = FakeScontrolOutput(
    job_id="12351",
    job_state="COMPLETED",
    req_tres="gpu:h100=2,cpu=64,mem=512G",
    alloc_tres="gpu:h100=1,cpu=32,mem=256G",
    node_list="gpu-node-005",
    exit_code=0,
)

# H100 full GPU
FIXTURE_FULL_H100 = FakeScontrolOutput(
    job_id="12352",
    job_state="COMPLETED",
    req_tres="gpu:h100=1,cpu=32,mem=256G",
    alloc_tres="gpu:h100=1,cpu=32,mem=256G",
    node_list="gpu-node-006",
    exit_code=0,
)

ALL_FIXTURES: list[FakeScontrolOutput] = [
    FIXTURE_FULL_A100,
    FIXTURE_MIG_A100,
    FIXTURE_IKKEMHPC_FALSE_POSITIVE,
    FIXTURE_PENDING_NO_ALLOC,
    FIXTURE_CPU_ONLY,
    FIXTURE_MULTI_FULL,
    FIXTURE_PARTIAL_ALLOC,
    FIXTURE_FULL_H100,
]
