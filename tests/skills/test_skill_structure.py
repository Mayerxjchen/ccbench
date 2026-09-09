"""Structure and fail-closed policy checks for the active Candidate skill."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "runtimes" / "recipes" / "skills" / "bench-compute-request"
_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


def test_request_skill_frontmatter_and_required_files() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    match = _FRONT.match(text)
    assert match
    frontmatter = yaml.safe_load(match.group(1))
    assert frontmatter["name"] == "bench-compute-request"
    assert frontmatter["description"]


def test_shared_compute_request_schema_is_present_and_closed() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "compute-request.schema.json").read_text(encoding="utf-8")
    )
    assert schema["$schema"]
    assert schema["additionalProperties"] is False


def test_skill_is_request_only_and_contains_no_operator_surface() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8").lower()
    for forbidden in (
        "ssh",
        "compshare-cli",
        "sbatch",
        "scancel",
        "docker",
    ):
        assert forbidden not in text, f"request skill leaks operator capability: {forbidden}"
    assert "compute-requests/" in text
    assert "request-only" in text
    assert "external" in text and "operator" in text
