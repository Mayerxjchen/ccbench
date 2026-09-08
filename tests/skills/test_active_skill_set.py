"""Repo constraint: exactly two active skills.

Per the two-skill convergence plan the active runtime skill set must be
exactly {``hpc-submit``, ``paper-reproduction``}. This test fails on any
addition or removal of a top-level skill under ``runtimes/recipes/skills/``,
and on any resurrected ``maintainer/skills/`` builder skill.

Deterministic skills live in the ``ccbench`` package, schemas, and CLI (see
``ccbench/mvp.py``, ``schemas/compute-request.schema.json``); they do not need
to be wrapped as Skills, so a new capability should not appear here.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "runtimes" / "recipes" / "skills"
MAINTAINER_SKILLS = ROOT / "maintainer" / "skills"

# The only active Skills the repository may ship.
ALLOWED_ACTIVE_SKILLS = frozenset({"hpc-submit", "paper-reproduction"})


def _active_skill_dirs():
    return sorted(
        p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()
    )


def test_active_skill_set_is_exactly_two():
    assert _active_skill_dirs() == sorted(ALLOWED_ACTIVE_SKILLS), (
        "Active runtime skill set must be exactly "
        "{hpc-submit, paper-reproduction}; found: "
        f"{_active_skill_dirs()!r}"
    )


def test_no_maintainer_skill_dir_remains():
    assert not MAINTAINER_SKILLS.exists(), (
        "maintainer/skills/ must be gone; its rules live in "
        "paper-reproduction/references/case-authoring.md"
    )
