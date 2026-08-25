"""Architecture-consistency gate: normative docs must define one enum universe.

The five documents under ``docs/architecture/`` are the single source of truth
for the benchmark's trust boundary, lifecycle, result classes, case contract,
and HPC contract.  This test fails RED before the docs exist and GREEN once
they define exactly the frozen enums and security invariants.
"""

import ast
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ARCH = ROOT / "docs" / "architecture"

EXPECTED_EXECUTION_CLASSES = {"local_sandbox", "hpc_controller"}
EXPECTED_RESULT_CLASSES = {"VALID_RESULT", "AGENT_FAILURE", "INFRA_INVALID"}
EXPECTED_JOB_STATES = {
    "QUEUED", "RUNNING", "SUCCEEDED", "FAILED",
    "CANCELLED", "TIMEOUT", "LOST",
}

DOC_FILES = (
    "THREAT-MODEL.md",
    "LIFECYCLE.md",
    "RESULT-TAXONOMY.md",
    "CASE-STANDARD.md",
    "HPC-CONTRACT-v1.md",
)


def _docs() -> dict[str, str]:
    return {name: (ARCH / name).read_text() for name in DOC_FILES}


def _extract_set(text: str, variable: str) -> set[str]:
    match = re.search(rf"{re.escape(variable)}\s*=\s*\{{([^}}]*)\}}", text)
    assert match is not None, f"docs must define {variable} = {{...}}"
    return set(ast.literal_eval("{" + match.group(1) + "}"))


def _all_docs_text() -> str:
    return "\n".join(_docs().values())


def execution_classes_from_docs() -> set[str]:
    return _extract_set(_docs()["CASE-STANDARD.md"], "execution_classes")


def result_classes_from_docs() -> set[str]:
    return _extract_set(_docs()["RESULT-TAXONOMY.md"], "result_classes")


def job_states_from_docs() -> set[str]:
    return _extract_set(_docs()["HPC-CONTRACT-v1.md"], "job_states")


def test_normative_documents_define_the_same_enums():
    assert execution_classes_from_docs() == EXPECTED_EXECUTION_CLASSES
    assert result_classes_from_docs() == EXPECTED_RESULT_CLASSES
    assert job_states_from_docs() == EXPECTED_JOB_STATES


def test_threat_model_treats_candidate_and_submission_as_untrusted():
    text = _docs()["THREAT-MODEL.md"]
    assert re.search(r"Candidate.*untrusted|untrusted.*Candidate", text, re.I | re.S)
    assert re.search(r"submission.*untrusted|untrusted.*submission", text, re.I | re.S)


def test_lifecycle_destroys_candidate_before_verifier_starts():
    text = _docs()["LIFECYCLE.md"]
    # Verifier launch is only reachable after CANDIDATE_DESTROYED.
    assert text.index("CANDIDATE_DESTROYED") < text.index("VERIFYING")


def test_hpc_contract_forbids_raw_ssh_or_scheduler_credentials_in_candidate():
    text = _docs()["HPC-CONTRACT-v1.md"].lower()
    assert "ssh" in text and "scheduler" in text
    assert "bench-hpc" in text  # the only HPC surface the Candidate sees


def test_all_five_documents_exist_and_have_content():
    for name in DOC_FILES:
        text = _docs()[name]
        assert len(text.strip()) > 200, f"{name} is empty"


def test_workspace_maintenance_policy_names_protected_areas():
    text = (ROOT / "docs/operations/workspace-maintenance.md").read_text()
    for required in (
        "042-go-water-dpmp",
        "evidence/hpc-cleanup-20260822",
        "evidence/hpc-dispatcher",
        "git worktree remove",
        "decision-ledger.json",
    ):
        assert required in text


def test_gitignore_covers_only_rebuildable_workspace_state():
    lines = set((ROOT / ".gitignore").read_text().splitlines())
    assert {".venv", "__pycache__/", "*.pyc", ".pytest_cache/", "/tmp/"} <= lines
    assert "/evidence/" not in lines
    assert "/reference/" not in lines
