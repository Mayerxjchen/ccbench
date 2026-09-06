"""Source integrity and taint tracking for CCBench Case Builder."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any


class SourceTier(str, Enum):
    """Trust classification for source files."""

    PUBLIC_SOURCE = "PUBLIC_SOURCE"
    MAINTAINER_SOURCE = "MAINTAINER_SOURCE"
    GOLD_SOURCE = "GOLD_SOURCE"
    REFERENCE_SOURCE = "REFERENCE_SOURCE"


def hash_file(path: Path) -> str:
    """Compute sha256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def build_sources_lock(
    source_dir: Path,
    manifest: dict[str, SourceTier],
) -> dict[str, Any]:
    """Generate locked source manifest with content hashes and taint tiers."""
    entries = []
    for rel_path, tier in manifest.items():
        p = source_dir / rel_path
        if not p.is_file():
            raise FileNotFoundError(f"Declared source file not found: {rel_path}")
        entries.append({
            "path": str(rel_path),
            "tier": tier.value if isinstance(tier, SourceTier) else str(tier),
            "sha256": hash_file(p),
            "size_bytes": p.stat().st_size,
        })

    lock_doc = {
        "schema_version": 1,
        "sources": entries,
    }
    target = source_dir / "sources.lock.json"
    target.write_text(json.dumps(lock_doc, indent=2), encoding="utf-8")
    return lock_doc


def check_gold_leakage(
    candidate_visible_paths: list[str],
    source_manifest: dict[str, str],
) -> list[str]:
    """Verify that no candidate-visible artifacts are derived from GOLD_SOURCE."""
    violations = []
    for path in candidate_visible_paths:
        tier = source_manifest.get(path)
        if tier == SourceTier.GOLD_SOURCE.value or tier == "GOLD_SOURCE":
            violations.append(
                f"Candidate visible file '{path}' is tainted with GOLD_SOURCE classification"
            )
    return violations
