"""``mlffbench`` — the thin unified user entry.

Five verbs, and no logic: each one locates an existing module and forwards.
This CLI must never reimplement resolution, submission, qualification, or
reporting — those live in the trusted modules it orchestrates:

- ``mlffbench setup``             — environment and repo sanity, next steps
- ``mlffbench site configure``    — materialize the private cluster profile
- ``mlffbench site qualify ...``  — -> scripts/qualification/qualify_case.py
- ``mlffbench run ...``           — -> eval.py (the case runner)
- ``mlffbench report ...``        — -> scripts/ablation/verify_evidence.py

A private cluster profile must live OUTSIDE the repository; ``site
configure`` enforces that at the door.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts" / "hpc" / "cluster_profile.toml"


class CliError(Exception):
    """A user-facing CLI precondition failed."""


def _setup(report_only: bool = False) -> int:
    checks: dict[str, Any] = {}
    checks["python_at_least_3_11"] = sys.version_info >= (3, 11)
    checks["repo_root"] = str(ROOT)
    checks["template_present"] = TEMPLATE.is_file()
    for mod in ("jsonschema", "yaml", "tomllib"):
        try:
            __import__(mod)
            checks[f"import_{mod}"] = True
        except ImportError:
            checks[f"import_{mod}"] = False
    try:
        from dftworld_bench.hpc.runtime_resolution import RuntimeResolver

        resolver = RuntimeResolver.from_lock_dir(ROOT / "reference" / "runtime")
        checks["runtime_locks"] = resolver.capabilities()
    except Exception as exc:  # fail closed with the reason, not a traceback
        checks["runtime_locks"] = f"error: {exc}"
    ok = all(
        v for k, v in checks.items() if k.startswith(("python_", "import_", "template_"))
    )
    print(json.dumps(checks, indent=2, ensure_ascii=False, sort_keys=True))
    if not report_only:
        print(
            "\nnext steps:\n"
            "  mlffbench site configure --out ~/cluster_profile.toml\n"
            "  # fill in every value, then:\n"
            "  mlffbench site qualify --profile ~/cluster_profile.toml --dry-run\n"
            "  mlffbench run 034-ai2kit-water64-end-to-end-potential\n",
            file=sys.stderr,
        )
    return 0 if ok else 1


def _site_configure(out: Path, validate: bool) -> int:
    out = out.expanduser().resolve()
    if ROOT in out.parents or out == ROOT:
        raise CliError(
            f"the private cluster profile must live OUTSIDE the repository "
            f"(refusing {out} under {ROOT})"
        )
    if out.exists():
        raise CliError(f"refusing to overwrite existing file: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATE, out)
    print(f"template copied to {out}", file=sys.stderr)
    print(
        "fill in every REQUIRED value for your cluster; partition must name "
        "exactly ONE queue (full-GPU and MIG are separate profiles with "
        "separate qualifications); then optionally run:\n"
        f"  mlffbench site configure --out {out} --validate",
        file=sys.stderr,
    )
    if validate:
        from scripts.cluster_profile import load_profile

        load_profile(out)  # raises ProfileError with a precise message
        print("profile validates: all required keys present and typed", file=sys.stderr)
    return 0


def _site_qualify(rest: list[str]) -> int:
    from scripts.qualification.qualify_case import main as qualify_main

    return qualify_main(rest)


def _run(rest: list[str]) -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))  # eval.py is a repo-root module
    import eval as eval_mod  # top-level case runner

    saved = sys.argv
    sys.argv = ["mlffbench run", *rest]
    try:
        eval_mod.main()
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv = saved


def _report(rest: list[str]) -> int:
    from scripts.ablation.verify_evidence import main as verify_main

    return verify_main(rest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mlffbench",
        description="thin operator/user entry — orchestrates existing modules only",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="environment and repo sanity")
    setup.add_argument("--quiet", action="store_true", help="no next-steps text")

    site = sub.add_parser("site", help="site configuration and qualification")
    site_sub = site.add_subparsers(dest="site_command", required=True)
    configure = site_sub.add_parser(
        "configure", help="materialize the private cluster profile template"
    )
    configure.add_argument("--out", type=Path, required=True)
    configure.add_argument(
        "--validate", action="store_true", help="validate an existing profile"
    )
    qualify = site_sub.add_parser(
        "qualify", help="run site/case qualification (forwards to qualify_case)"
    )
    qualify.add_argument("qualify_args", nargs="*", help=argparse.SUPPRESS)

    run = sub.add_parser("run", help="run a case (forwards to eval.py)")
    run.add_argument("run_args", nargs="*", help=argparse.SUPPRESS)

    report = sub.add_parser(
        "report", help="verify evidence for a run (forwards to verify_evidence)"
    )
    report.add_argument("report_args", nargs="*", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else ""
    try:
        if command == "setup":
            args = build_parser().parse_args(argv)
            return _setup(report_only=args.quiet)
        if command == "site":
            site_command = argv[1] if len(argv) > 1 else ""
            if site_command == "configure":
                args = build_parser().parse_args(argv)
                return _site_configure(args.out, args.validate)
            if site_command == "qualify":
                # Raw pass-through: qualify_case owns its own argparse.
                return _site_qualify(argv[2:])
            build_parser().error("site needs a subcommand: configure | qualify")
        if command == "run":
            return _run(argv[1:])
        if command == "report":
            return _report(argv[1:])
        build_parser().error(f"unknown command {command!r}")
    except CliError as exc:
        print(f"mlffbench: {exc}", file=sys.stderr)
        return 2
    raise SystemExit("unreachable")  # argparse exits on error paths above


if __name__ == "__main__":
    raise SystemExit(main())
