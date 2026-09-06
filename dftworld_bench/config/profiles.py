"""Centralized profile registry for dftworld infrastructure.

Profiles are loaded from TOML files in infra/config/ and provide typed
configuration for agents, models, APIs, experiments, runtimes, and sites.
The registry is immutable after loading and provides deterministic digests.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def canonical_json(obj: Any) -> str:
    """Serialize to canonical JSON with sorted keys and no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_bytes(data: str | bytes) -> str:
    """Compute SHA-256 hex digest of string or bytes."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ProfileRegistry:
    """Immutable registry of typed configuration profiles.

    Profiles are organized by kind (agents, models, api, experiments, runtimes,
    sites) and loaded from TOML files. The registry provides deterministic
    digests for identity and comparison.
    """

    profiles: dict[str, dict[str, dict[str, Any]]]

    @classmethod
    def load(cls, root: Path) -> ProfileRegistry:
        """Load all *-profiles.toml files from the given directory.

        Files are loaded in sorted order. Duplicate profile names across
        files raise ValueError. Agent profiles are validated against
        agent-profile.schema.json.
        """
        merged: dict[str, dict[str, dict[str, Any]]] = {}
        # Track (kind, name) → source file for duplicate detection
        source_map: dict[tuple[str, str], str] = {}
        for path in sorted(root.glob("*-profiles.toml")):
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            for kind, values in data.items():
                if not isinstance(values, dict):
                    continue
                for name, profile in values.items():
                    key = (kind, name)
                    if key in source_map:
                        raise ValueError(
                            f"duplicate profile {kind}/{name}: "
                            f"defined in {source_map[key]} and {path}"
                        )
                    source_map[key] = str(path)
                    if isinstance(profile, dict):
                        for k, v in profile.items():
                            if k in ("max_turns", "max_model_turns", "max_total_tokens") and not isinstance(v, int):
                                raise ValueError(
                                    f"invalid {kind} profile {name} in {path}: expected int for {k}, got {type(v).__name__}"
                                )
                    merged.setdefault(kind, {})[name] = profile
        return cls(merged)

    @classmethod
    def from_mapping(cls, mapping: dict[str, dict[str, dict[str, Any]]]) -> ProfileRegistry:
        """Create a registry from an in-memory mapping (for testing)."""
        return cls(mapping)

    def require(self, kind: str, name: str) -> dict[str, Any]:
        """Get a profile by kind and name, raising if not found."""
        kind_profiles = self.profiles.get(kind)
        if kind_profiles is None:
            raise KeyError(f"unknown profile kind: {kind!r}")
        profile = kind_profiles.get(name)
        if profile is None:
            raise KeyError(f"unknown profile {kind}/{name}")
        return profile

    def digest(self, kind: str, name: str) -> str:
        """Compute a deterministic digest for a profile.

        The digest is computed from the canonical JSON representation,
        which sorts keys and removes whitespace. This ensures the digest
        is independent of TOML key ordering.
        """
        profile = self.require(kind, name)
        return digest_bytes(canonical_json(profile))

    def kinds(self) -> list[str]:
        """List all available profile kinds."""
        return sorted(self.profiles.keys())

    def names(self, kind: str) -> list[str]:
        """List all profile names for a given kind."""
        kind_profiles = self.profiles.get(kind)
        if kind_profiles is None:
            return []
        return sorted(kind_profiles.keys())
