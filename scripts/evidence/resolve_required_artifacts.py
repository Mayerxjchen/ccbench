#!/usr/bin/env python3
"""Resolve the minimal verifier-driven artifact set for a run workspace (plan Task 3).

Turns a case evidence policy into the exact list of files the hidden verifier
reads plus declared reproduction records. Fail-closed: every reference must
resolve inside the workspace to a regular file; anything absolute, escaping,
symlinked, missing, or double-roled raises ``EvidencePolicyError``.

Supported entry kinds:

- ``static`` — ``path`` names a file.
- ``glob`` — ``glob`` pattern with single-``*`` wildcards (no ``**``, no
  traversal); every match is a file.
- ``ref`` — ``path`` is a JSON path into ``source`` (default ``result.json``):
  ``trajectories[].path``, ``history[].models[].path``,
  ``history[].trajectory``, ``history[].exploration_trajectories[].path``,
  and ``artifact_hashes.keys()`` (dict keys of the referenced file).

A file resolved by more than one entry with the *same* role is deduplicated; a
file claimed under *different* roles is a policy error.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any


class EvidencePolicyError(ValueError):
    """The policy cannot be satisfied by this workspace (fail closed)."""


@dataclass(frozen=True)
class EvidenceFile:
    path: str
    role: str
    size_bytes: int
    sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_unsafe(rel: str) -> bool:
    return (
        rel.startswith("/")
        or rel.startswith("..")
        or "/../" in f"/{rel}"
        or "\\" in rel
    )


def _materialize(workspace: Path, rel: str, role: str) -> list[EvidenceFile]:
    if _is_unsafe(rel):
        raise EvidencePolicyError(f"unsafe reference path: {rel!r}")
    target = (workspace / rel).resolve()
    try:
        target.relative_to(workspace.resolve())
    except ValueError:
        raise EvidencePolicyError(f"reference escapes workspace: {rel!r}")
    if target.is_symlink():
        raise EvidencePolicyError(f"symlink not allowed in evidence: {rel!r}")
    if not target.is_file():
        raise EvidencePolicyError(f"missing referenced artifact: {rel!r}")
    return [EvidenceFile(path=rel, role=role, size_bytes=target.stat().st_size,
                         sha256=_sha256_file(target))]


def _walk_json(value: Any, tokens: list[str], where: str) -> list[str]:
    if not tokens:
        return [str(value)]
    token = tokens[0]
    if token.endswith("keys()"):
        # "artifact_hashes.keys()" splits into ["artifact_hashes", "keys()"];
        # the special token is exactly "keys()" applied to the current value.
        if token == "keys()":
            obj = value
        else:
            name = token[:-len(".keys()")]
            obj = value.get(name) if isinstance(value, dict) else None
        if not isinstance(obj, dict):
            raise EvidencePolicyError(f"{where}: {token!r} target is not an object with keys()")
        return list(obj.keys())
    if token.endswith("[]"):
        name = token[:-2]
        seq = value.get(name) if isinstance(value, dict) else None
        if seq is None:
            return []  # e.g. an exploration round with no exploration_trajectories
        if not isinstance(seq, list):
            raise EvidencePolicyError(f"{where}: {name!r} is not a list")
        out: list[str] = []
        for item in seq:
            out.extend(_walk_json(item, tokens[1:], where))
        return out
    if isinstance(value, dict) and token in value:
        return _walk_json(value[token], tokens[1:], where)
    raise EvidencePolicyError(f"{where}: path {token!r} not found")


def _resolve_ref(workspace: Path, entry: dict) -> list[EvidenceFile]:
    role = entry["role"]
    source = entry.get("source", "result.json")
    source_path = workspace / source
    if _is_unsafe(source) or not source_path.is_file():
        raise EvidencePolicyError(f"ref source missing: {source!r}")
    try:
        data = json.loads(source_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise EvidencePolicyError(f"ref source unreadable {source!r}: {exc}")
    paths = _walk_json(data, entry["path"].split("."), f"{source}:{entry['path']}")
    files: list[EvidenceFile] = []
    for rel in paths:
        files.extend(_materialize(workspace, str(rel), role))
    return files


def _resolve_glob(workspace: Path, entry: dict) -> list[EvidenceFile]:
    pattern = entry.get("glob") or entry.get("path")
    if not pattern:
        raise EvidencePolicyError(f"glob entry without pattern: {entry['path']!r}")
    # Restricted glob: recursion inside the workspace is fine (** matches across
    # separators); anything that could escape the tree is rejected here and again
    # by the containment check in _materialize.
    if pattern.startswith("/") or pattern.startswith("..") or "\\" in pattern:
        raise EvidencePolicyError(f"restricted glob only: {pattern!r}")
    files: list[EvidenceFile] = []
    base = workspace
    for path in sorted(base.rglob("*")):
        if path.is_dir() or path.is_symlink():
            continue
        rel = path.relative_to(base).as_posix()
        if fnmatchcase(rel, pattern):
            files.extend(_materialize(workspace, rel, entry["role"]))
    return files


def resolve_required_artifacts(workspace: Path | str, policy: dict,
                               roles: tuple[str, ...] = ("scoring_required", "reproduction_required")
                               ) -> list[EvidenceFile]:
    """Resolve the policy against ``workspace``, fail-closed, deduplicated."""
    workspace = Path(workspace).resolve()
    if not workspace.is_dir():
        raise EvidencePolicyError(f"workspace missing: {workspace}")
    if not policy.get("finalization_allowed", True):
        raise EvidencePolicyError(f"case {policy.get('case_id')} may not finalize evidence")

    resolved: list[EvidenceFile] = []
    role_by_path: dict[str, str] = {}
    for role in roles:
        for entry in policy.get(role, []):
            kind = entry.get("kind")
            if kind is None:
                kind = "glob" if entry.get("glob") else "static"
            if kind == "static":
                hits = _materialize(workspace, entry["path"], role)
            elif kind == "glob":
                hits = _resolve_glob(workspace, entry)
            elif kind == "ref":
                hits = _resolve_ref(workspace, entry)
            else:
                raise EvidencePolicyError(f"unknown entry kind: {kind!r}")
            for hit in hits:
                existing = role_by_path.get(hit.path)
                if existing and existing != role:
                    raise EvidencePolicyError(
                        f"file {hit.path!r} claimed under two roles: {existing} and {role}")
                if existing is None:
                    role_by_path[hit.path] = role
                    resolved.append(hit)
    return sorted(resolved, key=lambda f: f.path)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workspace", type=Path)
    ap.add_argument("--policy", required=True, type=Path)
    args = ap.parse_args(argv)

    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    files = resolve_required_artifacts(args.workspace, policy)
    total = sum(f.size_bytes for f in files)
    print(f"resolved {len(files)} files, {total} bytes")
    for f in files:
        print(f"  {f.role:<22} {f.size_bytes:>10} {f.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
