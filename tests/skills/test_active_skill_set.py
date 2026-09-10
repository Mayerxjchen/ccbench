"""The Candidate receives exactly one static, request-only skill."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "runtimes" / "recipes" / "skills"


def _active_skill_dirs() -> list[str]:
    return sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())


def test_active_skill_set_is_exactly_request_only() -> None:
    assert _active_skill_dirs() == ["bench-compute-request"]


def test_request_skill_is_allowlisted_by_host_profile() -> None:
    from bench.config.profiles import load_infra_profiles

    profiles = load_infra_profiles()
    assert profiles.require("agents", "claude-mvp")["allowed_skills"] == [
        "bench-compute-request"
    ]


def test_no_maintainer_skill_dir_remains() -> None:
    assert not (ROOT / "maintainer" / "skills").exists()
