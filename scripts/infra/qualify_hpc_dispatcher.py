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

  verify     — replay every anchor offline and derive the status; prints the
               derivation result and exits non-zero on any broken anchor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
    parse_cp2k_output,
    parse_probe_stdout,
    verify_receipt,
)
from scripts.ablation.transport.slurm_transport import (  # noqa: E402
    JobState,
    SshConfig,
    SshSlurmTransport,
)

CANARY_ROOT = ROOT / "evidence" / "hpc-dispatcher" / "qualification" / "site-v1"
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
    sinfo = _ssh(profile, "sinfo -h -o '%R'")
    known = {ln.strip() for ln in sinfo.splitlines() if ln.strip()}
    # comma lists are legal sbatch multi-partitions; validate each element
    parts = [p.strip() for p in partition.split(",") if p.strip()]
    unknown = [p for p in parts if p not in known]
    if unknown:
        raise QualifyError(
            f"partitions {unknown} from {partition!r} not known to sinfo")
    checks["partition"] = partition

    cpu_partition = profile["slurm"].get("cpu_partition")
    if cpu_partition:
        cpu_sinfo = _ssh(
            profile, f"sinfo -h -o '%R' | grep -Fx {shlex.quote(cpu_partition)}"
        )
        if cpu_partition not in cpu_sinfo:
            raise QualifyError(
                f"cpu partition {cpu_partition!r} not known to sinfo"
            )
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


# -- phase: canary (authorized scope: one combined GPU job) -------------------


def canary(profile: dict, lock: dict, *, skip_preflight: bool = False) -> dict:
    """Two independent short canaries after cpu-ACL restoration:

      1. CPU job (native ``cpu`` partition, gpus=0): echo/hostname +
         containment probes.
      2. GPU job (``gpu`` partition, --gres=gpu:1): nvidia-smi device probe +
         containment probes.

    Uses the production SiteProfile resolver for resource mapping and live
    ACL pre-checks on both partitions.  The ACL-era assumption that cpu
    workloads must ride the gpu queue is gone: the profile must map cpu
    workloads natively or this phase refuses to run.
    """
    from dftworld_bench.hpc.site_profile import SiteProfileBlockedError

    if not skip_preflight:
        preflight(profile, lock)

    # Production SiteProfile resolver — single source of truth.
    site = _build_site_profile(profile)
    resolved_gpu = site.resolve_workload("gpu")
    resolved_cpu = site.resolve_workload("cpu")

    # The restored site maps cpu workloads to their native queue; refuse to
    # qualify against a profile still carrying ACL-era routing.
    mapping_cpu_queue = (site.resource_mapping.get("cpu") or {}).get("queue")
    if mapping_cpu_queue != "cpu":
        raise QualifyError(
            "SiteProfile does not map cpu workloads to the native cpu queue; "
            "set [slurm] cpu_partition in cluster_profile.toml first"
        )

    # Live ACL pre-check on both routes before any submission attempt.
    for resolved in (resolved_cpu, resolved_gpu):
        try:
            site.check_acl(resolved, ssh_fn=lambda cmd: _ssh(profile, cmd))
        except SiteProfileBlockedError:
            raise

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
        "transfer, nothing else",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="scripts/hpc/cluster_profile.toml")
    parser.add_argument("--runtime-lock",
                        default="033-matclaw-cips-domain-wall-search/reference/"
                                "compute-runtime.lock.json")
    parser.add_argument("--phase",
                        choices=("preflight", "canary", "cp2k", "verify"),
                        required=True)
    parser.add_argument("--verify", metavar="RECEIPT_JSON")
    parser.add_argument("--cp2k-lock", default=None,
                        help="runtime lock for the cp2k stack (required by "
                             "--phase cp2k): runtime.{sif_path_remote,"
                             "sif_sha256} + cp2k.{binary,version}")
    parser.add_argument("--authorized", action="store_true",
                        help="explicit user authorization for --phase cp2k "
                             "(separate scope from the 2026-08-22 echo canary)")
    args = parser.parse_args()

    profile_path = ROOT / args.profile

    if args.verify:
        result = verify(Path(args.verify), profile_path=profile_path)
        return 0 if result["consistent"] else 1

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

    profile = _load_profile(profile_path)
    lock = json.loads((ROOT / args.runtime_lock).read_text(encoding="utf-8"))

    if args.phase == "preflight":
        preflight(profile, lock)
        return 0

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
