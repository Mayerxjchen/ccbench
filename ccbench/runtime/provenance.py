"""ccbench runtime provenance — Audit and validation for immutable runtime recipes and locks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ccbench.paths import ROOT, RUNTIMES_DIR, RUNTIME_PROVENANCE_DIR


class ProvenanceError(RuntimeError):
    """Raised when runtime provenance checks fail."""


def verify_runtime_lock_provenance(
    lock_doc: dict[str, Any],
    repo_root: Path | None = None,
) -> bool:
    """Verify runtime lock provenance integrity."""
    root = repo_root or ROOT
    prov = lock_doc.get("provenance") or {}

    # Check built_from snapshot
    built_from = prov.get("built_from")
    if built_from:
        snap_rel = built_from.get("snapshot_path")
        expected_digest = built_from.get("recipe_digest")
        if not snap_rel or not expected_digest:
            raise ProvenanceError("built_from missing snapshot_path or recipe_digest")
        snap_path = root / snap_rel
        if not snap_path.is_file():
            raise ProvenanceError(f"built_from snapshot file not found: {snap_rel}")
        actual_sha = hashlib.sha256(snap_path.read_bytes()).hexdigest()
        expected_sha = expected_digest.removeprefix("sha256:")
        if actual_sha != expected_sha:
            raise ProvenanceError(
                f"built_from snapshot digest mismatch for {snap_rel}: "
                f"have={actual_sha}, want={expected_sha}"
            )

    # Check active_recipe if declared
    active_recipe = prov.get("active_recipe")
    if active_recipe:
        act_rel = active_recipe.get("path")
        if not act_rel:
            raise ProvenanceError("active_recipe missing path")

    # Check legacy recipe_path / recipe_digest fallback
    recipe_rel = prov.get("recipe_path")
    recipe_digest = prov.get("recipe_digest")
    if recipe_rel and recipe_digest:
        rpath = root / recipe_rel
        if not rpath.is_file():
            raise ProvenanceError(f"recipe_path file not found: {recipe_rel}")
        try:
            from scripts.infra.audit_compshare_image_recipe import canonical_recipe_digest
            recipe_doc = json.loads(rpath.read_text(encoding="utf-8"))
            computed_digest = canonical_recipe_digest(recipe_doc)
            if computed_digest != recipe_digest:
                raise ProvenanceError(
                    f"recipe_path digest mismatch for {recipe_rel}: "
                    f"computed={computed_digest}, want={recipe_digest}"
                )
        except ImportError:
            pass

    return True
