"""Every numbered local case satisfies the infra v2 CaseSpec contract.

A local case manifest must be strict: ``[execution] class = "local_sandbox"``
(never inferred), carry no legacy agent policy (``legacy_agent_fields == ()``),
and own no candidate image — the harness resolves the candidate runtime from
the case's scientific runtime requirements.
"""
from __future__ import annotations

import re
from pathlib import Path

from dftworld_bench.contracts.case import CaseSpec

ROOT = Path(__file__).resolve().parents[2]

# Local cases: 001–030 (001–042 span all; 031–034 and 042 are HPC-controller
# cases and are governed by the generic HPC contract, Task 16).
LOCAL_IDS = set(range(1, 31)) | set(range(35, 42))


def discover_numbered_cases(root: Path = ROOT) -> list[Path]:
    """All numbered case directories under root, sorted by number."""
    return sorted(
        p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{3}-.+", p.name)
    )


def test_all_local_cases_are_strict_and_infra_free():
    for case in discover_numbered_cases(ROOT):
        if int(case.name[:3]) not in LOCAL_IDS:
            continue
        spec = CaseSpec.load(case)
        assert spec.execution_class == "local_sandbox"
        assert spec.legacy_agent_fields == ()
        assert spec.candidate_image is None
