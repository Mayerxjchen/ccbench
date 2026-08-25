"""Trusted secret lookup for dftworld infrastructure.

Secrets are resolved from environment variables and never serialized.
The SecretValue class prevents accidental exposure in logs, repr, or JSON.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


class SecretValue:
    """A secret value that prevents accidental serialization.

    Secret values can only be revealed via the reveal() method. They cannot
    be serialized to JSON, printed in repr, or converted to string.
    """

    def __init__(self, value: str, source: str = "env") -> None:
        self._value = value
        self._source = source

    def reveal(self) -> str:
        """Return the secret value. Only call this inside trusted code."""
        return self._value

    def __repr__(self) -> str:
        return f"SecretValue(source={self._source!r}, value=<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def __json__(self) -> None:
        """Prevent JSON serialization."""
        raise TypeError("SecretValue cannot be serialized to JSON")

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, SecretValue):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)


@dataclass(frozen=True)
class EnvSecretProvider:
    """Resolve secrets from environment variables.

    The provider maps profile IDs to environment variable names. Secrets are
    resolved lazily and never stored in the provider itself.
    """

    env_map: dict[str, str]

    def get(self, profile_id: str) -> SecretValue:
        """Get a secret value by profile ID.

        Raises KeyError if the profile ID is not mapped, or ValueError if
        the environment variable is not set.
        """
        env_name = self.env_map.get(profile_id)
        if env_name is None:
            raise KeyError(f"unknown secret profile: {profile_id!r}")
        value = os.environ.get(env_name)
        if value is None:
            raise ValueError(f"environment variable {env_name!r} is not set")
        return SecretValue(value, source=f"env:{env_name}")

    def available(self) -> list[str]:
        """List profile IDs that are currently available (env var is set)."""
        return [
            pid for pid, env_name in self.env_map.items()
            if os.environ.get(env_name) is not None
        ]
