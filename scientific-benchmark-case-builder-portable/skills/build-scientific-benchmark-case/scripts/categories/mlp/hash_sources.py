#!/usr/bin/env python3
"""Create a portable source identity lock file.

Uses only the Python standard library. It hashes local files/directories and can
record a local Git repository HEAD/tree identity. It does not download sources.

`--exclude PATTERN` (repeatable, fnmatch on the file name or its path relative
to the walked root) drops leave-one-out answer files — `acceptance.json` and
friends — from directory/repository hashes and records the patterns in the
lock so the omission is auditable, not invisible.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def sha256_file(path: Path) -> str:
    if path.is_symlink():
        raise SystemExit(f"symlink is not a stable source identity: {path}")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_excluded(path: Path, root: Path, excludes: list[str]) -> bool:
    rel = path.relative_to(root).as_posix()
    return any(
        fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern)
        for pattern in excludes
    )


def iter_files(root: Path, excludes: list[str] | None = None) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"symlink is not allowed in source tree: {path}")
        if path.is_file() and ".git" not in path.parts:
            if excludes and is_excluded(path, root, excludes):
                continue
            yield path


def sha256_dir(root: Path, excludes: list[str] | None = None) -> str:
    h = hashlib.sha256()
    for path in iter_files(root, excludes):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        h.update(rel)
        h.update(b"\0")
        h.update(sha256_file(path).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def split_label(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected LABEL=VALUE")
    label, val = value.split("=", 1)
    if not label or not val:
        raise argparse.ArgumentTypeError("expected non-empty LABEL=VALUE")
    return label, val


def git(args: list[str], cwd: Path) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", action="append", default=[], metavar="LABEL=PATH")
    parser.add_argument("--dir", action="append", default=[], metavar="LABEL=PATH")
    parser.add_argument("--repo", action="append", default=[], metavar="LABEL=PATH")
    parser.add_argument("--identity", action="append", default=[], metavar="LABEL=VALUE")
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="PATTERN",
        help="fnmatch name/relpath pattern dropped from --dir/--repo trees "
             "(e.g. acceptance.json); recorded in the lock",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    result: dict[str, object] = {"schema_version": 1, "generated_at": now, "sources": {}}
    sources: dict[str, object] = result["sources"]  # type: ignore[assignment]

    for item in args.file:
        label, raw = split_label(item)
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"not a file: {path}")
        sources[label] = {
            "kind": "file",
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "retrieved_at": now,
        }

    for item in args.dir:
        label, raw = split_label(item)
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise SystemExit(f"not a directory: {path}")
        entry: dict[str, object] = {
            "kind": "directory",
            "path": str(path),
            "tree_sha256": sha256_dir(path, args.exclude or None),
            "retrieved_at": now,
        }
        if args.exclude:
            entry["excluded"] = list(args.exclude)
        sources[label] = entry

    for item in args.repo:
        label, raw = split_label(item)
        path = Path(raw).expanduser().resolve()
        if not path.is_dir():
            raise SystemExit(f"not a directory: {path}")
        head = git(["rev-parse", "HEAD"], path)
        tree = git(["rev-parse", "HEAD^{tree}"], path)
        status = git(["status", "--porcelain"], path)
        dirty = status is None or bool(status)
        repo_entry: dict[str, object] = {
            "kind": "git_repository",
            "path": str(path),
            "commit": head,
            "tree": tree,
            "worktree_dirty": dirty,
            "worktree_tree_sha256": sha256_dir(path, args.exclude or None),
            "identity_status": "locked" if head and tree and not dirty else "partial",
            "retrieved_at": now,
        }
        if args.exclude:
            repo_entry["excluded"] = list(args.exclude)
        sources[label] = repo_entry

    for item in args.identity:
        label, value = split_label(item)
        sources[label] = {
            "kind": "external_identity",
            "value": value,
            "identity_status": "declared",
            "retrieved_at": now,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
