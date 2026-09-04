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
        checks["runtime_locks"] = resolver.all_capabilities()
        checks["qualified_runtimes"] = resolver.qualified_capabilities()
    except Exception as exc:  # fail closed with the reason, not a traceback
        checks["runtime_locks"] = f"error: {exc}"
        checks["qualified_runtimes"] = []
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

    # Translate --compute <profile> to --compute-profile <profile>
    translated: list[str] = []
    i = 0
    while i < len(rest):
        if rest[i] == "--compute":
            translated.append("--compute-profile")
            if i + 1 < len(rest):
                translated.append(rest[i + 1])
                i += 2
                continue
        elif rest[i].startswith("--compute="):
            _, val = rest[i].split("=", 1)
            translated.extend(["--compute-profile", val])
            i += 1
            continue
        elif rest[i] == "--site":
            translated.append("--site-profile")
            if i + 1 < len(rest):
                translated.append(rest[i + 1])
                i += 2
                continue
        elif rest[i].startswith("--site="):
            _, val = rest[i].split("=", 1)
            translated.extend(["--site-profile", val])
            i += 1
            continue
        else:
            translated.append(rest[i])
        i += 1

    saved = sys.argv
    sys.argv = ["mlffbench run", *translated]
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


def _compute_configure(out: Path, template: str) -> int:
    out = out.expanduser().resolve()
    if ROOT in out.parents or out == ROOT:
        raise CliError(
            f"the compute profile must live OUTSIDE the repository (refusing {out} under {ROOT})"
        )
    if out.exists():
        raise CliError(f"refusing to overwrite existing file: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    src = ROOT / "examples" / "hpc" / f"{template}-compute-profile.json"
    if not src.is_file():
        raise CliError(f"template {template!r} not found at {src}")
    shutil.copyfile(src, out)
    print(f"compute profile template copied to {out}", file=sys.stderr)
    return 0


def _compute_qualify(
    profile_path: Path,
    receipt_path: Path | None = None,
    site_receipts_dir: Path | None = None,
) -> int:
    import json
    from dftworld_bench.experiments.compute_profile_qualification import (
        verify_and_derive_qualification,
    )
    from dftworld_bench.hpc.compute_profile import ComputeProfile

    profile_path = profile_path.expanduser().resolve()
    if not profile_path.is_file():
        raise CliError(f"profile file not found: {profile_path}")

    prof = ComputeProfile.from_file(profile_path)
    if receipt_path is None:
        raise CliError(
            "qualification verification requires an official receipt: pass --receipt <path> "
            "pointing to a signed Layer 2 qualification receipt, or run a formal qualification probe."
        )
    receipt_path = receipt_path.expanduser().resolve()
    if not receipt_path.is_file():
        raise CliError(f"qualification receipt file not found: {receipt_path}")

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CliError(f"failed to parse qualification receipt: {exc}") from exc

    if not site_receipts_dir and len(prof.routes) > 1:
        raise CliError(
            "hybrid compute profile qualification requires --site-receipts-dir pointing to verified site receipts"
        )

    # Zero-orphan live verification query helper via official account instances list
    def live_cloud_instances_checker() -> list[str]:
        from dftworld_bench.hpc.drivers.compshare.cli import CompShareCli, CompShareCliError

        cli = CompShareCli()
        try:
            items = cli.instance_list(all=True)
            active = []
            for item in items:
                name = str(item.get("name") or "")
                remark = str(item.get("remark") or "")
                inst_id = str(item.get("instance_id") or item.get("id") or "")
                # Filter strictly by trusted MLFFBench ownership marker
                if name.startswith("mlffbench-") or remark.startswith("mlffbench:") or inst_id.startswith("mlffbench-"):
                    status = str(item.get("status") or "").lower()
                    if status not in ("terminated", "deleted", "stopped", "failed"):
                        active.append(inst_id or name)
            return active
        except CompShareCliError as exc:
            raise RuntimeError(f"Cannot verify live cloud zero-orphan status: {exc}") from exc

    verdict = verify_and_derive_qualification(
        receipt,
        expected_profile=prof,
        site_receipts_dir=site_receipts_dir,
        active_instances_checker=live_cloud_instances_checker if prof.routes.get("gpu") else None,
        require_live_check=bool(prof.routes.get("gpu")),
    )
    if verdict.passed:
        print(
            f"compute profile {verdict.compute_profile_id!r} QUALIFIED (Zero-Orphan Gate: PASS)",
            file=sys.stderr,
        )
        return 0
    else:
        print(
            f"compute profile {verdict.compute_profile_id!r} QUALIFICATION FAILED: {verdict.errors}",
            file=sys.stderr,
        )
        return 2


def _compute_validate(profile_path: Path) -> int:
    import json
    import jsonschema
    from dftworld_bench.hpc.compute_profile import ComputeProfile

    profile_path = profile_path.expanduser().resolve()
    if not profile_path.is_file():
        raise CliError(f"profile file not found: {profile_path}")
    schema_path = ROOT / "schemas" / "compute-profile.schema.json"
    schema = json.loads(schema_path.read_text())
    doc = json.loads(profile_path.read_text())
    try:
        jsonschema.validate(instance=doc, schema=schema)
    except jsonschema.ValidationError as exc:
        raise CliError(f"compute profile schema validation error: {exc.message}") from exc
    profile = ComputeProfile.from_dict(doc)
    print(
        f"compute profile {profile.profile_id!r} validates: digest {profile.digest}",
        file=sys.stderr,
    )
    return 0


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

    compute = sub.add_parser("compute", help="manage compute routing profiles")
    compute_sub = compute.add_subparsers(dest="compute_command", required=True)
    comp_conf = compute_sub.add_parser(
        "configure", help="materialize a compute profile template"
    )
    comp_conf.add_argument("--out", type=Path, required=True)
    comp_conf.add_argument(
        "--template",
        type=str,
        default="generic-slurm",
        choices=["generic-slurm", "maintainer-hybrid"],
    )
    comp_val = compute_sub.add_parser(
        "validate", help="validate a compute profile document"
    )
    comp_val.add_argument("--profile", type=Path, required=True)
    comp_qual = compute_sub.add_parser(
        "qualify", help="qualify a compute profile with Layer 2 zero-orphan verification"
    )
    comp_qual.add_argument("--profile", type=Path, required=True)
    comp_qual.add_argument("--receipt", type=Path, default=None)
    comp_qual.add_argument("--site-receipts-dir", type=Path, default=None)

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
        if command == "compute":
            comp_command = argv[1] if len(argv) > 1 else ""
            if comp_command == "configure":
                args = build_parser().parse_args(argv)
                return _compute_configure(args.out, args.template)
            if comp_command == "validate":
                args = build_parser().parse_args(argv)
                return _compute_validate(args.profile)
            if comp_command == "qualify":
                args = build_parser().parse_args(argv)
                return _compute_qualify(args.profile, args.receipt, args.site_receipts_dir)
            build_parser().error("compute needs a subcommand: configure | validate | qualify")
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
