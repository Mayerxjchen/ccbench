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
    """Ensure no file under runtimes/, scripts/infra/, schemas/, or eval.py references base-env-build/."""
    scan_targets = [
        RUNTIMES_DIR,
        ROOT / "scripts" / "infra",
        ROOT / "schemas",
        ROOT / "eval.py",
    ]
    offending_files = []
    for target in scan_targets:
        if target.is_file():
            paths = [target]
        else:
            paths = list(target.rglob("*"))
        for path in paths:
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

            if recipe_doc.get("image_status") == "UNBUILT":
                # For unbuilt recipes (e.g. jax-gpu awaiting cloud rebuild),
                # lock must maintain honest BUILT_NOT_QUALIFIED status and match build_evidence.json
                assert doc.get("qualification", {}).get("status") == "BUILT_NOT_QUALIFIED"
                build_ev_file = recipe_file.parent / "build_evidence.json"
                if build_ev_file.is_file():
                    ev = json.loads(build_ev_file.read_text(encoding="utf-8"))
                    assert ev.get("recipe_digest") == lock_recipe_digest, (
                        f"{lock_file.name}: lock recipe_digest {lock_recipe_digest} "
                        f"does not match build_evidence.json {ev.get('recipe_digest')}"
                    )
            else:
                assert computed_digest == lock_recipe_digest, (
                    f"{lock_file.name}: lock recipe_digest {lock_recipe_digest} != canonical {computed_digest}"
                )
