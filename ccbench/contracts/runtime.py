"""ccbench runtime contracts and typed schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeContract:
    """Declared execution requirements for a benchmark case or experiment."""

    execution_class: str
    image: str
    timeout_sec: float
    gpus: int = 0
    capabilities: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
