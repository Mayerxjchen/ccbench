"""Case intake parsing, validation, and registration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def record_intake(
    run_dir: Path,
    category: str,
    title: str,
    sources: list[dict[str, Any]],
    notes: str = "",
) -> Path:
    """Record an intake proposal into source/intake.json."""
    source_dir = Path(run_dir) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    intake_doc = {
        "category": category,
        "title": title,
        "sources": sources,
        "notes": notes,
        "status": "INTAKE_COMPLETE",
    }
    target = source_dir / "intake.json"
    target.write_text(json.dumps(intake_doc, indent=2), encoding="utf-8")
    return target
