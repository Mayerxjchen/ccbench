"""Target Adapter protocol for dftworld Case Factory.

A Target Adapter converts an approved Builder ``case-design.yaml`` plus the
generic scaffold into a dftworld-executable Draft Case.  The adapter:

- validates the design (never guesses or changes the approved execution class),
- renders generated files (``task.toml`` v1.2, ``Dockerfile``, ``.dockerignore``,
  ``source/dftworld-target.lock.json``),
- validates the generated output without re-reading rendered bytes as truth.

The adapter owns only rendering/validation of declared inputs.  Mounts,
networks, secrets, site config, scheduler credentials and Gateway wiring are
infrastructure-owned and never generated here.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from ccbench.case_factory.state import FactoryGates

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "dftworld-target.schema.json"


@dataclass(frozen=True)
class TargetVerdict:
    """Validation result of a Target Adapter check.

    ``valid=True`` with ``errors=[]`` is the only acceptance form.  ``errors``
    is human-readable, stable and sorted; it never carries free-form runtime
    text.
    """

    valid: bool
    errors: tuple[str, ...] = ()
    gates: FactoryGates = dataclasses.field(default_factory=FactoryGates)


@dataclass(frozen=True)
class GeneratedFile:
    """One deterministic generated file.  ``bytes`` must be reproducible.

    Timestamps never participate in the digest; ``digest`` is computed by the
    renderer and must be a ``sha256:<hex>`` string.
    """

    path: Path  # case-relative
    content: bytes
    digest: str


@runtime_checkable
class TargetAdapter(Protocol):
    """Protocol implemented by every dftworld target adapter."""

    name: str
    version: str

    def validate_design(self, case_dir: Path, design: dict) -> TargetVerdict: ...
    def render(
        self, case_dir: Path, design: dict
    ) -> tuple[GeneratedFile, ...]: ...
    def validate_output(
        self, case_dir: Path, design: dict
    ) -> TargetVerdict: ...


def load_target_schema() -> dict:
    """Load the case-design additions schema (cached by caller)."""
    import json

    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
