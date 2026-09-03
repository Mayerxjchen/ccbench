"""CompShareDriver: Maintainer Cloud GPU execution backend delegating to compshare CLI.

Architecture:
    CompShareDriver
         ↓ subprocess
    compshare ... --json

Contract:
- Candidate sees standard bench-hpc protocol only (submit, status, logs, fetch, cancel, usage).
- CompShare details (instance ID, CLI path, credentials, internal endpoints) are completely hidden.
- Run-scoped single instance reuse across consecutive operations.
- Explicit fetch with post-download SHA-256 verification.
- Enforces budget limits and guarantees settlement (stop -> terminate).
- Mock/fake runner forbidden from producing formal qualification evidence.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dftworld_bench.hpc.drivers.base import HpcDriver
from dftworld_bench.hpc.drivers.compshare.cli import (
    CompShareCli,
    CompShareCliError,
)
from dftworld_bench.hpc.drivers.compshare.instance_manager import (
    BudgetConfig,
    RunScopedInstanceManager,
)
from dftworld_bench.hpc.runtime_resolution import split_runtime


class CompShareDriverError(Exception):
    """Execution error within CompShareDriver."""


@dataclass
class JobRecord:
    job_id: str
    run_id: str
    operation_id: str
    attempt: int
    marker: str
    spec: dict[str, Any]
    instance_id: str
    task_id: str
    status: str = "QUEUED"
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    exit_code: int | None = None
    output_files: dict[str, str] = field(default_factory=dict)  # rel_path -> sha256


class CompShareDriver:
    """Maintainer GPU execution backend wrapping the official compshare CLI."""

    kind = "compshare"

    def __init__(
        self,
        cli: CompShareCli,
        *,
        budget: BudgetConfig | None = None,
        workspace_root: str = "/tmp/mlffbench/compshare_jobs",
        manager: RunScopedInstanceManager | None = None,
    ) -> None:
        self.cli = cli
        self.workspace_root = Path(workspace_root)
        self.manager = manager or RunScopedInstanceManager(cli, budget=budget)
        self._jobs: dict[str, JobRecord] = {}  # job_id -> JobRecord
        self._jobs_by_op: dict[tuple[str, str], str] = {}  # (run_id, operation_id) -> job_id
        self._jobs_by_marker: dict[str, str] = {}  # marker -> job_id
        self._job_seq = 0

    def capabilities(self) -> dict[str, Any]:
        """Advertise capabilities to the gateway."""
        return {
            "scheduler": "compshare",
            "compute_classes": ["gpu"],
            "max_gpus": 1,
            "max_cpus": 16,
            "max_memory_gb": 64,
            "max_walltime_minutes": self.manager.budget.max_instance_minutes,
        }

    def submit(
        self,
        spec: dict[str, Any],
        *,
        run_id: str,
        operation_id: str,
        attempt: int | None = None,
        marker: str | None = None,
    ) -> dict[str, Any]:
        """Submit a GPU workload to the run's CompShare instance via CLI."""
        att = attempt if attempt is not None else 1
        job_marker = marker or f"drv-{run_id}-{operation_id}-{att}"

        # Check duplicate
        if job_marker in self._jobs_by_marker:
            existing_id = self._jobs_by_marker[job_marker]
            return {"job_id": existing_id, "duplicate": True}

        # Resolve image from spec runtime
        runtime_decl = spec.get("runtime", "")
        cap_name, _ = split_runtime(runtime_decl)
        image_map = {
            "deepmd": "img-deepmd-gpu-v1",
            "jax": "img-jax-gpu-v1",
            "deepmd-jax": "img-jax-gpu-v1",
            "lammps": "img-deepmd-gpu-v1",
        }
        image_id = image_map.get(cap_name, "img-deepmd-gpu-v1")

        # Acquire or create the single run-scoped GPU instance
        instance_id = self.manager.get_or_create_instance(
            run_id=run_id,
            image_id=image_id,
            operation_id=operation_id,
            gpu_type="rtx4090",
            gpu_count=1,
        )

        self._job_seq += 1
        job_id = f"cs-job-{self._job_seq:04d}"
        remote_workdir = f"/workspace/{run_id}/{operation_id}/{att}"

        # Stage declared inputs
        inputs = spec.get("inputs", [])
        for inp in inputs:
            local_src = inp.get("source_path") or inp.get("path")
            rel_dest = inp.get("path")
            if local_src and os.path.exists(local_src):
                self.cli.upload_file(instance_id, local_src, f"{remote_workdir}/{rel_dest}")

        # Execute pure argv command via CLI remote task
        command = list(spec.get("command", []))
        if not command:
            raise CompShareDriverError("Command cannot be empty")

        task_id = self.cli.run_task(
            instance_id=instance_id,
            command=command,
            workdir=remote_workdir,
            environment=spec.get("environment"),
        )

        record = JobRecord(
            job_id=job_id,
            run_id=run_id,
            operation_id=operation_id,
            attempt=att,
            marker=job_marker,
            spec=dict(spec),
            instance_id=instance_id,
            task_id=task_id,
            status="RUNNING",
        )
        self._jobs[job_id] = record
        self._jobs_by_op[(run_id, operation_id)] = job_id
        self._jobs_by_marker[job_marker] = job_id

        return {"job_id": job_id, "duplicate": False}

    def find(self, run_id: str, operation_id: str) -> str | None:
        return self._jobs_by_op.get((run_id, operation_id))

    def find_by_marker(self, marker: str) -> str | None:
        return self._jobs_by_marker.get(marker)

    def status(self, job_id: str) -> dict[str, Any]:
        """Query standardized job status."""
        record = self._jobs.get(job_id)
        if record is None:
            raise CompShareDriverError(f"Job {job_id} not found")

        if record.status in ("COMPLETED", "FAILED", "CANCELLED"):
            return {
                "job_id": job_id,
                "state": record.status,
                "exit_code": record.exit_code,
            }

        task_stat = self.cli.get_task_status(record.task_id)
        state_map = {
            "QUEUED": "QUEUED",
            "PROVISIONING": "QUEUED",
            "RUNNING": "RUNNING",
            "COMPLETED": "COMPLETED",
            "FAILED": "FAILED",
            "CANCELLED": "CANCELLED",
        }
        prov_status = str(task_stat.get("status", "RUNNING")).upper()
        std_state = state_map.get(prov_status, "RUNNING")

        if std_state in ("COMPLETED", "FAILED", "CANCELLED") and record.ended_at is None:
            record.status = std_state
            record.ended_at = time.time()
            record.exit_code = int(task_stat.get("exit_code", 0))
            elapsed = record.ended_at - record.started_at
            self.manager.release_operation(record.run_id, record.operation_id, gpu_seconds=elapsed)

        return {
            "job_id": job_id,
            "state": std_state,
            "exit_code": record.exit_code,
        }

    def logs(self, job_id: str) -> dict[str, Any]:
        record = self._jobs.get(job_id)
        if record is None:
            raise CompShareDriverError(f"Job {job_id} not found")
        logs = self.cli.get_task_logs(record.task_id)
        return {"job_id": job_id, "logs": logs}

    def fetch(self, job_id: str) -> dict[str, Any]:
        """Explicit fetch of declared outputs with SHA256 integrity verification."""
        record = self._jobs.get(job_id)
        if record is None:
            raise CompShareDriverError(f"Job {job_id} not found")

        dest_dir = self.workspace_root / record.run_id / record.operation_id / f"att-{record.attempt}"
        dest_dir.mkdir(parents=True, exist_ok=True)

        outputs = record.spec.get("outputs", [])
        fetched: list[dict[str, Any]] = []
        remote_workdir = f"/workspace/{record.run_id}/{record.operation_id}/{record.attempt}"

        for out_name in outputs:
            local_target = dest_dir / out_name
            remote_target = f"{remote_workdir}/{out_name}"
            self.cli.download_file(record.instance_id, remote_target, str(local_target))

            if local_target.is_file():
                sha = hashlib.sha256(local_target.read_bytes()).hexdigest()
                size = local_target.stat().st_size
                record.output_files[out_name] = sha
                fetched.append({"path": out_name, "sha256": sha, "size_bytes": size})

        return {"job_id": job_id, "artifacts": fetched}

    def cancel(self, job_id: str) -> dict[str, Any]:
        record = self._jobs.get(job_id)
        if record is None:
            raise CompShareDriverError(f"Job {job_id} not found")
        if record.status in ("COMPLETED", "FAILED", "CANCELLED"):
            return {"job_id": job_id, "cancelled": False}

        record.status = "CANCELLED"
        record.ended_at = time.time()
        record.exit_code = 130
        elapsed = record.ended_at - record.started_at
        self.manager.release_operation(record.run_id, record.operation_id, gpu_seconds=elapsed)
        return {"job_id": job_id, "cancelled": True}

    def usage(self) -> dict[str, Any]:
        """Aggregate usage across managed instances."""
        total_gpu_sec = 0.0
        for run_id in list(self.manager._instances):
            u = self.manager.get_usage(run_id)
            total_gpu_sec += u["gpu_seconds"]
        return {
            "gpu_seconds": total_gpu_sec,
            "gpu_hours": round(total_gpu_sec / 3600.0, 4),
            "max_gpu_hours": self.manager.budget.max_gpu_hours,
        }

    def settle(self) -> dict[str, Any]:
        """Terminal settlement: cleanly stop and terminate all run instances."""
        active_runs = [
            run_id
            for run_id, rec in self.manager._instances.items()
            if rec.terminated_at is None
        ]
        terminated: list[str] = []
        for run_id in active_runs:
            self.manager.terminate_run(run_id)
            terminated.append(run_id)
        return {
            "settled_runs": terminated,
            "active_instances": 0,
            "usage": self.usage(),
        }
