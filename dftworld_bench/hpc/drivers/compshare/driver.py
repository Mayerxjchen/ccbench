"""CompShareDriver: thin HpcDriver implementation over official CompShare CLI.

Invariants:
- Implements HpcDriver protocol.
- Only accepts GPU workloads; rejects CPU runtimes (e.g. cp2k) fail-closed.
- Uses official compshare CLI commands via CompShareCli.
- Enforces input SHA-256 integrity checks before staging.
- Durable recovery from persistent job journal.
- Real remote cancellation via `instance job cancel`.
- Guaranteed settlement: stops and deletes run's instance on settle(run_id).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from dftworld_bench.hpc.drivers.base import HpcDriver, HpcDriverError
from dftworld_bench.hpc.drivers.compshare.cli import (
    CompShareCli,
    CompShareCliCapacityError,
    CompShareCliError,
)
from dftworld_bench.hpc.drivers.compshare.instance_manager import (
    BudgetConfig,
    RunScopedInstanceManager,
)
from dftworld_bench.hpc.runtime_resolution import is_placeholder_artifact, split_runtime

logger = logging.getLogger(__name__)

SUPPORTED_GPU_RUNTIMES = frozenset({"deepmd", "jax", "deepmd-jax", "lammps"})


class CompShareDriverError(HpcDriverError):
    """Execution error within CompShareDriver."""


@dataclass
class JobRecord:
    job_id: str
    run_id: str
    operation_id: str
    marker: str
    instance_id: str
    remote_job_id: str
    state: str = "QUEUED"
    submitted_at: float = field(default_factory=time.time)
    declared_outputs: list[dict[str, Any]] = field(default_factory=list)
    remote_workdir: str = ""


class CompShareDriver(HpcDriver):
    """Maintainer GPU execution backend wrapping the official compshare CLI."""

    kind = "compshare"

    def __init__(
        self,
        cli: CompShareCli,
        *,
        budget: BudgetConfig | None = None,
        workspace_root: str = "/tmp/mlffbench/compshare_jobs",
        state_root: str | Path | None = None,
        production: bool | None = None,
        manager: RunScopedInstanceManager | None = None,
        site_profile: Any | None = None,
        audit: Any | None = None,
    ) -> None:
        self.cli = cli
        self.audit = audit
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        if manager is None:
            # Extract GPU site profile configuration if provided
            region = "cn-sh2"
            zone = "cn-sh2-02"
            default_cpus = 16
            default_memory = "64GiB"
            default_disk = "100GiB"
            default_image_source = "platform"
            profile_state_root: str | Path | None = None
            target_binding: str | None = None
            account: str | None = None
            provider = "compshare"
            if site_profile is not None:
                rp = getattr(site_profile, "runtime_policy", {}) or {}
                profile_state_root = getattr(
                    site_profile, "compshare_state_root", None
                ) or rp.get("compshare_state_root")
                connection = getattr(site_profile, "connection", {}) or {}
                target_binding = connection.get("target_binding") or getattr(
                    site_profile, "target_binding", None
                )
                account = getattr(site_profile, "account", None) or getattr(
                    site_profile, "account_id", None
                )
                provider = rp.get("provider", provider)
                region = rp.get("region", region)
                zone = rp.get("zone", zone)
                gpu_queue = (getattr(site_profile, "queues", {}) or {}).get("gpu", {})
                default_cpus = gpu_queue.get("max_cpus", default_cpus)
                mem_gb = gpu_queue.get("max_memory_gb")
                if mem_gb:
                    default_memory = f"{mem_gb}GiB"
                # Consume only the normalized immutable provider policy.  The
                # raw mapping remains available for digest serialization, but
                # execution never interprets arbitrary keys such as a
                # failover standby hint.
                bp = None
                try:
                    bp = site_profile.compshare_budget_policy
                except AttributeError:
                    # Compatibility with lightweight test doubles and legacy
                    # profiles that predate the typed accessor.
                    raw_bp = rp.get("budget_policy") or {}
                    if raw_bp:
                        bp = raw_bp
                if bp and budget is None:
                    if hasattr(bp, "max_instance_hours"):
                        budget = BudgetConfig(
                            max_gpu_hours=bp.max_instance_hours,
                            max_cost_cny=bp.max_budget_cny,
                            max_instances=bp.max_instances,
                            managed_account_scope_id=bp.managed_account_scope_id,
                        )
                    else:
                        budget = BudgetConfig(
                            max_gpu_hours=bp.get("max_instance_hours", 4.0),
                            max_cost_cny=bp.get("max_budget_cny", 150.0),
                            max_instances=bp.get("max_instances", 1),
                            managed_account_scope_id=bp.get("managed_account_scope_id"),
                        )
                default_image_source = rp.get("image_source", "platform")
            production_mode = (site_profile is not None) if production is None else production
            if profile_state_root is not None and state_root is not None:
                if Path(profile_state_root) != Path(state_root):
                    raise CompShareDriverError(
                        "explicit compshare_state_root disagrees with the trusted SiteProfile"
                    )
            resolved_state_root = state_root or profile_state_root
            if production_mode and resolved_state_root is None:
                raise CompShareDriverError(
                    "production CompShare driver requires an explicit persistent "
                    "compshare_state_root"
                )
            if resolved_state_root is None:
                resolved_state_root = self.workspace_root
            ledger_root = Path(resolved_state_root)
            manager = RunScopedInstanceManager(
                cli,
                budget=budget,
                state_root=ledger_root,
                ledger_path=ledger_root / "compshare-instance-ledger.jsonl",
                orphan_ledger_path=ledger_root / "orphan-ledger.jsonl",
                provider=provider,
                target_binding=target_binding,
                account=account,
                production=bool(production_mode),
                region=region,
                zone=zone,
                default_cpus=default_cpus,
                default_memory=default_memory,
                default_disk=default_disk,
                default_image_source=default_image_source,
                audit=audit,
            )
        elif audit is not None and getattr(manager, "audit", None) is None:
            manager.audit = audit
        self.manager = manager
        # Store default GPU type from site profile for submissions
        self._default_gpu_type = "4090"
        if site_profile is not None:
            rp = getattr(site_profile, "runtime_policy", {}) or {}
            self._default_gpu_type = rp.get("default_gpu_type", "4090")
        self._journal_path = self.workspace_root / "compshare_jobs.jsonl"
        self._jobs: dict[str, JobRecord] = {}
        self._jobs_by_op: dict[tuple[str, str], str] = {}
        self._jobs_by_marker: dict[str, str] = {}
        self._job_seq = 0
        self._load_journal()

    def _load_journal(self) -> None:
        """Durable recovery: load existing jobs from journal."""
        if not self._journal_path.is_file():
            return
        try:
            with open(self._journal_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    rec = JobRecord(
                        job_id=data["job_id"],
                        run_id=data["run_id"],
                        operation_id=data["operation_id"],
                        marker=data["marker"],
                        instance_id=data["instance_id"],
                        remote_job_id=data["remote_job_id"],
                        state=data.get("state", "QUEUED"),
                        submitted_at=data.get("submitted_at", time.time()),
                        declared_outputs=data.get("declared_outputs", []),
                        remote_workdir=data.get("remote_workdir", ""),
                    )
                    self._jobs[rec.job_id] = rec
                    self._jobs_by_op[(rec.run_id, rec.operation_id)] = rec.job_id
                    self._jobs_by_marker[rec.marker] = rec.job_id
                    if rec.job_id.startswith("cs-job-"):
                        try:
                            seq = int(rec.job_id.split("-")[-1])
                            if seq > self._job_seq:
                                self._job_seq = seq
                        except ValueError:
                            pass
        except Exception as exc:
            logger.error("Error reading job journal %s: %s", self._journal_path, exc)
            raise HpcDriverError(
                f"Job journal {self._journal_path} is corrupt; refusing to start in unverified state: {exc}"
            ) from exc

    def _persist_job(self, rec: JobRecord) -> None:
        try:
            with open(self._journal_path, "a", encoding="utf-8") as f:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(asdict(rec)) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as exc:
            logger.error("Failed to write to job journal: %s", exc)
            raise HpcDriverError(f"Failed to persist job journal: {exc}") from exc

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

        # Check runtime capability compatibility
        runtime_decl = spec.get("runtime")
        if runtime_decl:
            cap_name, _ = split_runtime(runtime_decl)
            if cap_name not in SUPPORTED_GPU_RUNTIMES:
                raise HpcDriverError(
                    f"CompShareDriver only accepts GPU runtimes ({sorted(SUPPORTED_GPU_RUNTIMES)}); "
                    f"got {cap_name!r}. Non-GPU workloads must route to CPU/Slurm."
                )

        # Resolve image strictly from trusted ResolvedRuntime
        resolved_runtime = spec.get("_resolved_runtime")
        if resolved_runtime is None:
            raise HpcDriverError(
                "CompShareDriver.submit() mandates a trusted '_resolved_runtime' in spec; "
                "candidate cannot supply ImageId or runtime declarations directly"
            )

        if getattr(resolved_runtime, "artifact_kind", "") != "compshare_image":
            raise HpcDriverError(
                f"CompShareDriver requires artifact_kind='compshare_image'; got {getattr(resolved_runtime, 'artifact_kind', '')!r}"
            )
        if getattr(resolved_runtime, "provider", "") != "compshare":
            raise HpcDriverError(
                f"CompShareDriver requires provider='compshare'; got {getattr(resolved_runtime, 'provider', '')!r}"
            )
        if not getattr(resolved_runtime, "qualification_verified", False):
            raise HpcDriverError(
                f"Runtime {getattr(resolved_runtime, 'capability', '')!r} has not passed qualification verification"
            )

        cap_name = getattr(resolved_runtime, "capability", "")
        if cap_name not in SUPPORTED_GPU_RUNTIMES:
            raise HpcDriverError(
                f"CompShareDriver only accepts GPU runtimes ({sorted(SUPPORTED_GPU_RUNTIMES)}); "
                f"got {cap_name!r}. Non-GPU workloads must route to CPU/Slurm."
            )

        image_id = getattr(resolved_runtime, "artifact_path_or_id", "") or getattr(resolved_runtime, "image_id", "")
        if not image_id or is_placeholder_artifact(image_id):
            raise HpcDriverError(
                f"Runtime {cap_name!r} has no valid CompShare ImageId (got {image_id!r}); "
                "placeholder or unbuilt images are rejected"
            )

        # Acquire or create the single run-scoped GPU instance
        instance_id = self.manager.get_or_create_instance(
            run_id=run_id,
            image_id=image_id,
            operation_id=operation_id,
            gpu_type=self._default_gpu_type,
            gpu_count=1,
        )

        self._job_seq += 1
        job_id = f"cs-job-{self._job_seq:04d}"
        remote_workdir = f"/workspace/{run_id}/{operation_id}/{att}"

        # Stage declared inputs with SHA-256 integrity verification
        inputs = spec.get("inputs", [])
        for inp in inputs:
            local_src = inp.get("source_path") or inp.get("path")
            rel_dest = inp.get("path")
            claimed_sha = inp.get("sha256")
            if not local_src or not os.path.exists(local_src):
                raise HpcDriverError(f"Declared input file missing on host: {local_src}")
            with open(local_src, "rb") as fh:
                actual_sha = hashlib.sha256(fh.read()).hexdigest()
            if claimed_sha and claimed_sha != actual_sha:
                raise HpcDriverError(
                    f"Input {rel_dest} sha256 mismatch: claimed {claimed_sha} != actual {actual_sha}"
                )
            self.cli.instance_cp(instance_id, local_src, f":{remote_workdir}/{rel_dest}")

        # Submit remote job via official CLI
        command = spec.get("command", [])
        if isinstance(command, str):
            command = [command]
        env_vars = spec.get("environment") or {}
        try:
            res = self.cli.instance_job_submit(
                instance_id,
                command,
                cwd=remote_workdir,
                environment=env_vars,
            )
            remote_job_id = res.get("job_id") or res.get("task_id")
            if not remote_job_id:
                raise HpcDriverError(f"instance job submit returned no job_id: {res}")
        except Exception as exc:
            self.manager.release_operation(run_id, operation_id)
            raise HpcDriverError(f"Failed to submit remote job: {exc}") from exc

        rec = JobRecord(
            job_id=job_id,
            run_id=run_id,
            operation_id=operation_id,
            marker=job_marker,
            instance_id=instance_id,
            remote_job_id=str(remote_job_id),
            state="QUEUED",
            submitted_at=time.time(),
            declared_outputs=spec.get("outputs", []),
            remote_workdir=remote_workdir,
        )
        self._jobs[job_id] = rec
        self._jobs_by_op[(run_id, operation_id)] = job_id
        self._jobs_by_marker[job_marker] = job_id
        self._persist_job(rec)

        return {"job_id": job_id, "duplicate": False}

    def find(self, run_id: str, idempotency_key: str) -> str | None:
        """Find an existing job by idempotency key (marker)."""
        return self._jobs_by_marker.get(idempotency_key)

    def find_by_marker(self, run_id: str, marker: str) -> str | None:
        """Find job by exact scheduler marker."""
        return self._jobs_by_marker.get(marker)

    def find_by_operation_id(self, run_id: str, operation_id: str) -> str | None:
        """Find job by logical operation ID."""
        return self._jobs_by_op.get((run_id, operation_id))

    def status(self, run_id: str, job_id: str) -> dict[str, Any]:
        """Query job execution state via official CLI."""
        job = self._jobs.get(job_id)
        if job is None:
            raise HpcDriverError(f"Unknown job {job_id}")

        try:
            info = self.cli.instance_job_show(job.instance_id, job.remote_job_id)
            raw_state = str(info.get("status", "")).upper()
            state_map = {
                "QUEUED": "QUEUED",
                "PENDING": "QUEUED",
                "RUNNING": "RUNNING",
                "COMPLETED": "SUCCEEDED",
                "SUCCEEDED": "SUCCEEDED",
                "FAILED": "FAILED",
                "CANCELLED": "CANCELLED",
                "CANCELED": "CANCELLED",
                "TIMEOUT": "TIMEOUT",
            }
            # Fail closed on unknown status: never treat unknown as running
            mapped_state = state_map.get(raw_state, "FAILED")
            job.state = mapped_state

            if mapped_state in ("SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT"):
                self.manager.release_operation(
                    run_id, job.operation_id, gpu_seconds=time.time() - job.submitted_at
                )

            return {
                "job_id": job_id,
                "state": mapped_state,
                "exit_code": info.get("exit_code", 0 if mapped_state == "SUCCEEDED" else 1),
            }
        except Exception as exc:
            logger.error("Error fetching job status: %s", exc)
            raise HpcDriverError(f"Error querying cloud status for job {job_id}: {exc}") from exc

    def logs(self, run_id: str, job_id: str, *, cursor: int = 0) -> dict[str, Any]:
        """Retrieve stdout/stderr logs from remote job."""
        job = self._jobs.get(job_id)
        if job is None:
            raise HpcDriverError(f"Unknown job {job_id}")
        text = self.cli.instance_job_logs(job.instance_id, job.remote_job_id)
        chunk = text[cursor:]
        return {
            "job_id": job_id,
            "logs": chunk,
            "cursor": len(text),
            "complete": job.state in ("SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT"),
        }

    def fetch(self, run_id: str, job_id: str) -> dict[str, Any]:
        """Download declared outputs from remote instance with strict schema and containment validation."""
        job = self._jobs.get(job_id)
        if job is None:
            raise HpcDriverError(f"Unknown job {job_id}")

        dest_dir = self.workspace_root / run_id / job.operation_id / "fetched"
        dest_dir.mkdir(parents=True, exist_ok=True)
        outputs: dict[str, Any] = {}

        for out_decl in job.declared_outputs:
            rel_path = out_decl if isinstance(out_decl, str) else out_decl.get("path")
            if not rel_path:
                continue
            if os.path.isabs(rel_path) or ".." in Path(rel_path).parts:
                raise HpcDriverError(f"Forbidden traversal or absolute output path: {rel_path}")

            remote_target = f"{job.remote_workdir}/{rel_path}"
            local_target = (dest_dir / rel_path).resolve()
            if not local_target.is_relative_to(dest_dir.resolve()):
                raise HpcDriverError(f"Output path escapes workspace: {rel_path}")
            if local_target.is_symlink():
                raise HpcDriverError(f"Symlinks are forbidden in fetched targets: {local_target}")
            if local_target.exists():
                raise HpcDriverError(f"Refusing to overwrite existing file: {local_target}")

            local_target.parent.mkdir(parents=True, exist_ok=True)
            self.cli.instance_cp(job.instance_id, f":{remote_target}", str(local_target))
            if not local_target.is_file():
                raise HpcDriverError(f"Fetched output file missing on host: {local_target}")

            content_bytes = local_target.read_bytes()
            outputs[rel_path] = content_bytes

        artifacts = {
            rel_p: {
                "sha256": f"sha256:{hashlib.sha256(b).hexdigest()}",
                "size_bytes": len(b),
                "path": str(dest_dir / rel_p),
            }
            for rel_p, b in outputs.items()
        }
        return {"job_id": job_id, "outputs": outputs, "artifacts": artifacts}

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a remote job on the CompShare instance."""
        job = self._jobs.get(job_id)
        if job is None:
            raise HpcDriverError(f"Unknown job {job_id}")
        try:
            self.cli.instance_job_cancel(job.instance_id, job.remote_job_id, yes=True)
        except Exception as exc:
            logger.error("Error cancelling remote job %s (%s): %s", job_id, job.remote_job_id, exc)
            raise HpcDriverError(f"Remote job cancellation failed for {job_id}: {exc}") from exc
        job.state = "CANCELLED"
        self.manager.release_operation(job.run_id, job.operation_id)
        return {"job_id": job_id, "state": "CANCELLED"}

    def usage(self) -> dict[str, Any]:
        """Return total usage across runs."""
        total_sec = 0.0
        for rec in self.manager._instances.values():
            elapsed = (rec.terminated_at or time.time()) - rec.created_at
            total_sec += max(rec.accumulated_gpu_seconds, elapsed)
        return {
            "gpu_seconds": round(total_sec, 2),
            "gpu_hours": round(total_sec / 3600.0, 4),
            "active_instances": sum(
                1 for r in self.manager._instances.values() if r.terminated_at is None
            ),
        }

    def settle(self, run_id: str | None = None) -> bool:
        """Mandatory run-scoped settlement: stop and delete the run's GPU instance."""
        if run_id is None:
            # If no run_id specified, terminate all active runs
            for rid in list(self.manager._instances.keys()):
                self.manager.terminate_run(rid)
                self.manager.log_zero_orphan_query(rid)
            return True
        res = self.manager.terminate_run(run_id)
        self.manager.log_zero_orphan_query(run_id)
        return res
