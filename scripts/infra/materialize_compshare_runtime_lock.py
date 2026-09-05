#!/usr/bin/env python3
"""Materialize or verify a CompShare runtime lock document linked to an image recipe."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import jsonschema

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.infra.audit_compshare_image_recipe import audit_image_recipe

RUNTIME_LOCK_SCHEMA_PATH = _ROOT / "schemas" / "compshare-runtime-lock.schema.json"
IMAGE_ID_PATTERN = re.compile(r"^compshareImage-[a-z0-9]+$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class MaterializeLockError(ValueError):
    """Failure during runtime lock materialization or validation."""


def build_runtime_lock_doc(
    recipe_doc: Mapping[str, Any],
    recipe_relpath: str,
    capability: str,
    *,
    image_id: str | None = None,
    image_sha256: str | None = None,
    receipt_path: str | None = None,
    receipt_digest: str | None = None,
    site_profile_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Construct a canonical runtime lock document from an audited recipe."""
    target_caps = recipe_doc.get("target_capabilities", [])
    if capability not in target_caps:
        raise MaterializeLockError(
            f"Capability {capability!r} is not declared in recipe target_capabilities {target_caps}"
        )

    image_name = str(recipe_doc.get("image_name") or "")
    if not image_name:
        raise MaterializeLockError("Recipe does not declare image_name")

    base_image_info = recipe_doc.get("base_image", {})
    base_image_ref = str(base_image_info.get("image") or "")
    if not base_image_ref:
        raise MaterializeLockError("Recipe does not declare base_image.image")

    recipe_digest = str(recipe_doc.get("recipe_digest") or "")
    if not DIGEST_PATTERN.match(recipe_digest):
        raise MaterializeLockError(f"Invalid recipe_digest: {recipe_digest!r}")

    # Determine qualification status and artifact identity
    if image_id is None or not image_id.strip():
        actual_image_id: str | None = None
        status = "UNBUILT"
    else:
        actual_image_id = image_id.strip()
        if not IMAGE_ID_PATTERN.match(actual_image_id):
            raise MaterializeLockError(
                f"Invalid image_id format {actual_image_id!r}, must match pattern '^compshareImage-[a-z0-9]+$'"
            )
        # Never allow QUALIFIED in static lock files; only UNBUILT or BUILT_NOT_QUALIFIED
        status = "BUILT_NOT_QUALIFIED"

    if receipt_digest is not None and not DIGEST_PATTERN.match(receipt_digest):
        raise MaterializeLockError(
            f"Invalid receipt_digest format: {receipt_digest!r}, must match '^sha256:[0-9a-f]{{64}}$'"
        )

    # Software versions from recipe
    software_versions: dict[str, str] = {}
    py_ver = recipe_doc.get("python", {}).get("version")
    if py_ver:
        software_versions["python"] = str(py_ver)

    # Derive cuda version from base image if available
    cuda_ver = "12.1"
    if "cuda" in base_image_ref:
        m = re.search(r"cuda([0-9]+\.[0-9]+)", base_image_ref)
        if m:
            cuda_ver = m.group(1)
    software_versions["cuda"] = cuda_ver

    artifact: dict[str, Any] = {
        "kind": "compshare_image",
        "image_id": actual_image_id,
        "image_source": "custom",
    }
    if image_sha256:
        artifact["sha256"] = image_sha256

    if note is None:
        target_cases_str = ", ".join(recipe_doc.get("target_cases", []))
        cap_title = "MatClaw CIPS" if capability == "matclaw-cips" else capability
        note = (
            f"Frozen {cap_title} GPU runtime for MLFFBench (Cases {target_cases_str}). "
            f"Built from recipe {recipe_relpath}."
        )

    if capability in ("jax", "deepmd-jax", "dpmp"):
        env_vars = {
            "CUDA_VISIBLE_DEVICES": "0",
            "JAX_ENABLE_X64": "1",
        }
    else:
        env_vars = {
            "CUDA_VISIBLE_DEVICES": "0",
            "TF_FORCE_GPU_ALLOW_GROWTH": "true",
        }

    doc: dict[str, Any] = {
        "schema": "dispatcher-compshare-runtime-lock/v2",
        "capability": capability,
        "image_name": image_name,
        "note": note,
        "provider": "compshare",
        "artifact": artifact,
        "provenance": {
            "base_image": base_image_ref,
            "recipe_digest": recipe_digest,
            "recipe_path": recipe_relpath,
            "cuda_version": cuda_ver,
            "software_versions": software_versions,
        },
        "qualification": {
            "status": status,
            "receipt_path": receipt_path,
            "receipt_digest": receipt_digest,
            "site_profile_id": site_profile_id,
        },
        "env": env_vars,
    }

    # Validate against schema
    if not RUNTIME_LOCK_SCHEMA_PATH.is_file():
        raise MaterializeLockError(f"Runtime lock schema not found: {RUNTIME_LOCK_SCHEMA_PATH}")
    schema = json.loads(RUNTIME_LOCK_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.Draft202012Validator(schema).validate(doc)
    except jsonschema.ValidationError as exc:
        raise MaterializeLockError(f"Generated doc failed schema validation: {exc.message}") from exc

    return doc


def materialize_runtime_lock(
    recipe_path: str | Path,
    *,
    out_path: str | Path,
    capability: str = "matclaw-cips",
    image_id: str | None = None,
    image_sha256: str | None = None,
    receipt_path: str | None = None,
    receipt_digest: str | None = None,
    site_profile_id: str | None = None,
    repo_root: str | Path = _ROOT,
    check_only: bool = False,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Audit recipe and materialize or verify the corresponding runtime lock."""
    root = Path(repo_root)
    r_path = Path(recipe_path)
    if not r_path.is_absolute():
        r_path = root / r_path

    # Strictly audit the recipe first
    audit_image_recipe(r_path, repo_root=root)
    recipe_doc = json.loads(r_path.read_text(encoding="utf-8"))

    try:
        recipe_relpath = str(r_path.relative_to(root))
    except ValueError:
        recipe_relpath = str(r_path)

    expected_doc = build_runtime_lock_doc(
        recipe_doc,
        recipe_relpath=recipe_relpath,
        capability=capability,
        image_id=image_id,
        image_sha256=image_sha256,
        receipt_path=receipt_path,
        receipt_digest=receipt_digest,
        site_profile_id=site_profile_id,
    )

    out = Path(out_path)
    if not out.is_absolute():
        out = root / out

    if check_only:
        if not out.is_file():
            raise MaterializeLockError(f"Check mode failed: output lock file {out} does not exist")
        existing_doc = json.loads(out.read_text(encoding="utf-8"))
        if existing_doc != expected_doc:
            raise MaterializeLockError(
                f"Check mode failed: existing lock file at {out} differs from materialized output"
            )
        return expected_doc

    if dry_run:
        return expected_doc

    if out.is_file() and not force:
        existing_doc = json.loads(out.read_text(encoding="utf-8"))
        if existing_doc == expected_doc:
            return expected_doc
        raise MaterializeLockError(
            f"Output lock file {out} already exists with different contents. Use --force to overwrite."
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(expected_doc, indent=2, ensure_ascii=False) + "\n"
    out.write_text(serialized, encoding="utf-8")
    return expected_doc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialize or verify a CompShare runtime lock document."
    )
    parser.add_argument(
        "--recipe",
        default="base-env-build/matclaw-cips-gpu/recipe.lock.json",
        help="Path to recipe.lock.json",
    )
    parser.add_argument(
        "--capability",
        default="matclaw-cips",
        help="Target capability to materialize (default: matclaw-cips)",
    )
    parser.add_argument(
        "--out",
        default="reference/runtime/matclaw-cips-runtime.lock.json",
        help="Output runtime lock JSON path",
    )
    parser.add_argument(
        "--image-id",
        default=None,
        help="CompShare image ID if built (e.g. compshareImage-xxx)",
    )
    parser.add_argument(
        "--image-sha256",
        default=None,
        help="SHA256 of built image artifact",
    )
    parser.add_argument(
        "--receipt-path",
        default=None,
        help="Path to qualification receipt",
    )
    parser.add_argument(
        "--receipt-digest",
        default=None,
        help="Digest of qualification receipt (sha256:...)",
    )
    parser.add_argument(
        "--site-profile-id",
        default=None,
        help="Site profile identifier",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that existing lock matches materialized result without modifying disk",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print materialized lock without writing to file",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing file if contents differ",
    )

    args = parser.parse_args()

    try:
        doc = materialize_runtime_lock(
            recipe_path=args.recipe,
            out_path=args.out,
            capability=args.capability,
            image_id=args.image_id,
            image_sha256=args.image_sha256,
            receipt_path=args.receipt_path,
            receipt_digest=args.receipt_digest,
            site_profile_id=args.site_profile_id,
            check_only=args.check,
            dry_run=args.dry_run,
            force=args.force,
        )
        if args.dry_run:
            print(json.dumps(doc, indent=2, ensure_ascii=False))
        elif args.check:
            print(f"[OK] Check passed: {args.out} matches recipe {args.recipe}")
        else:
            print(f"[OK] Materialized runtime lock written to {args.out}")
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
