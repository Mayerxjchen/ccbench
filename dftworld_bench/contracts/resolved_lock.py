"""Resolved Run Lock v2: immutable, complete identity for a single benchmark run.

The lock captures every dimension of a run's identity: case, experiment, agent,
API, runtime, HPC, verifier, infrastructure, and budgets. It is write-once and
secret-free.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

from dftworld_bench.config.profiles import canonical_json, digest_bytes

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "infra" / "schemas" / "resolved-run-lock.schema.json"


class FrozenExperimentOverrideError(ValueError):
    """Raised when someone tries to override a frozen experiment dimension."""


@dataclass(frozen=True)
class ResolvedRunLock:
    """Immutable, validated run identity with deterministic digest.

    The lock is created from a complete payload, validated against the schema,
    and written exactly once. It never contains secrets.
    """

    payload: dict[str, Any]
    digest: str

    @classmethod
    def create(cls, payload: dict[str, Any]) -> ResolvedRunLock:
        """Create a validated lock from a payload dict.

        The payload is validated against the resolved-run-lock schema, then
        canonicalized (sorted keys, no whitespace) for deterministic digest.
        """
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(payload)
        canonical = canonical_json(payload)
        return cls(json.loads(canonical), digest_bytes(canonical))

    def write_once(self, path: Path) -> None:
        """Write the lock file exactly once.

        Uses O_EXCL to prevent overwriting an existing lock. The file includes
        the lock_digest field for verification.
        """
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {**self.payload, "lock_digest": self.digest},
                handle,
                sort_keys=True,
                indent=2,
            )
            handle.write("\n")

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the payload (without lock_digest)."""
        return copy.deepcopy(self.payload)

    def verify(self) -> bool:
        """Verify the digest matches the canonical payload."""
        canonical = canonical_json(self.payload)
        return self.digest == digest_bytes(canonical)
