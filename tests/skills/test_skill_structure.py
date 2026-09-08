"""Skill structure validation for the two active skills (TS7 item 1).

Repo-side equivalent of the operator ``quick_validate hpc-submit`` /
``quick_validate paper-reproduction``: every active skill must carry a
``SKILL.md`` whose frontmatter is well-formed and whose name matches its
directory, and the deterministic files the skills document must actually exist
(references/templates/scripts and the shared schema).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "runtimes" / "recipes" / "skills"

ACTIVE = ("hpc-submit", "paper-reproduction")

# Per-skill files the two-skill plan requires.
REQUIRED = {
    "hpc-submit": {
        "SKILL.md",
        "references/running.md",
        "references/errors.md",
        "references/validation.md",
        "references/resources.md",
        "references/request-schema.md",
        "references/resource-guidance.md",
        "references/compute-capabilities.json",
        "examples/execution-request.yaml",
    },
    "paper-reproduction": {
        "SKILL.md",
        "references/evidence-and-provenance.md",
        "references/contract-freeze.md",
        "references/discrepancy-protocol.md",
        "references/research-state.md",
        "references/case-authoring.md",
        "templates/reproduction-contract.yaml",
        "templates/run-record.yaml",
        "templates/comparison-table.md",
        "templates/discrepancy-log.md",
        "templates/final-report.md",
        "templates/benchmark-export.md",
        "scripts/init_reproduction.py",
        "scripts/validate_reproduction.py",
    },
}

_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


def _frontmatter(skill_dir: Path) -> dict:
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    m = _FRONT.match(text)
    assert m, f"{skill_dir.name}/SKILL.md has no frontmatter block"
    return yaml.safe_load(m.group(1))


@pytest.mark.parametrize("skill", ACTIVE)
def test_skill_has_wellformed_frontmatter_matching_dir(skill):
    skill_dir = SKILLS / skill
    fm = _frontmatter(skill_dir)
    assert fm.get("name") == skill, "frontmatter name must equal directory name"
    assert fm.get("description"), "frontmatter description must be non-empty"


@pytest.mark.parametrize("skill", ACTIVE)
def test_skill_required_files_exist(skill):
    skill_dir = SKILLS / skill
    missing = [rel for rel in REQUIRED[skill] if not (skill_dir / rel).is_file()]
    assert missing == [], f"{skill} missing required files: {missing}"


def test_shared_compute_request_schema_survives():
    """schemas/compute-request.schema.json stays as the shared deterministic
    schema for the request-only mode (TS4), independent of any Skill."""
    schema = ROOT / "schemas" / "compute-request.schema.json"
    assert schema.is_file()
    import json

    doc = json.loads(schema.read_text(encoding="utf-8"))
    assert doc.get("$schema")


@pytest.mark.parametrize(
    "skill,script",
    [
        ("paper-reproduction", "init_reproduction.py"),
        ("paper-reproduction", "validate_reproduction.py"),
    ],
)
def test_skill_scripts_compile(skill, script):
    src = (SKILLS / skill / "scripts" / script).read_text(encoding="utf-8")
    compile(src, str(SKILLS / skill / "scripts" / script), "exec")
