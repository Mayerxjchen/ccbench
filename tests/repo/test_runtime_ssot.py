"""Runtime Single Source of Truth (SSOT) integrity gate.

Enforces zero-dangling reference, correct active recipe schema,
and honest qualification states across runtimes/.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import pytest

from scripts.infra.audit_compshare_image_recipe import audit_image_recipe, canonical_recipe_digest

ROOT = Path(__file__).resolve().parents[2]
RUNTIMES_DIR = ROOT / "runtimes"


def test_zero_base_env_build_references():
    """Ensure no file under runtimes/ references the deleted base-env-build/ directory."""
    offending_files = []
    for path in RUNTIMES_DIR.rglob("*"):
        if not path.is_file() or path.suffix in {".pyc", ".gz", ".pb", ".cif", ".zip"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "base-env-build" in text:
            offending_files.append(str(path.relative_to(ROOT)))

    assert not offending_files, (
        f"Found dangling base-env-build reference in {len(offending_files)} file(s): "
        + ", ".join(offending_files)
    )


def test_recipe_locks_pass_strict_audit():
    """All recipe.lock.json files must strictly pass audit_image_recipe."""
    recipe_locks = list(RUNTIMES_DIR.rglob("recipe.lock.json"))
    assert len(recipe_locks) >= 2, f"Expected at least 2 recipe locks, found {len(recipe_locks)}"

    for lock_path in recipe_locks:
        res = audit_image_recipe(lock_path, repo_root=ROOT)
        assert res["status"] == "RECIPE_VERIFIED"
        assert res["image_status"] in ("BUILT", "BUILT_NOT_QUALIFIED"), (
            f"{lock_path}: image_status must not be UNBUILT"
        )

        doc = json.loads(lock_path.read_text(encoding="utf-8"))
        # All declared target cases must be modern canonical cases 001-005
        cases = doc.get("target_cases", [])
        assert all(c in {"001", "002", "003", "004", "005"} for c in cases), (
            f"{lock_path}: contains legacy cases {cases}"
        )


def test_runtime_locks_match_recipe_digests():
    """Runtime locks declaring recipe_digest must match canonical recipe.lock digest."""
    locks_dir = RUNTIMES_DIR / "locks"
    for lock_file in locks_dir.glob("*-runtime.lock.json"):
        doc = json.loads(lock_file.read_text(encoding="utf-8"))
        prov = doc.get("provenance", {})
        recipe_path_str = prov.get("recipe_path")
        expected_digest = prov.get("recipe_digest")

        if recipe_path_str and expected_digest:
            recipe_file = ROOT / recipe_path_str
            assert recipe_file.is_file(), f"Recipe file {recipe_file} declared in {lock_file.name} missing"
            recipe_doc = json.loads(recipe_file.read_text(encoding="utf-8"))
            computed_digest = canonical_recipe_digest(recipe_doc)
            assert computed_digest == expected_digest, (
                f"{lock_file.name}: lock recipe_digest {expected_digest} != canonical {computed_digest}"
            )
