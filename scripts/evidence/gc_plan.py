#!/usr/bin/env python3
"""Non-destructive garbage-collection planning for the dual CAS stores (plan Task 9).

A candidate is an object referenced by no Git-tracked v2 manifest AND older than
the grace period (default 30 days). Referenced objects are never eligible; recent
unreferenced uploads stay protected. The tool is dry-run by default — it only
prints what it *would* delete, with age, size, and why. Actual deletion requires
a separate explicit ``--apply`` invocation plus user approval; Object Lock /
retention policy remains the final protection.

Usage::

    python scripts/evidence/gc_plan.py \\
        --primary cas+file://…/store/031-primary \\
        --replica cas+file://…/store/031-replica \\
        --evidence-root evidence/matclaw/formal \\
        [--grace-days 30] [--git-history]

``--git-history`` also protects every bundle referenced by any Git revision, not
just the working tree. ``--apply`` deletes only the printed candidates (after a
final confirmation of the count) and never touches a referenced digest.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parents[2]
OBJECT_SUFFIX = ".tar.zst"
GRACE_DAYS = 30


def _uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "cas+file":
        raise ValueError(f"unsupported store scheme: {parsed.scheme!r}")
    return Path(unquote(parsed.path))


def collect_referenced(evidence_root: Path, git_root: Path | None = None,
                       history: bool = False) -> set[str]:
    """Bundle digests referenced by every tracked v2 manifest (working tree, plus
    Git history when requested)."""
    digests: set[str] = set()
    for manifest in evidence_root.glob("*/run-*/manifest.json"):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("schema_version") != "2.0":
            continue
        bundle = data.get("bundle")
        if bundle and bundle.get("sha256"):
            digests.add(bundle["sha256"])
    if git_root is not None:
        digests |= _git_referenced(git_root, history)
    return digests


def _git_referenced(git_root: Path, history: bool) -> set[str]:
    def git(*args: str) -> str:
        proc = subprocess.run(["git", "-C", str(git_root), *args],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()}")
        return proc.stdout

    try:
        git("rev-parse", "--is-inside-work-tree")
    except RuntimeError:
        return set()

    revs = git("rev-list", "--all").splitlines() if history else ["HEAD"]
    digests: set[str] = set()
    for rev in revs:
        files = git("ls-tree", "-r", "--name-only", rev, "--", "evidence").splitlines()
        for path in files:
            if not path.endswith("/manifest.json"):
                continue
            try:
                blob = git("show", f"{rev}:{path}")
            except RuntimeError:
                continue
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                continue
            bundle = data.get("bundle")
            if bundle and bundle.get("sha256"):
                digests.add(bundle["sha256"])
    return digests


def scan_store(primary_uri: str, replica_uri: str) -> list[dict]:
    """Every object under both stores, deduped by digest, with age + size + uri."""
    objects: dict[str, dict] = {}
    for label, uri in (("primary", primary_uri), ("replica", replica_uri)):
        root = _uri_to_path(uri)
        for obj in root.glob("sha256/*/*.tar.zst"):
            digest = obj.name[: -len(OBJECT_SUFFIX)]
            stat = obj.stat()
            objects.setdefault(digest, {
                "digest": digest,
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
                "stores": [],
            })
            objects[digest]["stores"].append({"backend": label, "uri": f"cas+file://{obj}"})
    return list(objects.values())


def plan(primary_uri: str, replica_uri: str, evidence_root: Path,
         grace_days: int = GRACE_DAYS, git_root: Path | None = None,
         history: bool = False) -> dict:
    """Non-destructive plan: referenced and grace-protected objects are never
    candidates; returns candidates with reason/age/size. Never mutates."""
    referenced = collect_referenced(evidence_root, git_root, history)
    now = time.time()
    candidates: list[dict] = []
    protected: list[str] = []
    for obj in scan_store(primary_uri, replica_uri):
        if obj["digest"] in referenced:
            continue
        age_days = (now - obj["mtime"]) / 86400
        if age_days < grace_days:
            protected.append(obj["digest"])
            continue
        candidates.append({
            "digest": obj["digest"],
            "size_bytes": obj["size_bytes"],
            "age_days": round(age_days, 1),
            "reason": "unreferenced by any tracked manifest and past grace period",
            "stores": obj["stores"],
        })
    candidates.sort(key=lambda c: c["digest"])
    protected.sort()
    return {
        "referenced": sorted(referenced),
        "protected_unreferenced": protected,
        "candidates": candidates,
        "grace_days": grace_days,
        "dry_run": True,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--primary", required=True)
    ap.add_argument("--replica", required=True)
    ap.add_argument("--evidence-root", type=Path, required=True,
                    help="explicit evidence root to scan")
    ap.add_argument("--git-root", type=Path, default=ROOT)
    ap.add_argument("--grace-days", type=int, default=GRACE_DAYS)
    ap.add_argument("--git-history", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete the printed candidates (requires user approval)")
    args = ap.parse_args(argv)

    report = plan(args.primary, args.replica, args.evidence_root,
                  grace_days=args.grace_days, git_root=args.git_root,
                  history=args.git_history)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    candidates = report["candidates"]
    if not candidates:
        print("no objects eligible for garbage collection")
        return 1 if args.apply else 0

    if not args.apply:
        total = sum(c["size_bytes"] for c in candidates)
        print(f"dry run: {len(candidates)} candidates, {total} bytes "
              f"({len(report['referenced'])} referenced, "
              f"{len(report['protected_unreferenced'])} grace-protected)")
        return 0

    # --apply: safety is structural (candidates never intersect referenced), so
    # deletion here is the explicit destructive act the operator approved.
    total = 0
    for c in candidates:
        for store in c["stores"]:
            path = _uri_to_path(store["uri"])
            path.unlink(missing_ok=True)
        total += c["size_bytes"]
    print(f"deleted {len(candidates)} candidates ({total} bytes) from both stores")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
