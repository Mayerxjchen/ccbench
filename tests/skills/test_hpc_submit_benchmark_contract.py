"""The bundled hpc-submit Skill teaches the bench-hpc descriptor lifecycle.

Contract:

- No Candidate-visible submission-surface Skill may contain site facts
  (hostname/account/partition answers), remote roots, SSH/bootstrap
  instructions, raw scheduler commands, or Case identities/thresholds.
- The Skill must teach all seven operations, operation attempts, parser
  gating (scheduler success != scientific success), and explicit fetch.

Scan scope: every ``base-env-build/skills/**/*.md`` EXCEPT ``rsess/``,
``research-orchestrator/``, and ``review-response/`` — those are not part of
the HPC submission surface (remote-session tooling / orchestration protocol /
worked-example corpus) and their cleanup is tracked separately from this
benchmark bundle task.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "runtimes" / "recipes" / "skills"
EXCLUDED = ("rsess", "research-orchestrator", "review-response")

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
    files = []
    for path in sorted(SKILLS.rglob("*.md")):
        relative = path.relative_to(SKILLS)
        if relative.parts[0] in EXCLUDED:
            continue
        files.append(path)
    return files


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


@pytest.mark.parametrize("skill", ["deepmd", "lammps", "cp2k", "comp-chem-workflow"])
def test_engine_skills_route_to_descriptor_workflow(skill):
    """Dependent Skills route to bench-hpc operations, never to raw remote work."""
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    if "hpc-submit" not in text:
        pytest.skip(f"{skill} does not route through hpc-submit")
    assert "cluster-agents" not in text
