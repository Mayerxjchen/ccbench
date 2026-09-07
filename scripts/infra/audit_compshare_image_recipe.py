#!/usr/bin/env python3
"""Audit a CompShare GPU Image Recipe against strict reproducibility gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import jsonschema

# Ensure project root is on sys.path
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

SCHEMA_PATH = _ROOT / "schemas" / "compshare-image-recipe.schema.json"

FORBIDDEN_CAPABILITIES = frozenset({"jax", "deepmd-jax", "ai2kit", "cp2k"})
FORBIDDEN_CASES = frozenset({"031", "032", "033", "034", "042"})


class RecipeAuditError(ValueError):
    """An image recipe violates reproducibility or trust boundaries."""


def canonical_recipe_digest(doc: Mapping[str, Any]) -> str:
    """Compute sha256 over canonical recipe payload excluding recipe_digest itself."""
    clean = {k: v for k, v in doc.items() if k != "recipe_digest"}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def audit_image_recipe(
    recipe_path: str | Path,
    *,
    repo_root: str | Path = _ROOT,
) -> dict[str, Any]:
    """Strictly audit an image recipe document and its referenced artifacts."""
    root = Path(repo_root)
    p = Path(recipe_path)
    if not p.is_absolute():
        p = root / p
    if not p.is_file():
        raise RecipeAuditError(f"Recipe file not found: {p}")

    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RecipeAuditError(f"Recipe file is not valid JSON: {exc}") from exc

    # 1. Schema gate
    if not SCHEMA_PATH.is_file():
        raise RecipeAuditError(f"Recipe schema missing: {SCHEMA_PATH}")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.Draft202012Validator(schema).validate(doc)
    except jsonschema.ValidationError as exc:
        raise RecipeAuditError(f"Recipe schema validation failed: {exc.message}") from exc

    # 2. Scope gate: strictly cases 001-003 or 005
    cases = set(doc.get("target_cases", []))
    caps = set(doc.get("target_capabilities", []))

    if cases == {"001", "002", "003"}:
        if cases.intersection({"004", "005"}):
            raise RecipeAuditError(f"MatClaw Recipe improperly includes forbidden cases: {cases.intersection({'004', '005'})}")
        if caps.intersection({"jax", "deepmd-jax", "ai2kit", "cp2k"}):
            raise RecipeAuditError(f"MatClaw Recipe improperly includes forbidden capabilities: {caps.intersection({'jax', 'deepmd-jax', 'ai2kit', 'cp2k'})}")
    elif cases == {"005"}:
        if cases.intersection({"001", "002", "003", "004"}):
            raise RecipeAuditError(f"JAX Recipe improperly includes forbidden cases: {cases.intersection({'001', '002', '003', '004'})}")
        if caps.intersection({"deepmd", "matclaw-cips", "lammps", "ai2kit", "cp2k"}):
            raise RecipeAuditError(f"JAX Recipe improperly includes forbidden capabilities: {caps.intersection({'deepmd', 'matclaw-cips', 'lammps', 'ai2kit', 'cp2k'})}")
        if "jax" not in caps:
            raise RecipeAuditError("JAX Recipe must include 'jax' capability")
    else:
        raise RecipeAuditError(f"Recipe target_cases must be exactly {{'001', '002', '003'}} or {{'005'}}; got {cases}")

    # 3. Base image must carry explicit OCI digest
    base_img = doc.get("base_image", {})
    oci_digest = base_img.get("oci_digest", "")
    if not oci_digest.startswith("sha256:") or len(oci_digest) != 71:
        raise RecipeAuditError(f"Base image requires an exact 64-hex sha256 OCI digest: {oci_digest!r}")

    # 4. Status gate: must be RECIPE_VERIFIED and UNBUILT/BUILT
    if doc.get("status") != "RECIPE_VERIFIED":
        raise RecipeAuditError("Recipe status must be RECIPE_VERIFIED")
    if doc.get("image_status") not in ("UNBUILT", "BUILT", "BUILT_NOT_QUALIFIED"):
        raise RecipeAuditError("Image status must be UNBUILT or BUILT")
    if doc.get("image_status") == "UNBUILT" and "image_id" in doc:
        raise RecipeAuditError("Recipe must not specify image_id while UNBUILT")

    def _resolve_recipe_rel(rel_str: str) -> Path:
        return root / rel_str

    # 5. Requirements lock verification
    req_meta = doc.get("requirements_lock", {})
    req_rel = req_meta.get("path")
    req_path = _resolve_recipe_rel(req_rel)
    if not req_path.is_file():
        raise RecipeAuditError(f"requirements_lock file not found: {req_path}")
    
    actual_req_sha = hashlib.sha256(req_path.read_bytes()).hexdigest()
    if actual_req_sha != req_meta.get("sha256"):
        raise RecipeAuditError(
            f"requirements_lock sha256 mismatch: recorded {req_meta.get('sha256')} != actual {actual_req_sha}"
        )
    
    # Check that every pinned requirement in requirements.lock carries sha256 hashes
    req_text = req_path.read_text(encoding="utf-8")
    current_pkg: str | None = None
    pkg_hashes: list[str] = []
    packages_seen: dict[str, list[str]] = {}

    for raw_line in req_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            if current_pkg is not None:
                packages_seen[current_pkg] = list(pkg_hashes)
            current_pkg = line.split("==")[0].strip()
            pkg_hashes = []
        hashes = re.findall(r"--hash=sha256:([0-9a-fA-F]{64})", line)
        if hashes:
            pkg_hashes.extend(hashes)

    if current_pkg is not None:
        packages_seen[current_pkg] = list(pkg_hashes)

    if not packages_seen:
        raise RecipeAuditError("requirements_lock contains no pinned packages")

    unhashed_pkgs = [pkg for pkg, h_list in packages_seen.items() if not h_list]
    if unhashed_pkgs:
        raise RecipeAuditError(
            f"requirements_lock contains unhashed packages (missing --hash=sha256:): {unhashed_pkgs}"
        )

    # 6. Assets verification
    assets = doc.get("assets", [])
    for asset in assets:
        rel = asset.get("path")
        asset_file = _resolve_recipe_rel(rel)
        if not asset_file.is_file():
            raise RecipeAuditError(f"Asset file not found: {asset_file}")
        actual_sha = hashlib.sha256(asset_file.read_bytes()).hexdigest()
        if actual_sha != asset.get("sha256"):
            raise RecipeAuditError(
                f"Asset {asset.get('name')} sha256 mismatch: recorded {asset.get('sha256')} != actual {actual_sha}"
            )

    # 7. Probes verification
    probes = doc.get("probes", [])
    for probe in probes:
        rel = probe.get("path")
        probe_file = _resolve_recipe_rel(rel)
        if not probe_file.is_file():
            raise RecipeAuditError(f"Probe file not found: {probe_file}")
        actual_sha = hashlib.sha256(probe_file.read_bytes()).hexdigest()
        if actual_sha != probe.get("sha256"):
            raise RecipeAuditError(
                f"Probe {probe.get('name')} sha256 mismatch: recorded {probe.get('sha256')} != actual {actual_sha}"
            )

    # 8. Canonical digest check
    expected_digest = canonical_recipe_digest(doc)
    if doc.get("recipe_digest") != expected_digest:
        raise RecipeAuditError(
            f"Recipe digest mismatch: recorded {doc.get('recipe_digest')} != calculated {expected_digest}"
        )

    return {
        "ok": True,
        "recipe_digest": expected_digest,
        "image_name": doc.get("image_name"),
        "status": doc.get("status"),
        "image_status": doc.get("image_status"),
        "target_cases": sorted(cases),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit CompShare Image Recipe")
    parser.add_argument(
        "recipe",
        type=Path,
        nargs="?",
        default=_ROOT / "runtimes" / "recipes" / "matclaw-cips-gpu" / "recipe.lock.json",
        help="Path to recipe.lock.json",
    )
    args = parser.parse_args(argv)

    try:
        res = audit_image_recipe(args.recipe)
        print("PASS: Image recipe audit succeeded.")
        print(f"  Image: {res['image_name']}")
        print(f"  Status: {res['status']} ({res['image_status']})")
        print(f"  Digest: {res['recipe_digest']}")
        return 0
    except RecipeAuditError as exc:
        print(f"FAIL: Image recipe audit failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
