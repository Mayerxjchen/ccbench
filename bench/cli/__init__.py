"""``bench`` — the single user and operator entry point.

The active MVP entry is ``bench pilot``.  The CLI must never reimplement
resolution, submission, qualification, or reporting — those live in the
trusted modules it orchestrates:

- ``bench setup``             — environment and repo sanity, next steps
- ``bench site configure``    — materialize the private cluster profile
- ``bench site qualify ...``  — -> scripts/qualification/qualify_case.py
- ``bench pilot ...``         — Claude Code inside Candidate Docker

A private cluster profile must live OUTSIDE the repository; ``site
configure`` enforces that at the door.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import shutil
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bench.paths import ROOT, RUNTIME_LOCKS_DIR
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
        from bench.hpc.runtime_resolution import RuntimeResolver

        lock_dir = RUNTIME_LOCKS_DIR
        resolver = RuntimeResolver.from_lock_dir(lock_dir)
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
            "  bench site configure --out ~/cluster_profile.toml\n"
            "  # fill in every value, then:\n"
            "  bench site qualify --profile ~/cluster_profile.toml --dry-run\n"
            "  bench run /path/to/paper/cases/001\n",
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
        f"  bench site configure --out {out} --validate",
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
    trust_store_path: Path | None = None,
    site_profile_registry_path: Path | None = None,
) -> int:
    import json
    from bench.experiments.compute_profile_qualification import (
        verify_and_derive_qualification,
    )
    from bench.hpc.compute_profile import ComputeProfile
    from bench.hpc.site_profile import HpcSiteProfile
    from bench.hpc.trust_store import QualificationTrustStore

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

    if trust_store_path is None:
        raise CliError(
            "formal compute qualification requires --trust-store pointing to "
            "an operator-configured qualification trust store"
        )
    trust_store_path = trust_store_path.expanduser().resolve()
    try:
        trust_store = QualificationTrustStore.from_file(trust_store_path)
    except Exception as exc:
        raise CliError(f"failed to load qualification trust store: {exc}") from exc

    if site_profile_registry_path is None:
        raise CliError(
            "formal compute qualification requires --site-profile-registry "
            "with trusted SiteProfile policy documents"
        )

    def _profile_documents(path: Path) -> list[dict[str, Any]]:
        """Load only operator-supplied registry documents; never examples."""
        candidates = sorted(path.glob("*.json")) + sorted(path.glob("*.toml")) if path.is_dir() else [path]
        documents: list[dict[str, Any]] = []
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                raw = (
                    tomllib.loads(candidate.read_text(encoding="utf-8"))
                    if candidate.suffix.lower() == ".toml"
                    else json.loads(candidate.read_text(encoding="utf-8"))
                )
            except Exception as exc:
                raise CliError(f"failed to parse SiteProfile registry {candidate}: {exc}") from exc
            if not isinstance(raw, dict):
                raise CliError(f"SiteProfile registry {candidate} must contain an object")
            sites = raw.get("sites")
            if isinstance(sites, dict):
                for site_id, value in sites.items():
                    if isinstance(value, dict):
                        documents.append({"site_id": site_id, **value})
            elif isinstance(raw.get("site_id"), str):
                documents.append(raw)
            else:
                raise CliError(
                    f"SiteProfile registry {candidate} must be a SiteProfile or [sites.*] mapping"
                )
        return documents

    registry_path = site_profile_registry_path.expanduser().resolve()
    if not registry_path.exists():
        raise CliError(f"trusted SiteProfile registry not found: {registry_path}")
    trusted_site_profiles: dict[str, HpcSiteProfile] = {}
    for raw_profile in _profile_documents(registry_path):
        try:
            profile = HpcSiteProfile.from_dict(raw_profile)
        except Exception as exc:
            raise CliError(f"invalid trusted SiteProfile in {registry_path}: {exc}") from exc
        if profile.site_id in trusted_site_profiles:
            raise CliError(f"duplicate trusted SiteProfile id: {profile.site_id}")
        trusted_site_profiles[profile.site_id] = profile
    if not trusted_site_profiles:
        raise CliError(f"trusted SiteProfile registry is empty: {registry_path}")

    # Zero-orphan live verification query helper via official account instances list
    def live_cloud_instances_checker() -> list[str]:
        from bench.hpc.drivers.compshare.cli import CompShareCli, CompShareCliError
        from bench.hpc.drivers.compshare.policy import (
            extract_verified_instance_id,
            instance_requires_cleanup,
            matches_ownership_marker,
        )

        cli = CompShareCli()
        try:
            items = cli.instance_list(all=True)
            if not isinstance(items, list):
                raise RuntimeError("provider instance list result is not a list")
            active: list[str] = []
            for item in items:
                if not isinstance(item, dict):
                    raise RuntimeError(
                        "provider instance list contained a non-object record"
                    )
                # Keep CLI qualification on exactly the same ownership and
                # cleanup policy as the trusted CompShare manager.  In
                # particular, STOPPED/FAILED/unknown are not safe deletion
                # states, and an owned record without an ID must fail closed.
                if not matches_ownership_marker(item):
                    continue
                try:
                    inst_id = extract_verified_instance_id(item)
                except ValueError as exc:
                    raise RuntimeError(str(exc)) from exc
                if not inst_id:
                    raise RuntimeError(
                        "owned provider record has no verified instance_id"
                    )
                if instance_requires_cleanup(item.get("status")):
                    active.append(inst_id)
            return active
        except CompShareCliError as exc:
            raise RuntimeError(f"Cannot verify live cloud zero-orphan status: {exc}") from exc

    verdict = verify_and_derive_qualification(
        receipt,
        expected_profile=prof,
        site_receipts_dir=site_receipts_dir,
        active_instances_checker=live_cloud_instances_checker if prof.routes.get("gpu") else None,
        require_live_check=bool(prof.routes.get("gpu")),
        trust_store=trust_store,
        trusted_site_profiles=trusted_site_profiles,
        receipt_dir=receipt_path.parent,
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
    from bench.hpc.compute_profile import ComputeProfile

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
        prog="bench",
        description="thin operator/user entry — orchestrates existing modules only",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    suite = sub.add_parser("suite", help="inspect or validate an external paper suite")
    suite_sub = suite.add_subparsers(dest="suite_command", required=True)
    for name in ("inspect", "validate"):
        suite_cmd = suite_sub.add_parser(name)
        suite_cmd.add_argument("--root", type=Path, required=True)

    sub.add_parser(
        "run",
        help="run one case with Candidate Docker and automatic local verification",
        add_help=False,
    )

    setup = sub.add_parser("setup", help="environment and repo sanity")
    setup.add_argument("--quiet", "--report-only", dest="quiet", action="store_true", help="no next-steps text")

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
    comp_qual.add_argument(
        "--trust-store",
        type=Path,
        default=None,
        help="explicit operator-configured qualification trust store (required)",
    )
    comp_qual.add_argument(
        "--site-profile-registry",
        "--trusted-site-profile-registry",
        dest="site_profile_registry",
        type=Path,
        default=None,
        help="explicit trusted SiteProfile JSON/TOML file or directory (required)",
    )

    # Runtime subcommands
    p_rt = sub.add_parser("runtime", help="Runtime recipes, locks, and provenance")
    rt_subs = p_rt.add_subparsers(dest="runtime_command")
    rt_subs.add_parser("audit", help="Audit runtime provenance and recipe locks")
    rt_list = rt_subs.add_parser("list", help="List operator-selectable runtime families")
    rt_list.add_argument("--catalog", type=Path, default=None)
    rt_inspect = rt_subs.add_parser("inspect", help="Inspect one runtime family")
    rt_inspect.add_argument("runtime_id")
    rt_inspect.add_argument("--catalog", type=Path, default=None)
    p_rt_build = rt_subs.add_parser("build", help="Build container runtime recipe")
    p_rt_build.add_argument("recipe", help="Recipe name to build")

    # Doctor alias
    doctor = sub.add_parser("doctor", help="Environment diagnostics")
    doctor.add_argument("--quiet", "--report-only", dest="quiet", action="store_true")
    doctor.add_argument("--json", action="store_true")

    mvp = sub.add_parser("mvp", help="direct-Claude-Code MVP run workflow")
    mvp_sub = mvp.add_subparsers(dest="mvp_command", required=True)
    mvp_export = mvp_sub.add_parser("export", help="export a public Candidate workspace")
    mvp_export.add_argument("--case", required=True)
    mvp_export.add_argument("--out", type=Path, required=True)
    mvp_export.add_argument("--lock", type=Path, default=None)
    mvp_check = mvp_sub.add_parser("check", help="verify immutable public workspace bytes")
    mvp_check.add_argument("--bundle", type=Path, required=True)
    mvp_check.add_argument("--lock", type=Path, default=None)
    mvp_freeze = mvp_sub.add_parser("freeze", help="seal only the Candidate final directory")
    mvp_freeze.add_argument("--bundle", type=Path, required=True)
    mvp_freeze.add_argument("--out", type=Path, required=True)
    mvp_freeze.add_argument("--lock", type=Path, default=None)
    mvp_eval = mvp_sub.add_parser("evaluate", help="run the hidden isolated verifier")
    mvp_eval.add_argument("--case", required=True)
    mvp_eval.add_argument("--submission", type=Path, required=True)
    mvp_eval.add_argument("--logs", type=Path, required=True)
    mvp_eval.add_argument("--image", default=None)
    mvp_eval.add_argument("--run-id", default=None)
    mvp_compute = mvp_sub.add_parser(
        "compute-check", help="validate a CPU/GPU operator handoff request"
    )
    mvp_compute.add_argument("--bundle", type=Path, required=True)
    mvp_compute.add_argument("--request", type=Path, required=True)
    mvp_compute.add_argument("--lock", type=Path, default=None)

    pilot = sub.add_parser("pilot", help="run a case with Claude Code inside Candidate Docker")
    pilot_sub = pilot.add_subparsers(dest="pilot_command")
    pilot_start = pilot_sub.add_parser("start", help="start a Candidate run")
    pilot_start.add_argument("case")
    pilot_start.add_argument("--out", type=Path, default=None)
    pilot_start.add_argument(
        "--config", type=Path, default=None,
        help="optional secret-free TOML Candidate config (model/image/compute route)",
    )
    pilot_start.add_argument("--model", dest="model_override", default=None)
    pilot_start.add_argument("--candidate-image", dest="image_override", default=None)
    pilot_start.add_argument("--sidecar-image", dest="sidecar_image_override", default=None)
    pilot_start.add_argument("--agent-profile", dest="agent_profile_name", default=None)
    pilot_start.add_argument("--formal", action="store_true")
    pilot_start.add_argument(
        "--formal-admission", type=Path, default=None,
        help="coordinator-signed admission token required with --formal",
    )
    pilot_status = pilot_sub.add_parser("status", help="show durable run state")
    pilot_status.add_argument("run_dir", type=Path)
    pilot_resume = pilot_sub.add_parser("resume", help="resume a run after operator return")
    pilot_resume.add_argument("run_dir", type=Path)
    pilot_eval = pilot_sub.add_parser("evaluate", help="freeze and run hidden verifier")
    pilot_eval.add_argument("run_dir", type=Path)
    pilot_import = pilot_sub.add_parser("import-results", help="import trusted Operator results")
    pilot_import.add_argument("run_dir", type=Path)
    pilot_import.add_argument("source", type=Path)
    pilot_import.add_argument(
        "--receipt", type=Path, default=None,
        help="signed operator handoff manifest (required for external/formal runs)",
    )

    return parser


def _run_case(argv: list[str]) -> int:
    """Run the Candidate lifecycle with publication-friendly defaults."""
    parser = argparse.ArgumentParser(prog="bench run")
    parser.add_argument("case")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--agent-profile", default=None)
    parser.add_argument("--candidate-image", default=None)
    parser.add_argument("--sidecar-image", default=None)
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--formal-admission", type=Path, default=None)
    parser.add_argument("--no-evaluate", action="store_true")
    args = parser.parse_args(argv)

    from bench.pilot import MvpError, evaluate, start

    if args.out is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(args.case).name).strip("-") or "case"
        args.out = Path.cwd() / "runs" / f"{stamp}-{slug}"
    try:
        state = start(
            args.case,
            run_dir=args.out,
            formal=args.formal,
            config_path=args.config,
            model_override=args.model,
            image_override=args.candidate_image,
            sidecar_image_override=args.sidecar_image,
            agent_profile_name=args.agent_profile,
            formal_admission_path=args.formal_admission,
        )
        if state.get("state") == "CANDIDATE_COMPLETE" and not args.no_evaluate:
            result = evaluate(args.out)
        else:
            result = {"state": state, "run_dir": str(args.out)}
            if state.get("state") == "COMPUTE_REQUIRED":
                result["next_action"] = (
                    "dispatch the sealed operator request, import its signed results, "
                    f"then run: bench pilot resume {args.out}"
                )
    except MvpError as exc:
        raise CliError(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


def _runtime(argv: list[str]) -> int:
    from bench.cli.commands import handle_runtime_cmd
    return handle_runtime_cmd(argv)


def main(argv: list[str] | None = None) -> int:
    # A parent shell (and some PTY launchers) may inherit SIGINT=SIG_IGN.
    # Install explicit handlers before dispatching into asyncio.run so one
    # Ctrl-C reaches the Candidate cleanup path.  SIGTERM uses the same
    # KeyboardInterrupt path; cleanup code is responsible for its one
    # bounded teardown and is not re-entered by a second signal handler.
    def _terminate_as_interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, _terminate_as_interrupt)
    except ValueError:
        # ``main`` is also imported by a few embedding tools; Python only
        # permits signal installation from the main thread.  The executable
        # entry point always runs here, while library callers retain normal
        # exception semantics in worker threads.
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        build_parser().parse_args(argv or ["--help"])
        return 0
    command = argv[0]
    try:
        if command == "suite":
            from bench.suite import inspect_suite, validate_suite
            args = build_parser().parse_args(argv)
            report = (
                inspect_suite(args.root)
                if args.suite_command == "inspect"
                else validate_suite(args.root)
            )
            print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
            return 0 if args.suite_command == "inspect" or report["ready"] else 2
        if command == "run":
            return _run_case(argv[1:])
        if command in ("setup", "doctor"):
            parser = argparse.ArgumentParser(prog=f"bench {command}")
            parser.add_argument("--quiet", "--report-only", dest="quiet", action="store_true")
            parser.add_argument("--json", action="store_true")
            args = parser.parse_args(argv[1:])
            report_only = args.quiet or args.json
            return _setup(report_only=report_only)
        if command == "runtime":
            if len(argv) > 1 and argv[1] in {"list", "inspect"}:
                from bench.runtime_catalog import inspect_runtime, load_catalog
                args = build_parser().parse_args(argv)
                try:
                    report = (
                        load_catalog(args.catalog)
                        if args.runtime_command == "list"
                        else inspect_runtime(args.runtime_id, args.catalog)
                    )
                except ValueError as exc:
                    raise CliError(str(exc)) from exc
                print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
                return 0
            return _runtime(argv[1:])
        if command == "mvp":
            from bench.core.packager import PackageError
            from bench.core.quarantine import QuarantineError
            from bench.mvp import (
                MvpError,
                check_bundle,
                evaluate_submission,
                export_case,
                freeze_submission,
                seal_to_dict,
                validate_compute_request,
            )

            args = build_parser().parse_args(argv)
            try:
                if args.mvp_command == "export":
                    payload = export_case(args.case, args.out, lock_path=args.lock)
                elif args.mvp_command == "check":
                    payload = check_bundle(args.bundle, lock_path=args.lock)
                elif args.mvp_command == "freeze":
                    payload = seal_to_dict(
                        freeze_submission(args.bundle, args.out, lock_path=args.lock)
                    )
                elif args.mvp_command == "evaluate":
                    payload = evaluate_submission(
                        args.case,
                        args.submission,
                        args.logs,
                        image=args.image,
                        run_id=args.run_id,
                    )
                elif args.mvp_command == "compute-check":
                    payload = validate_compute_request(
                        args.bundle, args.request, lock_path=args.lock
                    )
                else:  # pragma: no cover - argparse owns this boundary
                    raise MvpError(f"unknown mvp command: {args.mvp_command}")
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            except (MvpError, PackageError, QuarantineError) as exc:
                raise CliError(str(exc)) from exc
        if command == "pilot":
            from bench.pilot import MvpError, evaluate as pilot_evaluate, import_results as pilot_import_results, resume as pilot_resume, start as pilot_start, status as pilot_status
            pilot_argv = list(argv)
            if len(pilot_argv) < 2 or pilot_argv[1] not in {"start", "status", "resume", "evaluate", "import-results"}:
                pilot_argv.insert(1, "start")
            args = build_parser().parse_args(pilot_argv)
            try:
                if args.pilot_command == "start":
                    out = args.out
                    if out is None:
                        import tempfile
                        out = Path(tempfile.mkdtemp(prefix=f"bench-{args.case}-"))
                    result = pilot_start(
                        args.case, run_dir=out, formal=args.formal,
                        config_path=args.config, model_override=args.model_override,
                        image_override=args.image_override,
                        sidecar_image_override=args.sidecar_image_override,
                        agent_profile_name=args.agent_profile_name,
                        formal_admission_path=args.formal_admission,
                    )
                elif args.pilot_command == "status":
                    result = pilot_status(args.run_dir)
                elif args.pilot_command == "resume":
                    result = pilot_resume(args.run_dir)
                elif args.pilot_command == "evaluate":
                    result = pilot_evaluate(args.run_dir)
                else:
                    result = pilot_import_results(args.run_dir, args.source, receipt=args.receipt)
            except MvpError as exc:
                raise CliError(str(exc)) from exc
            print(json.dumps(result, indent=2, sort_keys=True, default=str))
            return 0
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
                return _compute_qualify(
                    args.profile,
                    args.receipt,
                    args.site_receipts_dir,
                    args.trust_store,
                    args.site_profile_registry,
                )
            build_parser().error("compute needs a subcommand: configure | validate | qualify")
        build_parser().error(f"unknown command {command!r}")
    except CliError as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("bench: interrupted; Candidate resources were asked to shut down", file=sys.stderr)
        return 130
    raise SystemExit("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
