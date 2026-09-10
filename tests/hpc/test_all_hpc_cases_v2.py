"""All preserved cases share the portable Candidate/container contract.

Compute placement (local, CPU IKKEM, GPU CompShare, or a future operator
profile) is selected outside the case manifest.  This keeps case definitions
portable while making the Candidate control layer uniform.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bench.contracts.case import CaseSpec
from bench.executors import LocalExecutor, resolve

ROOT = Path(__file__).resolve().parents[2]

# The five HPC-controller cases.  ``*``-prefix so the directory slug may vary;
# ``discover`` asserts exactly one match per prefix.
HPC_CASE_PREFIXES = ("001-", "002-", "003-", "004-", "005-")


def discover_numbered_cases(root: Path = ROOT) -> list[Path]:
    """All numbered case directories under root, sorted by number."""
    return sorted(
        p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{3}-.+", p.name)
    )


def find_hpc_case(prefix: str, root: Path = ROOT) -> Path:
    search_dir = root / "cases" if (root / "cases").is_dir() else root
    matches = sorted(p for p in search_dir.iterdir() if p.name.startswith(prefix))
    assert len(matches) == 1, f"expected exactly one {prefix}* case, got {matches}"
    return matches[0]


@pytest.fixture()
def hpc_cases() -> list[CaseSpec]:
    return [CaseSpec.load(find_hpc_case(p)) for p in HPC_CASE_PREFIXES]


def test_all_cases_resolve_same_portable_executor(hpc_cases) -> None:
    """Every case uses the same local lifecycle and Candidate runner."""
    assert {spec.execution_class for spec in hpc_cases} == {"local_sandbox"}
    for spec in hpc_cases:
        assert isinstance(resolve(spec.execution_class), LocalExecutor), spec.case_id
        assert spec.candidate_runner == "container_claude_code"


def test_all_hpc_cases_have_no_legacy_agent_fields(hpc_cases) -> None:
    """No HPC case may carry harness-owned legacy agent policy such as
    ``agent.timeout_sec``; those belong to the infra profile, not the case."""
    for spec in hpc_cases:
        assert spec.legacy_agent_fields == (), (
            f"{spec.path.name}: legacy agent fields {spec.legacy_agent_fields} "
            "must be migrated to the infra profile"
        )
