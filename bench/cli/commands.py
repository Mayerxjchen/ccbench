"""Helper commands for Bench CLI operations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from bench.paths import ROOT, RUNTIME_LOCKS_DIR


def handle_portfolio_cmd(argv: list[str]) -> int:
    from bench.portfolio.registry import generate_portfolio_report

    parser = argparse.ArgumentParser(prog="bench portfolio")
    sub = parser.add_subparsers(dest="subcommand", required=True)
    rep_p = sub.add_parser("report")
    rep_p.add_argument("--out", type=Path, default=None)
    sub.add_parser("validate")
    args = parser.parse_args(argv)

    rep = generate_portfolio_report()
    if args.subcommand == "report":
        out_str = json.dumps(rep, indent=2)
        if args.out:
            args.out.write_text(out_str, encoding="utf-8")
            print(f"Portfolio report written to {args.out}")
        else:
            print(out_str)
        return 0
    if args.subcommand == "validate":
        errs = rep.get("errors", [])
        if errs:
            print(f"Portfolio validation errors: {errs}", file=sys.stderr)
            return 1
        print(f"Portfolio valid: {rep['total_cases']} cases checked.")
        return 0
    return 1


def handle_runtime_cmd(argv: list[str]) -> int:
    import subprocess

    parser = argparse.ArgumentParser(prog="bench runtime")
    sub = parser.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("audit")
    b_p = sub.add_parser("build")
    b_p.add_argument("recipe")
    args = parser.parse_args(argv)

    if args.subcommand == "audit":
        from bench.runtime.provenance import verify_runtime_lock_provenance

        locks_dir = RUNTIME_LOCKS_DIR
        all_ok = True
        for lock_file in sorted(locks_dir.glob("*-runtime.lock.json")):
            try:
                doc = json.loads(lock_file.read_text(encoding="utf-8"))
                verify_runtime_lock_provenance(doc)
                print(f"  OK: {lock_file.name}")
            except Exception as exc:
                print(f"  FAIL: {lock_file.name} - {exc}", file=sys.stderr)
                all_ok = False
        return 0 if all_ok else 1

    if args.subcommand == "build":
        build_script = ROOT / "runtimes" / "recipes" / "build.sh"
        return subprocess.run(["bash", str(build_script), args.recipe]).returncode
    return 1
