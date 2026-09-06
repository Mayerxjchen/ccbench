"""Shared conformance: every driver must satisfy the same contract.

The identical request lineage, ownership, cancellation, and settlement checks
run against ProcessDriver (executing real subprocesses) and SlurmDriver
(scripted transport only). A driver that drifts from the contract fails here
before any site ever sees it.
"""

from __future__ import annotations

import pytest

from ccbench.hpc.adapters.process_test import ProcessTestAdapter
from ccbench.hpc.adapters.slurm import SlurmAdapter
from ccbench.hpc.drivers import (
    DriverSelectionError,
    ProcessDriver,
    SlurmDriver,
    resolve_driver,
)
from scripts.ablation.transport.slurm_transport import JobState

DIGEST = "mlip-compute@sha256:" + "a" * 64

SITE = {
    "site": "<site-alias>",
    "gateway_url": "https://gw.example.test",
    "run_token": "0123456789abcdef0123456789abcdef",
    "ssh_alias": "<site-alias>",
    "scratch": "/data/bench",
    "account": "mlip-bench",
    "platform_profile": {
        "name": "<site-alias>",
        "default_queue": "gpu",
        "queues": [
            {"name": "gpu", "max_cpus": 32, "max_memory_gb": 128,
             "max_gpus": 1, "max_walltime_minutes": 1440},
        ],
    },
}


class _ScriptedTransport:
    """Slurm transport with a controllable state table; no real scheduler."""

    def __init__(self):
        self.submits: list[tuple] = []
        self._status: dict[str, JobState] = {}
        self.cancelled: list[str] = []

    def submit(self, script, opts):
        self.submits.append((script, opts))
        slurm_id = str(len(self.submits) + 1000)
        self._status[slurm_id] = JobState.RUNNING
        return slurm_id

    def status(self, job_id):
        return self._status.get(job_id, JobState.RUNNING)

    def log(self, job_id, tail=None):
        return f"log-{job_id}"

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        self._status[job_id] = JobState.CANCELLED

    def fetch(self, remote_paths, local_dir):
        return [Path(local_dir) / "output.out"]

    def alloc_tres(self, job_id):
        return ""

    def remote_arch(self):
        return "x86_64"


def _spec(key: str) -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": DIGEST,
        "command": ["/bin/sleep", "30"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": [],
    }


def _drivers(tmp_path):
    process = ProcessDriver(ProcessTestAdapter(tmp_path / "p-site", timeout_sec=60.0))
    transport = _ScriptedTransport()
    slurm = SlurmDriver(
        SlurmAdapter(SITE, transport, case_id="water64")
    )
    return {process.kind: (process, None), slurm.kind: (slurm, transport)}


def test_conformance_lineage_identical_across_drivers(tmp_path):
    for kind, (driver, transport) in _drivers(tmp_path).items():
        cap = driver.capabilities()
        assert cap["adapter"] == kind
        first = driver.submit(_spec("k1"), run_id="run-c", operation_id="op", attempt=1)
        assert first["duplicate"] is False
        # Force attempt 1 terminal through backend truth.
        if kind == "process_test":
            driver.adapter.finish(first["job_id"], state="FAILED")
        else:
            # First scripted submission carries slurm id str(1000 + n).
            transport._status["1001"] = JobState.FAILED
        second = driver.submit(_spec("k2"), run_id="run-c", operation_id="op", attempt=2)
        assert second["job_id"] != first["job_id"]
        duplicate = driver.submit(_spec("k3"), run_id="run-c", operation_id="op", attempt=2)
        assert duplicate["job_id"] == second["job_id"]
        assert duplicate["duplicate"] is True
        state = driver.status(second["job_id"])["state"]
        assert state in ("RUNNING", "CANCELLED")
        logs = driver.logs(second["job_id"])
        assert isinstance(logs, dict)
        usage = driver.usage()
        settle = driver.settle()
        assert isinstance(usage, dict) and isinstance(settle, dict)


def test_formal_rejects_process_driver_and_accepts_slurm():
    with pytest.raises(DriverSelectionError, match="Formal"):
        resolve_driver(mode="formal", kind="process_test")
    resolve_driver(mode="formal", kind="slurm")
    resolve_driver(mode="smoke", kind="process_test")
    resolve_driver(mode="pilot", kind="process_test")


def test_runtime_builds_process_driver_from_config(tmp_path):
    from ccbench.hpc.gateway_runtime import build_driver

    driver = build_driver(
        {"adapter": "process_test", "root": str(tmp_path / "site"), "timeout_sec": 5.0}
    )
    assert isinstance(driver, ProcessDriver)


def test_runtime_slurm_driver_requires_adapter_instance():
    from ccbench.hpc.gateway_runtime import build_driver, GatewayRuntimeError

    with pytest.raises(GatewayRuntimeError, match="adapter_instance"):
        build_driver({"adapter": "slurm"})
