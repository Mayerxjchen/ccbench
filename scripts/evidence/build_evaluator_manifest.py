#!/usr/bin/env python3
"""Build the immutable case-level evaluator bundle identity (plan Task 2).

A case's hidden evaluator = ``tests/`` (verifier), ``solution/`` (reference
solution), ``reference/`` (hidden inputs/thresholds), and ``profiles/``
(frozen resource/platform/smoke/formal profiles). The digest covers the
normalized relative paths and SHA-256 of exactly those files — nothing in
``public/`` (shipped to agents) and no scratch.

Determinism: bytes are hashed as-is (no normalization), paths use forward
slashes, and the record is sorted before hashing — so mtime, ownership, and
walk order never change the digest.

Usage::

    python scripts/evidence/build_evaluator_manifest.py <case_dir> [--state STATE] [--check]
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys
from pathlib import Path

SELECTED_SUBDIRS = ("tests", "solution", "reference", "profiles")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(case_dir: Path, path: Path) -> str:
    rel = path.relative_to(case_dir).as_posix()
    if rel.startswith("..") or rel == "" or "/.." in f"/{rel}":
        raise ValueError(f"unsafe relative path: {rel}")
    return rel


def _select_evaluator_files(case_dir: Path) -> list[dict]:
    if not case_dir.is_dir():
        raise ValueError(f"not a directory: {case_dir}")
    records: list[tuple[str, str]] = []
    for sub in SELECTED_SUBDIRS:
        base = case_dir / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.name == "__pycache__" or "__pycache__" in path.parts:
                continue
            if path.is_dir():
                continue  # walked; only regular files carry bytes
            rel = _safe_relative(case_dir, path)
            if path.is_symlink():
                # Internal symlinks are fine (content is hashed by target bytes);
                # a link that escapes the case dir would leak evaluator bytes.
                target = path.resolve(strict=False)
                try:
                    target.relative_to(case_dir)
                except ValueError:
                    raise ValueError(f"symlink escapes case_dir: {path} -> {target}")
                if not target.is_file():
                    raise ValueError(f"symlink target not a regular file: {path}")
                records.append((rel, _sha256_file(target)))
                continue
            if not path.is_file():
                raise ValueError(f"non-regular file not allowed in evaluator bundle: {path}")
            records.append((rel, _sha256_file(path)))
    return [{"path": rel, "sha256": digest} for rel, digest in sorted(records)]


@dataclasses.dataclass
class EvaluatorBundleManifest:
    schema_version: str
    case_id: str
    state: str
    bundle_sha256: str
    files: list[dict]

    @classmethod
    def from_files(cls, case_dir: Path, state: str = "constructed") -> "EvaluatorBundleManifest":
        case_dir = case_dir.resolve()  # containment checks need absolute roots
        files = _select_evaluator_files(case_dir)
        record = "\0".join(f"{f['path']}:{f['sha256']}" for f in files) or "__empty__"
        digest = hashlib.sha256(record.encode("utf-8")).hexdigest()
        return cls(
            schema_version="1",
            case_id=case_dir.name,
            state=state,
            bundle_sha256=digest,
            files=files,
        )

    def public_projection(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "case_id": self.case_id,
            "state": self.state,
            "evaluator_bundle_sha256": self.bundle_sha256,
            "bundle_sha256": self.bundle_sha256,
        }

    def to_dict(self) -> dict:
        data = self.public_projection()
        data["files"] = [dict(f) for f in self.files]
        return data


def build_evaluator_manifest(case_dir: Path | str, state: str = "constructed") -> EvaluatorBundleManifest:
    return EvaluatorBundleManifest.from_files(Path(case_dir), state=state)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("--state", default="constructed",
                    choices=("construction", "constructed", "benchmark_valid"))
    ap.add_argument("--check", action="store_true",
                    help="verify <case_dir>/evaluator-manifest.json matches, exit nonzero otherwise")
    args = ap.parse_args(argv)

    out = args.case_dir / "evaluator-manifest.json"
    state = args.state
    if args.check:
        if not out.is_file():
            print(f"{out} missing", file=sys.stderr)
            return 1
        existing = json.loads(out.read_text(encoding="utf-8"))
        state = existing.get("state", args.state)  # compare against the recorded state
    manifest = build_evaluator_manifest(args.case_dir, state=state)
    if args.check:
        if existing != manifest.to_dict():
            print(f"{out} stale — rerun without --check", file=sys.stderr)
            return 1
        print(f"OK {args.case_dir.name} bundle_sha256={manifest.bundle_sha256} "
              f"files={len(manifest.files)} state={manifest.state}")
        return 0
    out.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"wrote {out} bundle_sha256={manifest.bundle_sha256} files={len(manifest.files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
