"""Anti-leakage and taint audit for CCBench cases."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ccbench.contracts.case import CaseSpec

DOI_RE = re.compile(rb"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)


def check_case_leakage(case_dir: Path) -> tuple[bool, list[str]]:
    """Audit case public surface for accidental paper DOI or identity leakage.

    Only applies strictly if coverage.paradigm == 'research_question'.
    Returns (passed: bool, violations: list[str]).
    """
    case_dir = Path(case_dir)
    spec = CaseSpec.load(case_dir)

    violations = []
    if spec.coverage.paradigm != "research_question":
        return True, []

    # Check task.md
    task_file = case_dir / "task.md"
    if task_file.is_file():
        content = task_file.read_bytes()
        if DOI_RE.search(content):
            violations.append("task.md contains paper DOI")

    # Check input files
    input_dir = case_dir / "input"
    if input_dir.is_dir():
        for p in input_dir.rglob("*"):
            if p.is_file():
                try:
                    content = p.read_bytes()
                    if DOI_RE.search(content):
                        violations.append(f"input file '{p.name}' contains paper DOI")
                except Exception:
                    pass

    return len(violations) == 0, violations
