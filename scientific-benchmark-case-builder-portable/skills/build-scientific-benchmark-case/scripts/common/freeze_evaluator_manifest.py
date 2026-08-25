#!/usr/bin/env python3
"""Freeze the evaluator manifest by hashing every verifier-owned asset.

The hidden verifier, hidden tests, tools, reference, and expert solution are
hashed into evaluator-manifest.json. check_release.py re-hashes the same paths
to detect evaluator drift after freeze.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA_VERSION = 1
EVALUATOR_PATHS = ("tests", "tools", "reference", "solution/expert")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_objects(case_dir: Path) -> dict:
    objects: dict[str, dict] = {}
    for rel in EVALUATOR_PATHS:
        root = case_dir / rel
        if not root.exists():
            continue
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            key = str(f.relative_to(case_dir))
            objects[key] = {
                "sha256": sha256_of(f),
                "size": f.stat().st_size,
            }
    return objects


def freeze(case_dir: Path) -> dict:
    objects = collect_objects(case_dir)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "frozen": True,
        "objects": objects,
    }
    (case_dir / "evaluator-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest = freeze(args.case_dir)
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(f"frozen {len(manifest['objects'])} objects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
