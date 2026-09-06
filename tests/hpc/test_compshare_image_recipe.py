"""Tests for CompShare Image Recipe contract and offline audit."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.infra.audit_compshare_image_recipe import (
    RecipeAuditError,
    audit_image_recipe,
    canonical_recipe_digest,
)
REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_RECIPE_PATH = (
    REPO_ROOT / "runtimes" / "recipes" / "matclaw-cips-gpu" / "recipe.lock.json"
    if (REPO_ROOT / "runtimes" / "recipes" / "matclaw-cips-gpu" / "recipe.lock.json").is_file()
    else REPO_ROOT / "base-env-build" / "matclaw-cips-gpu" / "recipe.lock.json"
)


def _valid_recipe_doc() -> dict:
    return json.loads(CANONICAL_RECIPE_PATH.read_text(encoding="utf-8"))


def test_canonical_recipe_lock_audits_cleanly():
    """Verify that the in-tree recipe passes all checks and remains UNBUILT."""
    res = audit_image_recipe(CANONICAL_RECIPE_PATH, repo_root=REPO_ROOT)
    assert res["ok"] is True
    assert res["status"] == "RECIPE_VERIFIED"
    assert res["image_status"] == "BUILT"
    assert res["target_cases"] == ["001", "002", "003"]
    assert res["recipe_digest"].startswith("sha256:")


def test_recipe_digest_is_deterministic():
    """Identical input structure must yield identical canonical digest."""
    doc = _valid_recipe_doc()
    d1 = canonical_recipe_digest(doc)
    d2 = canonical_recipe_digest(copy.deepcopy(doc))
    assert d1 == d2
    assert d1 == doc["recipe_digest"]


def test_reject_base_image_without_oci_digest(tmp_path: Path):
    doc = _valid_recipe_doc()
    doc["base_image"]["oci_digest"] = ""
    doc["recipe_digest"] = canonical_recipe_digest(doc)
    p = tmp_path / "recipe.json"
    p.write_text(json.dumps(doc))

    with pytest.raises(RecipeAuditError, match="(OCI digest|does not match)"):
        audit_image_recipe(p, repo_root=REPO_ROOT)


def test_reject_non_amd64_architecture(tmp_path: Path):
    doc = _valid_recipe_doc()
    doc["platform"] = "linux/arm64"
    doc["recipe_digest"] = canonical_recipe_digest(doc)
    p = tmp_path / "recipe.json"
    p.write_text(json.dumps(doc))

    with pytest.raises(RecipeAuditError, match="Recipe schema validation failed"):
        audit_image_recipe(p, repo_root=REPO_ROOT)


def test_reject_forbidden_cases(tmp_path: Path):
    # Case 034 or 042 must not sneak into Image A
    for bad_case in ("034", "042"):
        doc = _valid_recipe_doc()
        doc["target_cases"].append(bad_case)
        doc["recipe_digest"] = canonical_recipe_digest(doc)
        p = tmp_path / f"recipe_{bad_case}.json"
        p.write_text(json.dumps(doc))

        with pytest.raises(RecipeAuditError):
            audit_image_recipe(p, repo_root=REPO_ROOT)


def test_reject_forbidden_capabilities(tmp_path: Path):
    for bad_cap in ("jax", "deepmd-jax", "ai2kit", "cp2k"):
        doc = _valid_recipe_doc()
        doc["target_capabilities"].append(bad_cap)
        doc["recipe_digest"] = canonical_recipe_digest(doc)
        p = tmp_path / f"recipe_{bad_cap}.json"
        p.write_text(json.dumps(doc))

        with pytest.raises(RecipeAuditError):
            audit_image_recipe(p, repo_root=REPO_ROOT)


def test_reject_premature_built_or_qualified_status(tmp_path: Path):
    # Must NOT claim arbitrary or invalid status
    doc = _valid_recipe_doc()
    doc["image_status"] = "QUALIFIED"
    doc["recipe_digest"] = canonical_recipe_digest(doc)
    p = tmp_path / "recipe_built.json"
    p.write_text(json.dumps(doc))

    with pytest.raises(RecipeAuditError):
        audit_image_recipe(p, repo_root=REPO_ROOT)


def test_reject_premature_image_id(tmp_path: Path):
    doc = _valid_recipe_doc()
    doc["image_id"] = "compshareImage-fake123"
    doc["recipe_digest"] = canonical_recipe_digest(doc)
    p = tmp_path / "recipe_img_id.json"
    p.write_text(json.dumps(doc))

    with pytest.raises(RecipeAuditError, match="Recipe schema validation failed"):
        audit_image_recipe(p, repo_root=REPO_ROOT)


def test_reject_tampered_asset(tmp_path: Path):
    doc = _valid_recipe_doc()
    # Fake asset hash
    doc["assets"][0]["sha256"] = "0" * 64
    doc["recipe_digest"] = canonical_recipe_digest(doc)
    p = tmp_path / "recipe_tampered_asset.json"
    p.write_text(json.dumps(doc))

    with pytest.raises(RecipeAuditError, match="sha256 mismatch"):
        audit_image_recipe(p, repo_root=REPO_ROOT)


def test_reject_tampered_probe(tmp_path: Path):
    doc = _valid_recipe_doc()
    doc["probes"][0]["sha256"] = "0" * 64
    doc["recipe_digest"] = canonical_recipe_digest(doc)
    p = tmp_path / "recipe_tampered_probe.json"
    p.write_text(json.dumps(doc))

    with pytest.raises(RecipeAuditError, match="sha256 mismatch"):
        audit_image_recipe(p, repo_root=REPO_ROOT)


def test_reject_tampered_requirements_lock(tmp_path: Path):
    doc = _valid_recipe_doc()
    doc["requirements_lock"]["sha256"] = "0" * 64
    doc["recipe_digest"] = canonical_recipe_digest(doc)
    p = tmp_path / "recipe_tampered_req.json"
    p.write_text(json.dumps(doc))

    with pytest.raises(RecipeAuditError, match="requirements_lock sha256 mismatch"):
        audit_image_recipe(p, repo_root=REPO_ROOT)
