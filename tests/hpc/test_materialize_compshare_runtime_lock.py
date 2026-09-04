"""Tests for materialize_compshare_runtime_lock.py and matclaw-cips runtime integration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import jsonschema
import pytest

from dftworld_bench.hpc.drivers.compshare.driver import SUPPORTED_GPU_RUNTIMES, CompShareDriver
from dftworld_bench.hpc.runtime_resolution import (
    RuntimeLockEntry,
    RuntimeResolver,
    RuntimeStatus,
)
from scripts.infra.materialize_compshare_runtime_lock import (
    MaterializeLockError,
    build_runtime_lock_doc,
    materialize_runtime_lock,
)

ROOT = Path(__file__).resolve().parents[2]
RECIPE_PATH = ROOT / "base-env-build" / "matclaw-cips-gpu" / "recipe.lock.json"
SCHEMA_PATH = ROOT / "schemas" / "compshare-runtime-lock.schema.json"


def test_schema_file_exists_and_is_valid():
    """Verify the compshare-runtime-lock.schema.json exists and is valid Draft 2020-12."""
    assert SCHEMA_PATH.is_file()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


def test_materialize_default_success(tmp_path: Path):
    """Test generating unbuilt runtime lock from genuine recipe."""
    out_file = tmp_path / "matclaw-cips-runtime.lock.json"
    doc = materialize_runtime_lock(
        RECIPE_PATH,
        out_path=out_file,
        capability="matclaw-cips",
        repo_root=ROOT,
    )
    assert out_file.is_file()
    assert doc["schema"] == "dispatcher-compshare-runtime-lock/v2"
    assert doc["capability"] == "matclaw-cips"
    assert doc["image_name"] == "mlff-matclaw-cips-gpu-v1"
    assert doc["provider"] == "compshare"
    assert doc["artifact"]["kind"] == "compshare_image"
    assert doc["artifact"]["image_id"] is None
    assert doc["qualification"]["status"] == "UNBUILT"
    assert doc["provenance"]["recipe_path"] == "base-env-build/matclaw-cips-gpu/recipe.lock.json"
    assert doc["provenance"]["recipe_digest"].startswith("sha256:")

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(doc)


def test_materialize_check_mode(tmp_path: Path):
    """Verify check mode passes on identical content and fails on modification/missing."""
    out_file = tmp_path / "lock.json"
    # Fails if missing
    with pytest.raises(MaterializeLockError, match="does not exist"):
        materialize_runtime_lock(
            RECIPE_PATH,
            out_path=out_file,
            capability="matclaw-cips",
            repo_root=ROOT,
            check_only=True,
        )

    # Generate file
    materialize_runtime_lock(
        RECIPE_PATH,
        out_path=out_file,
        capability="matclaw-cips",
        repo_root=ROOT,
    )

    # Passes when identical
    doc = materialize_runtime_lock(
        RECIPE_PATH,
        out_path=out_file,
        capability="matclaw-cips",
        repo_root=ROOT,
        check_only=True,
    )
    assert doc["capability"] == "matclaw-cips"

    # Fails when modified
    out_file.write_text(json.dumps({"tampered": True}))
    with pytest.raises(MaterializeLockError, match="differs from materialized output"):
        materialize_runtime_lock(
            RECIPE_PATH,
            out_path=out_file,
            capability="matclaw-cips",
            repo_root=ROOT,
            check_only=True,
        )


def test_materialize_with_valid_image_id(tmp_path: Path):
    """Verify image_id sets status to BUILT_NOT_QUALIFIED (never QUALIFIED)."""
    out_file = tmp_path / "lock.json"
    doc = materialize_runtime_lock(
        RECIPE_PATH,
        out_path=out_file,
        capability="matclaw-cips",
        image_id="compshareImage-abc12345",
        image_sha256="sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        repo_root=ROOT,
    )
    assert doc["artifact"]["image_id"] == "compshareImage-abc12345"
    assert doc["qualification"]["status"] == "BUILT_NOT_QUALIFIED"


def test_materialize_rejects_invalid_image_id(tmp_path: Path):
    """Ensure non-conforming image_ids are strictly rejected."""
    out_file = tmp_path / "lock.json"
    bad_ids = [
        "image-123",
        "compshareImage_123",
        "compshareImage-ABC",  # only lower-case alphanum
        "docker://compshareImage-123",
        "compshareImage-",
    ]
    for bad in bad_ids:
        with pytest.raises(MaterializeLockError, match="Invalid image_id format"):
            materialize_runtime_lock(
                RECIPE_PATH,
                out_path=out_file,
                capability="matclaw-cips",
                image_id=bad,
                repo_root=ROOT,
            )


def test_materialize_rejects_undeclared_capability(tmp_path: Path):
    """Ensure capabilities not declared in recipe cannot be materialized."""
    out_file = tmp_path / "lock.json"
    with pytest.raises(MaterializeLockError, match="not declared in recipe"):
        materialize_runtime_lock(
            RECIPE_PATH,
            out_path=out_file,
            capability="jax",  # forbidden and not in recipe
            repo_root=ROOT,
        )


def test_materialize_rejects_corrupted_recipe(tmp_path: Path):
    """Ensure corrupted recipes fail audit before lock generation."""
    bad_recipe = tmp_path / "bad_recipe.lock.json"
    bad_recipe.write_text(json.dumps({"schema_version": 1, "target_cases": ["034"]}))
    out_file = tmp_path / "lock.json"
    with pytest.raises(Exception):
        materialize_runtime_lock(
            bad_recipe,
            out_path=out_file,
            capability="matclaw-cips",
            repo_root=ROOT,
        )


def test_reference_runtime_matclaw_cips_in_repo():
    """Verify the repo's reference/runtime/matclaw-cips-runtime.lock.json is valid and UNBUILT."""
    lock_path = ROOT / "reference" / "runtime" / "matclaw-cips-runtime.lock.json"
    assert lock_path.is_file()

    # Must pass check mode against current recipe
    doc = materialize_runtime_lock(
        RECIPE_PATH,
        out_path=lock_path,
        capability="matclaw-cips",
        repo_root=ROOT,
        check_only=True,
    )
    assert doc["qualification"]["status"] == "UNBUILT"
    assert doc["artifact"]["image_id"] is None


def test_runtime_resolver_loads_matclaw_cips():
    """Verify RuntimeResolver correctly loads and reports matclaw-cips."""
    ref_dir = ROOT / "reference" / "runtime"
    resolver = RuntimeResolver.from_lock_dir(ref_dir)
    assert "matclaw-cips" in resolver.all_capabilities()
    entry = resolver.get("matclaw-cips")
    assert entry is not None
    assert entry.status == RuntimeStatus.UNBUILT
    assert entry.image_name == "mlff-matclaw-cips-gpu-v1"
    assert entry.provider == "compshare"
    assert not entry.qualification_verified


def test_compshare_driver_supports_matclaw_cips():
    """Verify CompShareDriver recognizes matclaw-cips as a supported GPU runtime."""
    assert "matclaw-cips" in SUPPORTED_GPU_RUNTIMES
