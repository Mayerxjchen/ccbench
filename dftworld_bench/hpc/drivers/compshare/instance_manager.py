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
"""

from __future__ import annotations

import json
import logging
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
    max_cost_usd: float = 20.0
    hourly_rate_usd: float = 1.80


@dataclass
class RunInstanceRecord:
    run_id: str
    instance_id: str
    image_id: str
    created_at: float
    gpu_type: str
    gpu_count: int
    terminated_at: float | None = None
    active_operation_id: str | None = None
    accumulated_gpu_seconds: float = 0.0


class RunScopedInstanceManager:
    """Manages CompShare GPU instance lifecycle bounded strictly to a single Run."""

    def __init__(
        self,
        cli: CompShareCli,
        *,
        budget: BudgetConfig | None = None,
        ledger_path: Path | None = None,
        orphan_ledger_path: Path | None = None,
    ) -> None:
        self.cli = cli
        self.budget = budget or BudgetConfig()
        self.ledger_path = Path(ledger_path) if ledger_path else None
        self.orphan_ledger_path = (
            Path(orphan_ledger_path)
            if orphan_ledger_path
            else Path("evidence/hpc-dispatcher/orphan-ledger.jsonl")
        )
        self._instances: dict[str, RunInstanceRecord] = {}

    def get_or_create_instance(
        self,
        run_id: str,
        image_id: str,
        *,
        operation_id: str,
        gpu_type: str = "rtx4090",
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
            return record.instance_id

        # Check pre-creation stock & capacity
        if not self.cli.check_stock(gpu_type, gpu_count):
            raise CompShareCliCapacityError(
                f"CompShare has no capacity for {gpu_type} (1 GPU)"
            )

        # Create new instance for run via CLI
        inst_info = self.cli.create_instance(
            name=f"mlff-{run_id}",
            image_id=image_id,
            gpu_type=gpu_type,
            count=1,
            auto_shutdown_minutes=self.budget.max_instance_minutes,
        )
        instance_id = inst_info["instance_id"]

        # Wait for instance to become RUNNING
        self.cli.wait_instance_ready(instance_id)

        record = RunInstanceRecord(
            run_id=run_id,
            instance_id=instance_id,
            image_id=image_id,
            created_at=time.time(),
            gpu_type=gpu_type,
            gpu_count=1,
            active_operation_id=operation_id,
        )
        self._instances[run_id] = record
        self._persist_ledger(record)
        return instance_id

    def release_operation(
        self, run_id: str, operation_id: str, gpu_seconds: float = 0.0
    ) -> None:
        """Mark an operation as completed on the run's instance."""
        record = self._instances.get(run_id)
        if record is not None and record.active_operation_id == operation_id:
            record.active_operation_id = None
            record.accumulated_gpu_seconds += gpu_seconds
            self._persist_ledger(record)

    def terminate_run(self, run_id: str) -> bool:
        """Mandatory settlement step: stop -> terminate instance for the run."""
        record = self._instances.get(run_id)
        if record is None or record.terminated_at is not None:
            return True

        instance_id = record.instance_id
        try:
            self.cli.stop_instance(instance_id)
            self.cli.terminate_instance(instance_id)
            record.terminated_at = time.time()
            record.active_operation_id = None
            self._persist_ledger(record)
            return True
        except Exception as exc:
            self._record_orphan(record, str(exc))
            raise CompShareOrphanError(
                f"Failed to terminate instance {instance_id} for run {run_id}: {exc}"
            ) from exc

    def get_usage(self, run_id: str) -> dict[str, Any]:
        """Return usage, cost, and remaining budget for a run."""
        record = self._instances.get(run_id)
        if record is None:
            return {
                "gpu_seconds": 0.0,
                "gpu_hours": 0.0,
                "cost_usd": 0.0,
                "remaining_budget_usd": self.budget.max_cost_usd,
                "has_active_instance": False,
            }

        elapsed = (record.terminated_at or time.time()) - record.created_at
        gpu_hours = max(record.accumulated_gpu_seconds, elapsed) / 3600.0
        cost = gpu_hours * self.budget.hourly_rate_usd
        return {
            "instance_id": record.instance_id,
            "gpu_seconds": max(record.accumulated_gpu_seconds, elapsed),
            "gpu_hours": round(gpu_hours, 4),
            "cost_usd": round(cost, 4),
            "remaining_budget_usd": max(0.0, round(self.budget.max_cost_usd - cost, 4)),
            "has_active_instance": record.terminated_at is None,
        }

    def _check_budget(self, record: RunInstanceRecord) -> None:
        usage = self.get_usage(record.run_id)
        if usage["gpu_hours"] >= self.budget.max_gpu_hours:
            raise CompShareBudgetExceededError(
                f"Run {record.run_id} exceeded max GPU hours: {usage['gpu_hours']} >= {self.budget.max_gpu_hours}"
            )
        if usage["cost_usd"] >= self.budget.max_cost_usd:
            raise CompShareBudgetExceededError(
                f"Run {record.run_id} exceeded max budget: ${usage['cost_usd']} >= ${self.budget.max_cost_usd}"
            )

    def _persist_ledger(self, record: RunInstanceRecord) -> None:
        if not self.ledger_path:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
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
            f.write(json.dumps(data) + "\n")

    def _record_orphan(self, record: RunInstanceRecord, reason: str) -> None:
        self.orphan_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "run_id": record.run_id,
            "instance_id": record.instance_id,
            "reason": reason,
            "timestamp": time.time(),
        }
        with open(self.orphan_ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
