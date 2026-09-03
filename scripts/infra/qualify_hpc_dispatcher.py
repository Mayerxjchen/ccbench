#!/usr/bin/env python3
"""Authorized real-site qualification for the HpcDispatcher stack.

Phases (each fail-closed):

  preflight  — no submission: SSH route, node arch, partition existence,
               Apptainer binary, remote SIF presence + digest vs the frozen
               runtime lock.

  canary     — REQUIRES EXPLICIT USER AUTHORIZATION (renewed scope after
               cpu-ACL restoration: exactly TWO short jobs — no CP2K, no
               image transfer, nothing else):
                 1. CPU job on the NATIVE cpu partition (gpus=0):
                    echo/hostname + in-container containment probes;
                 2. GPU job (--gres=gpu:1): nvidia-smi device probe +
                    containment probes.
               Trusted Infra temporarily places external sentinels (remote
               HOME, remote-home credential, other-run directory); the
               in-container probe must find NONE of them, /workspace must be
               read-write, and settlement must leave no orphan jobs.

The receipt is verdict-free: it embeds only evidence primitives bound to
independently recomputable anchors (runtime lock, SiteProfile digest, code
identity, source commit, hash-chained audit ledger, sacct accounting lines,
settlement report digests, artifact bytes).  ``qualification_status`` /
``formal_qualified`` are derived at verification time by
``dftworld_bench.experiments.qualification_receipt.verify_receipt`` — never
declared by this producer and never trusted as input.

  cp2k       — REQUIRES ITS OWN EXPLICIT USER AUTHORIZATION (``--authorized``;
               the canary scope explicitly excluded CP2K) plus a cp2k runtime
               lock (``--cp2k-lock``: own SIF digest, pinned binary, expected
               version).  Runs one deterministic H2O ENERGY job on the native
               cpu partition in the pinned container, fetches input/output as
               manifest-anchored artifacts, and MERGES the evidence into the
               existing consistent receipt — which then derives
               qualification_status=PASS (formal_qualified=true) when every
               anchor holds.

  ai2kit     — REQUIRES EXPLICIT USER AUTHORIZATION (``--authorized``; no
               ai2kit canary authorization is on file) plus an ai2kit runtime
               lock (``--ai2kit-lock``: own SIF digest + ``software.ai2_kit``
               version, ``ai2kit-runtime-v1`` — the derived-runtime lock
               whose SIF is the trusted CP2K SIF rootfs, per the Architecture
               Freeze §4; AI2Kit is a runtime, not a control plane).
               Runs one CPU job in the pinned runtime container that
               imports ai2_kit, asserts the version equals the lock (via
               importlib.metadata — the package ships no __version__),
               round-trips
               a minimal workflow config through the workspace, and passes the
               standard containment probes; fetches stdout/stderr, and MERGES
               ``evidence.ai2kit_gate`` into the existing consistent receipt —
               ``runtime.ai2kit`` derives PASS iff the lock anchor and the
               full job record hold.

  cancel     — REQUIRES ITS OWN EXPLICIT USER AUTHORIZATION (``--authorized``;
               P4 step 7, the teardown half of the dispatcher.cpu proof): one
               longer sleep on the native cpu route in the pinned runtime,
               cancelled only after it reaches RUNNING, through the
               dispatcher's cancel operation (gateway token + ownership +
               adapter scancel — never a raw scancel by this driver).  Settles
               terminal CANCELLED, runs the post-cancel orphan sweep, and
               MERGES ``evidence.cancel_gate`` into the existing consistent
               receipt — ``dispatcher.cancel`` derives PASS iff the gateway
               cancel event, CANCELLED settlement, allocated-then-killed
               accounting and empty sweep all hold.  Kept out of the canary on
               purpose: the durable ``--phase resume`` path stays submit-free.

  verify     — replay every anchor offline and derive the status; prints the
               derivation result and exits non-zero on any broken anchor.

  resume     — complete an interrupted dual-canary collection WITHOUT
               submitting anything new: wait for the already-queued GPU job to
               reach a scheduler-terminal state, rehydrate the trusted-side
               ownership from the durable SUBMIT_INTENT marker, settle through
               the frozen gateway API on the same audit chain, reconstruct the
               CPU record read-only, clean the interrupted stamp's sentinels
               last, and seal the same evidence envelope a fresh canary
               produces.  (The supervisor-died-after-durable-submit incident
               shape; see docs/superpowers/specs/2026-09-02-bench-hpc-resume
               -design.md.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dftworld_bench.experiments.qualification_receipt import (  # noqa: E402
    CP2K_INPUT_NAME,
    CP2K_INPUT_TEXT,
    CP2K_OUTPUT_NAME,
    MARKER_CONTAINMENT,
    MARKER_GPU,
    build_receipt_body,
    compute_settlement_digest,
    parse_cp2k_output,
    parse_probe_stdout,
    verify_receipt,
)
from scripts.ablation.transport.slurm_transport import (  # noqa: E402
    JobState,
    SshConfig,
    SshSlurmTransport,
    normalize_state,
)

# Per-site evidence root, REBOUND by main() from --site (qualify_case passes
# it through).  site-v1 stays the default so existing fixtures and operator
# muscle memory keep working; site-v3 re-seals land under their own dir and
# never touch old evidence (retain, never overwrite or delete).
DEFAULT_SITE = "site-v1"
CANARY_ROOT = ROOT / "evidence" / "hpc-dispatcher" / "qualification" / DEFAULT_SITE
RECEIPT_PATH = CANARY_ROOT / "receipt.json"

# First character must be alphanumeric: "." and ".." are path components,
# not site names, even though every character in them is otherwise legal.
_SITE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def set_site(site_name: str) -> None:
    """Rebind the module-level evidence paths for one qualification run."""
    global CANARY_ROOT, RECEIPT_PATH
    if not _SITE_NAME_RE.match(site_name):
        raise QualifyError(f"unsafe --site name: {site_name!r}")
    CANARY_ROOT = (
        ROOT / "evidence" / "hpc-dispatcher" / "qualification" / site_name
    )
    RECEIPT_PATH = CANARY_ROOT / "receipt.json"

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT", "LOST"}


class QualifyError(RuntimeError):
    """A qualification step failed; the receipt must not claim success."""


def _load_profile(path: Path) -> dict:
    import tomllib

    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def _ssh(profile: dict, command: str, timeout: int = 120, *, retries: int = 4) -> str:
    ssh_cfg = profile["ssh"]
    options = {k: str(v) for k, v in (ssh_cfg.get("options") or {}).items()}
    # One persistent multiplexed connection instead of a fresh handshake per
    # poll: hundreds of 10-second-interval handshakes look exactly like an
    # attack to fail2ban-style guards on the login node.
    options.setdefault("ControlMaster", "auto")
    options.setdefault("ControlPath", "/tmp/dftworld-qual-cm-%C")
    options.setdefault("ControlPersist", "600")
    config = SshConfig(
        host=ssh_cfg["host"],
        user=ssh_cfg.get("user") or None,
        port=int(ssh_cfg.get("port") or 22) or None,
        options=options,
    )
    argv = config.to_ssh_argv(command)
    last_err = ""
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            last_err = f"timeout after {timeout}s"
        else:
            if proc.returncode == 0:
                return proc.stdout
            # ssh itself reports connection/protocol failures as 255; those
            # are transient site/network blips, not command failures.
            if proc.returncode != 255:
                raise QualifyError(
                    f"remote command failed ({command!r}, "
                    f"rc={proc.returncode}): "
                    f"{proc.stderr.strip() or proc.stdout.strip()}"
                )
            last_err = proc.stderr.strip() or "rc=255"
        if attempt < retries:
            delay = min(60.0, 5.0 * (2 ** attempt))
            print(f"    [ssh] transient failure ({last_err[:80]}); "
                  f"retry {attempt + 1}/{retries} in {delay:.0f}s", flush=True)
            time.sleep(delay)
    raise QualifyError(
        f"remote command failed after {retries + 1} attempts ({command!r}): "
        f"{last_err}"
    )


# -- phase: preflight ---------------------------------------------------------


def preflight(profile: dict, lock: dict) -> dict:
    sif_path = lock["runtime"]["sif_path_remote"]
    sif_digest = lock["runtime"]["sif_sha256"]
    checks: dict[str, object] = {}

    checks["ssh_route"] = _ssh(profile, "hostname").strip()
    arch = _ssh(profile, "uname -m").strip()
    expected_arch = profile["runtime"]["expected_node_arch"]
    if arch != expected_arch:
        raise QualifyError(f"node arch {arch!r} != expected {expected_arch!r}")
    checks["node_arch"] = arch

    partition = profile["slurm"]["partition"]
    # ADR 2026-09-03 §5: one queue per profile.  A comma list (full-GPU plus
    # MIG) would mix compute classes inside one site identity — SiteProfile
    # refuses it too; preflight refuses before any submission attempt.
    def _known_queue(name: str, label: str) -> str:
        if "," in name:
            raise QualifyError(
                f"[slurm] {label} {name!r} names multiple queues; full-GPU "
                "and MIG need separate cluster profiles and separate "
                "qualifications"
            )
        sinfo = _ssh(profile, "sinfo -h -o '%R'")
        known = {ln.strip() for ln in sinfo.splitlines() if ln.strip()}
        if name not in known:
            raise QualifyError(f"queue {name!r} ({label}) not known to sinfo")
        return name

    _known_queue(partition, "partition")
    checks["partition"] = partition

    cpu_partition = profile["slurm"].get("cpu_partition")
    if cpu_partition:
        _known_queue(cpu_partition, "cpu_partition")
        checks["cpu_partition"] = cpu_partition

    apptainer = profile["paths"]["apptainer"]
    _ssh(profile, f"test -x {shlex.quote(apptainer)}")
    checks["apptainer"] = apptainer

    remote_digest = _ssh(
        profile, f"sha256sum {shlex.quote(sif_path)} | cut -d' ' -f1", timeout=600
    ).strip()
    if remote_digest != sif_digest:
        raise QualifyError(
            f"SIF digest mismatch: lock={sif_digest} remote={remote_digest}"
        )
    checks["sif"] = {"path": sif_path, "sha256": sif_digest}

    root = profile["paths"]["remote_root"]
    _ssh(profile, f"mkdir -p {shlex.quote(root)} && test -d {shlex.quote(root)}")
    checks["remote_root"] = root

    home = _ssh(profile, "echo $HOME").strip()
    checks["remote_home"] = home

    print(json.dumps({"phase": "preflight", "ok": True, "checks": checks}, indent=2))
    return checks


# -- dispatcher wiring (production SiteProfile resolver) ----------------------


def _build_site_profile(profile: dict) -> "HpcSiteProfile":
    """Bridge cluster_profile.toml → HpcSiteProfile (shared factory)."""
    from dftworld_bench.hpc.site_profile import HpcSiteProfile

    return HpcSiteProfile.from_cluster_config(profile)


def _open_session(
    site: "HpcSiteProfile",
    resolved: "ResolvedResource",
    profile: dict,
    lock: dict,
    run_id: str,
):
    """One dispatcher session over the real transport (trusted composition).

    Uses the production SiteProfile resolver for account/partition/QOS/GRES.
    SSH transport config still comes from the private cluster_profile (the
    SiteProfile schema does not carry SSH options).
    """
    from dftworld_bench.hpc.adapters.slurm import SlurmAdapter
    from dftworld_bench.hpc.audit import GatewayAudit
    from dftworld_bench.hpc.dispatcher import HpcDispatcher
    from dftworld_bench.hpc.gateway_runtime import GatewayRuntime

    adapter_config = site.to_adapter_config(
        resolved, workspace_root=profile["paths"]["remote_root"],
        gateway_url="local://qualify", run_token="0" * 32,
    )
    resource_profile = {
        "name": f"{resolved.partition}-{resolved.max_gpus}gpu",
        # qos rides on the resource profile so _directives renders --qos
        # from the SiteProfile resolution (never from a Candidate).
        "qos": resolved.qos,
        "max_cpus": resolved.max_cpus,
        "max_memory_gb": resolved.max_memory_gb,
        "max_gpus": resolved.max_gpus,
        "max_walltime_minutes": resolved.max_walltime_minutes,
    }
    transport_options = {
        k: str(v) for k, v in (profile["ssh"].get("options") or {}).items()
    }
    transport_options.setdefault("ControlMaster", "auto")
    transport_options.setdefault("ControlPath", "/tmp/dftworld-qual-cm-%C")
    transport_options.setdefault("ControlPersist", "600")
    transport = SshSlurmTransport(
        ssh=SshConfig(
            host=profile["ssh"]["host"],
            user=profile["ssh"].get("user") or None,
            port=int(profile["ssh"].get("port") or 22) or None,
            options=transport_options,
        ),
        workspace=str(ROOT),
        remote_workspace=profile["paths"]["remote_root"],
        sync="sync_back",
    )
    run_local = CANARY_ROOT / run_id
    adapter = SlurmAdapter(
        adapter_config,
        transport,
        case_id="dispatcher-qual",
        resource_profile=resource_profile,
        script_dir=run_local / "scripts",
    )
    dispatcher = HpcDispatcher(
        GatewayRuntime(audit=GatewayAudit(run_local / "audit.jsonl")),
        {"adapter": "slurm", "adapter_instance": adapter},
    )
    workspace = run_local / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    # Qualification sessions must outlive scheduler queue waits: the default
    # 300 s run-token TTL expired mid-poll on the first slow GPU queue.  The
    # override rides open_run's per-run config (consumed by the gateway when
    # issuing the token) and never reaches site-config validation.  open_run
    # REPLACES rather than merges, so the adapter identity must ride along.
    session = dispatcher.open_run(
        run_id,
        workspace=workspace,
        adapter_config={
            "adapter": "slurm",
            "adapter_instance": adapter,
            "token_ttl_sec": 129600.0,
        },
    )
    return session, transport


def _wrapper_renderer(profile: dict, lock: dict, *, gpu: bool = False):
    """Trusted exec-line renderer: pinned SIF path gated by its frozen digest,
    exactly one rw bind of the job workspace at /workspace, argv via shlex.

    ``gpu=True`` adds ``--nv`` so the container binds the host NVIDIA driver
    and devices — without it nvidia-smi produces no output inside the
    container even on a correctly allocated GPU node (site precedent: every
    proven 031-033 GPU batch script runs apptainer with --nv)."""
    sif_path = lock["runtime"]["sif_path_remote"]
    sif_digest = lock["runtime"]["sif_sha256"]
    apptainer = profile["paths"]["apptainer"]
    nv_flag = "--nv " if gpu else ""

    def render(spec: dict, workspace: str) -> str:
        command = shlex.join(spec["command"])
        return (
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "# Runtime identity gate: refuse to execute an unverified SIF.\n"
            f"actual=$(sha256sum {shlex.quote(sif_path)} | cut -d' ' -f1)\n"
            f'[ \"$actual\" = \"{sif_digest}\" ] || {{ echo \"SIF digest mismatch\" >&2; exit 30; }}\n'
            f"mkdir -p {shlex.quote(workspace)}\n"
            f"cd {shlex.quote(workspace)}\n"
            f"exec {shlex.quote(apptainer)} exec --cleanenv --contain {nv_flag}"
            f"--bind {shlex.quote(workspace)}:/workspace:rw "
            f"{shlex.quote(sif_path)} {command}\n"
        )

    return render


def _spec(runtime_decl: str, command: list[str], *, cpus: int = 2,
          memory_gb: int = 8, gpus: int = 0, walltime_minutes: int = 15) -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": "placeholder",
        "runtime": runtime_decl,
        "command": command,
        "resources": {
            "cpus": cpus,
            "memory_gb": memory_gb,
            "gpus": gpus,
            "walltime_minutes": walltime_minutes,
        },
        "inputs": [],
        "outputs": [],
    }


def _wait_terminal(session, job_id: str, *, timeout_sec: float) -> str:
    deadline = time.monotonic() + timeout_sec
    last = ""
    while time.monotonic() < deadline:
        state = session.status(job_id)["state"]
        if state != last:
            print(f"    [{job_id}] state={state}", flush=True)
            last = state
        if state in TERMINAL:
            return state
        time.sleep(30.0)
    raise QualifyError(f"job {job_id} not terminal after {timeout_sec}s")


# -- sentinel protocol --------------------------------------------------------


def _place_sentinels(profile: dict, stamp: str) -> dict:
    """Trusted Infra places external sentinels OUTSIDE any run workspace."""
    home = _ssh(profile, "echo $HOME").strip()
    root = profile["paths"]["remote_root"]
    paths = {
        "home_sentinel": f"{home}/.bench-sentinel-{stamp}",
        "credential_sentinel": f"{home}/.bench-cred-sentinel-{stamp}",
        "other_run_dir": f"{root}/sentinel-other-run-{stamp}",
        "solution_sentinel": f"{root}/sentinel-solution-{stamp}",
        "reference_sentinel": f"{root}/sentinel-reference-{stamp}",
    }
    _ssh(profile, f"printf 'sentinel {stamp}' > {shlex.quote(paths['home_sentinel'])}")
    _ssh(
        profile,
        f"printf 'fake-credential {stamp}' > "
        f"{shlex.quote(paths['credential_sentinel'])}",
    )
    _ssh(
        profile,
        f"mkdir -p {shlex.quote(paths['other_run_dir'])} && "
        f"printf 'other-run marker' > "
        f"{shlex.quote(paths['other_run_dir'] + '/marker')}",
    )
    _ssh(
        profile,
        f"printf 'solution marker' > {shlex.quote(paths['solution_sentinel'])}",
    )
    _ssh(
        profile,
        f"printf 'reference marker' > {shlex.quote(paths['reference_sentinel'])}",
    )
    return paths


def _cleanup_sentinels(profile: dict, sentinels: dict) -> dict:
    targets = [
        sentinels["home_sentinel"],
        sentinels["credential_sentinel"],
        sentinels["other_run_dir"],
        sentinels["solution_sentinel"],
        sentinels["reference_sentinel"],
    ]
    rm = "rm -rf " + " ".join(shlex.quote(t) for t in targets)
    _ssh(profile, rm)
    leftover = []
    for target in targets:
        out = _ssh(profile, f"test -e {shlex.quote(target)} && echo PRESENT || echo GONE")
        if "PRESENT" in out:
            leftover.append(target)
    return {"removed": targets, "leftover": leftover}


def _containment_fragment(profile: dict, sentinels: dict) -> str:
    """In-container probe as one bash fragment.

    Each check emits a ``BENCH_PROBE <key>=pass`` line only when it holds;
    any leak exits non-zero (the job then cannot end SUCCEEDED).  The
    verifier re-parses these lines from the embedded stdout tail, so the
    receipt's structured probe results are cross-checked against raw output.
    """
    runs_root = str(Path(profile["paths"]["remote_root"]).parent)
    return ";".join(
        [
            "( touch /workspace/.probe-write && rm /workspace/.probe-write ) "
            "&& echo 'BENCH_PROBE workspace_rw=pass' || exit 20",
            f"test ! -e {shlex.quote(sentinels['home_sentinel'])} "
            "&& echo 'BENCH_PROBE home_sentinel_absent=pass' || exit 21",
            f"test ! -e {shlex.quote(sentinels['credential_sentinel'])} "
            "&& echo 'BENCH_PROBE credential_sentinel_absent=pass' || exit 22",
            f"test ! -e {shlex.quote(sentinels['other_run_dir'])} "
            "&& echo 'BENCH_PROBE other_run_dir_absent=pass' || exit 23",
            f"test ! -e {shlex.quote(sentinels['solution_sentinel'])} "
            "&& echo 'BENCH_PROBE solution_absent=pass' || exit 24",
            f"test ! -e {shlex.quote(sentinels['reference_sentinel'])} "
            "&& echo 'BENCH_PROBE reference_absent=pass' || exit 25",
            f"ls {shlex.quote(runs_root)} >/dev/null 2>&1 "
            "&& exit 26 || echo 'BENCH_PROBE runs_root_not_listable=pass'",
        ]
    )


# -- dispatcher job collection (shared by canary and cp2k phases) --------------


def _run_dispatcher_job(
    profile: dict,
    site,
    resolved,
    lock: dict,
    stamp: str,
    *,
    name: str,
    operation_id: str,
    spec: dict,
    timeout_sec: float,
    sentinels: dict,
    records_sink: list,
    probe_class: str,
    fetch_names: tuple[str, ...] = ("stdout.log", "stderr.log"),
) -> dict:
    """Submit one dispatcher job and collect its full evidence record.

    ``lock`` parameterizes the runtime wrapper so each stack (the echo
    canaries, CP2K) pins its own SIF digest inside the exec line.  ``probe_
    class`` selects the expected probe set: cpu jobs prove containment only
    (cpu-partition nodes have no GPU); gpu jobs additionally prove device
    visibility.  Every record — including failure-path records — is appended
    to ``records_sink`` before raising, so a failed run still leaves honest
    evidence behind.
    """
    session, transport = _open_session(site, resolved, profile, lock, f"run-{name}-{stamp}")
    session.gateway._adapter._runtime_wrapper = _wrapper_renderer(
        profile, lock, gpu=(probe_class == "gpu")
    )
    try:
        submitted = session.submit(spec, operation_id=operation_id, attempt=1)
        job_id = submitted["job_id"]
        try:
            state = _wait_terminal(session, job_id, timeout_sec=timeout_sec)
        except BaseException:
            # The outer canary finally block removes adversarial sentinels.
            # Never allow a queued/running job to outlive those sentinels and
            # later false-pass absence probes after a timeout or Ctrl-C.
            session.settle(cancel_pending=True)
            raise
        slurm_id = session.gateway._adapter._jobs[job_id]["slurm_id"]
        accounting = transport.accounting(str(slurm_id))
        # Scheduler-reported exit code (returned:first_failed); the receipt
        # schema requires the leading integer on every job record.
        exit_code = int((accounting.get("exit_code_raw") or "-1").split(":")[0])

        # Fetch FIRST and parse from the fetched bytes: transport.log()
        # resolves legacy layouts (submit-dir / slurm.out) and does not know
        # the wrapper's <workspace>/stdout.log convention.  The manifest and
        # the parsed probes then share one source of truth.
        workdir = session.gateway._adapter._jobs[job_id]["workspace"]
        local_out = CANARY_ROOT / f"fetched-{name}-{stamp}"
        try:
            fetched = transport.fetch(
                [f"{workdir}/{n}" for n in fetch_names], str(local_out)
            )
            fetched_by_name = {p.name: p for p in fetched if p.exists()}
        except Exception:  # noqa: BLE001 — failed jobs may have no artifacts
            fetched_by_name = {}
        stdout = (
            fetched_by_name.get("stdout.log", Path("")).read_text(
                encoding="utf-8", errors="replace"
            )
            if "stdout.log" in fetched_by_name
            else ""
        )
        stderr_text = (
            fetched_by_name.get("stderr.log", Path("")).read_text(
                encoding="utf-8", errors="replace"
            )
            if "stderr.log" in fetched_by_name
            else ""
        )

        record: dict[str, object] = {
            "canary": name,
            "run_id": session.run_id,
            "operation_id": operation_id,
            "attempt": 1,
            "job_id": job_id,
            "runtime_decl": spec["runtime"],
            "state": state,
            "exit_code": exit_code,
            "gpus_requested": int(spec["resources"]["gpus"]),
            "requested_resources": dict(spec["resources"]),
            "probe_class": probe_class,
            "scheduler_job_id": str(slurm_id),
            "accounting": accounting,
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr_text[-800:],
        }
        if state != "SUCCEEDED":
            records_sink.append(record)
            raise QualifyError(f"job {name} ended {state}: {stdout[-500:]}")

        # Structured probes must agree with the raw BENCH_PROBE lines.
        parsed = parse_probe_stdout(stdout)
        missing = [
            key for key in (
                "workspace_rw", "home_sentinel_absent",
                "credential_sentinel_absent", "other_run_dir_absent",
                "solution_absent", "reference_absent",
                "runs_root_not_listable",
            ) if parsed["probes"].get(key) != "pass"
        ]
        if missing:
            records_sink.append(record)
            raise QualifyError(
                f"job {name}: containment probes absent/failed: {missing}"
            )
        if MARKER_CONTAINMENT not in stdout:
            records_sink.append(record)
            raise QualifyError(
                f"job {name}: {MARKER_CONTAINMENT} marker missing"
            )
        probe_results = {
            key: parsed["probes"].get(key) == "pass"
            for key in (
                "workspace_rw", "home_sentinel_absent",
                "credential_sentinel_absent", "other_run_dir_absent",
                "solution_absent", "reference_absent",
                "runs_root_not_listable",
            )
        }
        if probe_class == "gpu":
            if MARKER_GPU not in stdout:
                records_sink.append(record)
                raise QualifyError(
                    f"job {name}: {MARKER_GPU} marker missing"
                )
            if not parsed["gpu_device_name"]:
                records_sink.append(record)
                raise QualifyError(
                    f"job {name}: device line unparseable: {stdout[-600:]}"
                )
            probe_results["gpu_device_name"] = parsed["gpu_device_name"]
            probe_results["gpu_memory_total_mb"] = parsed["gpu_memory_total_mb"]
        elif parsed["gpu_device_name"]:
            # A cpu-partition node cannot have produced this honestly.
            records_sink.append(record)
            raise QualifyError(
                f"job {name}: unexpected device line on cpu partition"
            )
        record["probe_results"] = probe_results

        report = session.settle(cancel_pending=True)
        record["settlement"] = {
            "report": report.to_dict(),
            "digest": report.digest,
        }
        record["fetch_manifest"] = {
            "job_id": job_id,
            "entries": [
                {
                    "path": name_,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "size_bytes": p.stat().st_size,
                }
                for name_, p in sorted(fetched_by_name.items())
            ],
        }
        record["artifacts_dir"] = local_out.name
        record["audit_log"] = f"{session.run_id}/audit.jsonl"
        # NOTE: the remote workspace path is deliberately NOT recorded in the
        # receipt (not part of the verdict-free contract); trusted-side
        # cleanup removes it after verification.
        records_sink.append(record)
        return record
    finally:
        session.close()


# -- phase: cancel (P4 step 7 — explicit cancel probe, separate authorization) -


def _run_cancel_probe(
    profile: dict, site, resolved, lock: dict, stamp: str
) -> dict:
    """P4 step 7 — the explicit cancel probe.

    Submits a long-running sleep on the native CPU route THROUGH the
    dispatcher, waits until the scheduler has allocated and started it, then
    cancels it through the dispatcher's cancel operation (gateway token
    check + ownership check + adapter scancel — never a raw scancel by the
    driver).  The probe's value is exactly what the canaries cannot show:
    a *running* containerized job can be torn down, reaches terminal
    CANCELLED, and leaves no orphan.  Anchors for offline derivation: the
    hash-chained ``cancel`` audit event in this run's ledger, the settlement
    attempt state, and scheduler accounting (AllocTRES present because the
    job was RUNNING when cancelled).
    """
    session, transport = _open_session(
        site, resolved, profile, lock, f"run-cancel-probe-{stamp}"
    )
    session.gateway._adapter._runtime_wrapper = _wrapper_renderer(profile, lock)
    sif_digest = lock["runtime"]["sif_sha256"]
    runtime_decl = f"matclaw-cips@sha256:{sif_digest}"
    spec = _spec(
        runtime_decl, ["sleep", "3600"],
        cpus=resolved.max_cpus,
        memory_gb=min(resolved.max_memory_gb, 8),
        gpus=0,
        walltime_minutes=15,
    )
    try:
        submitted = session.submit(
            spec, operation_id="qual-cancel-probe-01", attempt=1
        )
        job_id = submitted["job_id"]
        # Wait for allocation: cancelling a PENDING job would prove strictly
        # less (no running container to tear down).  Refuse to record a
        # pending-state cancel rather than silently weaken the probe.
        deadline = time.monotonic() + 1800
        state = ""
        while time.monotonic() < deadline:
            state = session.status(job_id)["state"]
            if state in ("RUNNING",) or state in TERMINAL:
                break
            time.sleep(10.0)
        if state != "RUNNING":
            session.settle(cancel_pending=True)
            raise QualifyError(
                f"cancel probe never reached RUNNING (observed {state!r}); "
                "refusing to record a weaker probe"
            )
        cancelled = session.cancel(job_id)
        final = _wait_terminal(session, job_id, timeout_sec=900)
        status_after = session.status(job_id)["state"]
        usage_op = session.usage()
        if (
            cancelled.get("state") != "CANCELLED"
            or final != "CANCELLED"
            or status_after != "CANCELLED"
        ):
            session.settle(cancel_pending=True)
            raise QualifyError(
                f"cancel probe inconsistent: cancel={cancelled!r} "
                f"final={final!r} status_after={status_after!r}"
            )
        slurm_id = session.gateway._adapter._jobs[job_id]["slurm_id"]
        accounting = transport.accounting(str(slurm_id))
        exit_code = int((accounting.get("exit_code_raw") or "-1").split(":")[0])
        workdir = session.gateway._adapter._jobs[job_id]["workspace"]
        local_out = CANARY_ROOT / f"fetched-cancel-probe-{stamp}"
        try:
            fetched = transport.fetch(
                [f"{workdir}/stdout.log", f"{workdir}/stderr.log"], str(local_out)
            )
            fetched_by_name = {p.name: p for p in fetched if p.exists()}
        except Exception:  # noqa: BLE001 — artifacts may be partial on kill
            fetched_by_name = {}

        record: dict[str, object] = {
            "canary": "cancel-probe",
            "run_id": session.run_id,
            "operation_id": "qual-cancel-probe-01",
            "attempt": 1,
            "job_id": job_id,
            "scheduler_job_id": str(slurm_id),
            "runtime_decl": runtime_decl,
            "state": final,
            "exit_code": exit_code,
            "gpus_requested": 0,
            "requested_resources": dict(spec["resources"]),
            "probe_class": "cpu",
            "cancel_proved": True,
            "cancel_ops": {
                "cancelled_via": "dispatcher.cancel",
                "state_at_cancel": "RUNNING",
                "status_after_cancel": status_after,
                "usage_op": dict(usage_op),
            },
            "accounting": accounting,
            "stdout_tail": (
                fetched_by_name.get("stdout.log", Path(""))
                .read_text(encoding="utf-8", errors="replace")[-2000:]
                if "stdout.log" in fetched_by_name else ""
            ),
            "stderr_tail": (
                fetched_by_name.get("stderr.log", Path(""))
                .read_text(encoding="utf-8", errors="replace")[-800:]
                if "stderr.log" in fetched_by_name else ""
            ),
            "fetch_manifest": {
                "job_id": job_id,
                "entries": [
                    {
                        "path": name_,
                        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                        "size_bytes": p.stat().st_size,
                    }
                    for name_, p in sorted(fetched_by_name.items())
                ],
            },
            "artifacts_dir": local_out.name,
            "audit_log": f"{session.run_id}/audit.jsonl",
        }
        report = session.settle(cancel_pending=True)
        record["settlement"] = {"report": report.to_dict(), "digest": report.digest}
        return record
    except BaseException:
        # Any unexpected exit: make sure the killed-sleep cannot outlive us.
        try:
            session.settle(cancel_pending=True)
        except Exception:  # noqa: BLE001 — report the original failure
            pass
        raise
    finally:
        session.close()


def canary(profile: dict, lock: dict, *, skip_preflight: bool = False) -> dict:
    """Two independent short canaries after cpu-ACL restoration:

      1. CPU job (native ``cpu`` partition, gpus=0): echo/hostname +
         containment probes.
      2. GPU job (``gpu`` partition, --gres=gpu:1): nvidia-smi device probe +
         containment probes.

    The explicit cancel probe (P4 step 7) is a SEPARATE authorized phase
    (``--phase cancel``) merged into the receipt afterwards — keeping it out
    of the canary means the durable ``--phase resume`` path stays
    submit-free.  Uses the production SiteProfile resolver for resource
    mapping and live ACL pre-checks on both partitions.  The ACL-era
    assumption that cpu workloads must ride the gpu queue is gone: the
    profile must map cpu workloads natively or this phase refuses to run.
    """
    if not skip_preflight:
        preflight(profile, lock)

    # Production SiteProfile resolver — single source of truth.
    site = _build_site_profile(profile)
    resolved_cpu, resolved_gpu = _assert_canary_routes(profile, site)

    def _resolved_dict(r) -> dict:
        return {
            "workload_type": r.workload_type,
            "queue_name": r.queue_name,
            "partition": r.partition,
            "account": r.account,
            "qos": r.qos,
            "gres": r.gres,
            "mapping_note": r.mapping_note,
        }

    stamp = secrets.token_hex(4)
    sif_digest = lock["runtime"]["sif_sha256"]
    runtime_decl = f"matclaw-cips@sha256:{sif_digest}"
    evidence: dict[str, object] = {
        "phase": "canary",
        "authorization": "user-authorized scope: two short jobs "
        "(CPU echo/hostname + containment on the native cpu partition; GPU "
        "nvidia-smi + containment under --gres=gpu:1); no CP2K, no image "
        "transfer, nothing else (the cancel probe is its own phase and its "
        "own authorization)",
        "site_profile_digest": site.digest,
        "resolved_workloads": {
            "cpu": _resolved_dict(resolved_cpu),
            "gpu": _resolved_dict(resolved_gpu),
        },
        "native_cpu_partition_accessible": True,
        "sentinel_cleanup": {},
        "single_mount_check": (
            "wrapper renders exactly one --bind <workdir>:/workspace:rw; the "
            "in-container probe additionally proves the runs root is not listable"
        ),
        "jobs": [],
        "cp2k_gate": {
            "detail": "CP2K ENERGY canary awaits separate authorization; "
                      "absent evidence derives NOT_RUN",
        },
        "ai2kit_gate": {
            "detail": "ai2kit runtime canary awaits separate authorization; "
                      "absent evidence derives NOT_RUN",
        },
        "cancel_gate": {
            "detail": "explicit cancel probe awaits separate authorization "
                      "(--phase cancel); absent evidence derives NOT_RUN",
        },
    }

    sentinels = _place_sentinels(profile, stamp)
    try:
        probe = _containment_fragment(profile, sentinels)

        # 1. CPU canary on the native cpu queue: echo/hostname + containment,
        #    zero GPUs requested and (per TRES reconciliation) allocated.
        _run_dispatcher_job(
            profile, site, resolved_cpu, lock, stamp,
            name="cpu-echo-probe-containment",
            operation_id="qual-cpu-canary-01",
            spec=_spec(
                runtime_decl,
                ["/bin/bash", "-c",
                 'echo HOST=$(hostname); echo USER=$(id --name); '
                 'date -u +%Y-%m-%dT%H:%M:%SZ; '
                 + probe + f'; echo {MARKER_CONTAINMENT}'],
                gpus=0,
                cpus=resolved_cpu.max_cpus,
                memory_gb=min(resolved_cpu.max_memory_gb, 32),
                walltime_minutes=15,
            ),
            timeout_sec=1800,
            sentinels=sentinels,
            records_sink=evidence["jobs"],
            probe_class="cpu",
        )

        # 2. GPU canary under --gres: nvidia-smi device probe + containment.
        #    The echo/hostname workload does not use the GPU; the receipt
        #    records that mapping via resolved_workloads.gpu.
        _run_dispatcher_job(
            profile, site, resolved_gpu, lock, stamp,
            name="gpu-nvidia-probe-containment",
            operation_id="qual-gpu-canary-01",
            spec=_spec(
                runtime_decl,
                ["/bin/bash", "-c",
                 "nvidia-smi --query-gpu=name,memory.total "
                 "--format=csv,noheader "
                 "| awk -F', *' '{gsub(/ MiB/,\"\",$2); "
                 "print \"BENCH_GPU_DEVICE mem_mib=\" $2 \" name=\" $1}' "
                 f"&& echo {MARKER_GPU}; "
                 + probe + f'; echo {MARKER_CONTAINMENT}'],
                gpus=resolved_gpu.max_gpus,
                cpus=resolved_gpu.max_cpus,
                memory_gb=min(resolved_gpu.max_memory_gb, 32),
                walltime_minutes=20,
            ),
            # GPU partitions queue by Priority.  A 30-hour synchronous ceiling
            # stays inside the 36-hour token lease; longer waits use the
            # durable recovery path instead of keeping an operator terminal.
            timeout_sec=108000,
            sentinels=sentinels,
            records_sink=evidence["jobs"],
            probe_class="gpu",
        )

        cleanup = _cleanup_sentinels(profile, sentinels)
        if cleanup["leftover"]:
            raise QualifyError(f"sentinel cleanup left {cleanup['leftover']}")
        evidence["sentinel_cleanup"] = cleanup

        # P4 pass-condition "no orphan job": both canary runs must be fully
        # settled in the scheduler's view before anything is sealed.
        evidence["orphan_check"] = _orphan_sweep(
            profile,
            [f"run-cpu-echo-probe-containment-{stamp}",
             f"run-gpu-nvidia-probe-containment-{stamp}"],
        )
        if evidence["orphan_check"]["active_total"]:
            raise QualifyError(
                f"orphan jobs still active after canary: "
                f"{evidence['orphan_check']['runs']}"
            )
    finally:
        # Never leave sentinels behind even on failure.
        try:
            _cleanup_sentinels(profile, sentinels)
        except QualifyError:
            pass

    return evidence


# -- phase: cp2k (requires separate explicit authorization) --------------------


def _cp2k_script(cp2k_lock: dict, probe: str) -> str:
    """Job script: stage the frozen input, run the pinned binary, probe.

    Runs on the native cpu partition (no nvidia-smi / GPU markers — the
    verifier rejects device lines from cpu-class jobs).
    """
    binary = shlex.quote(cp2k_lock["cp2k"]["binary"])
    return (
        f"printf '%s\\n' {shlex.quote(CP2K_INPUT_TEXT)} > "
        f"{shlex.quote(CP2K_INPUT_NAME)}\n"
        f"{binary} -i {shlex.quote(CP2K_INPUT_NAME)} > "
        f"{shlex.quote(CP2K_OUTPUT_NAME)} 2> cp2k.err\n"
        "rc=$?\n"
        + probe + f'\necho {MARKER_CONTAINMENT}\n'
        "exit $rc\n"
    )


def cp2k_phase(
    profile: dict,
    cp2k_lock: dict,
    *,
    cp2k_lock_relpath: str,
    profile_path: Path,
) -> bool:
    """CP2K ENERGY canary, merged into the existing receipt (fail-closed).

    Requires its own explicit user authorization (the 2026-08-22 scope
    explicitly excluded CP2K) and an existing consistent receipt — the echo
    canary must have qualified the site first.  The phase refuses to run on
    any pre-existing inconsistency, never overwrites evidence, and only ever
    adds/derives ``evidence.cp2k_gate``.
    """
    import hashlib as _hashlib

    if not RECEIPT_PATH.is_file():
        raise QualifyError(
            f"no receipt to merge into ({RECEIPT_PATH}); run --phase canary first"
        )
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    base = verify_receipt(
        receipt,
        root=ROOT,
        receipt_dir=RECEIPT_PATH.parent,
        profile_path=profile_path,
    )
    if not base["consistent"]:
        raise QualifyError(
            "existing receipt fails derivation; refusing merge: "
            + "; ".join(base["problems"][:3])
        )

    preflight(profile, cp2k_lock)
    site = _build_site_profile(profile)
    resolved_cpu = site.resolve_workload("cpu")

    stamp = secrets.token_hex(4)
    sif_digest = cp2k_lock["runtime"]["sif_sha256"]
    runtime_decl = f"{cp2k_lock.get('image_name', 'dftworld-cp2k')}@sha256:{sif_digest}"

    sentinels = _place_sentinels(profile, stamp)
    try:
        probe = _containment_fragment(profile, sentinels)
        records: list = []
        try:
            _run_dispatcher_job(
                profile, site, resolved_cpu, cp2k_lock, stamp,
                name="cp2k-energy-canary",
                operation_id="qual-cp2k-energy-01",
                spec=_spec(
                    runtime_decl,
                    ["/bin/bash", "-c", _cp2k_script(cp2k_lock, probe)],
                    gpus=0,
                    cpus=resolved_cpu.max_cpus,
                    memory_gb=min(resolved_cpu.max_memory_gb, 32),
                    walltime_minutes=30,
                ),
                timeout_sec=3600,
                sentinels=sentinels,
                records_sink=records,
                probe_class="cpu",
                fetch_names=(
                    "stdout.log", CP2K_INPUT_NAME, CP2K_OUTPUT_NAME, "stderr.log",
                ),
            )
        finally:
            try:
                _cleanup_sentinels(profile, sentinels)
            except QualifyError:
                pass
        record = records[-1]

        cleanup = _cleanup_sentinels(profile, sentinels)
        if cleanup["leftover"]:
            raise QualifyError(f"sentinel cleanup left {cleanup['leftover']}")

        # Parse the fetched output with the SAME function the verifier uses.
        output_text = (
            CANARY_ROOT / record["artifacts_dir"] / CP2K_OUTPUT_NAME
        ).read_text(encoding="utf-8", errors="replace")
        facts = parse_cp2k_output(output_text)
        if not facts["version_string"] or facts["energy_eh"] is None:
            raise QualifyError(
                f"cp2k output unparseable: version={facts['version_string']!r} "
                f"energy={facts['energy_eh']!r}"
            )

        input_sha = hashlib.sha256(CP2K_INPUT_TEXT.encode("utf-8")).hexdigest()
        receipt["evidence"]["cp2k_gate"] = {
            "detail": (
                "CP2K ENERGY canary under separate authorization: pinned "
                f"binary {cp2k_lock['cp2k']['binary']}, input "
                f"{CP2K_INPUT_NAME} sha256:{input_sha[:12]}…"
            ),
            "evidence": {
                "job": record,
                "input": {
                    "name": CP2K_INPUT_NAME,
                    "text": CP2K_INPUT_TEXT,
                    "sha256": input_sha,
                },
                "output_artifact": CP2K_OUTPUT_NAME,
                "parsed": {
                    "version_string": facts["version_string"],
                    "energy_eh": facts["energy_eh"],
                    "scf_converged": facts["scf_converged"],
                },
                "runtime_lock": {
                    "path": cp2k_lock_relpath,
                    "sif_path_remote": cp2k_lock["runtime"]["sif_path_remote"],
                    "sif_sha256": sif_digest,
                },
            },
        }
    finally:
        # Never leave sentinels behind even on failure.
        try:
            _cleanup_sentinels(profile, sentinels)
        except QualifyError:
            pass

    # Re-seal and re-verify the merged receipt; the derivation must now
    # produce PASS or the merge is refused on disk.
    receipt.pop("digest", None)
    from dftworld_bench.experiments.qualification_receipt import seal_receipt

    merged = seal_receipt(receipt)
    RECEIPT_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result = verify_receipt(
        merged,
        root=ROOT,
        receipt_dir=RECEIPT_PATH.parent,
        profile_path=profile_path,
    )
    derived = result["derived"]
    print(
        f"receipt merged: {RECEIPT_PATH}  "
        f"derived={derived['qualification_status']} "
        f"formal_qualified={derived['formal_qualified']}"
    )
    return result["consistent"] and derived["qualification_status"] == "PASS"


# -- phase: ai2kit (requires separate explicit authorization) ------------------


def _ai2kit_script(ai2kit_lock: dict, probe: str) -> str:
    """In-container ai2kit runtime canary (cpu-partition).

    Imports the ai2kit package, asserts the version equals the lock's
    ``software.ai2_kit``, round-trips a minimal workflow config through
    /workspace (config parsing + workspace write in one shot), then runs the
    standard containment probes and the containment marker.  The script's own
    assertions fail the job — the phase additionally re-checks the version and
    config markers on the fetched stdout before sealing any evidence.

    The heredoc is quoted ('PY') so bash performs NO interpolation; every
    injected value is produced by :func:`json.dumps`, which is a valid Python
    literal for these scalars.  ``python`` is the image's interpreter (034 lock
    software.python 3.11.15; the runtime image runs ``python``).

    Version probe discipline (Architecture Freeze §4): the ``ai2_kit`` 1.1.0
    distribution ships an EMPTY ``__init__.py`` with no ``__version__``, so
    ``getattr(mod, '__version__', ...)`` can never equal the lock.  The truth
    is the dist-info METADATA, read via ``importlib.metadata.version`` with
    the attribute as fallback only.
    """
    version_raw = str(ai2kit_lock["software"]["ai2_kit"])
    version_lit = json.dumps(version_raw)
    cfg_lit = json.dumps(
        {
            "benchmark_id": "034-ai2kit-water64-end-to-end-potential",
            "software": ai2kit_lock.get("software") or {},
        },
        sort_keys=True,
    )
    python_lines = [
        "import json, os",
        "import ai2_kit as _a  # the only module the dist ships (no ai2kit alias)",
        "import importlib.metadata as _md",
        "try:",
        "    version = _md.version('ai2_kit')",
        "except Exception:",
        "    version = str(getattr(_a, '__version__', '?'))",
        "print('AI2KIT_MODULE=ai2_kit')",
        "print('AI2KIT_VERSION=' + version)",
        f"assert version == {version_lit}, version",
        f"cfg = {cfg_lit}",
        "path = '/workspace/qual-ai2kit-min-config.json'",
        "with open(path, 'w', encoding='utf-8') as fh:",
        "    json.dump(cfg, fh)",
        "loaded = json.load(open(path, encoding='utf-8'))",
        "assert loaded == cfg, (loaded, cfg)",
        "os.remove(path)",
        "print('AI2KIT_CONFIG_LOAD=pass')",
    ]
    body = "python - <<'PY'\n" + "\n".join(python_lines) + "\nPY\n"
    return (
        body
        + "rc=$?\n"
        + probe + f'\necho {MARKER_CONTAINMENT}\n'
        "exit $rc\n"
    )


def _ai2kit_version_from_stdout(stdout: str) -> str:
    """The ``AI2KIT_VERSION=...`` line the canary script emits."""
    match = re.search(r"^AI2KIT_VERSION=(\S+)\s*$", stdout, re.MULTILINE)
    return match.group(1) if match else ""


def ai2kit_phase(
    profile: dict,
    ai2kit_lock: dict,
    *,
    ai2kit_lock_relpath: str,
    profile_path: Path,
) -> bool:
    """AI2Kit runtime canary, merged into the existing receipt (fail-closed).

    Requirement behind 034's ``runtime.ai2kit`` cell (spec §3/§5b): the
    ai2kit controller image must import, report its locked version, load a
    minimal workflow config, and pass containment — all inside a pinned-SIF,
    single-node dispatcher job on the native cpu partition.  Requires its own
    explicit user authorization and an existing consistent receipt; the phase
    refuses to run on any pre-existing inconsistency, never overwrites
    evidence, and only ever adds/derives ``evidence.ai2kit_gate``.
    """
    runtime = ai2kit_lock.get("runtime") or {}
    if not runtime.get("sif_sha256"):
        raise QualifyError(
            "--ai2kit-lock has no runtime.sif_sha256 yet: the ai2kit "
            "runtime SIF digest is mandatory runtime data captured at the "
            "site (fill the lock before running this phase); an empty "
            "digest keeps runtime.ai2kit NOT_RUN"
        )
    if not RECEIPT_PATH.is_file():
        raise QualifyError(
            f"no receipt to merge into ({RECEIPT_PATH}); run --phase canary first"
        )
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    base = verify_receipt(
        receipt,
        root=ROOT,
        receipt_dir=RECEIPT_PATH.parent,
        profile_path=profile_path,
    )
    if not base["consistent"]:
        raise QualifyError(
            "existing receipt fails derivation; refusing merge: "
            + "; ".join(base["problems"][:3])
        )

    preflight(profile, ai2kit_lock)
    site = _build_site_profile(profile)
    resolved_cpu = site.resolve_workload("cpu")

    stamp = secrets.token_hex(4)
    sif_digest = runtime["sif_sha256"]
    # Capability-sealed declaration (Architecture Freeze §3): evidence names
    # the capability, the digest suffix stays the receipt's binding anchor.
    runtime_decl = f"ai2kit@sha256:{sif_digest}"

    sentinels = _place_sentinels(profile, stamp)
    try:
        probe = _containment_fragment(profile, sentinels)
        records: list = []
        try:
            _run_dispatcher_job(
                profile, site, resolved_cpu, ai2kit_lock, stamp,
                name="ai2kit-runtime-canary",
                operation_id="qual-ai2kit-runtime-01",
                spec=_spec(
                    runtime_decl,
                    ["/bin/bash", "-c", _ai2kit_script(ai2kit_lock, probe)],
                    gpus=0,
                    cpus=resolved_cpu.max_cpus,
                    memory_gb=min(resolved_cpu.max_memory_gb, 32),
                    walltime_minutes=30,
                ),
                timeout_sec=3600,
                sentinels=sentinels,
                records_sink=records,
                probe_class="cpu",
            )
        finally:
            try:
                _cleanup_sentinels(profile, sentinels)
            except QualifyError:
                pass
        record = records[-1]

        cleanup = _cleanup_sentinels(profile, sentinels)
        if cleanup["leftover"]:
            raise QualifyError(f"sentinel cleanup left {cleanup['leftover']}")

        # Cross-check the canary's own assertions on the RAW fetched stdout —
        # the receipt binds only the record bytes, so an honest canary must
        # pass here before its evidence is sealed at all.
        stdout = (
            CANARY_ROOT / record["artifacts_dir"] / "stdout.log"
        ).read_text(encoding="utf-8", errors="replace")
        locked_version = str(ai2kit_lock["software"]["ai2_kit"])
        observed_version = _ai2kit_version_from_stdout(stdout)
        if observed_version != locked_version:
            raise QualifyError(
                f"ai2kit canary reported version {observed_version!r}, "
                f"lock pins {locked_version}"
            )
        if "AI2KIT_CONFIG_LOAD=pass" not in stdout:
            raise QualifyError(
                f"ai2kit minimal config load failed in-container; "
                f"stdout tail: {stdout[-400:]}"
            )

        receipt["evidence"]["ai2kit_gate"] = {
            "detail": (
                "ai2kit runtime canary under separate authorization: "
                f"import + version {locked_version} (lock software.ai2_kit, "
                "via importlib.metadata) "
                "+ minimal config load + containment probes, in the pinned "
                "shared runtime (ai2kit-runtime-v1, derived lineage); absent "
                "evidence derived NOT_RUN before this merge"
            ),
            "evidence": {
                "job": record,
                "runtime_lock": {
                    "path": ai2kit_lock_relpath,
                    "sif_path_remote": runtime["sif_path_remote"],
                    "sif_sha256": sif_digest,
                },
            },
        }
    finally:
        # Never leave sentinels behind even on failure.
        try:
            _cleanup_sentinels(profile, sentinels)
        except QualifyError:
            pass

    # Re-seal and re-verify the merged receipt; the derivation must now
    # produce PASS or the merge is refused on disk.
    receipt.pop("digest", None)
    from dftworld_bench.experiments.qualification_receipt import seal_receipt

    merged = seal_receipt(receipt)
    RECEIPT_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result = verify_receipt(
        merged,
        root=ROOT,
        receipt_dir=RECEIPT_PATH.parent,
        profile_path=profile_path,
    )
    derived = result["derived"]
    print(
        f"receipt merged: {RECEIPT_PATH}  "
        f"derived={derived['qualification_status']} "
        f"formal_qualified={derived['formal_qualified']}"
    )
    return result["consistent"] and derived["qualification_status"] == "PASS"


def cancel_phase(profile: dict, lock: dict, *, profile_path: Path) -> bool:
    """Explicit cancel probe (P4 step 7), merged into an existing receipt.

    The dispatcher.cpu proof's teardown half: a long-running containerized
    sleep is submitted through the Gateway on the native cpu route, reaches
    RUNNING under the pinned SIF, is cancelled through ``session.cancel``
    (token check + ownership check + adapter scancel — never a raw scancel
    by the driver), settles terminal CANCELLED, and the post-cancel orphan
    sweep comes back empty.  Requires its own explicit authorization,
    refuses to run over an inconsistent receipt, never overwrites existing
    cancel evidence, and only ever adds/derives ``evidence.cancel_gate``.
    """
    if not RECEIPT_PATH.is_file():
        raise QualifyError(
            f"no receipt to merge into ({RECEIPT_PATH}); run --phase canary first"
        )
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    gate = (receipt.get("evidence") or {}).get("cancel_gate") or {}
    if gate.get("evidence"):
        raise QualifyError(
            "cancel_gate evidence is already sealed in this receipt; keep "
            "old evidence — archive the site directory (or choose a new "
            "--site) and re-qualify instead of overwriting"
        )
    base = verify_receipt(
        receipt,
        root=ROOT,
        receipt_dir=RECEIPT_PATH.parent,
        profile_path=profile_path,
    )
    if not base["consistent"]:
        raise QualifyError(
            "existing receipt fails derivation; refusing merge: "
            + "; ".join(base["problems"][:3])
        )

    preflight(profile, lock)
    site = _build_site_profile(profile)
    resolved_cpu = site.resolve_workload("cpu")
    stamp = secrets.token_hex(4)
    record = _run_cancel_probe(profile, site, resolved_cpu, lock, stamp)
    orphan = _orphan_sweep(profile, [f"run-cancel-probe-{stamp}"])
    if orphan["active_total"]:
        raise QualifyError(
            f"cancel probe left an orphan job: {orphan['runs']}"
        )

    receipt["evidence"]["cancel_gate"] = {
        "detail": (
            "explicit cancel probe under separate authorization: a RUNNING "
            "containerized sleep on the native cpu route was cancelled via "
            "dispatcher.cancel, reached terminal CANCELLED, settled with the "
            "cancel recorded in the attempt ledger, and the post-cancel "
            "orphan sweep found no active job; absent evidence derived "
            "NOT_RUN before this merge"
        ),
        "evidence": {"job": record, "orphan_check": orphan},
    }

    # Re-seal and re-verify the merged receipt; the derivation must now
    # produce a consistent envelope or the merge fails on the return code.
    receipt.pop("digest", None)
    from dftworld_bench.experiments.qualification_receipt import seal_receipt

    merged = seal_receipt(receipt)
    RECEIPT_PATH.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    result = verify_receipt(
        merged,
        root=ROOT,
        receipt_dir=RECEIPT_PATH.parent,
        profile_path=profile_path,
    )
    derived = result["derived"]
    print(
        f"receipt merged: {RECEIPT_PATH}  "
        f"derived={derived['qualification_status']} "
        f"formal_qualified={derived['formal_qualified']}"
    )
    return result["consistent"] and derived["qualification_status"] == "PASS"


def build_receipt(
    profile: dict, lock: dict, evidence: dict, *, lock_relpath: str
) -> Path:
    """Seal the collected evidence into a verdict-free receipt on disk."""
    body = build_receipt_body(
        profile_site=profile["ssh"]["host"],
        site_profile_digest=evidence["site_profile_digest"],
        runtime_lock={
            "path": lock_relpath,
            "sif_path_remote": lock["runtime"]["sif_path_remote"],
            "sif_sha256": lock["runtime"]["sif_sha256"],
        },
        authorization_scope=evidence["authorization"],
        evidence=evidence,
        root=ROOT,
    )
    RECEIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT_PATH.write_text(
        json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return RECEIPT_PATH


def verify(receipt_path: Path, *, profile_path: Path | None = None) -> dict:
    """Offline derivation: replay every anchor; never trust declared labels.

    The receipt carries no verdict fields, so this derives the status purely
    from the bound evidence and reports every broken anchor.
    """
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    result = verify_receipt(
        receipt,
        root=ROOT,
        receipt_dir=Path(receipt_path).parent,
        profile_path=profile_path,
    )
    print(json.dumps(result, indent=2))
    return result


# -- phase: resume (complete an interrupted canary; never submits) ------------


RESUME_WAIT_DEADLINE_SEC = 48 * 3600
RESUME_POLL_SEC = 120


def _assert_canary_routes(profile: dict, site):
    """Guard the two canary routes before any submission attempt.

    Refuses to qualify against a profile still carrying ACL-era cpu→gpu
    routing, then live-checks the ACL on both native routes.  Shared by the
    canary phase and resume: a resume cannot honestly assert
    ``native_cpu_partition_accessible = True`` against a profile that routes
    cpu workloads off the native queue.
    """
    from dftworld_bench.hpc.site_profile import SiteProfileBlockedError

    resolved_cpu = site.resolve_workload("cpu")
    resolved_gpu = site.resolve_workload("gpu")
    mapping_cpu_queue = (site.resource_mapping.get("cpu") or {}).get("queue")
    if mapping_cpu_queue != "cpu":
        raise QualifyError(
            "SiteProfile does not map cpu workloads to the native cpu queue; "
            "set [slurm] cpu_partition in cluster_profile.toml first"
        )
    for resolved in (resolved_cpu, resolved_gpu):
        try:
            site.check_acl(resolved, ssh_fn=lambda cmd: _ssh(profile, cmd))
        except SiteProfileBlockedError:
            raise
    return resolved_cpu, resolved_gpu


def _remote_qual_root(profile: dict) -> str:
    """Remote dispatcher-qual root, derived from the site profile (site
    identifiers never enter the repository)."""
    return f"{profile['paths']['remote_root'].rstrip('/')}/dispatcher-qual"


def _workdir_for(profile: dict, run_id: str) -> str:
    return f"{_remote_qual_root(profile)}/{run_id}/job-0001"


def _find_active_by_dirname(profile: dict, run_id: str) -> str | None:
    out = _ssh(profile, "squeue -u $USER -h -o '%i'")
    for job_id in out.split():
        info = _ssh(
            profile,
            f"scontrol show job {job_id} 2>/dev/null | grep -E 'Command|WorkDir' "
            "|| true",
        )
        if run_id in info:
            return job_id
    return None


def _orphan_sweep(profile: dict, run_ids: list[str]) -> dict:
    """P4 pass-condition anchor: no orphan job left by this phase.

    Every qualification run directory is matched against the live queue via
    ``scontrol`` WorkDir — the same lookup the durable resume path uses — so
    an abandoned allocation under a qualification run id cannot hide behind
    a renamed job.  The receipt records the sweep; offline derivation
    refuses PASS while ``active_total`` is nonzero.
    """
    runs = {run_id: _find_active_by_dirname(profile, run_id) for run_id in run_ids}
    return {
        "method": "squeue -u $USER + scontrol WorkDir match per qualification run",
        "runs": runs,
        "active_total": sum(1 for v in runs.values() if v is not None),
    }


def _find_accounted_by_dirname(profile: dict, run_id: str, since: str) -> list[str]:
    raw = _ssh(
        profile,
        "sacct -X -P -n --format=JobID,JobName%25,State%12,WorkDir%150 "
        f"-S {since} 2>/dev/null",
    )
    found = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) >= 3 and run_id in parts[-1]:
            found.append(parts[0])
    return sorted(set(found))


def _wait_terminal_scheduler(profile: dict, job_id: str) -> str:
    """Poll until the job leaves the queue, then read its state from sacct.

    The wait holds no dispatcher session, so the 36 h token lease does not
    bound it; the queue ETA may drift under Priority congestion (site
    precedent: next-day backfill), hence the 48 h ceiling.
    """
    deadline = time.time() + RESUME_WAIT_DEADLINE_SEC
    while time.time() < deadline:
        queued = _ssh(
            profile, f"squeue -j {job_id} -h -o '%T' 2>/dev/null || true"
        ).strip()
        if queued:
            print(f"[resume] job {job_id} scheduler state={queued.splitlines()[0]}", flush=True)
            time.sleep(RESUME_POLL_SEC)
            continue
        acct = _ssh(
            profile,
            f"sacct -X -P -n --format=State -j {job_id} 2>/dev/null | head -1",
        ).strip()
        if acct:
            return acct
        print(f"[resume] job {job_id} left the queue, accounting not ready", flush=True)
        time.sleep(30)
    raise QualifyError(f"job {job_id} not terminal after {RESUME_WAIT_DEADLINE_SEC}s")


def locate_gpu_job(
    profile: dict, run_id: str, hint: str | None, since: str | None
) -> tuple[str, str]:
    """Anchor on the known scheduler id; verify identity via WorkDir.

    A stale hint falls back to an active match by directory name, then to
    exactly-one accounted match since ``since``.  Zero or several matches
    fail closed — nothing is adopted, nothing is resubmitted.
    """
    workdir = _workdir_for(profile, run_id)
    if hint:
        info = _ssh(
            profile,
            f"scontrol show job {hint} 2>/dev/null | grep -E 'WorkDir|Command' "
            "|| true",
        )
        if run_id in info:
            return hint, workdir
    active = _find_active_by_dirname(profile, run_id)
    if active:
        return active, workdir
    accounted: list[str] = []
    if since:
        accounted = _find_accounted_by_dirname(profile, run_id, since)
        if len(accounted) == 1:
            return accounted[0], workdir
    raise QualifyError(
        f"cannot uniquely identify the {run_id} scheduler job "
        f"(active={active!r}, accounted={accounted!r})"
    )


def _find_completed_cpu_job(profile: dict, run_id: str, since: str) -> str:
    """Locate the finished CPU echo job of THIS chain among today's runs.

    Primary discriminator: accounting WorkDir containing the chain dirname.
    Fallback (sites without WorkDir accounting): COMPLETED dispatcher jobs
    whose ReqTRES carries no gres/gpu.  Fail closed on ambiguity.
    """
    raw = _ssh(
        profile,
        "sacct -X -P -n --format=JobID,JobName%25,State%12,ReqTRES%100,"
        f"Start%24,End%24,WorkDir%150 -S {since} 2>/dev/null",
    )
    exact: list[str] = []
    weak: list[str] = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) < 7 or "dispatcher-qual-job-0001" not in parts[1]:
            continue
        job_id, _name, state, req_tres, _start, _end, workdir = parts[:7]
        if state != "COMPLETED":
            continue
        if run_id in workdir:
            exact.append(job_id)
            continue
        no_gpu = "gres" not in req_tres and "gpu" not in req_tres
        if no_gpu:
            weak.append(job_id)
    pool = sorted(set(exact or weak))
    if len(pool) != 1:
        raise QualifyError(
            f"cannot uniquely identify the {run_id} scheduler job "
            f"(candidates={pool})"
        )
    return pool[0]


def _audit_marker(audit_path: Path) -> str:
    """The durable SUBMIT_INTENT marker from the interrupted chain's ledger."""
    if not audit_path.is_file():
        raise QualifyError(f"audit ledger missing: {audit_path}")
    marker = ""
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = (json.loads(line).get("event") or {})
        if event.get("kind") == "SUBMIT_INTENT":
            marker = event.get("marker", "")
    if not marker:
        raise QualifyError(f"no durable SUBMIT_INTENT marker in {audit_path}")
    return marker


def _find_interrupted_stamp(canary_root: Path) -> str:
    """Auto-detect the interrupted GPU chain: unique ``run-gpu-*`` dir
    carrying an un-settled SUBMIT_INTENT audit ledger."""
    candidates: list[str] = []
    for path in sorted(canary_root.glob("run-gpu-*")):
        if not path.is_dir():
            continue
        audit = path / "audit.jsonl"
        if not audit.is_file():
            continue
        try:
            marker = _audit_marker(audit)
        except QualifyError:
            continue
        event_kinds = {
            (json.loads(line).get("event") or {}).get("kind")
            for line in audit.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        if "SETTLEMENT_BEGIN" in event_kinds:
            continue  # already settled; nothing to resume
        candidates.append(path.name)
    if len(candidates) != 1:
        raise QualifyError(
            f"cannot auto-detect the interrupted GPU chain "
            f"(candidates={candidates}); pass --stamp"
        )
    return candidates[0].removeprefix("run-gpu-nvidia-probe-containment-")


def _rehydrate_ownership(
    gateway,
    run_id: str,
    operation_id: str,
    marker: str,
    slurm_id: str,
    workdir: str,
) -> None:
    """Reconstruct the trusted-side ownership the dead process held in memory.

    Every fact comes from durable sources: the marker from the audit ledger,
    the scheduler id and workdir from the scheduler.  This mirrors the same
    adoption a frozen ``_submit_v2`` gap resolution would perform, minus the
    submission itself — the job was already submitted by the dead process.
    An already-known adapter record (the live job at the scheduler) is never
    clobbered; rehydration only fills absent state.
    """
    adapter = gateway._adapter
    adapter._jobs.setdefault(
        "job-0001", {"slurm_id": slurm_id, "workspace": workdir}
    )
    adapter._markers[marker] = "job-0001"
    adapter._idem["placeholder"] = "job-0001"
    adapter._ops[(run_id, operation_id)] = "job-0001"
    lineage = gateway._op_attempts.setdefault(run_id, {})
    lineage.setdefault(operation_id, {})[1] = "job-0001"
    gateway._remember(run_id, "placeholder", "job-0001")


def _probe_assertions(stdout: str, probe_class: str) -> dict:
    """Cross-check the raw BENCH_PROBE lines and markers of a fetched job.

    Shared contract between the canary path and resume: the structured probe
    results a resume record carries must survive the same verifier re-parse
    as a fresh canary's.
    """
    parsed = parse_probe_stdout(stdout)
    keys = (
        "workspace_rw",
        "home_sentinel_absent",
        "credential_sentinel_absent",
        "other_run_dir_absent",
        "solution_absent",
        "reference_absent",
        "runs_root_not_listable",
    )
    missing = [k for k in keys if parsed["probes"].get(k) != "pass"]
    if missing:
        raise QualifyError(f"{probe_class}: probes absent/failed: {missing}")
    if MARKER_CONTAINMENT not in stdout:
        raise QualifyError(f"{probe_class}: containment marker missing")
    results = {k: parsed["probes"].get(k) == "pass" for k in keys}
    if probe_class == "gpu":
        if MARKER_GPU not in stdout:
            raise QualifyError("gpu: device marker missing")
        if not parsed["gpu_device_name"]:
            raise QualifyError(f"device line unparseable: {stdout[-600:]}")
        results["gpu_device_name"] = parsed["gpu_device_name"]
        results["gpu_memory_total_mb"] = parsed["gpu_memory_total_mb"]
    elif parsed["gpu_device_name"]:
        raise QualifyError("unexpected device line on cpu partition")
    return results


def _make_transport(profile: dict):
    """Same transport construction as ``_open_session``, minus any session."""
    options = {k: str(v) for k, v in (profile["ssh"].get("options") or {}).items()}
    options.setdefault("ControlMaster", "auto")
    options.setdefault("ControlPath", "/tmp/dftworld-qual-cm-%C")
    options.setdefault("ControlPersist", "600")
    return SshSlurmTransport(
        ssh=SshConfig(
            host=profile["ssh"]["host"],
            user=profile["ssh"].get("user") or None,
            port=int(profile["ssh"].get("port") or 22) or None,
            options=options,
        ),
        workspace=str(ROOT),
        remote_workspace=profile["paths"]["remote_root"],
        sync="sync_back",
    )


def _single_attempt_report(run_id: str, op: str, usage: dict) -> dict:
    return {
        "run_id": run_id,
        "attempts": [
            {"operation_id": op, "attempt": 1, "job_id": "job-0001",
             "state": "SUCCEEDED"}
        ],
        "cancelled_jobs": [],
        "usage": dict(usage),
    }


def _resume_gpu(
    profile: dict,
    site,
    lock: dict,
    *,
    run_id: str,
    operation_id: str,
    gpu_job_id: str | None,
    since: str | None,
    stamp: str,
    runtime_decl: str,
) -> dict:
    """Reattach to the GPU run and finish collection through the real API."""
    slurm_id, workdir = locate_gpu_job(profile, run_id, gpu_job_id, since)
    print(f"[resume] GPU scheduler job={slurm_id} workdir={workdir}", flush=True)
    raw_state = _wait_terminal_scheduler(profile, slurm_id)
    print(f"[resume] GPU job reached scheduler-terminal state={raw_state}", flush=True)

    resolved_gpu = site.resolve_workload("gpu")
    session, transport = _open_session(site, resolved_gpu, profile, lock, run_id)
    try:
        marker = _audit_marker(CANARY_ROOT / run_id / "audit.jsonl")
        _rehydrate_ownership(
            session.gateway, run_id, operation_id, marker, slurm_id, workdir
        )
        state = session.status("job-0001")["state"]
        print(f"[resume] gateway status={state}", flush=True)
        accounting = transport.accounting(slurm_id)
        exit_code = int((accounting.get("exit_code_raw") or "-1").split(":")[0])

        local_out = CANARY_ROOT / f"fetched-gpu-nvidia-probe-containment-{stamp}"
        fetched = transport.fetch(
            [f"{workdir}/stdout.log", f"{workdir}/stderr.log"], str(local_out)
        )
        by_name = {p.name: p for p in fetched if p.exists()}
        stdout = (
            by_name["stdout.log"].read_text(encoding="utf-8", errors="replace")
            if "stdout.log" in by_name else ""
        )
        stderr_text = (
            by_name["stderr.log"].read_text(encoding="utf-8", errors="replace")
            if "stderr.log" in by_name else ""
        )
        record = {
            "canary": "gpu-nvidia-probe-containment",
            "run_id": run_id,
            "operation_id": operation_id,
            "attempt": 1,
            "job_id": "job-0001",
            "runtime_decl": runtime_decl,
            "state": state,
            "exit_code": exit_code,
            "gpus_requested": int(resolved_gpu.max_gpus),
            "requested_resources": {
                "cpus": int(resolved_gpu.max_cpus),
                "memory_gb": min(int(resolved_gpu.max_memory_gb), 32),
                "gpus": int(resolved_gpu.max_gpus),
                # Same convention the canary phase submits under (the verifier
                # does not compare walltime against accounting).
                "walltime_minutes": 20,
            },
            "probe_class": "gpu",
            "scheduler_job_id": str(slurm_id),
            "accounting": accounting,
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr_text[-800:],
        }
        if state != "SUCCEEDED":
            record_dump = CANARY_ROOT / f"FAILED-gpu-{stamp}.record.json"
            record_dump.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
            raise QualifyError(f"GPU job ended {state}: {stdout[-500:]}")
        record["probe_results"] = _probe_assertions(stdout, "gpu")

        # Real protocol settlement over the reconstructed lineage: appends
        # SETTLEMENT_BEGIN (and, at close, token_revoked) to the SAME chain.
        report = session.settle(cancel_pending=True)
        record["settlement"] = {"report": report.to_dict(), "digest": report.digest}
        record["fetch_manifest"] = {
            "job_id": "job-0001",
            "entries": [
                {
                    "path": name_,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "size_bytes": p.stat().st_size,
                }
                for name_, p in sorted(by_name.items())
            ],
        }
        record["artifacts_dir"] = local_out.name
        record["audit_log"] = f"{run_id}/audit.jsonl"
        return record
    finally:
        session.close()


def _resume_cpu(
    profile: dict,
    site,
    lock: dict,
    *,
    run_id: str,
    operation_id: str,
    since: str,
    stamp: str,
    runtime_decl: str,
) -> dict:
    """Read-only rebuild of the CPU record: disk artifacts + fresh sacct.

    The CPU chain already carries the full submission/settlement event set,
    so no session is opened and its audit is not touched.
    """
    transport = _make_transport(profile)
    cpu_slurm = _find_completed_cpu_job(profile, run_id, since)
    print(f"[resume] CPU scheduler job identified: {cpu_slurm}", flush=True)
    accounting = transport.accounting(cpu_slurm)
    norm = normalize_state(accounting.get("raw_state", ""))
    if norm is None or norm.name != "COMPLETED":
        raise QualifyError(
            f"CPU accounting state drifted: {accounting.get('raw_state')!r}"
        )
    exit_code = int((accounting.get("exit_code_raw") or "-1").split(":")[0])
    if exit_code != 0:
        raise QualifyError(f"CPU exit code drifted: {exit_code}")

    art_dir = CANARY_ROOT / f"fetched-cpu-echo-probe-containment-{stamp}"
    if not (art_dir / "stdout.log").is_file():
        raise QualifyError(
            f"CPU artifacts missing under {art_dir}; nothing to reconstruct"
        )
    stdout = (art_dir / "stdout.log").read_text(encoding="utf-8", errors="replace")
    stderr_text = (art_dir / "stderr.log").read_text(
        encoding="utf-8", errors="replace"
    )
    probe_results = _probe_assertions(stdout, "cpu")
    resolved_cpu = site.resolve_workload("cpu")

    report = _single_attempt_report(run_id, operation_id, {"jobs": 1, "submitted": 1})
    return {
        "canary": "cpu-echo-probe-containment",
        "run_id": run_id,
        "operation_id": operation_id,
        "attempt": 1,
        "job_id": "job-0001",
        "runtime_decl": runtime_decl,
        "state": "SUCCEEDED",
        "exit_code": exit_code,
        "gpus_requested": 0,
        "requested_resources": {
            "cpus": int(resolved_cpu.max_cpus),
            "memory_gb": min(int(resolved_cpu.max_memory_gb), 32),
            "gpus": 0,
            "walltime_minutes": 15,
        },
        "probe_class": "cpu",
        "scheduler_job_id": cpu_slurm,
        "accounting": accounting,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr_text[-800:],
        "probe_results": probe_results,
        "settlement": {
            "report": report,
            "digest": compute_settlement_digest(report),
        },
        "fetch_manifest": {
            "job_id": "job-0001",
            "entries": [
                {
                    "path": p.name,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "size_bytes": p.stat().st_size,
                }
                for p in sorted(art_dir.iterdir())
                if p.is_file()
            ],
        },
        "artifacts_dir": art_dir.name,
        "audit_log": f"{run_id}/audit.jsonl",
    }


def _acquire_resume_lock() -> int:
    """Exclusive singleton guard: a second resume instance must never append
    duplicate protocol events to the same audit chain."""
    import fcntl

    lock_path = Path("/tmp/bench-hpc-resume.lock")
    fh = open(lock_path, "w")  # noqa: SIM115 - held for process lifetime
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another resume instance already holds the lock", flush=True)
        return 1
    fh.write(str(os.getpid()))
    fh.flush()
    globals()["_RESUME_LOCK_FH"] = fh
    return 0


def _resolved_dict(r) -> dict:
    return {
        "workload_type": r.workload_type,
        "queue_name": r.queue_name,
        "partition": r.partition,
        "account": r.account,
        "qos": r.qos,
        "gres": r.gres,
        "mapping_note": r.mapping_note,
    }


def resume(
    profile: dict,
    lock: dict,
    *,
    stamp: str | None,
    gpu_job_id: str | None,
    since: str | None,
) -> dict:
    """Complete an interrupted dual-canary collection without resubmission.

    The supervisor process died while polling the GPU canary AFTER the
    durable SUBMIT_INTENT landed; the scheduler job kept queueing
    independently.  This phase waits for the existing job, rehydrates the
    trusted-side ownership from the durable audit marker, settles through
    the frozen gateway API, reconstructs the CPU record read-only, cleans
    the interrupted stamp's sentinels last, and seals the same evidence
    envelope a fresh canary produces.  It NEVER submits anything.
    """
    if _acquire_resume_lock():
        raise QualifyError("another resume instance already holds the lock")
    if RECEIPT_PATH.is_file():
        raise QualifyError(
            f"receipt already sealed at {RECEIPT_PATH}; nothing to resume"
        )
    stamp = stamp or _find_interrupted_stamp(CANARY_ROOT)
    print(f"[resume] interrupted stamp={stamp}", flush=True)

    site = _build_site_profile(profile)
    resolved_cpu, resolved_gpu = _assert_canary_routes(profile, site)
    runtime_decl = f"matclaw-cips@sha256:{lock['runtime']['sif_sha256']}"

    evidence: dict[str, object] = {
        "phase": "canary",
        "authorization": "user-authorized scope: two short jobs "
        "(CPU echo/hostname + containment on the native cpu partition; GPU "
        "nvidia-smi + containment under --gres=gpu:1); no CP2K, no image "
        "transfer, nothing else (the cancel probe is its own phase and its "
        "own authorization)",
        "site_profile_digest": site.digest,
        "resolved_workloads": {
            "cpu": _resolved_dict(resolved_cpu),
            "gpu": _resolved_dict(resolved_gpu),
        },
        "native_cpu_partition_accessible": True,
        "sentinel_cleanup": {},
        "single_mount_check": (
            "wrapper renders exactly one --bind <workdir>:/workspace:rw; the "
            "in-container probe additionally proves the runs root is not listable"
        ),
        "jobs": [],
        "cp2k_gate": {
            "detail": "CP2K ENERGY canary awaits separate authorization; "
                      "absent evidence derives NOT_RUN",
        },
        "ai2kit_gate": {
            "detail": "ai2kit runtime canary awaits separate authorization; "
                      "absent evidence derives NOT_RUN",
        },
        "cancel_gate": {
            "detail": "explicit cancel probe awaits separate authorization "
                      "(--phase cancel); absent evidence derives NOT_RUN",
        },
    }

    gpu_record = _resume_gpu(
        profile, site, lock,
        run_id=f"run-gpu-nvidia-probe-containment-{stamp}",
        operation_id="qual-gpu-canary-01",
        gpu_job_id=gpu_job_id,
        since=since,
        stamp=stamp,
        runtime_decl=runtime_decl,
    )
    print("[resume] GPU record collected", flush=True)
    cpu_record = _resume_cpu(
        profile, site, lock,
        run_id=f"run-cpu-echo-probe-containment-{stamp}",
        operation_id="qual-cpu-canary-01",
        since=since,
        stamp=stamp,
        runtime_decl=runtime_decl,
    )
    print("[resume] CPU record reconstructed", flush=True)
    evidence["jobs"] = [cpu_record, gpu_record]

    # The dead process's finally never ran: clean its sentinels last, after
    # both records are complete, so absence probes cannot false-pass against
    # a job that is still running.
    home = _ssh(profile, "echo $HOME").strip()
    sentinels = {
        "home_sentinel": f"{home}/.bench-sentinel-{stamp}",
        "credential_sentinel": f"{home}/.bench-cred-sentinel-{stamp}",
        "other_run_dir": (
            f"{profile['paths']['remote_root']}/sentinel-other-run-{stamp}"
        ),
        "solution_sentinel": (
            f"{profile['paths']['remote_root']}/sentinel-solution-{stamp}"
        ),
        "reference_sentinel": (
            f"{profile['paths']['remote_root']}/sentinel-reference-{stamp}"
        ),
    }
    try:
        cleanup = _cleanup_sentinels(profile, sentinels)
        if cleanup["leftover"]:
            raise QualifyError(f"sentinel cleanup left {cleanup['leftover']}")
        evidence["sentinel_cleanup"] = cleanup

        # Same no-orphan gate as the live canary phase: the recovered stamp's
        # two runs must both be fully settled in the scheduler's view.
        evidence["orphan_check"] = _orphan_sweep(
            profile,
            [f"run-cpu-echo-probe-containment-{stamp}",
             f"run-gpu-nvidia-probe-containment-{stamp}"],
        )
        if evidence["orphan_check"]["active_total"]:
            raise QualifyError(
                f"orphan jobs still active after resume: "
                f"{evidence['orphan_check']['runs']}"
            )
    finally:
        try:
            _cleanup_sentinels(profile, sentinels)
        except QualifyError:
            pass
    print("[resume] interrupted stamp's sentinels cleaned", flush=True)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="scripts/hpc/cluster_profile.toml")
    parser.add_argument("--site", default=DEFAULT_SITE,
                        help="site evidence directory name under "
                             "evidence/hpc-dispatcher/qualification/ "
                             "(receipt + per-run evidence land here)")
    parser.add_argument("--runtime-lock",
                        default="033-matclaw-cips-domain-wall-search/reference/"
                                "compute-runtime.lock.json")
    parser.add_argument("--phase",
                        choices=("preflight", "canary", "cancel", "cp2k",
                                 "ai2kit", "verify", "resume"),
                        required=True)
    parser.add_argument("--verify", metavar="RECEIPT_JSON")
    parser.add_argument("--stamp", default=None,
                        help="interrupted chain stamp for --phase resume "
                             "(auto-detected when omitted)")
    parser.add_argument("--gpu-job-id", default=None,
                        help="scheduler id of the queued GPU canary, as "
                             "observed at crash time (--phase resume)")
    parser.add_argument("--since", default=None,
                        help="sacct start time for exact WorkDir lookup "
                             "(--phase resume)")
    parser.add_argument("--cp2k-lock", default=None,
                        help="runtime lock for the cp2k stack (required by "
                             "--phase cp2k): runtime.{sif_path_remote,"
                             "sif_sha256} + cp2k.{binary,version}")
    parser.add_argument("--ai2kit-lock",
                        default="reference/runtime/ai2kit-runtime.lock.json",
                        help="runtime lock for the ai2kit stack (required by "
                             "--phase ai2kit): runtime.{sif_path_remote,"
                             "sif_sha256} + software.ai2_kit")
    parser.add_argument("--authorized", action="store_true",
                        help="explicit user authorization for --phase cancel, "
                             "--phase cp2k and --phase ai2kit (each is its "
                             "own scope, separate from the 2026-08-22 echo "
                             "canary)")
    args = parser.parse_args()

    profile_path = ROOT / args.profile

    if args.verify:
        result = verify(Path(args.verify), profile_path=profile_path)
        return 0 if result["consistent"] else 1

    set_site(args.site)

    if args.phase == "cancel":
        if not args.authorized:
            parser.error(
                "--phase cancel requires --authorized: the cancel probe "
                "submits a 15-minute RUNNING sleep on the cpu route before "
                "tearing it down — its own authorization scope"
            )
        profile = _load_profile(profile_path)
        lock = json.loads((ROOT / args.runtime_lock).read_text(encoding="utf-8"))
        ok = cancel_phase(profile, lock, profile_path=profile_path)
        return 0 if ok else 1

    if args.phase == "cp2k":
        if not args.cp2k_lock:
            parser.error("--phase cp2k requires --cp2k-lock")
        if not args.authorized:
            parser.error(
                "--phase cp2k requires --authorized: no cp2k authorization "
                "is on file (the 2026-08-22 scope excluded CP2K)"
            )
        profile = _load_profile(profile_path)
        cp2k_lock = json.loads((ROOT / args.cp2k_lock).read_text(encoding="utf-8"))
        ok = cp2k_phase(
            profile,
            cp2k_lock,
            cp2k_lock_relpath=args.cp2k_lock,
            profile_path=profile_path,
        )
        return 0 if ok else 1

    if args.phase == "ai2kit":
        if not args.authorized:
            parser.error(
                "--phase ai2kit requires --authorized: no ai2kit canary "
                "authorization is on file (the 2026-08-22 scope excluded "
                "runtime canaries for new stacks)"
            )
        profile = _load_profile(profile_path)
        ai2kit_lock = json.loads(
            (ROOT / args.ai2kit_lock).read_text(encoding="utf-8")
        )
        ok = ai2kit_phase(
            profile,
            ai2kit_lock,
            ai2kit_lock_relpath=args.ai2kit_lock,
            profile_path=profile_path,
        )
        return 0 if ok else 1

    profile = _load_profile(profile_path)
    lock = json.loads((ROOT / args.runtime_lock).read_text(encoding="utf-8"))

    if args.phase == "preflight":
        preflight(profile, lock)
        return 0

    if args.phase == "resume":
        CANARY_ROOT.mkdir(parents=True, exist_ok=True)
        evidence = resume(
            profile, lock,
            stamp=args.stamp,
            gpu_job_id=args.gpu_job_id,
            since=args.since,
        )
        receipt = build_receipt(profile, lock, evidence,
                                lock_relpath=args.runtime_lock)
        result = verify(receipt, profile_path=profile_path)
        derived = result["derived"]
        print(
            f"receipt written (resume): {receipt}  "
            f"derived={derived['qualification_status']} "
            f"formal_qualified={derived['formal_qualified']}"
        )
        return 0 if result["consistent"] else 1

    if args.phase == "canary" and RECEIPT_PATH.is_file():
        # Stale-evidence rule: old qualification evidence is archived, never
        # overwritten in place.  A fresh attempt needs a fresh site dir.
        raise QualifyError(
            f"refusing to overwrite sealed receipt {RECEIPT_PATH}: archive "
            "the site directory (or choose a new --site) before re-running "
            "the canary"
        )

    CANARY_ROOT.mkdir(parents=True, exist_ok=True)
    evidence = canary(profile, lock)
    receipt = build_receipt(profile, lock, evidence, lock_relpath=args.runtime_lock)
    result = verify(receipt, profile_path=profile_path)
    derived = result["derived"]
    print(
        f"receipt written: {receipt}  "
        f"derived={derived['qualification_status']} "
        f"formal_qualified={derived['formal_qualified']}"
    )
    return 0 if result["consistent"] else 1


if __name__ == "__main__":
    sys.exit(main())
