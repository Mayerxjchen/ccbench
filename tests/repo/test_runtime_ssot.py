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
import jsonschema

from scripts.infra.audit_compshare_image_recipe import audit_image_recipe, canonical_recipe_digest

ROOT = Path(__file__).resolve().parents[2]
RUNTIMES_DIR = ROOT / "runtimes"


def test_active_tree_has_no_legacy_runtime_paths():
    """Ensure active code trees contain zero legacy runtime paths or legacy case identifiers.
    
    Active surface: dftworld_bench/, scripts/infra/, scripts/qualification/, scripts/hpc/,
                   runtimes/recipes/, schemas/, eval.py.
    Exempted historical archives: runtimes/history/, docs/history/, evidence/, maintainer/, releases/.
    """
    scan_targets = [
        ROOT / "dftworld_bench",
        ROOT / "scripts" / "infra",
        ROOT / "scripts" / "qualification",
        ROOT / "scripts" / "hpc",
        RUNTIMES_DIR / "recipes",
        ROOT / "schemas",
        ROOT / "eval.py",
    ]
    banned_patterns = [
        "base-env-build",
        "reference/runtime",
        "/031-", "/032-", "/033-", "/034-", "/042-",
        "031-matclaw", "032-matclaw", "033-matclaw", "034-ai2kit", "042-go",
    ]
    offending_files: list[str] = []

    for target in scan_targets:
        if target.is_file():
            paths = [target]
        else:
            paths = list(target.rglob("*"))
        for path in paths:
            if not path.is_file() or path.suffix in {
                ".pyc", ".gz", ".pb", ".cif", ".zip", ".pt", ".safetensors"
            }:
                continue
            # Skip historical archives if traversed
            parts = path.relative_to(ROOT).parts
            if any(p in ("history", "maintainer", "releases", "evidence") for p in parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for pattern in banned_patterns:
                if pattern in text:
                    offending_files.append(f"{path.relative_to(ROOT)} (contains '{pattern}')")

    assert not offending_files, (
        f"Found legacy references in active tree ({len(offending_files)} occurrence(s)):\n"
        + "\n".join(offending_files)
    )


def test_all_runtime_locks_validate_against_schema():
    """Every runtime lock in runtimes/locks/ must strictly validate against compshare-runtime-lock.schema.json."""
    schema_path = ROOT / "schemas" / "compshare-runtime-lock.schema.json"
    assert schema_path.is_file(), f"Missing schema file: {schema_path}"
    schema_doc = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema_doc)

    locks_dir = RUNTIMES_DIR / "locks"
    lock_files = list(locks_dir.glob("*-runtime.lock.json"))
    assert len(lock_files) >= 3, f"Expected at least 3 runtime locks, found {len(lock_files)}"

    for lock_file in lock_files:
        doc = json.loads(lock_file.read_text(encoding="utf-8"))
        errors = list(validator.iter_errors(doc))
        assert not errors, f"Schema validation failed for {lock_file.name}: {[e.message for e in errors]}"

        # Qualification schema invariants
        qual = doc.get("qualification", {})
        if qual.get("status") == "BUILT_NOT_QUALIFIED" and not qual.get("receipt_digest"):
            assert qual.get("receipt_digest") is None, (
                f"{lock_file.name}: unsealed receipt_digest must be JSON null, got {qual.get('receipt_digest')!r}"
            )
            assert qual.get("receipt_path") is None, (
                f"{lock_file.name}: unsealed receipt_path must be JSON null, got {qual.get('receipt_path')!r}"
            )


def test_recipe_locks_pass_strict_audit():
    """All recipe.lock.json files must strictly pass audit_image_recipe."""
    recipe_locks = list(RUNTIMES_DIR.rglob("recipe.lock.json"))
    assert len(recipe_locks) >= 2, f"Expected at least 2 recipe locks, found {len(recipe_locks)}"

    schema_file = ROOT / "schemas" / "compshare-image-recipe.schema.json"
    schema_doc = json.loads(schema_file.read_text(encoding="utf-8"))
    allowed_cases_enum = set(
        schema_doc.get("properties", {})
        .get("target_cases", {})
        .get("items", {})
        .get("enum", [])
    )
    assert allowed_cases_enum == {"001", "002", "003", "004", "005"}, (
        f"Schema target_cases enum contains non-canonical cases: {allowed_cases_enum}"
    )

    for lock_path in recipe_locks:
        res = audit_image_recipe(lock_path, repo_root=ROOT)
        assert res["status"] == "RECIPE_VERIFIED"
        assert res["image_status"] in ("UNBUILT", "BUILT", "BUILT_NOT_QUALIFIED")

        doc = json.loads(lock_path.read_text(encoding="utf-8"))
        cases = doc.get("target_cases", [])
        assert all(c in {"001", "002", "003", "004", "005"} for c in cases), (
            f"{lock_path}: contains non-canonical cases {cases}"
        )


def test_runtime_locks_match_recipe_digests_and_preserve_provenance():
    """Runtime locks must maintain honest provenance and immutable historical evidence."""
    locks_dir = RUNTIMES_DIR / "locks"
    for lock_file in locks_dir.glob("*-runtime.lock.json"):
        doc = json.loads(lock_file.read_text(encoding="utf-8"))
        prov = doc.get("provenance", {})
        recipe_path_str = prov.get("recipe_path")
        lock_recipe_digest = prov.get("recipe_digest")

        if recipe_path_str and lock_recipe_digest:
            recipe_file = ROOT / recipe_path_str
            assert recipe_file.is_file(), f"Recipe file {recipe_file} declared in {lock_file.name} missing"
            recipe_doc = json.loads(recipe_file.read_text(encoding="utf-8"))
            computed_digest = canonical_recipe_digest(recipe_doc)

            # All active locks must compute to exactly their declared recipe_digest
            assert computed_digest == lock_recipe_digest, (
                f"{lock_file.name}: lock recipe_digest {lock_recipe_digest} != canonical {computed_digest}"
            )

