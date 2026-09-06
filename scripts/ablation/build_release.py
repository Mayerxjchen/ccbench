#!/usr/bin/env python3
"""Rebuild release component digests from the frozen Git tree at source_commit.

Reads a release manifest, recomputes every locally verifiable digest with the
canonical methods (file sha256 / tree digest excluding ``__pycache__``) from
the pinned source_commit Git tree, and re-derives ``release_digest``.
Remote-only entries (cluster SIFs) are left untouched — they are verified
on the cluster, not the host.

Usage:
    python3 scripts/ablation/build_release.py                 # dry-run summary
    python3 scripts/ablation/build_release.py --apply         # rewrite in place
    python3 scripts/ablation/build_release.py --verify        # exit nonzero on mismatch
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dftworld_bench.experiments.release_builder import (  # noqa: E402
    regenerate_release,
    release_mismatches,
    validate_source_commit,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--release", type=Path,
                    default=ROOT / "releases" / "ablation-ready-v0.json")
    ap.add_argument("--commit", type=str, default=None,
                    help="40-character git commit SHA to verify against or regenerate from (defaults to release source_commit)")
    ap.add_argument("--disk", action="store_true",
                    help="verify or regenerate directly against host disk instead of git tree")
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the release file with regenerated digests")
    ap.add_argument("--verify", action="store_true",
                    help="exit nonzero if any digest does not recompute from target tree")
    args = ap.parse_args(argv)

    release = json.loads(args.release.read_text(encoding="utf-8"))
    target_commit = "DISK" if args.disk else (args.commit or release.get("source_commit"))

    try:
        if target_commit != "DISK":
            validate_source_commit(target_commit, ROOT)
    except ValueError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    mismatches = release_mismatches(release, ROOT, commit=target_commit)

    target_label = "host disk" if target_commit == "DISK" else f"git commit {target_commit}"

    if args.verify:
        if mismatches:
            for component, label, want, have in mismatches:
                print(f"  [{component}] {label}: release={have} target={want}")
            print(f"{len(mismatches)} digest(s) do not recompute from {target_label}")
            return 1
        print(f"all digests recompute from {target_label}")
        return 0

    regenerated = regenerate_release(release, ROOT, commit=target_commit)
    new_errors = release_mismatches(regenerated, ROOT, commit=target_commit)
    if new_errors:
        print(f"ERROR: regeneration produced {len(new_errors)} new mismatches against {target_label}")
        return 1

    print(f"target_commit  : {target_commit}")
    print(f"source_commit  : {release.get('source_commit')}  ->  {regenerated.get('source_commit')}")
    print(f"digests fixed  : {len(mismatches)}")
    print(f"release_digest : {release.get('release_digest')}  ->  {regenerated.get('release_digest')}")
    if args.apply:
        args.release.write_text(
            json.dumps(regenerated, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.release}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
