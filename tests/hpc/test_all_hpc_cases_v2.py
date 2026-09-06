"""All five HPC-controller cases satisfy the generic HPC contract (Task 16).

031–034 and 042 are governed by the same ``hpc_controller`` execution
contract, not by per-case branches.  A case manifest must:

* declare ``execution.class = hpc_controller`` (never inferred);
* resolve through the case-agnostic executor registry to the same
  ``HpcExecutor``;
* carry no legacy agent policy (``legacy_agent_fields == ()``);
* declare scientific runtime / capability needs instead of owning a concrete
  image (the runtime registry resolves the image);

This module is the RED spec for Task 16: it must fail until the migration
(``scripts/infra/migrate_case_contracts.py --scope hpc``) removes the legacy
agent fields and the per-case runtime/Dockerfile authority.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ccbench.contracts.case import CaseSpec
from ccbench.executors import HpcExecutor, resolve

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


def test_all_hpc_cases_resolve_same_executor_different_requirements(hpc_cases) -> None:
    """Every HPC case declares the same execution class and resolves through the
    case-agnostic registry to the same HpcExecutor — no per-case executor."""
    assert {spec.execution_class for spec in hpc_cases} == {"hpc_controller"}
    from ccbench.hpc.dispatcher import HpcDispatcher
    from ccbench.hpc.gateway_runtime import GatewayRuntime

    for spec in hpc_cases:
        executor = resolve(
            spec.execution_class,
            dispatcher=HpcDispatcher(GatewayRuntime(), {}),
        )
        assert isinstance(executor, HpcExecutor), spec.case_id


def test_all_hpc_cases_have_no_legacy_agent_fields(hpc_cases) -> None:
    """No HPC case may carry harness-owned legacy agent policy such as
    ``agent.timeout_sec``; those belong to the infra profile, not the case."""
    for spec in hpc_cases:
        assert spec.legacy_agent_fields == (), (
            f"{spec.path.name}: legacy agent fields {spec.legacy_agent_fields} "
            "must be migrated to the infra profile"
        )
