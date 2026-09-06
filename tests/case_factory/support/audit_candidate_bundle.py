#!/usr/bin/env python3
"""Audit a Candidate-visible bundle against a positive allowlist.

Every visible byte must come from an allowlisted root; hidden material must be
physically absent from the visible tree and its semantic content must not
leak (hashes, thresholds, expert filenames, reference values). Rejects
symlink/hardlink/path traversal on any visible path. Local and HPC cases are
audited identically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
# Default Candidate-visible allowlist. Overridable via case-design.yaml:
#   candidate_visible: [root, ...]
VISIBLE_DEFAULTS = ("public", "instruction.md", "task.toml", "CONTRACT.md")
# Hidden roots whose content and hashes are the leakage sources.
HIDDEN_ROOTS = (
    "reference", "solution", "tests", "tools", "evidence",
    "profiles", "evaluator-manifest.json", "VALIDATION.json",
    "benchmark_valid.json", "case-design.yaml",
)
# Substring path components never allowed inside the visible tree.
FORBIDDEN_SUBSTRINGS = (
    "hidden", "reference", "solution", "tests", "thresholds", "fixtures", "old",
)
# Exact path components never allowed (names like .gitkeep must not match .git).
FORBIDDEN_EXACT = (".git", ".ssh", ".aws", "credentials", "docker.sock")
HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")
HASHED_JSON = (
    "reference/reference.json",
    "evidence/manifest.json",
    "reference/compute-runtime.lock.json",
    "evaluator-manifest.json",
)


def load_yaml_or_none(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def flatten(value: Any) -> list[Any]:
    items: list[Any] = []
    if isinstance(value, dict):
        for k, v in value.items():
            items.append(k)
            items.extend(flatten(v))
    elif isinstance(value, list):
        for v in value:
            items.extend(flatten(v))
    else:
        items.append(value)
    return items


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_hidden_tokens(case_dir: Path) -> set[str]:
    tokens: set[str] = set()
    for rel in HASHED_JSON:
        path = case_dir / rel
        if path.is_file():
            tokens.update(HASH_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
    thresholds = case_dir / "reference/thresholds.json"
    if thresholds.is_file():
        try:
            data = json.loads(thresholds.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
        for value in flatten(data or {}):
            # Calibrated threshold constants are the leakage-sensitive values.
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if value not in (0, 1):
                    tokens.add(repr(value))
    expert = case_dir / "solution/expert"
    if expert.is_dir():
        for f in expert.rglob("*"):
            if f.is_file() and f.name != ".gitkeep":
                tokens.add(f.name)
    return {t for t in tokens if t}


def visible_entries(case_dir: Path, visible_roots: tuple[str, ...]) -> list[Path]:
    entries: list[Path] = []
    for rel in visible_roots:
        path = case_dir / rel
        if path.is_dir():
            # rglob yields symlinked dirs as items but does not recurse into them;
            # the symlink itself is the thing we flag, not its target contents.
            entries.extend(path.rglob("*"))
        elif path.is_file() or path.is_symlink():
            entries.append(path)
    return entries


def audit_case(case_dir: Path) -> dict:
    case_dir = case_dir.resolve()
    errors: list[str] = []

    design = load_yaml_or_none(case_dir / "case-design.yaml")
    override = (design or {}).get("candidate_visible")
    if isinstance(override, list) and override:
        visible_roots = tuple(str(r) for r in override)
    else:
        visible_roots = VISIBLE_DEFAULTS

    for rel in visible_roots:
        if not (case_dir / rel).exists():
            errors.append(f"visible root missing: {rel}")

    entries = visible_entries(case_dir, visible_roots)
    files = [f for f in entries if f.is_file()]
    for f in entries:
        rel = f.relative_to(case_dir)
        if f.is_symlink():
            errors.append(f"symlink in visible tree: {rel}")
            continue
        if not f.is_file():
            continue
        try:
            st = f.stat()
        except OSError:
            errors.append(f"unreadable visible file: {rel}")
            continue
        if st.st_nlink > 1:
            errors.append(f"hardlink in visible tree: {rel}")
        try:
            resolved = f.resolve()
            resolved.relative_to(case_dir)
        except ValueError:
            errors.append(f"visible path escapes case dir: {rel}")
        low = str(rel).lower()
        for token in FORBIDDEN_SUBSTRINGS:
            if token in low:
                errors.append(f"forbidden path component {token!r}: {rel}")
        for part in rel.parts:
            if part.lower() in FORBIDDEN_EXACT:
                errors.append(f"forbidden path component {part!r}: {rel}")

    visible_basenames = {f.name for f in files}
    hidden_tokens = {
        t for t in collect_hidden_tokens(case_dir) if t not in visible_basenames
    }
    for f in files:
        rel = f.relative_to(case_dir)
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for token in sorted(hidden_tokens):
            if token and token in text:
                errors.append(f"semantic leakage in {rel}: {token[:40]}")

    # Detectable old-run / host state must not sit inside the case at all.
    for rel in HIDDEN_ROOTS:
        hidden = case_dir / rel
        if hidden.is_dir() and rel in ("reference", "solution", "tests", "tools", "evidence"):
            for f in hidden.rglob("*"):
                if f.is_file():
                    parts = f.relative_to(case_dir).parts
                    if any(p in FORBIDDEN_EXACT for p in parts):
                        errors.append(f"forbidden host/git state under hidden root: {f.relative_to(case_dir)}")

    return {
        "schema_version": SCHEMA_VERSION,
        "case_dir": str(case_dir),
        "visible_roots": list(visible_roots),
        "valid": not errors,
        "errors": sorted(errors),
        "visible_files": len(files),
        "hidden_tokens": len(hidden_tokens),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = audit_case(args.case_dir)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for err in report["errors"]:
            print(f"ERROR: {err}")
        print("OK" if report["valid"] else "FAILED")
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
