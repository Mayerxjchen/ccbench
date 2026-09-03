#!/usr/bin/env python3
"""Build a physically isolated clean source view for a builder run.

Harness tool (evaluator side), NOT part of the skill. Copies a source
materials tree to an output view while DROPPING every file whose basename
matches a blocked answer name — the skill's default list
(`DEFAULT_SOURCE_EXCLUDES` in the category's check_draft_consistency.py,
plus any extra `--exclude` the evaluator names). The dropped set is printed
as a manifest so the evaluator can record exactly what the driver could not
see.

Usage:
    python make_clean_view.py MATERIALS_DIR OUT_VIEW_DIR \
        --skill-root .../scripts [--exclude PATTERN]...

Exit 0 and write the view atomically (refuse if OUT exists and is non-empty).
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import shutil
import sys
from pathlib import Path

FALLBACK_BLOCKED = [
    "acceptance.json",
    "expected-output.json",
    "expected.json",
    "held-out-targets.json",
    "scores.json",
]


def load_blocked_names(skill_scripts: Path) -> list[str]:
    checker = skill_scripts / "categories" / "mlp" / "check_draft_consistency.py"
    spec = importlib.util.spec_from_file_location("cdc", checker)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:  # missing dep — fall back, but say so loudly
        print(f"warn: could not import {checker}; using fallback list",
              file=sys.stderr)
        return list(FALLBACK_BLOCKED)
    names = list(getattr(mod, "DEFAULT_SOURCE_EXCLUDES", []))
    if not names:
        print("warn: skill exports no DEFAULT_SOURCE_EXCLUDES; using fallback",
              file=sys.stderr)
        names = list(FALLBACK_BLOCKED)
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("materials", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--skill-root", type=Path, required=True,
                    help="skill package scripts/ directory")
    ap.add_argument("--exclude", action="append", default=[],
                    metavar="PATTERN",
                    help="extra basename globs to drop (repeatable)")
    args = ap.parse_args()

    src = args.materials.resolve()
    dst = args.out.resolve()
    if not src.is_dir():
        print(f"error: not a directory: {src}", file=sys.stderr)
        return 2
    if dst.exists() and any(dst.iterdir()):
        print(f"error: refusing to overwrite non-empty view: {dst}", file=sys.stderr)
        return 2

    blocked = load_blocked_names(args.skill_root.resolve()) + args.exclude
    dropped: list[Path] = []
    kept: list[Path] = []
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if path.is_file() and any(fnmatch.fnmatch(path.name, pat) for pat in blocked):
            dropped.append(rel)
        elif path.is_file():
            kept.append(rel)

    dst.mkdir(parents=True, exist_ok=True)
    for rel in kept:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)

    print("blocked names:", ", ".join(sorted(set(blocked))))
    print(f"dropped ({len(dropped)}) — invisible to the driver:")
    for rel in dropped:
        print(f"  - {rel}")
    print(f"kept ({len(kept)}) in {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
