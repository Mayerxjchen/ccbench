"""Run-scoped instance manager for CompShare GPU resources using CompShareCli.

Core Invariants:
1. Max 1 GPU instance per Run.
2. Max 1 GPU per instance.
3. Max 1 concurrent GPU operation per Run.
4. Consecutive GPU operations within the same Run reuse the active instance.
5. Different Runs NEVER share an instance.
6. Settlement guarantees: stop -> terminate.
7. Recycling failure is logged to orphan ledger and produces INFRA_INVALID.
8. Budget limits (GPU hours, instance walltime, cost) strictly enforced.
9. Durable recovery from persistent ledger on restart.
10. Instance creation failures automatically trigger immediate deletion.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dftworld_bench.hpc.drivers.compshare.cli import (
    CompShareCli,
    CompShareCliCapacityError,
    CompShareCliError,
)

logger = logging.getLogger(__name__)


class CompShareManagerError(Exception):
    """General error in CompShare instance management."""


class CompShareBudgetExceededError(CompShareManagerError):
    """Budget constraint violated (GPU hours, duration, or cost)."""


class CompShareOrphanError(CompShareManagerError):
    """An instance could not be cleanly terminated, producing an orphan."""


@dataclass
class BudgetConfig:
    max_gpu_hours: float = 4.0
    max_instance_minutes: int = 120
    max_cost_cny: float = 150.0
    hourly_rate_cny: float = 14.0


@dataclass
class RunInstanceRecord:
    run_id: str
    instance_id: str
    image_id: str
    created_at: float
    gpu_type: str = "4090"
    gpu_count: int = 1
    terminated_at: float | None = None
    active_operation_id: str | None = None
    accumulated_gpu_seconds: float = 0.0


@dataclass
class RecoveryReport:
    active_instances: list[str] = field(default_factory=list)
    recovered_instances: list[str] = field(default_factory=list)
    failed_instances: list[str] = field(default_factory=list)
    dangling_cloud_instances: list[str] = field(default_factory=list)
    clean: bool = True


class RunScopedInstanceManager:
    """Manages CompShare GPU instance lifecycle bounded strictly to a single Run."""

    def __init__(
        self,
        cli: CompShareCli,
        *,
        budget: BudgetConfig | None = None,
        ledger_path: Path | None = None,
        orphan_ledger_path: Path | None = None,
        region: str = "default",
        zone: str = "default",
        default_cpus: int = 16,
        default_memory: str = "64GiB",
        default_disk: str = "100GiB",
        default_image_source: str = "platform",
    ) -> None:
        self.cli = cli
        self.budget = budget or BudgetConfig()
        self.region = region
        self.zone = zone
        self.default_image_source = default_image_source
        self.default_cpus = default_cpus
        self.default_memory = default_memory
        self.default_disk = default_disk
        self.ledger_path = ledger_path or Path("runs/compshare-ledger.jsonl")
        self.orphan_ledger_path = orphan_ledger_path or Path("runs/compshare-orphans.jsonl")
        self._instances: dict[str, RunInstanceRecord] = {}
        self._load_ledger()

    def _load_ledger(self) -> None:
        if not self.ledger_path or not self.ledger_path.exists():
            return
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    rec = RunInstanceRecord(
                        run_id=data["run_id"],
                        instance_id=data["instance_id"],
                        image_id=data["image_id"],
                        created_at=data["created_at"],
                        gpu_type=data.get("gpu_type", "4090"),
                        gpu_count=data.get("gpu_count", 1),
                        terminated_at=data.get("terminated_at"),
                        active_operation_id=data.get("active_operation_id"),
                        accumulated_gpu_seconds=data.get("accumulated_gpu_seconds", 0.0),
                    )
                    self._instances[rec.run_id] = rec
        except Exception as exc:
            logger.error("Failed to load instance ledger %s: %s", self.ledger_path, exc)
            raise CompShareManagerError(
                f"Ledger file {self.ledger_path} is corrupt; refusing to start in unverified state: {exc}"
            ) from exc

    def get_or_create_instance(
        self,
        run_id: str,
        image_id: str,
        *,
        operation_id: str,
        gpu_type: str = "4090",
        gpu_count: int = 1,
    ) -> str:
        """Acquire the single Run-scoped GPU instance, or create it if not yet active."""
        if gpu_count != 1:
            raise CompShareManagerError(
                f"Each instance must have exactly 1 GPU, requested {gpu_count}"
            )

        # Check existing instance for this run
        record = self._instances.get(run_id)
        if record is not None and record.terminated_at is None:
            if record.active_operation_id is not None:
                raise CompShareManagerError(
                    f"Run {run_id} already has an active GPU operation: {record.active_operation_id}"
                )
            if record.image_id != image_id:
                raise CompShareManagerError(
                    f"Run {run_id} instance was created with image {record.image_id}, "
                    f"cannot reuse with different image {image_id}"
                )
            self._check_budget(record)
            record.active_operation_id = operation_id
            self._persist_ledger(record)
            return record.instance_id

        # Check pre-creation stock & capacity via official CLI
        available = self.cli.instance_search(
            region=self.region,
            zone=self.zone,
            gpu=gpu_type,
            image=image_id,
        )
        if not available:
            raise CompShareCliCapacityError(
                f"CompShare has no available stock for GPU {gpu_type} with image {image_id}"
            )

        # Create new instance for run via CLI with auto-cleanup guard
        created_id: str | None = None
        instance_name = f"mlffbench-{run_id}-worker"
        instance_remark = f"mlffbench:{run_id}:worker"
        try:
            # 1. First validate via dry-run
            self.cli.instance_create(
                image=image_id,
                name=instance_name,
                remark=instance_remark,
                region=self.region,
                zone=self.zone,
                gpu=gpu_type,
                cpu=self.default_cpus,
                memory=self.default_memory,
                disk=self.default_disk,
                count=1,
                timeout=900,
                dry_run=True,
                image_source=self.default_image_source,
            )

            # 2. Real create
            inst_info = self.cli.instance_create(
                image=image_id,
                name=instance_name,
                remark=instance_remark,
                region=self.region,
                zone=self.zone,
                gpu=gpu_type,
                cpu=self.default_cpus,
                memory=self.default_memory,
                disk=self.default_disk,
                count=1,
                timeout=900,
                dry_run=False,
                image_source=self.default_image_source,
            )
            created_id = inst_info.get("instance_id") or inst_info.get("id")
            if not created_id:
                raise CompShareManagerError(f"instance create returned no instance_id: {inst_info}")

            # Wait for instance to become RUNNING
            self.cli.wait_instance_ready(created_id)

            record = RunInstanceRecord(
                run_id=run_id,
                instance_id=created_id,
                image_id=image_id,
                created_at=time.time(),
                gpu_type=gpu_type,
                gpu_count=1,
                active_operation_id=operation_id,
            )
            self._instances[run_id] = record
            self._persist_ledger(record)
            return created_id
        except Exception as exc:
            # Immediate fail-safe: if instance was created but setup failed, delete it
            if created_id:
                try:
                    self.cli.instance_delete(created_id)
                except Exception as del_exc:
                    logger.error("Failed to delete failed instance %s: %s", created_id, del_exc)
                    orphan_rec = RunInstanceRecord(
                        run_id=run_id,
                        instance_id=created_id,
                        image_id=image_id,
                        created_at=time.time(),
                        gpu_type=gpu_type,
                        gpu_count=1,
                    )
                    self._record_orphan(
                        orphan_rec,
                        f"Creation readiness failed ({exc}); rollback deletion failed ({del_exc})",
                    )
            raise

    def release_operation(
        self, run_id: str, operation_id: str, *, gpu_seconds: float = 0.0
    ) -> None:
        """Release the active operation lock and accumulate billable GPU seconds."""
        record = self._instances.get(run_id)
        if record is not None and record.active_operation_id == operation_id:
            record.active_operation_id = None
            record.accumulated_gpu_seconds += gpu_seconds
            self._persist_ledger(record)

    def terminate_run(self, run_id: str) -> bool:
        """Mandatory settlement step: stop -> delete instance for the run."""
        record = self._instances.get(run_id)
        if record is None or record.terminated_at is not None:
            return True

        instance_id = record.instance_id
        try:
            self.cli.instance_stop(instance_id)
            self.cli.instance_delete(instance_id)
            record.terminated_at = time.time()
            record.active_operation_id = None
            self._persist_ledger(record)
            return True
        except Exception as exc:
            self._record_orphan(record, str(exc))
            raise CompShareOrphanError(
                f"Failed to terminate instance {instance_id} for run {run_id}: {exc}"
            ) from exc

    def reconcile_and_recover(self) -> RecoveryReport:
        """Explicit startup/watchdog reconciliation with provider cloud.

        1. Inspects local ledger for any non-terminated instances.
        2. Queries cloud status for each non-terminated instance.
           - If cloud status is already deleted/terminated, marks terminated locally.
           - If cloud instance is still alive/stopped, attempts active stop & delete.
           - If delete succeeds, records as recovered.
           - If delete fails, logs to orphan ledger and records as failed.
        3. Queries cloud instance list (--all) to find any dangling MLFFBench instances
           (matching prefix mlffbench-) that are active/stopped.
        4. Fails closed (clean=False) if any failed or dangling instances remain.
        """
        report = RecoveryReport()
        # 1 & 2: Reconcile local ledger records
        for run_id, rec in list(self._instances.items()):
            if rec.terminated_at is None:
                report.active_instances.append(rec.instance_id)
                try:
                    info = self.cli.instance_show(rec.instance_id)
                    status = str(info.get("status") or "").lower()
                    if status in ("deleted", "terminated"):
                        rec.terminated_at = time.time()
                        self._persist_ledger(rec)
                    else:
                        try:
                            self.terminate_run(run_id)
                            report.recovered_instances.append(rec.instance_id)
                        except Exception:
                            report.failed_instances.append(rec.instance_id)
                except Exception:
                    # Cloud query failed or instance not found, attempt termination
                    try:
                        self.terminate_run(run_id)
                        report.recovered_instances.append(rec.instance_id)
                    except Exception:
                        report.failed_instances.append(rec.instance_id)

        # 3: Cloud sweep for any dangling mlffbench instances
        try:
            cloud_instances = self.cli.instance_list(all=True)
            for item in cloud_instances:
                inst_id = item.get("instance_id") or item.get("id") or ""
                name = str(item.get("name") or "")
                remark = str(item.get("remark") or "")
                status = str(item.get("status") or "").lower()
                if (name.startswith("mlffbench-") or remark.startswith("mlffbench:")) and status not in ("deleted", "terminated"):
                    if inst_id not in report.failed_instances and inst_id not in report.recovered_instances:
                        try:
                            self.cli.instance_stop(inst_id)
                            self.cli.instance_delete(inst_id)
                            report.recovered_instances.append(inst_id)
                        except Exception as exc:
                            logger.critical("Failed to delete dangling cloud instance %s: %s", inst_id, exc)
                            report.dangling_cloud_instances.append(inst_id)
        except Exception as exc:
            logger.warning("Could not list cloud instances during reconciliation: %s", exc)
            report.clean = False
            return report

        report.clean = (len(report.failed_instances) == 0 and len(report.dangling_cloud_instances) == 0)
        return report

    def recover_dangling_instances(self) -> list[str]:
        """Durable teardown recovery: terminate any active instances left from prior runs/crashes."""
        return self.reconcile_and_recover().recovered_instances

    def get_usage(self, run_id: str) -> dict[str, Any]:
        """Return usage, cost, and remaining budget for a run."""
        record = self._instances.get(run_id)
        if record is None:
            return {
                "gpu_seconds": 0.0,
                "gpu_hours": 0.0,
                "cost_cny": 0.0,
                "remaining_budget_cny": self.budget.max_cost_cny,
                "has_active_instance": False,
            }

        elapsed = (record.terminated_at or time.time()) - record.created_at
        gpu_hours = max(record.accumulated_gpu_seconds, elapsed) / 3600.0
        cost = gpu_hours * self.budget.hourly_rate_cny
        return {
            "instance_id": record.instance_id,
            "gpu_seconds": max(record.accumulated_gpu_seconds, elapsed),
            "gpu_hours": round(gpu_hours, 4),
            "cost_cny": round(cost, 4),
            "remaining_budget_cny": max(0.0, round(self.budget.max_cost_cny - cost, 4)),
            "has_active_instance": record.terminated_at is None,
        }

    def _check_budget(self, record: RunInstanceRecord) -> None:
        usage = self.get_usage(record.run_id)
        if usage["gpu_hours"] >= self.budget.max_gpu_hours:
            raise CompShareBudgetExceededError(
                f"Run {record.run_id} exceeded max GPU hours: {usage['gpu_hours']} >= {self.budget.max_gpu_hours}"
            )
        if usage["cost_cny"] >= self.budget.max_cost_cny:
            raise CompShareBudgetExceededError(
                f"Run {record.run_id} exceeded max budget: ¥{usage['cost_cny']} >= ¥{self.budget.max_cost_cny}"
            )

    def _persist_ledger(self, record: RunInstanceRecord) -> None:
        if not self.ledger_path:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "run_id": record.run_id,
            "instance_id": record.instance_id,
            "image_id": record.image_id,
            "gpu_type": record.gpu_type,
            "created_at": record.created_at,
            "terminated_at": record.terminated_at,
            "active_operation_id": record.active_operation_id,
            "accumulated_gpu_seconds": record.accumulated_gpu_seconds,
        }
        try:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(data) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as exc:
            logger.error("Failed to write ledger %s: %s", self.ledger_path, exc)
            raise CompShareManagerError(f"Failed to persist instance ledger: {exc}") from exc

    def _record_orphan(self, record: RunInstanceRecord, reason: str) -> None:
        self.orphan_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "run_id": record.run_id,
            "instance_id": record.instance_id,
            "reason": reason,
            "timestamp": time.time(),
        }
        try:
            with open(self.orphan_ledger_path, "a", encoding="utf-8") as f:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(entry) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as exc:
            logger.critical("Failed to write orphan ledger %s: %s", self.orphan_ledger_path, exc)
