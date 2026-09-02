"""``--phase resume`` — pure logic + fake SSH + real-gateway rehydration.

The resume phase completes collection AFTER the supervisor process died while
polling the GPU canary. It never resubmits: it waits for the already-queued
scheduler job to reach a terminal state, reconstructs in-memory ownership from
the durable audit SUBMIT_INTENT marker, and settles through the frozen gateway
API on the SAME hash-chained audit file.

SSH-coupled parts are driven against a fake ``q._ssh`` whose canned responses
model the long-queue lifecycle (PENDING under Priority → backfill → RUNNING →
COMPLETED). The rehydration + settlement contract is exercised against a real
``Gateway`` over a temp audit chain, proving ``SETTLEMENT_BEGIN`` lands on the
same chain and no submission is ever replayed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts" / "infra") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "infra"))

import qualify_hpc_dispatcher as q  # noqa: E402
from dftworld_bench.experiments.qualification_receipt import (  # noqa: E402
    CODE_IDENTITY_PATHS,
    MARKER_CONTAINMENT,
    MARKER_GPU,
)
from dftworld_bench.hpc.adapters.process_test import ProcessTestAdapter  # noqa: E402
from dftworld_bench.hpc.audit import GatewayAudit  # noqa: E402
from dftworld_bench.hpc.dispatcher import DispatcherSession  # noqa: E402
from dftworld_bench.hpc.gateway import ALL_OPS, Gateway  # noqa: E402

STAMP = "3d8dc664"
RUN_GPU_ID = f"run-gpu-nvidia-probe-containment-{STAMP}"
RUN_CPU_ID = f"run-cpu-echo-probe-containment-{STAMP}"
SINCE = "2026-08-25T13:00"

PROFILE = {
    "ssh": {"host": "site", "user": "operator", "port": 22, "options": {}},
    "paths": {"remote_root": "/data/bench", "apptainer": "/bin/apptainer"},
}

WORKDIR_GPU = q._workdir_for(PROFILE, RUN_GPU_ID)
CPU_PROBE_STDOUT = "\n".join(
    [
        "HOST=cn001",
        "BENCH_PROBE workspace_rw=pass",
        "BENCH_PROBE home_sentinel_absent=pass",
        "BENCH_PROBE credential_sentinel_absent=pass",
        "BENCH_PROBE other_run_dir_absent=pass",
        "BENCH_PROBE solution_absent=pass",
        "BENCH_PROBE reference_absent=pass",
        "BENCH_PROBE runs_root_not_listable=pass",
        MARKER_CONTAINMENT,
    ]
) + "\n"

GPU_PROBE_STDOUT = (
    "BENCH_GPU_DEVICE mem_mib=81920 name=NVIDIA-A100-SXM4-80GB\n"
    "GPU_DEVICE_OK\n"
    + CPU_PROBE_STDOUT
    + f"{MARKER_GPU}\n"
)


def _audit_entry(kind: str, **extra) -> dict:
    return {"ts": 1, "event": {"kind": kind, "run_id": RUN_GPU_ID, **extra}}


def _write_audit(path: Path, kinds: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(entry) for entry in kinds) + "\n",
        encoding="utf-8",
    )


# -- _probe_assertions (pure) -------------------------------------------------


def test_probe_assertions_cpu_passes_without_gpu_device():
    results = q._probe_assertions(CPU_PROBE_STDOUT, "cpu")
    assert results["workspace_rw"] is True
    assert results["home_sentinel_absent"] is True
    assert "gpu_device_name" not in results


def test_probe_assertions_gpu_records_device():
    results = q._probe_assertions(GPU_PROBE_STDOUT, "gpu")
    assert results["gpu_device_name"] == "NVIDIA-A100-SXM4-80GB"
    assert results["gpu_memory_total_mb"] == 81920


def test_probe_assertions_rejects_missing_probe():
    bad = CPU_PROBE_STDOUT.replace(
        "BENCH_PROBE credential_sentinel_absent=pass\n", ""
    )
    with pytest.raises(q.QualifyError, match="credential_sentinel_absent"):
        q._probe_assertions(bad, "cpu")


def test_probe_assertions_rejects_device_line_on_cpu_partition():
    with pytest.raises(q.QualifyError, match="unexpected device line"):
        q._probe_assertions(GPU_PROBE_STDOUT, "cpu")


def test_probe_assertions_rejects_gpu_without_device_marker():
    missing_marker = GPU_PROBE_STDOUT.replace(f"{MARKER_GPU}\n", "")
    with pytest.raises(q.QualifyError, match="device marker missing"):
        q._probe_assertions(missing_marker, "gpu")


# -- locate_gpu_job (fake _ssh) ------------------------------------------------


def test_locate_gpu_job_anchors_on_hint(monkeypatch):
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        assert "scontrol show job" in command
        return f"WorkDir={WORKDIR_GPU} Command=/bin/sh"

    monkeypatch.setattr(q, "_ssh", fake_ssh)
    job_id, workdir = q.locate_gpu_job(PROFILE, RUN_GPU_ID, hint="3669100",
                                        since=SINCE)
    assert job_id == "3669100"
    assert workdir == WORKDIR_GPU


def test_locate_gpu_job_resolves_by_dirname_when_hint_stale(monkeypatch):
    """Hint stale (job completed/requeued): resolve via squeue + WorkDir."""
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        if command.startswith("squeue -u"):
            return "3669200\n"
        if command.startswith("scontrol show job 3669200"):
            return f"WorkDir={WORKDIR_GPU}"
        return ""

    monkeypatch.setattr(q, "_ssh", fake_ssh)
    job_id, workdir = q.locate_gpu_job(PROFILE, RUN_GPU_ID, hint="0000",
                                        since=SINCE)
    assert job_id == "3669200"
    assert workdir == WORKDIR_GPU


def test_locate_gpu_job_fails_closed_on_ambiguity(monkeypatch):
    """No active job and multiple accounted jobs → fail closed (never guess)."""
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        if command.startswith("squeue"):
            return ""
        if command.startswith("sacct"):
            return (
                "7001|...|COMPLETED|...\n"
                "7002|...|COMPLETED|...\n"
            )
        return ""

    monkeypatch.setattr(q, "_ssh", fake_ssh)
    with pytest.raises(q.QualifyError, match="cannot uniquely identify"):
        q.locate_gpu_job(PROFILE, RUN_GPU_ID, hint="0000", since=SINCE)


# -- _wait_terminal_scheduler (fake long-queue lifecycle) ---------------------


def test_wait_terminal_scheduler_polls_pending_to_completed(monkeypatch):
    """Model the long-queue lifecycle: PENDING (Priority) → RUNNING → leaves
    the queue → sacct reports COMPLETED. The wait phase holds no session, so
    it must not bound on the 36h token lease."""
    states = iter(["PENDING", "RUNNING", "", "", "COMPLETED"])
    sleeps: list[float] = []

    def fake_ssh(profile, command, timeout=120, *, retries=4):
        if command.startswith("squeue"):
            s = next(states, "")
            return s + "\n" if s else ""
        if command.startswith("sacct"):
            return "COMPLETED\n"
        return ""

    monkeypatch.setattr(q, "_ssh", fake_ssh)
    monkeypatch.setattr(q.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(q, "RESUME_POLL_SEC", 0)

    state = q._wait_terminal_scheduler(PROFILE, "3669100")
    assert state == "COMPLETED"
    assert sleeps  # the lifecycle exercised at least one poll interval


def test_wait_terminal_scheduler_times_out(monkeypatch):
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        if command.startswith("squeue"):
            return "PENDING\n"
        return ""

    monkeypatch.setattr(q, "_ssh", fake_ssh)
    monkeypatch.setattr(q.time, "sleep", lambda s: None)
    monkeypatch.setattr(q, "RESUME_POLL_SEC", 0)
    monkeypatch.setattr(q, "RESUME_WAIT_DEADLINE_SEC", 0)
    with pytest.raises(q.QualifyError, match="not terminal"):
        q._wait_terminal_scheduler(PROFILE, "3669100")


# -- _find_completed_cpu_job (fake sacct) --------------------------------------


def test_find_completed_cpu_job_matches_workdir_exactly(monkeypatch):
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        assert "sacct" in command
        return (
            f"9001|dispatcher-qual-job-0001|COMPLETED|billing=2|2026-08-25T05:49|"
            f"2026-08-25T05:49|{RUN_CPU_ID}\n"
        )

    monkeypatch.setattr(q, "_ssh", fake_ssh)
    assert q._find_completed_cpu_job(PROFILE, RUN_CPU_ID, SINCE) == "9001"


def test_find_completed_cpu_job_fails_closed_on_multiple(monkeypatch):
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        return (
            f"9001|dispatcher-qual-job-0001|COMPLETED|billing=2|t|t|{RUN_CPU_ID}\n"
            f"9002|dispatcher-qual-job-0001|COMPLETED|billing=2|t|t|{RUN_CPU_ID}\n"
        )

    monkeypatch.setattr(q, "_ssh", fake_ssh)
    with pytest.raises(q.QualifyError, match="cannot uniquely identify"):
        q._find_completed_cpu_job(PROFILE, RUN_CPU_ID, SINCE)


def test_find_completed_cpu_job_skips_non_completed(monkeypatch):
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        return (
            "9001|dispatcher-qual-job-0001|FAILED|billing=2|t|t|/elsewhere\n"
            f"9002|dispatcher-qual-job-0001|COMPLETED|billing=2|t|t|{RUN_CPU_ID}\n"
        )

    monkeypatch.setattr(q, "_ssh", fake_ssh)
    assert q._find_completed_cpu_job(PROFILE, RUN_CPU_ID, SINCE) == "9002"


# -- durable audit marker & stamp auto-detection -------------------------------


def test_audit_marker_reads_durable_intent(tmp_path):
    _write_audit(
        tmp_path / "audit.jsonl",
        [_audit_entry("SUBMIT_INTENT", marker="m-fresh", operation_id="o"),
         _audit_entry("SUBMIT_ACCEPTED", marker="m-fresh", job_id="9")],
    )
    assert q._audit_marker(tmp_path / "audit.jsonl") == "m-fresh"


def test_audit_marker_fails_closed_without_intent(tmp_path):
    _write_audit(tmp_path / "audit.jsonl", [_audit_entry("SUBMIT_ACCEPTED")])
    with pytest.raises(q.QualifyError, match="no durable SUBMIT_INTENT"):
        q._audit_marker(tmp_path / "audit.jsonl")
    with pytest.raises(q.QualifyError, match="audit ledger missing"):
        q._audit_marker(tmp_path / "missing.jsonl")


def test_find_interrupted_stamp_detects_unique_chain(tmp_path):
    _write_audit(
        tmp_path / RUN_GPU_ID / "audit.jsonl",
        [_audit_entry("SUBMIT_INTENT", marker="m-1")],
    )
    assert q._find_interrupted_stamp(tmp_path) == STAMP


def test_find_interrupted_stamp_skips_settled_chain(tmp_path):
    _write_audit(
        tmp_path / RUN_GPU_ID / "audit.jsonl",
        [_audit_entry("SUBMIT_INTENT", marker="m-1"),
         _audit_entry("SETTLEMENT_BEGIN")],
    )
    with pytest.raises(q.QualifyError, match="cannot auto-detect"):
        q._find_interrupted_stamp(tmp_path)


def test_find_interrupted_stamp_fails_closed_on_ambiguity(tmp_path):
    for suffix in ("aaaa", "bbbb"):
        run = f"run-gpu-nvidia-probe-containment-{suffix}"
        _write_audit(
            tmp_path / run / "audit.jsonl",
            [_audit_entry("SUBMIT_INTENT", marker=f"m-{suffix}")],
        )
    with pytest.raises(q.QualifyError, match="cannot auto-detect"):
        q._find_interrupted_stamp(tmp_path)


def test_resume_refuses_when_receipt_already_sealed(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "RECEIPT_PATH", tmp_path / "receipt.json")
    (tmp_path / "receipt.json").write_text("{}", encoding="utf-8")
    with pytest.raises(q.QualifyError, match="nothing to resume"):
        q.resume(PROFILE, {"runtime": {}}, stamp=STAMP, gpu_job_id=None,
                 since=SINCE)


# -- rehydration (real gateway over a durable audit) ---------------------------


def _rehydrated_gateway(tmp_path, adapter) -> tuple[Gateway, str]:
    audit = GatewayAudit(tmp_path / "audit.jsonl")
    gateway = Gateway(adapter, audit=audit, workspace_root=tmp_path / "ws")
    return gateway, audit


def test_rehydrate_ownership_builds_exact_tables(tmp_path):
    adapter = ProcessTestAdapter(tmp_path / "site", timeout_sec=60.0)
    gateway, _audit = _rehydrated_gateway(tmp_path, adapter)
    q._rehydrate_ownership(gateway, RUN_GPU_ID, "qual-gpu-canary-01",
                           "m-1", "3629410", "/runs/x/job-0001")
    assert adapter._jobs["job-0001"] == {
        "slurm_id": "3629410", "workspace": "/runs/x/job-0001",
    }
    assert adapter._markers["m-1"] == "job-0001"
    assert adapter._idem["placeholder"] == "job-0001"
    assert adapter._ops[(RUN_GPU_ID, "qual-gpu-canary-01")] == "job-0001"
    assert gateway._op_attempts[RUN_GPU_ID]["qual-gpu-canary-01"][1] == "job-0001"
    assert gateway._jobs[RUN_GPU_ID]["placeholder"] == "job-0001"
    assert gateway._run_jobs[RUN_GPU_ID] == {"job-0001"}


def test_resume_settlement_lands_on_same_chain_and_never_submits(tmp_path):
    """Phase A (interrupted submit, no settle) then Phase B (resume):

    a fresh Gateway over the SAME audit rehydrates ownership from the durable
    marker and settles; SETTLEMENT_BEGIN appears on the same chain, and the
    adapter's submit is never called again.
    """
    run_id = "run-echo-1"
    op_id = "qual-gpu-canary-01"
    adapter = ProcessTestAdapter(tmp_path / "site", timeout_sec=60.0)

    # -- Phase A: the original submission, then "death" (no settle, no close).
    audit = GatewayAudit(tmp_path / "audit.jsonl")
    gateway_a = Gateway(adapter, audit=audit, workspace_root=tmp_path / "ws")
    token_a = gateway_a.issue(run_id, ALL_OPS)
    spec = {
        "schema_version": 1,
        "idempotency_key": "placeholder",
        "runtime": "img@sha256:" + "a" * 64,
        "command": ["/bin/echo", "ok"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0,
                      "walltime_minutes": 5},
        "inputs": [],
        "outputs": [],
    }
    submitted = gateway_a.submit(token_a, run_id, spec,
                                 operation_id=op_id, attempt=1)
    # The scheduler completes the job independently of the dead supervisor.
    for _ in range(500):
        if adapter.status(submitted["job_id"])["state"] == "SUCCEEDED":
            break
        time.sleep(0.01)

    marker = q._audit_marker(tmp_path / "audit.jsonl")
    # In the process-test world the adapter job id IS the scheduler identity;
    # rehydration must not clobber the adapter's live record either way.
    slurm_id = submitted["job_id"]
    workdir = f"/data/bench/dispatcher-qual/{run_id}/job-0001"

    # -- The dead driver's finally never ran; sentinel cleanup ordering is
    #    pinned by the operator-scripts test, not here.

    # -- Phase B: resume opens a FRESH gateway over the same audit.
    audit_b = GatewayAudit(tmp_path / "audit.jsonl")  # same file
    gateway_b = Gateway(adapter, audit=audit_b, workspace_root=tmp_path / "ws")
    q._rehydrate_ownership(gateway_b, run_id, op_id, marker, slurm_id, workdir)

    # Any submit attempt from here on is a replay bug: fail loudly.
    def _forbid_submit(*_a, **_k):
        raise AssertionError("resume must never resubmit")

    original_submit = adapter.submit
    adapter.submit = _forbid_submit  # type: ignore[method-assign]

    token_b = gateway_b.issue(run_id, ALL_OPS)
    session = DispatcherSession.__new__(DispatcherSession)
    session._lease = type(
        "Lease", (), {
            "run_id": run_id, "token": token_b, "gateway": gateway_b,
            "closed": False, "close": lambda self: None,
        },
    )()
    session._settlement = None

    report = session.settle(cancel_pending=True)
    assert [a.job_id for a in report.attempts] == [submitted["job_id"]]
    assert report.attempts[0].state == "SUCCEEDED"

    # Settlement appended to the SAME chain the dead process wrote.
    chain = audit_b.entries()
    kinds = [(e.get("event") or {}).get("kind") for e in chain]
    assert "SUBMIT_INTENT" in kinds
    assert "SETTLEMENT_BEGIN" in kinds
    assert kinds.index("SUBMIT_INTENT") < kinds.index("SETTLEMENT_BEGIN")
    adapter.submit = original_submit  # type: ignore[method-assign]


# -- deletion & anchor ----------------------------------------------------------


def test_recover_script_deleted_and_anchor_updated():
    assert "recover_gpu_canary.py" not in CODE_IDENTITY_PATHS
    assert not (ROOT / "recover_gpu_canary.py").exists()