#!/usr/bin/env python3
"""Read-only cross-layer consistency checks for MLP Runnable Draft cases."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


DATASET_PATH_RE = re.compile(r"(?:train_dataset|datasets?)/([A-Za-z0-9._-]+)")
HIDDEN_BASES = (
    Path("reference/hidden-validation"),
    Path("tests/hidden"),
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_canonical_systems(case_dir: Path) -> set[str]:
    path = case_dir / "public/system.json"
    if not path.is_file():
        raise ValueError("public/system.json missing")
    interfaces = _json(path).get("interfaces")
    if not isinstance(interfaces, dict) or not interfaces:
        raise ValueError("public/system.json#/interfaces must be a non-empty object")
    return {str(name) for name in interfaces}


def _manifest_scope(path: Path) -> tuple[set[str], list[str]]:
    data = _json(path)
    systems = data.get("systems")
    if isinstance(systems, list):
        declared = {str(name) for name in systems}
    else:
        declared = set()
    frames = data.get("frames") or []
    frame_systems: set[str] = set()
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        source = str(frame.get("source", ""))
        match = DATASET_PATH_RE.search(source)
        if match:
            frame_systems.add(match.group(1))
    errors: list[str] = []
    if declared and frame_systems and declared != frame_systems:
        errors.append(
            f"{path}: systems {sorted(declared)} disagree with frame sources {sorted(frame_systems)}"
        )
    count = data.get("total_held_out_frames")
    if isinstance(count, int) and count != len(frames):
        errors.append(f"{path}: total_held_out_frames={count} but frames={len(frames)}")
    return declared or frame_systems, errors


def _recipe_scope(public_dir: Path) -> set[str]:
    systems: set[str] = set()
    for path in public_dir.rglob("*"):
        if not path.is_file() or path.name == "system.json":
            continue
        if path.suffix.lower() not in {".py", ".json", ".yaml", ".yml", ".toml", ".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        systems.update(DATASET_PATH_RE.findall(text))
    return systems


def _hash_entries(lock: dict[str, Any]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for section in ("public_input", "hidden_validation"):
        files = (lock.get(section) or {}).get("files") or {}
        if not isinstance(files, dict):
            continue
        for rel, value in files.items():
            expected = value.get("sha256") if isinstance(value, dict) else value
            if isinstance(expected, str):
                entries.append((str(rel), expected))
    return entries


def _check_hash_lock(case_dir: Path) -> list[str]:
    lock_path = case_dir / "reference/source.lock.json"
    if not lock_path.is_file():
        return []
    errors: list[str] = []
    for rel, expected in _hash_entries(_json(lock_path)):
        path = case_dir / rel
        if not path.is_file():
            errors.append(f"{lock_path}: locked file missing: {rel}")
        else:
            actual = _sha256(path)
            if actual != expected:
                errors.append(
                    f"{lock_path}: hash mismatch {rel}: expected {expected}, got {actual}"
                )
    return errors


def check_case(case_dir: Path) -> dict[str, Any]:
    case_dir = case_dir.resolve()
    errors: list[str] = []
    observed: dict[str, list[str]] = {}
    hash_errors: list[str] = []
    try:
        canonical = load_canonical_systems(case_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "errors": [str(exc)],
            "canonical_systems": [],
            "observed_scopes": {},
            "hash_mismatches": [],
        }

    for base_rel in HIDDEN_BASES:
        base = case_dir / base_rel
        hidden = base / "hidden-frames"
        if hidden.is_dir():
            dirs = {path.name for path in hidden.iterdir() if path.is_dir()}
            key = f"{base_rel.as_posix()}/hidden-frames"
            observed[key] = sorted(dirs)
            if dirs != canonical:
                errors.append(
                    f"hidden scope mismatch at {key}: expected {sorted(canonical)}, got {sorted(dirs)}"
                )
        manifest = base / "manifest.json"
        if manifest.is_file():
            try:
                scope, manifest_errors = _manifest_scope(manifest)
                observed[manifest.relative_to(case_dir).as_posix()] = sorted(scope)
                errors.extend(manifest_errors)
                if scope != canonical:
                    errors.append(
                        f"hidden manifest scope mismatch at {manifest.relative_to(case_dir)}: "
                        f"expected {sorted(canonical)}, got {sorted(scope)}"
                    )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(str(exc))

    recipe = _recipe_scope(case_dir / "public")
    observed["public_recipe_systems"] = sorted(recipe)
    extra_recipe = recipe - canonical
    if extra_recipe:
        errors.append(
            f"public recipe names systems absent from public/system.json: {sorted(extra_recipe)}"
        )

    try:
        hash_errors = _check_hash_lock(case_dir)
        errors.extend(hash_errors)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))

    return {
        "valid": not errors,
        "errors": errors,
        "canonical_systems": sorted(canonical),
        "observed_scopes": observed,
        "hash_mismatches": hash_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = check_case(args.case_dir)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for error in result["errors"]:
            print(f"ERROR: {error}")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
