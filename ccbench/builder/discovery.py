"""Discovery run classification for candidate cases."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any


class DiscoveryDecision(str, Enum):
    PROMOTED = "PROMOTED"
    REFINE = "REFINE"
    REJECT = "REJECT"


def record_discovery_result(
    run_dir: Path,
    decision: DiscoveryDecision,
    metrics: dict[str, Any] | None = None,
    notes: str = "",
) -> Path:
    """Record the deterministic discovery decision in discovery/classification.json."""
    disc_dir = Path(run_dir) / "discovery"
    disc_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "decision": decision.value if isinstance(decision, DiscoveryDecision) else str(decision),
        "metrics": metrics or {},
        "notes": notes,
    }
    target = disc_dir / "classification.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return target
