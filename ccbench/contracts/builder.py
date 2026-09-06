"""ccbench builder contracts and specifications."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ccbench.builder.state import CaseLifecycleState


@dataclass(frozen=True)
class CaseBuilderContract:
    """Declared builder session contract."""

    run_id: str
    category: str
    status: CaseLifecycleState
