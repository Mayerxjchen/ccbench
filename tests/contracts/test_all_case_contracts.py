"""Every numbered local case satisfies the infra v2 CaseSpec contract.

A local case manifest must be strict: ``[execution] class = "local_sandbox"``
(never inferred), carry no legacy agent policy (``legacy_agent_fields == ()``),
and own no candidate image — the harness resolves the candidate runtime from
the case's scientific runtime requirements.
"""
from __future__ import annotations

import re
from pathlib import Path

from ccbench.contracts.case import CaseSpec

ROOT = Path(__file__).resolve().parents[2]

def discover_numbered_cases(root: Path = ROOT) -> list[Path]:
    """All numbered case directories under root, sorted by number."""
    return sorted(
        p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{3}-.+", p.name)
    )


def test_all_active_cases_are_host_candidate_and_infra_free():
    for case in discover_numbered_cases(ROOT):
        spec = CaseSpec.load(case)
        assert spec.execution_class == "local_sandbox"
        assert spec.legacy_agent_fields == ()
        assert spec.candidate_image is None
        assert spec.candidate_runner == "host_claude_code"
        assert spec.agent_profile == "claude-mvp"
        assert spec.verifier_profile
        raw = (case / "case.toml").read_text(encoding="utf-8")
        assert "hpc_controller" not in raw
        assert "candidate.image" not in raw
        assert "dispatcher." not in raw
        assert "[hpc" not in raw
