"""The bundled hpc-submit Skill teaches the bench-hpc descriptor lifecycle.

The repo keeps exactly two active skills (see ``test_active_skill_set.py``):
``hpc-submit`` and ``paper-reproduction``. This test pins the *submission
surface* contract for that pair.

Contract:

- No Candidate-visible submission-surface Skill may contain site facts
  (hostname/account/partition answers), remote roots, SSH/bootstrap
  instructions, raw scheduler commands, or Case identities/thresholds.
- ``hpc-submit`` must teach the gateway operations, operation attempts,
  parser gating (scheduler success != scientific success), and explicit
  fetch.
- The dependent Skill (``paper-reproduction``) routes its compute-heavy
  stages to ``hpc-submit`` rather than to raw remote work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "runtimes" / "recipes" / "skills"

ACTIVE_SKILLS = sorted(
    p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()
)

# Site facts and identities that must never appear in a Candidate-visible Skill.
_SITE_FACTS = (
    "acct-blocked",
    "<site-alias>",
    "<site-user>",
    "dftworld2-runs",
    "~/.cluster-agents.md",
)

# Raw remote workflow instructions replaced by the descriptor lifecycle.
_RAW_REMOTE = (
    "rsess",
    "#SBATCH",
    "sbatch ",
    "squeue",
    "sacct",
    "scancel",
    "sinfo",
    "module avail",
    "scp ",
    "rsync ",
    "ssh ",
    "ssh://",
)

# Case identities / hidden assets.
_CASE_ASSETS = (
    "matclaw-cips",
    "thresholds.json",
)


def _submission_surface_files() -> list[Path]:
    # Every markdown under the active skills is part of the submission surface.
    return sorted(SKILLS.rglob("*.md"))


def test_no_site_facts_or_raw_remote_instructions_anywhere():
    violations = []
    for path in _submission_surface_files():
        text = path.read_text(encoding="utf-8")
        for pattern in _SITE_FACTS + _RAW_REMOTE + _CASE_ASSETS:
            if pattern in text:
                violations.append(f"{path.relative_to(SKILLS)}: {pattern!r}")
    assert violations == [], "\n".join(violations)


def test_skill_teaches_seven_operations_attempts_and_gates():
    skill = (SKILLS / "hpc-submit" / "SKILL.md").read_text(encoding="utf-8")
    for command in ("capabilities", "submit", "status", "logs", "fetch",
                    "cancel", "usage"):
        assert command in skill, f"SKILL.md never teaches {command!r}"
    assert "--attempt" in skill
    # Scheduler success != scientific success: the engine parser gates.
    assert "parser" in skill.lower()
    assert "COMPLETED" in skill or "SUCCEEDED" in skill
    # Attempts are explicit scientific retries.
    assert "attempt" in skill.lower()
    # Explicit fetch only.
    assert "explicit" in skill.lower()


def test_execution_request_example_is_valid_v2_payload():
    import json

    import jsonschema

    example_path = SKILLS / "hpc-submit" / "examples" / "execution-request.yaml"
    assert example_path.exists(), "descriptor example missing"
    text = example_path.read_text(encoding="utf-8")
    try:
        import yaml

        payload = yaml.safe_load(text)
    except ImportError:  # pragma: no cover - pyyaml ships with the venv
        payload = json.loads(text)
    schema = json.loads(
        (ROOT / "schemas" / "execution-request.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(payload)


def test_no_raw_scheduler_tooling_shipped_in_bundle():
    scripts = SKILLS / "hpc-submit" / "scripts"
    assert not scripts.exists(), (
        "raw scheduler tooling must not ship in the descriptor-lifecycle bundle"
    )
    template = SKILLS / "hpc-submit" / "references" / "cluster-guide-template.md"
    assert not template.exists(), (
        "raw cluster-guide bootstrap template must not ship to Candidates"
    )


_DEPENDENT_SKILLS = [s for s in ACTIVE_SKILLS if s != "hpc-submit"]


@pytest.mark.parametrize("skill", _DEPENDENT_SKILLS)
def test_dependent_skills_route_to_descriptor_workflow(skill):
    """Dependent Skills route compute to bench-hpc operations, never to raw
    remote work (site facts, cluster-agents bootstrap, scheduler scripts)."""
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "hpc-submit" in text, f"{skill} must route compute stages to hpc-submit"
    assert "cluster-agents" not in text
