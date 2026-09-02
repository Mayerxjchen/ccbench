"""Recover an interrupted dual-canary qualification — pure logic + fake SSH.

``recover_gpu_canary.py`` completes collection AFTER the supervisor process
died while polling the GPU canary. It never resubmits: it waits for the
already-queued scheduler job to reach a terminal state, reconstructs in-memory
ownership from the durable audit marker, and settles through the frozen
gateway API.

The recovery driver is SSH-coupled (``q._ssh``), so these tests drive its
pure-logic scheduler-discovery and state-machine functions against a fake
``_ssh`` whose canned responses model the long-queue lifecycle
(PENDING under Priority → backfill → RUNNING → COMPLETED). The pure probe
parser ``_probe_assertions`` is exercised directly.

This is NOT a full collect_gpu (the cross-session settle reconstruction is a
separate, larger seam — see Task 5 scope); it pins the parts the long-queue
wait depends on: locate uniquely, wait terminal, fail closed on ambiguity, and
parse the canary stdout contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts" / "infra") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "infra"))

import recover_gpu_canary as rgc  # noqa: E402
import qualify_hpc_dispatcher as q  # noqa: E402
from dftworld_bench.experiments.qualification_receipt import (  # noqa: E402
    MARKER_CONTAINMENT,
    MARKER_GPU,
)


PROFILE = {
    "ssh": {"host": "site", "user": "operator", "port": 22, "options": {}},
    "paths": {"remote_root": "/data/bench", "apptainer": "/bin/apptainer"},
}

WORKDIR_GPU = (
    f"{rgc._remote_qual_root(PROFILE)}/{rgc.RUN_GPU_ID}/job-0001"
)
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


# -- _probe_assertions (pure) -----------------------------------------------


def test_probe_assertions_cpu_passes_without_gpu_device():
    results = rgc._probe_assertions(CPU_PROBE_STDOUT, "cpu")
    assert results["workspace_rw"] is True
    assert results["home_sentinel_absent"] is True
    assert "gpu_device_name" not in results


def test_probe_assertions_gpu_records_device():
    results = rgc._probe_assertions(GPU_PROBE_STDOUT, "gpu")
    assert results["gpu_device_name"] == "NVIDIA-A100-SXM4-80GB"
    assert results["gpu_memory_total_mb"] == 81920


def test_probe_assertions_rejects_missing_probe():
    bad = CPU_PROBE_STDOUT.replace(
        "BENCH_PROBE credential_sentinel_absent=pass\n", ""
    )
    with pytest.raises(q.QualifyError, match="credential_sentinel_absent"):
        rgc._probe_assertions(bad, "cpu")


def test_probe_assertions_rejects_device_line_on_cpu_partition():
    with pytest.raises(q.QualifyError, match="unexpected device line"):
        rgc._probe_assertions(GPU_PROBE_STDOUT, "cpu")


def test_probe_assertions_rejects_gpu_without_device_marker():
    missing_marker = GPU_PROBE_STDOUT.replace(f"{MARKER_GPU}\n", "")
    with pytest.raises(q.QualifyError, match="device marker missing"):
        rgc._probe_assertions(missing_marker, "gpu")


# -- locate_gpu_job (fake _ssh) ---------------------------------------------


def test_locate_gpu_job_anchors_on_hint(monkeypatch):
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        assert "scontrol show job" in command
        return f"WorkDir={WORKDIR_GPU} Command=/bin/sh"

    monkeypatch.setattr(rgc.q, "_ssh", fake_ssh)
    job_id, workdir = rgc.locate_gpu_job(PROFILE, hint="3669100")
    assert job_id == "3669100"
    assert workdir == WORKDIR_GPU


def test_locate_gpu_job_resolves_by_dirname_when_hint_stale(monkeypatch):
    """Hint stale (job completed/requeued): resolve via squeue + WorkDir."""
    calls: list[str] = []

    def fake_ssh(profile, command, timeout=120, *, retries=4):
        calls.append(command)
        if command.startswith("squeue -u"):
            return "3669200\n"
        if command.startswith("scontrol show job 3669200"):
            return f"WorkDir={WORKDIR_GPU}"
        return ""

    monkeypatch.setattr(rgc.q, "_ssh", fake_ssh)
    monkeypatch.setattr(rgc, "_find_active_by_dirname", rgc._find_active_by_dirname)
    job_id, workdir = rgc.locate_gpu_job(PROFILE, hint="0000")
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

    monkeypatch.setattr(rgc.q, "_ssh", fake_ssh)
    with pytest.raises(q.QualifyError, match="cannot uniquely identify"):
        rgc.locate_gpu_job(PROFILE, hint="0000")


# -- _wait_terminal_scheduler (fake long-queue lifecycle) ------------------


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

    monkeypatch.setattr(rgc.q, "_ssh", fake_ssh)
    monkeypatch.setattr(rgc.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(rgc, "POLL_SEC", 0)  # no real sleeping in the poll loop

    state = rgc._wait_terminal_scheduler(PROFILE, "3669100")
    assert state == "COMPLETED"
    assert sleeps  # the lifecycle exercised at least one poll interval


def test_wait_terminal_scheduler_times_out(monkeypatch):
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        if command.startswith("squeue"):
            return "PENDING\n"
        return ""

    monkeypatch.setattr(rgc.q, "_ssh", fake_ssh)
    monkeypatch.setattr(rgc.time, "sleep", lambda s: None)
    monkeypatch.setattr(rgc, "POLL_SEC", 0)
    monkeypatch.setattr(rgc, "WAIT_DEADLINE_SEC", 0)
    with pytest.raises(q.QualifyError, match="not terminal"):
        rgc._wait_terminal_scheduler(PROFILE, "3669100")


# -- _find_completed_cpu_job (fake sacct) -----------------------------------


def test_find_completed_cpu_job_matches_workdir_exactly(monkeypatch):
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        assert "sacct" in command
        return (
            f"9001|dispatcher-qual-job-0001|COMPLETED|billing=2|2026-08-25T05:49|"
            f"2026-08-25T05:49|{rgc.RUN_CPU_ID}\n"
        )

    monkeypatch.setattr(rgc.q, "_ssh", fake_ssh)
    assert rgc._find_completed_cpu_job(PROFILE) == "9001"


def test_find_completed_cpu_job_fails_closed_on_multiple(monkeypatch):
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        return (
            f"9001|dispatcher-qual-job-0001|COMPLETED|billing=2|t|t|{rgc.RUN_CPU_ID}\n"
            f"9002|dispatcher-qual-job-0001|COMPLETED|billing=2|t|t|{rgc.RUN_CPU_ID}\n"
        )

    monkeypatch.setattr(rgc.q, "_ssh", fake_ssh)
    with pytest.raises(q.QualifyError, match="cannot uniquely identify"):
        rgc._find_completed_cpu_job(PROFILE)


def test_find_completed_cpu_job_skips_non_completed(monkeypatch):
    def fake_ssh(profile, command, timeout=120, *, retries=4):
        return (
            "9001|dispatcher-qual-job-0001|FAILED|billing=2|t|t|/elsewhere\n"
            f"9002|dispatcher-qual-job-0001|COMPLETED|billing=2|t|t|{rgc.RUN_CPU_ID}\n"
        )

    monkeypatch.setattr(rgc.q, "_ssh", fake_ssh)
    assert rgc._find_completed_cpu_job(PROFILE) == "9002"
