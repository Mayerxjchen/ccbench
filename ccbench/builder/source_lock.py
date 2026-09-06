"""Source integrity and taint lineage tracking for CCBench Case Builder."""

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


class TaintLineageError(ValueError):
    """Raised when an artifact violates trust boundaries via gold taint propagation."""


def hash_file(path: Path) -> str:
    """Compute sha256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def build_sources_lock(
    source_dir: Path,
    manifest: dict[str, SourceTier | str],
    lineage: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Generate locked source manifest with content hashes, taint tiers, and lineage.

    Args:
        source_dir: Directory containing source files.
        manifest: Mapping from relative file paths to their SourceTier.
        lineage: Optional mapping from child artifact to list of parent source paths.
    """
    entries = []
    lineage_map = lineage or {}

    for rel_path, tier in manifest.items():
        p = source_dir / rel_path
        if not p.is_file():
            raise FileNotFoundError(f"Declared source file not found: {rel_path}")
        tier_val = tier.value if isinstance(tier, SourceTier) else str(tier)
        parents = lineage_map.get(str(rel_path), [])

        entries.append({
            "path": str(rel_path),
            "tier": tier_val,
            "sha256": hash_file(p),
            "size_bytes": p.stat().st_size,
            "derived_from": parents,
        })

    lock_doc = {
        "schema_version": 1,
        "sources": entries,
    }
    target = source_dir / "sources.lock.json"
    target.write_text(json.dumps(lock_doc, indent=2), encoding="utf-8")
    return lock_doc


def get_transitive_ancestors(
    artifact: str,
    lineage: dict[str, list[str]],
) -> set[str]:
    """Compute the transitive closure of all ancestor artifacts."""
    ancestors: set[str] = set()
    stack = list(lineage.get(artifact, []))
    visited = set()

    while stack:
        curr = stack.pop()
        if curr in visited:
            continue
        visited.add(curr)
        ancestors.add(curr)
        stack.extend(lineage.get(curr, []))

    return ancestors


def check_gold_leakage(
    candidate_visible_paths: list[str],
    source_manifest: dict[str, str],
    lineage: dict[str, list[str]] | None = None,
) -> list[str]:
    """Verify that no candidate-visible artifacts are derived from GOLD_SOURCE.

    Propagates taint transitively across lineage parents. If an artifact or any
    of its transitive ancestors is classified as GOLD_SOURCE, it is flagged.
    """
    violations = []
    lineage_map = lineage or {}

    for path in candidate_visible_paths:
        norm_path = str(path)
        tier = source_manifest.get(norm_path)

        # 1. Direct check
        if tier in (SourceTier.GOLD_SOURCE.value, "GOLD_SOURCE"):
            violations.append(
                f"Candidate-visible file '{norm_path}' is directly classified as GOLD_SOURCE"
            )
            continue

        # 2. Transitive lineage propagation
        ancestors = get_transitive_ancestors(norm_path, lineage_map)
        for anc in ancestors:
            anc_tier = source_manifest.get(anc)
            if anc_tier in (SourceTier.GOLD_SOURCE.value, "GOLD_SOURCE"):
                violations.append(
                    f"Candidate-visible file '{norm_path}' is tainted: "
                    f"ancestor '{anc}' is classified as GOLD_SOURCE"
                )
                break

    return violations
