#!/usr/bin/env python3
"""``bench hpc qualify`` — thin operator wrapper around the trusted driver.

Runs exactly the qualification phases a named case needs, in the fixed
order canary (creates/seals the site receipt) then the separately
authorized submit phases — cancel probe / runtime gates (each merges into
the receipt) — and never reruns a capability that already derives PASS.

    python scripts/qualification/qualify_case.py \
        --case /path/to/paper/cases/004 \
        --site site-v1 --profile /path/to/cluster_profile.toml

First version is a THIN wrapper (spec stage 3): every submission /
settlement / evidence-merging step stays in
``scripts/infra/qualify_hpc_dispatcher.py`` (frozen at
``qualification-candidate-site-v2``).  This script only:
  * resolves the case's EFFECTIVE ``[hpc.qualification]`` requires
    (declared ∪ registry auto-derivation);
  * derives each required capability OFFLINE from the site receipt
    (verdict-free, re-computed every run);
  * delegates the NOT_RUN phases to the driver as subprocesses and
    HALTS on any FAIL or broken anchor (never reruns a failing gate);
  * persists per-run state (stamp, since window, last capabilities) under
    the site evidence dir so an interrupted canary can be recovered via
    ``--phase resume`` on the driver.

Runtime gates (ai2kit / cp2k) and the explicit cancel probe (P4 step 7,
--phase cancel) need explicit ``--authorized``, exactly like the
underlying driver phases — this wrapper never authorizes silently.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DRIVER = ROOT / "scripts" / "infra" / "qualify_hpc_dispatcher.py"

# Which driver phase proves which capability (spec §3/§5b matrix).
CANARY_CAPABILITIES = ("dispatcher.cpu", "dispatcher.gpu", "runtime.matclaw-gpu")
CAPABILITY_PHASE = {
    "dispatcher.cpu": "canary",
    "dispatcher.gpu": "canary",
    "dispatcher.cancel": "cancel",
    "runtime.matclaw-gpu": "canary",
    "runtime.ai2kit": "ai2kit",
    "runtime.cp2k": "cp2k",
}

DEFAULT_RUNTIME_LOCK = (
    "runtimes/locks/matclaw-cips-runtime.lock.json"
)
DEFAULT_AI2KIT_LOCK = "runtimes/locks/ai2kit-runtime.lock.json"
DEFAULT_CP2K_LOCK = "runtimes/locks/cp2k-runtime.lock.json"

# First character must be alphanumeric: '.' and '..' are path components,
# not site names (same hardening as the driver — both entry points reject).
_SITE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


class QualifyPlanError(RuntimeError):
    """The case cannot be qualified by this wrapper: planning failed closed."""


def site_root(site_name: str) -> Path:
    if not _SITE_NAME_RE.match(site_name):
        raise QualifyPlanError(f"unsafe --site name: {site_name!r}")
    configured = os.environ.get("BENCH_HPC_QUALIFICATION_ROOT")
    base = (Path(configured).expanduser() if configured
            else ROOT / "runs" / "hpc-qualification")
    return base.resolve() / site_name


def case_dir_for(name: str) -> Path:
    """Resolve ``--case`` as an explicit existing external case path."""
    candidate = Path(name).expanduser()
    if candidate.is_dir() and any(
        (candidate / manifest).is_file() for manifest in ("case.toml", "task.toml")
    ):
        return candidate.resolve()
    raise QualifyPlanError(
        f"--case must be an existing external case directory containing "
        f"case.toml or task.toml (received {name!r}); numeric/id aliases and "
        "repository case discovery are retired"
    )


def derive(receipt_path: Path, profile_path: Path) -> dict:
    """Offline derivation: capabilities + consistency, mirroring the driver.

    A missing receipt derives an EMPTY view (every capability NOT_RUN-ish);
    the plan then runs the canary first, which creates the receipt the
    runtime gates merge into.
    """
    import json as _json

    from bench.experiments.qualification_receipt import verify_receipt

    if not receipt_path.is_file():
        return {"consistent": False, "present": False,
                "derived": {"capabilities": {}}}
    receipt = _json.loads(receipt_path.read_text(encoding="utf-8"))
    result = verify_receipt(
        receipt,
        root=ROOT,
        receipt_dir=receipt_path.parent,
        profile_path=profile_path,
    )
    result["present"] = True
    return result


def plan_phases(requires: list[str], capabilities: dict, *, receipt_present: bool) -> list[str]:
    """Ordered driver phases to run for THIS case, or fail closed.

    * unknown required capability → error (a typo must never read as done);
    * any required capability already FAIL → error ("FAIL 时 halt", never a
      blind rerun);
    * NOT_RUN (or absent receipt) caps → their phases; runtime gates need a
      consistent receipt first, so a missing receipt prepends canary even for
      a runtime-only case.
    """
    unknown = sorted(set(requires) - set(CAPABILITY_PHASE))
    if unknown:
        raise QualifyPlanError(
            f"case requires unknown capabilities {unknown}; "
            f"known: {sorted(CAPABILITY_PHASE)}"
        )
    failed = [c for c in requires if capabilities.get(c) == "FAIL"]
    if failed:
        raise QualifyPlanError(
            f"case requires already derive FAIL: {failed} — halting; "
            f"fix the evidence before rerunning, never rerun a failing gate"
        )
    need = [c for c in requires if capabilities.get(c) != "PASS"]
    if not need:
        return []

    phases: list[str] = []
    dispatcher = [c for c in need if c in CANARY_CAPABILITIES]
    runtime = [c for c in need if c not in CANARY_CAPABILITIES]
    if dispatcher or (runtime and not receipt_present):
        phases.append("canary")  # creates the receipt runtime gates merge into
    for cap in need:
        phase = CAPABILITY_PHASE[cap]
        if phase not in phases:
            phases.append(phase)
    return phases


def driver_argv(
    phase: str,
    *,
    profile_path: Path,
    site_name: str,
    runtime_lock: str,
    ai2kit_lock: str,
    cp2k_lock: str,
    authorized: bool,
    stamp: str | None = None,
    gpu_job_id: str | None = None,
    since: str | None = None,
) -> list[str]:
    argv = [
        sys.executable, str(DRIVER),
        "--profile", str(profile_path),
        # The driver's evidence dir must be the SAME site dir this wrapper
        # reads receipts from — without --site the driver would default to
        # site-v1 while we derive from <site>.
        "--site", site_name,
        "--phase", phase,
        "--runtime-lock", str(ROOT / runtime_lock),
    ]
    if phase == "ai2kit":
        argv += ["--ai2kit-lock", str(ROOT / ai2kit_lock)]
    if phase == "cp2k":
        argv += ["--cp2k-lock", str(ROOT / cp2k_lock)]
    if phase in ("ai2kit", "cp2k", "cancel"):
        if not authorized:
            raise QualifyPlanError(
                f"phase {phase!r} needs explicit --authorized "
                f"(no such submission scope is on file)"
            )
        argv.append("--authorized")
    if phase == "resume":
        if stamp:
            argv += ["--stamp", stamp]
        if gpu_job_id:
            argv += ["--gpu-job-id", gpu_job_id]
        if since:
            argv += ["--since", since]
    return argv


def _verbose_resolved_tiers(profile: dict) -> list[str]:
    """Slurm parameters the site profile's resource-class resolution yields
    for each tier (spec stage 3 ``--verbose``: shows what will be submitted)."""
    from bench.hpc.site_profile import HpcSiteProfile

    site = HpcSiteProfile.from_cluster_config(profile)
    lines = []
    for tier in ("cpu", "gpu"):
        try:
            wl = site.resolve_workload(tier)
        except Exception as exc:  # noqa: BLE001 — offline preview, fail-labeled
            lines.append(f"  {tier}: resolve failed: {exc}")
            continue
        lines.append(
            f"  {tier}: partition={wl.partition} gres={wl.gres} "
            f"account={wl.account} qos={wl.qos} cpus={wl.max_cpus} "
            f"mem={wl.max_memory_gb}G walltime={wl.max_walltime_minutes}min "
            f"({wl.mapping_note})"
        )
    return lines


def _state_path(site: Path) -> Path:
    return site / "qualify-state.json"


def write_state(site: Path, payload: dict) -> None:
    site.mkdir(parents=True, exist_ok=True)
    _state_path(site).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_state(site: Path) -> dict:
    path = _state_path(site)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _canary_stamp(site: Path) -> str | None:
    """The newest run-gpu-* chain stamp under the site evidence dir, if any."""
    stamps = []
    for path in site.glob("run-gpu-nvidia-probe-containment-*"):
        if path.is_dir():
            stamps.append(path.stat().st_mtime_ns)
    if not stamps:
        return None
    newest = max(
        (p for p in site.glob("run-gpu-nvidia-probe-containment-*")
         if p.is_dir() and p.stat().st_mtime_ns == max(stamps)),
    )
    return newest.name.split("containment-", 1)[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--case", required=True,
                        help="explicit external case directory containing case.toml")
    parser.add_argument("--site", default="site-v1",
                        help="qualification site evidence name "
                             "(runs/hpc-qualification/<site>)")
    parser.add_argument("--profile", default=str(ROOT / "scripts/hpc/cluster_profile.toml"),
                        help="private cluster profile (operator data)")
    parser.add_argument("--runtime-lock", default=DEFAULT_RUNTIME_LOCK)
    parser.add_argument("--ai2kit-lock", default=DEFAULT_AI2KIT_LOCK)
    parser.add_argument("--cp2k-lock", default=DEFAULT_CP2K_LOCK)
    parser.add_argument("--authorized", action="store_true",
                        help="explicitly authorize separately-scoped submit "
                             "phases (needed when the plan includes "
                             "ai2kit/cp2k/cancel)")
    parser.add_argument("--resume", action="store_true",
                        help="first recover an interrupted canary via the "
                             "driver's --phase resume (never resubmits)")
    parser.add_argument("--stamp", default=None,
                        help="interrupted chain stamp for --resume "
                             "(auto-detected when omitted)")
    parser.add_argument("--gpu-job-id", default=None,
                        help="scheduler id of the queued GPU canary (--resume)")
    parser.add_argument("--since", default=None,
                        help="sacct start window for --resume (defaults to "
                             "state.since, then today)")
    parser.add_argument("--verbose", action="store_true",
                        help="print the plan + the resolved Slurm parameters")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute the plan and exit without running phases")
    args = parser.parse_args(argv)

    from bench.experiments.qualification_receipt import _case_qualification_requires

    site = site_root(args.site)
    case_dir = case_dir_for(args.case)
    case_name = case_dir.name
    requires = list(_case_qualification_requires(case_dir))
    profile_path = ROOT / args.profile

    state = load_state(site)
    today = datetime.now(timezone.utc).date().isoformat()

    receipt_path = site / "receipt.json"
    if args.resume:
        if receipt_path.is_file():
            print(
                f"[qualify] --resume but a receipt already exists at "
                f"{receipt_path}; nothing to resume",
                file=sys.stderr,
            )
            return 1
        argv_resume = driver_argv(
            "resume",
            profile_path=profile_path,
            site_name=args.site,
            runtime_lock=args.runtime_lock,
            ai2kit_lock=args.ai2kit_lock,
            cp2k_lock=args.cp2k_lock,
            authorized=args.authorized,
            stamp=args.stamp or state.get("stamp"),
            gpu_job_id=args.gpu_job_id,
            since=args.since or state.get("since") or today,
        )
        print(f"[qualify] recovering interrupted canary via --phase resume …")
        if not args.dry_run and subprocess.run(argv_resume).returncode != 0:
            return 1

    view = derive(receipt_path, profile_path)
    if view["present"] and not view["consistent"]:
        print(
            f"[qualify] existing receipt is INCONSISTENT — halting: "
            + "; ".join((view.get("problems") or [])[:3]),
            file=sys.stderr,
        )
        return 1

    caps = view["derived"].get("capabilities") or {}
    plan = plan_phases(requires, caps, receipt_present=view["present"])

    if args.verbose or args.dry_run:
        print(f"[qualify] case={case_name} requires={requires}")
        for cap in requires:
            print(f"  {cap}: {caps.get(cap, '—')}")
        print(f"[qualify] plan: {plan or '(nothing to run — all required PASS)'}")
        if args.verbose:
            for line in _verbose_resolved_tiers(_load_profile(profile_path)):
                print(line)

    if args.dry_run:
        return 0

    if not plan:
        print(f"[qualify] all {len(requires)} required capabilities already PASS")
        return 0

    if any(p in ("ai2kit", "cp2k", "cancel") for p in plan) and not args.authorized:
        print(
            "[qualify] the plan includes a separately-authorized submit phase "
            f"({sorted(set(plan) & {'ai2kit', 'cp2k', 'cancel'})}); rerun with "
            "--authorized to authorize it explicitly",
            file=sys.stderr,
        )
        return 2

    since_window = state.get("since") or today
    for phase in plan:
        argv_phase = driver_argv(
            phase,
            profile_path=profile_path,
            site_name=args.site,
            runtime_lock=args.runtime_lock,
            ai2kit_lock=args.ai2kit_lock,
            cp2k_lock=args.cp2k_lock,
            authorized=args.authorized,
        )
        print(f"[qualify] running --phase {phase}: "
              f"{' '.join(argv_phase)}", flush=True)
        rc = subprocess.run(argv_phase).returncode
        if rc != 0:
            print(f"[qualify] --phase {phase} failed (rc={rc}); halting",
                  file=sys.stderr)
            return rc
        view = derive(receipt_path, profile_path)
        caps = view["derived"].get("capabilities") or {}
        done = [c for c in requires if caps.get(c) == "PASS"]
        print(f"[qualify] after --phase {phase}: {len(done)}/{len(requires)} required PASS")
        failed = [c for c in requires if caps.get(c) == "FAIL"]
        if failed:
            print(f"[qualify] required capabilities now derive FAIL: {failed} — halting",
                  file=sys.stderr)
            return 1
        write_state(site, {
            "case": case_name,
            "site": args.site,
            "requires": requires,
            "profile": str(profile_path),
            "phases_run": plan[:plan.index(phase) + 1],
            "capabilities": caps,
            "stamp": _canary_stamp(site),
            "since": since_window,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    view = derive(receipt_path, profile_path)
    caps = view["derived"].get("capabilities") or {}
    missing = [c for c in requires if caps.get(c) != "PASS"]
    if view["present"] and view["consistent"] and not missing:
        print(f"[qualify] case {case_name} requires fully satisfied: {requires}")
        return 0
    print(
        f"[qualify] case {case_name} NOT qualified: "
        f"consistent={view['present'] and view['consistent']} "
        f"unmet={missing}",
        file=sys.stderr,
    )
    return 1


def _load_profile(profile_path: Path) -> dict:
    import tomllib

    return tomllib.loads(profile_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main())
