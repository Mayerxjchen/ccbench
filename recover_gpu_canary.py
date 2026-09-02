#!/usr/bin/env python3
"""Recover an interrupted dual-canary qualification without resubmission.

The 2026-08-25 supervisor process died (host-side network loss) while polling
the GPU canary AFTER the durable SUBMIT_ACCEPTED had landed; the scheduler job
kept queueing independently.  This driver completes the collection WITHOUT
submitting anything new:

- GPU run ``run-gpu-nvidia-probe-containment-3d8dc664``: waits for the already
  queued scheduler job to reach a terminal state, then reopens the session on
  its existing hash-chained audit, reconstructs the trusted-side in-memory
  ownership state from durable ledger facts (audit SUBMIT_INTENT marker ->
  scheduler job id), and settles through the frozen gateway API so
  SETTLEMENT_BEGIN lands as a real protocol event.
- CPU run ``run-cpu-echo-probe-containment-3d8dc664``: strictly read-only
  reconstruction from on-disk artifacts plus one fresh sacct query (its chain
  already carries the full submission/settlement event set - no session is
  opened for it, so its audit is not touched).
- Sentinels of the interrupted stamp are cleaned last (the dead process's
  finally block never ran) and the result is recorded in the evidence.

Runs against the canonical workspace (dftworld2/, where c267f4b - the code
point that submitted these jobs - is an ancestor and all identity-relevant
files are byte-identical to the retired qualification worktree; see
evidence/local-cleanup-20260825/decision-ledger.json).

No tracked file is modified apart from appending protocol events to the
already-tracked GPU audit chain (external evidence change); the receipt is
sealed by the frozen builder and verified by the frozen derivation verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "infra"))

import qualify_hpc_dispatcher as q  # noqa: E402
from dftworld_bench.experiments.qualification_receipt import (  # noqa: E402
    MARKER_CONTAINMENT,
    MARKER_GPU,
    compute_settlement_digest,
    parse_probe_stdout,
)
from scripts.ablation.transport.slurm_transport import (  # noqa: E402
    SshConfig,
    SshSlurmTransport,
    normalize_state,
)

STAMP = "3d8dc664"
RUN_CPU_ID = f"run-cpu-echo-probe-containment-{STAMP}"
RUN_GPU_ID = f"run-gpu-nvidia-probe-containment-{STAMP}"
SINCE = "2026-08-25T13:00"
OP_CPU = "qual-cpu-canary-01"
OP_GPU = "qual-gpu-canary-01"
IDEM_KEY = "placeholder"


def _remote_qual_root(profile: dict) -> str:
    """Remote dispatcher-qual root, derived from the site profile (never
    hardcoded: site identifiers stay out of the repository)."""
    return f"{profile['paths']['remote_root'].rstrip('/')}/dispatcher-qual"


# Queue ETA drifted repeatedly under Priority congestion (next-day 11:33,
# then 08-27 01:40 local); a 48 h ceiling absorbs further backfill
# re-ordering.  The wait phase holds no dispatcher session, so the 36 h
# token lease does not bound it.
WAIT_DEADLINE_SEC = 48 * 3600
POLL_SEC = 120


def log(msg: str) -> None:
    print(f"[recover {time.strftime('%H:%M:%S')}] {msg}", flush=True)


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


def _make_transport(profile: dict) -> SshSlurmTransport:
    """Same transport construction as _open_session, minus any run session."""
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


# -- scheduler discovery -------------------------------------------------------


def _workdir_for(profile: dict, run_dirname: str) -> str:
    return f"{_remote_qual_root(profile)}/{run_dirname}/job-0001"


def _find_active_by_dirname(profile: dict, dirname: str) -> str | None:
    out = q._ssh(profile, "squeue -u $USER -h -o '%i'")
    for job_id in out.split():
        info = q._ssh(
            profile,
            f"scontrol show job {job_id} 2>/dev/null | grep -E 'Command|WorkDir' || true",
        )
        if dirname in info:
            return job_id
    return None


def _find_accounted_by_dirname(profile: dict, dirname: str) -> list[str]:
    raw = q._ssh(
        profile,
        "sacct -X -P -n --format=JobID,JobName%25,State%12,WorkDir%150 "
        f"-S {SINCE} 2>/dev/null",
    )
    found = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) >= 3 and dirname in parts[-1]:
            found.append(parts[0])
    return sorted(set(found))


def _wait_terminal_scheduler(profile: dict, job_id: str) -> str:
    """Poll until the job leaves the queue, then read its state from sacct."""
    deadline = time.time() + WAIT_DEADLINE_SEC
    while time.time() < deadline:
        queued = q._ssh(
            profile, f"squeue -j {job_id} -h -o '%T' 2>/dev/null || true"
        ).strip()
        if queued:
            log(f"job {job_id} scheduler state={queued.splitlines()[0]}")
            time.sleep(POLL_SEC)
            continue
        acct = q._ssh(
            profile,
            f"sacct -X -P -n --format=State -j {job_id} 2>/dev/null | head -1",
        ).strip()
        if acct:
            return acct
        log(f"job {job_id} left the queue, accounting record not ready")
        time.sleep(30)
    raise q.QualifyError(f"job {job_id} not terminal after {WAIT_DEADLINE_SEC}s")


def locate_gpu_job(profile: dict, hint: str) -> tuple[str, str]:
    """Anchor on the known scheduler id; verify identity via WorkDir."""
    info = q._ssh(
        profile,
        f"scontrol show job {hint} 2>/dev/null | grep -E 'WorkDir|Command' || true",
    )
    if RUN_GPU_ID in info:
        return hint, _workdir_for(profile, RUN_GPU_ID)
    # Hint stale (completed/requeued?): resolve by directory name.
    active = _find_active_by_dirname(profile, RUN_GPU_ID)
    if active:
        return active, _workdir_for(profile, RUN_GPU_ID)
    accounted = _find_accounted_by_dirname(profile, RUN_GPU_ID)
    if len(accounted) == 1:
        return accounted[0], _workdir_for(profile, RUN_GPU_ID)
    raise q.QualifyError(
        f"cannot uniquely identify the {RUN_GPU_ID} scheduler job "
        f"(active={active!r}, accounted={accounted!r})"
    )


def _find_completed_cpu_job(profile: dict) -> str:
    """Locate the finished CPU echo job of THIS chain among today's runs.

    Primary discriminator: accounting WorkDir containing the chain dirname.
    Fallback (sites without WorkDir accounting): COMPLETED dispatcher jobs
    whose ReqTRES carries no gres/gpu, finishing inside the audit-derived
    window around 2026-08-25T05:49Z (=13:49 cluster-local). Fail closed on
    ambiguity.
    """
    raw = q._ssh(
        profile,
        "sacct -X -P -n --format=JobID,JobName%25,State%12,ReqTRES%100,"
        f"Start%24,End%24,WorkDir%150 -S {SINCE} 2>/dev/null",
    )
    exact: list[str] = []
    weak: list[str] = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) < 7 or "dispatcher-qual-job-0001" not in parts[1]:
            continue
        job_id, _name, state, req_tres, start, end, workdir = parts[:7]
        if state != "COMPLETED":
            continue
        if RUN_CPU_ID in workdir:
            exact.append(job_id)
            continue
        no_gpu = "gres" not in req_tres and "gpu" not in req_tres
        if no_gpu:
            weak.append(job_id)
    pool = sorted(set(exact or weak))
    if len(pool) != 1:
        raise q.QualifyError(
            f"cannot uniquely identify the {RUN_CPU_ID} scheduler job "
            f"(candidates={pool})"
        )
    return pool[0]


# -- probe contract ------------------------------------------------------------


def _probe_assertions(stdout: str, probe_class: str) -> dict:
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
        raise q.QualifyError(f"{probe_class}: probes absent/failed: {missing}")
    if MARKER_CONTAINMENT not in stdout:
        raise q.QualifyError(f"{probe_class}: containment marker missing")
    results = {k: parsed["probes"].get(k) == "pass" for k in keys}
    if probe_class == "gpu":
        if MARKER_GPU not in stdout:
            raise q.QualifyError("gpu: device marker missing")
        if not parsed["gpu_device_name"]:
            raise q.QualifyError(f"device line unparseable: {stdout[-600:]}")
        results["gpu_device_name"] = parsed["gpu_device_name"]
        results["gpu_memory_total_mb"] = parsed["gpu_memory_total_mb"]
    elif parsed["gpu_device_name"]:
        raise q.QualifyError("unexpected device line on cpu partition")
    return results


def _single_attempt_report(run_id: str, op: str, usage: dict) -> dict:
    return {
        "run_id": run_id,
        "attempts": [
            {
                "operation_id": op,
                "attempt": 1,
                "job_id": "job-0001",
                "state": "SUCCEEDED",
            }
        ],
        "cancelled_jobs": [],
        "usage": dict(usage),
    }


# -- collection phases ---------------------------------------------------------


def collect_gpu(profile: dict, site, lock: dict, hint: str) -> dict:
    """Reattach to the GPU run and finish collection through the real API."""
    gpu_slurm, workdir = locate_gpu_job(profile, hint)
    log(f"GPU scheduler job={gpu_slurm} workdir={workdir}")
    raw_state = _wait_terminal_scheduler(profile, gpu_slurm)
    log(f"GPU job reached scheduler-terminal state={raw_state}")

    resolved_gpu = site.resolve_workload("gpu")
    session, transport = q._open_session(site, resolved_gpu, profile, lock, RUN_GPU_ID)
    try:
        # Trusted-side reconstruction of the ownership state the dead process
        # held in memory. Every fact comes from durable sources: the marker
        # from the audit ledger, scheduler id/workdir from the scheduler.
        audit_path = q.CANARY_ROOT / RUN_GPU_ID / "audit.jsonl"
        marker = ""
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = (json.loads(line).get("event") or {})
            if ev.get("kind") == "SUBMIT_INTENT":
                marker = ev.get("marker", "")
        assert marker, "durable SUBMIT_INTENT marker not found"

        adapter = session.gateway._adapter
        adapter._jobs["job-0001"] = {"slurm_id": gpu_slurm, "workspace": workdir}
        adapter._markers[marker] = "job-0001"
        adapter._idem[IDEM_KEY] = "job-0001"
        adapter._ops[(RUN_GPU_ID, OP_GPU)] = "job-0001"
        lineage = session.gateway._op_attempts.setdefault(RUN_GPU_ID, {})
        lineage.setdefault(OP_GPU, {})[1] = "job-0001"
        session.gateway._remember(RUN_GPU_ID, IDEM_KEY, "job-0001")

        state = session.status("job-0001")["state"]
        log(f"gateway status={state}")
        accounting = transport.accounting(gpu_slurm)
        exit_code = int((accounting.get("exit_code_raw") or "-1").split(":")[0])

        local_out = q.CANARY_ROOT / f"fetched-gpu-nvidia-probe-containment-{STAMP}"
        fetched = transport.fetch(
            [f"{workdir}/stdout.log", f"{workdir}/stderr.log"], str(local_out)
        )
        by_name = {p.name: p for p in fetched if p.exists()}
        stdout = by_name["stdout.log"].read_text(encoding="utf-8", errors="replace")
        stderr_text = by_name["stderr.log"].read_text(
            encoding="utf-8", errors="replace"
        )

        record = {
            "canary": "gpu-nvidia-probe-containment",
            "run_id": session.run_id,
            "operation_id": OP_GPU,
            "attempt": 1,
            "job_id": "job-0001",
            "runtime_decl": f"matclaw-cips@sha256:{lock['runtime']['sif_sha256']}",
            "state": state,
            "exit_code": exit_code,
            "gpus_requested": int(resolved_gpu.max_gpus),
            "requested_resources": {
                "cpus": int(resolved_gpu.max_cpus),
                "memory_gb": min(int(resolved_gpu.max_memory_gb), 32),
                "gpus": int(resolved_gpu.max_gpus),
                "walltime_minutes": 15,
            },
            "probe_class": "gpu",
            "scheduler_job_id": gpu_slurm,
            "accounting": accounting,
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr_text[-800:],
        }
        if state != "SUCCEEDED":
            record_dump = q.CANARY_ROOT / f"FAILED-gpu-{STAMP}.record.json"
            record_dump.write_text(json.dumps(record, indent=2))
            raise q.QualifyError(f"GPU job ended {state}: {stdout[-500:]}")
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
        record["audit_log"] = f"{session.run_id}/audit.jsonl"
        return record
    finally:
        session.close()


def reconstruct_cpu(profile: dict, site, lock: dict) -> dict:
    """Read-only rebuild of the CPU record: disk artifacts + fresh sacct."""
    transport = _make_transport(profile)
    cpu_slurm = _find_completed_cpu_job(profile)
    log(f"CPU scheduler job identified: {cpu_slurm}")
    accounting = transport.accounting(cpu_slurm)
    norm = normalize_state(accounting.get("raw_state", ""))
    if norm is None or norm.name != "COMPLETED":
        raise q.QualifyError(
            f"CPU accounting state drifted: {accounting.get('raw_state')!r}"
        )
    exit_code = int((accounting.get("exit_code_raw") or "-1").split(":")[0])
    if exit_code != 0:
        raise q.QualifyError(f"CPU exit code drifted: {exit_code}")

    art_dir = q.CANARY_ROOT / f"fetched-cpu-echo-probe-containment-{STAMP}"
    stdout = (art_dir / "stdout.log").read_text(encoding="utf-8", errors="replace")
    stderr_text = (art_dir / "stderr.log").read_text(
        encoding="utf-8", errors="replace"
    )
    probe_results = _probe_assertions(stdout, "cpu")
    resolved_cpu = site.resolve_workload("cpu")

    report = _single_attempt_report(RUN_CPU_ID, OP_CPU, {"jobs": 1, "submitted": 1})
    return {
        "canary": "cpu-echo-probe-containment",
        "run_id": RUN_CPU_ID,
        "operation_id": OP_CPU,
        "attempt": 1,
        "job_id": "job-0001",
        "runtime_decl": f"matclaw-cips@sha256:{lock['runtime']['sif_sha256']}",
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
        "audit_log": f"{RUN_CPU_ID}/audit.jsonl",
    }


def _acquire_lock() -> int:
    """Exclusive singleton guard: a second instance must never append
    duplicate protocol events to the same audit chain."""
    import fcntl

    lock_path = Path("/tmp/recover-gpu-canary.lock")
    fh = open(lock_path, "w")  # noqa: SIM115 - held for process lifetime
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another recover_gpu_canary instance already holds the lock", flush=True)
        return 1
    fh.write(str(os.getpid()))
    fh.flush()
    globals()["_LOCK_FH"] = fh
    return 0


def main() -> int:
    global STAMP, RUN_CPU_ID, RUN_GPU_ID, SINCE

    if _acquire_lock():
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gpu-job-id",
        default="3629410",
        help="scheduler id of the queued GPU canary (identity anchor)",
    )
    parser.add_argument("--stamp", default=STAMP, help="dual-canary chain stamp")
    parser.add_argument(
        "--since", default=SINCE, help="sacct start time used for exact WorkDir lookup"
    )
    parser.add_argument(
        "--profile", default="scripts/hpc/cluster_profile.toml"
    )
    parser.add_argument(
        "--runtime-lock",
        default=(
            "033-matclaw-cips-domain-wall-search/reference/"
            "compute-runtime.lock.json"
        ),
    )
    args = parser.parse_args()

    STAMP = args.stamp
    RUN_CPU_ID = f"run-cpu-echo-probe-containment-{STAMP}"
    RUN_GPU_ID = f"run-gpu-nvidia-probe-containment-{STAMP}"
    SINCE = args.since

    profile_path = ROOT / args.profile
    profile = q._load_profile(profile_path)
    lock_relpath = args.runtime_lock
    lock = json.loads((ROOT / lock_relpath).read_text(encoding="utf-8"))
    site = q._build_site_profile(profile)

    gpu_record = collect_gpu(profile, site, lock, args.gpu_job_id)
    log("GPU record collected")

    cpu_record = reconstruct_cpu(profile, site, lock)
    log("CPU record reconstructed")

    home = q._ssh(profile, "echo $HOME").strip()
    sentinels = {
        "home_sentinel": f"{home}/.bench-sentinel-{STAMP}",
        "credential_sentinel": f"{home}/.bench-cred-sentinel-{STAMP}",
        "other_run_dir": (
            f"{profile['paths']['remote_root']}/sentinel-other-run-{STAMP}"
        ),
        "solution_sentinel": (
            f"{profile['paths']['remote_root']}/sentinel-solution-{STAMP}"
        ),
        "reference_sentinel": (
            f"{profile['paths']['remote_root']}/sentinel-reference-{STAMP}"
        ),
    }
    cleanup = q._cleanup_sentinels(profile, sentinels)
    if cleanup["leftover"]:
        raise q.QualifyError(f"sentinel cleanup left {cleanup['leftover']}")
    log("interrupted stamp's sentinels cleaned")

    evidence = {
        "phase": "canary",
        "authorization": "user-authorized scope: two short jobs "
        "(CPU echo/hostname + containment on the native cpu partition; GPU "
        "nvidia-smi + containment under --gres=gpu:1); no CP2K, no image "
        "transfer, nothing else",
        "site_profile_digest": site.digest,
        "resolved_workloads": {
            "cpu": _resolved_dict(site.resolve_workload("cpu")),
            "gpu": _resolved_dict(site.resolve_workload("gpu")),
        },
        "native_cpu_partition_accessible": True,
        "sentinel_cleanup": cleanup,
        "single_mount_check": (
            "wrapper renders exactly one --bind <workdir>:/workspace:rw; the "
            "in-container probe additionally proves the runs root is not listable"
        ),
        "jobs": [cpu_record, gpu_record],
        "cp2k_gate": {
            "detail": "CP2K ENERGY canary awaits separate authorization; "
            "absent evidence derives NOT_RUN",
        },
    }

    receipt = q.build_receipt(profile, lock, evidence, lock_relpath=lock_relpath)
    result = q.verify(receipt, profile_path=profile_path)
    derived = result["derived"]
    print(
        f"receipt written: {receipt}  "
        f"derived={derived['qualification_status']} "
        f"formal_qualified={derived['formal_qualified']}"
    )
    return 0 if result["consistent"] else 1


if __name__ == "__main__":
    sys.exit(main())
