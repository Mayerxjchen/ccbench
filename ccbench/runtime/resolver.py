"""ccbench runtime resolver — Single source of truth for runtime recipes and tool policies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ccbench.config.profiles import canonical_json, digest_bytes
from ccbench.paths import RUNTIME_RECIPES_DIR


class RuntimeContractError(RuntimeError):
    """Raised when runtime environment contracts or tool policies cannot be validated."""


def resolve_tool_policy(
    agent_recipe_name: str = "agent-claude-code",
    recipes_dir: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """Load and validate the formal tool policy for candidate agent.

    Args:
        agent_recipe_name: Subdirectory in recipes/ containing tool-policy.json
        recipes_dir: Root recipes directory (defaults to RUNTIME_RECIPES_DIR)

    Returns:
        tuple of (tool_policy_payload: dict, tool_surface_digest: str)

    Raises:
        RuntimeContractError: If tool-policy.json is missing or malformed.
    """
    base_dir = recipes_dir or RUNTIME_RECIPES_DIR
    policy_file = base_dir / agent_recipe_name / "tool-policy.json"

    if not policy_file.is_file():
        if agent_recipe_name == "agent-claude-code":
            policy_file = Path(__file__).resolve().parents[2] / "infra" / "config" / "tool-policy.json"
        if not policy_file.is_file():
            raise RuntimeContractError(
                f"Candidate tool policy missing at {policy_file}. "
                "Formal run lock requires locked tool policy for provenance."
            )

    try:
        payload = json.loads(policy_file.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeContractError(
            f"Malformed tool policy at {policy_file}: {exc}"
        ) from exc

    tool_surface_digest = digest_bytes(canonical_json(payload))
    return payload, tool_surface_digest
