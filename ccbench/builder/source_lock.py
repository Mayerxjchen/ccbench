"""Source integrity and taint lineage tracking for CCBench Case Builder."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any

METADATA_FILES = frozenset({
    "intake.json",
    "sources.lock.json",
    "admission-report.json",
    ".DS_Store",
})


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


def verify_sources_lock_bidirectional(
    source_dir: Path,
    require_non_empty: bool = True,
) -> tuple[bool, list[str]]:
    """Bidirectional verification between sources.lock.json and actual files on disk.

    Invariants:
    1. sources.lock.json exists and is valid JSON.
    2. Locked sources must be non-empty if require_non_empty is True.
    3. Lock -> Disk: Every locked file exists and has identical sha256.
    4. Disk -> Lock: Every physical file in source/ (except metadata) must be locked.
       Any untracked/unlocked file added after locking triggers a failure.
    """
    source_dir = Path(source_dir).resolve()
    lock_file = source_dir / "sources.lock.json"
    if not lock_file.is_file():
        return False, ["Missing sources.lock.json"]

    try:
        lock_doc = json.loads(lock_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"Failed to parse sources.lock.json: {exc}"]

    sources = lock_doc.get("sources", [])
    if require_non_empty and not sources:
        return False, ["sources.lock.json contains 0 sources (non-empty source required)"]

    errors: list[str] = []
    locked_paths: set[str] = set()

    # 1. Lock -> Disk check
    for s in sources:
        rel = s.get("path")
        if not rel:
            errors.append("Entry in sources.lock.json missing 'path'")
            continue
        locked_paths.add(rel)
        disk_path = source_dir / rel
        if not disk_path.is_file():
            errors.append(f"Locked source missing on disk: {rel}")
            continue
        actual_sha = hash_file(disk_path)
        expected_sha = s.get("sha256")
        if actual_sha != expected_sha:
            errors.append(
                f"Source hash mismatch for '{rel}': disk={actual_sha} != lock={expected_sha}"
            )

    # 2. Disk -> Lock check (no untracked source files)
    for p in source_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(source_dir).as_posix()
        if rel in METADATA_FILES or p.name.startswith("."):
            continue
        if rel not in locked_paths:
            errors.append(f"Untracked/unlocked source artifact on disk: '{rel}'")

    return len(errors) == 0, errors


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
    """Verify that no candidate-visible artifacts are derived from GOLD_SOURCE."""
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
