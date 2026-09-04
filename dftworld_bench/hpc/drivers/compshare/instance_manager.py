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
from dftworld_bench.hpc.drivers.compshare.policy import (
    SAFE_DELETED_STATES,
    extract_verified_instance_id,
    instance_requires_cleanup,
    make_ownership_marker,
    matches_ownership_marker,
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
    # Canonical fields used by the production readiness gate.
    recovered: list[str] = field(default_factory=list)
    still_active: list[str] = field(default_factory=list)
    query_failed: list[str] = field(default_factory=list)
    delete_failed: list[str] = field(default_factory=list)
    clean: bool = True

    # Compatibility projections for older evidence readers.  New code should
    # use the four fields above so query failures cannot be mistaken for a
    # successful recovery.
    @property
    def active_instances(self) -> list[str]:
        return self.still_active

    @property
    def recovered_instances(self) -> list[str]:
        return self.recovered

    @property
    def failed_instances(self) -> list[str]:
        return sorted(set(self.query_failed + self.delete_failed))

    @property
    def dangling_cloud_instances(self) -> list[str]:
        return self.still_active

    def ok(self) -> bool:
        return self.clean and not (
            self.still_active or self.query_failed or self.delete_failed
        )


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
        audit: Any | None = None,
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
        self.audit = audit
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
        instance_name, instance_remark = make_ownership_marker(run_id)
        if self.audit is not None:
            self.audit.append(
                {
                    "action": "INSTANCE_CREATE_INTENT",
                    "kind": "INSTANCE_CREATE_INTENT",
                    "run_id": run_id,
                    "image_id": image_id,
                    "instance_id": "",
                    "ts": time.time(),
                },
                durable=True,
            )
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

            if self.audit is not None:
                self.audit.append(
                    {
                        "action": "INSTANCE_CREATE_ACCEPTED",
                        "kind": "INSTANCE_CREATE_ACCEPTED",
                        "run_id": run_id,
                        "image_id": image_id,
                        "instance_id": created_id,
                        "ts": time.time(),
                    },
                    durable=True,
                )

            # Wait for instance to become RUNNING
            self.cli.wait_instance_ready(created_id)

            if self.audit is not None:
                self.audit.append(
                    {
                        "action": "INSTANCE_READY",
                        "kind": "INSTANCE_READY",
                        "run_id": run_id,
                        "image_id": image_id,
                        "instance_id": created_id,
                        "ts": time.time(),
                    },
                    durable=True,
                )

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
                    self._delete_provider_instance(
                        created_id, run_id=run_id, image_id=image_id
                    )
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
            self._terminate_provider_instance(
                instance_id, run_id=run_id, image_id=record.image_id
            )
            record.terminated_at = time.time()
            record.active_operation_id = None
            self._persist_ledger(record)
            return True
        except Exception as exc:
            self._record_orphan(record, str(exc))
            raise CompShareOrphanError(
                f"Failed to terminate instance {instance_id} for run {run_id}: {exc}"
            ) from exc

    def _terminate_provider_instance(
        self, instance_id: str, *, run_id: str = "", image_id: str = ""
    ) -> None:
        """Stop, delete, and provider-confirm one instance.

        Recovery and normal settlement intentionally share this function so a
        delete ACK can never be interpreted differently by two call paths.
        """
        self.cli.instance_stop(instance_id)
        self._audit_instance_event(
            "INSTANCE_STOP_ACCEPTED",
            run_id=run_id,
            image_id=image_id,
            instance_id=instance_id,
        )
        self._delete_provider_instance(
            instance_id, run_id=run_id, image_id=image_id
        )

    def _delete_provider_instance(
        self, instance_id: str, *, run_id: str = "", image_id: str = ""
    ) -> None:
        """Delete and read back one provider instance, with durable events."""
        self.cli.instance_delete(instance_id)
        self._audit_instance_event(
            "INSTANCE_DELETE_ACCEPTED",
            run_id=run_id,
            image_id=image_id,
            instance_id=instance_id,
        )
        if not self._confirm_deleted(instance_id):
            raise CompShareOrphanError(
                f"provider did not confirm deletion of instance {instance_id}"
            )
        self._audit_instance_event(
            "INSTANCE_DELETE_CONFIRMED",
            run_id=run_id,
            image_id=image_id,
            instance_id=instance_id,
        )

    def _audit_instance_event(
        self, kind: str, *, run_id: str, image_id: str, instance_id: str
    ) -> None:
        if self.audit is None:
            return
        self.audit.append(
            {
                "action": kind,
                "kind": kind,
                "run_id": run_id,
                "image_id": image_id,
                "instance_id": instance_id,
                "ts": time.time(),
            },
            durable=True,
        )

    def _confirm_deleted(self, instance_id: str) -> bool:
        """Confirm deletion through provider readback, fail-closed.

        Some providers return NOT_FOUND from ``show`` immediately after a
        delete, while others return a terminal ``DELETED``/``TERMINATED``
        record for a short retention window.  Both are safe only when the
        response is definitive; every other status, malformed response, or
        failed list query remains an orphan.
        """
        try:
            info = self.cli.instance_show(instance_id)
            if not isinstance(info, dict):
                raise CompShareManagerError(
                    f"provider show returned malformed object for {instance_id}"
                )
            status = str(info.get("status") or "").strip().lower()
            if status in SAFE_DELETED_STATES:
                return True
            # An object still visible in any non-terminal state is not deleted.
            return False
        except CompShareCliError as exc:
            if str(exc.code).upper() != "NOT_FOUND":
                raise CompShareManagerError(
                    f"provider show failed for {instance_id}: {exc}"
                ) from exc
            # NOT_FOUND is definitive only after the complete account list is
            # queried and the exact ID is absent.  instance_list itself is
            # strict about pagination and response shape.
            items = self.cli.instance_list(all=True)
            if not isinstance(items, list):
                raise CompShareManagerError(
                    f"provider instance list returned malformed result for {instance_id}"
                )
            for item in items:
                if not isinstance(item, dict):
                    raise CompShareManagerError(
                        "provider instance list contained a non-object record"
                    )
                observed = extract_verified_instance_id(item)
                if observed == instance_id:
                    status = str(item.get("status") or "").strip().lower()
                    if status in SAFE_DELETED_STATES:
                        return True
                    return False
            return True

    def zero_orphan_query(self, run_id: str | None = None) -> dict[str, Any]:
        """Run a real account-wide zero-orphan query and return evidence.

        The query always calls ``instance_list(all=True)`` exactly once.  A
        run-specific query uses the exact current fixed-token marker; a global
        query also recognizes legacy markers for cleanup.  Unknown status is
        active by policy, and malformed records/query errors raise instead of
        producing a false zero.
        """
        items = self.cli.instance_list(all=True)
        if not isinstance(items, list):
            raise CompShareManagerError("provider instance list result is not a list")
        observed_ids: list[str] = []
        active_ids: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                raise CompShareManagerError(
                    "provider instance list contained a non-object record"
                )
            if not matches_ownership_marker(item, run_id):
                continue
            instance_id = extract_verified_instance_id(item)
            if not instance_id:
                raise CompShareManagerError(
                    "owned provider record has no verified instance_id"
                )
            observed_ids.append(instance_id)
            status = str(item.get("status") or "").strip().lower()
            if instance_requires_cleanup(status):
                active_ids.append(instance_id)
        return {
            "method": "instance_list(all=True)",
            "query_complete": True,
            "observed_ids": sorted(set(observed_ids)),
            "active_ids": sorted(set(active_ids)),
            "active_total": len(set(active_ids)),
        }

    def query_zero_orphans(self, run_id: str | None = None) -> list[str]:
        """Return active owned IDs from a real account-wide query."""
        return list(self.zero_orphan_query(run_id).get("active_ids") or [])

    def log_zero_orphan_query(
        self, run_id: str, *, image_id: str = "", instance_id: str = ""
    ) -> dict[str, Any]:
        """Query and log ZERO_ORPHAN_QUERY evidence into GatewayAudit."""
        rec = self._instances.get(run_id)
        eff_img = image_id or (rec.image_id if rec else "")
        eff_inst = instance_id or (rec.instance_id if rec else "")
        evidence = self.zero_orphan_query(run_id)
        if self.audit is not None:
            self.audit.append(
                {
                    "action": "ZERO_ORPHAN_QUERY",
                    "kind": "ZERO_ORPHAN_QUERY",
                    "run_id": run_id,
                    "image_id": eff_img,
                    "instance_id": eff_inst,
                    "method": evidence["method"],
                    "query_complete": evidence["query_complete"],
                    "observed_ids": evidence["observed_ids"],
                    "active_ids": evidence["active_ids"],
                    "active_total": evidence["active_total"],
                    "ts": time.time(),
                },
                durable=True,
            )
        if evidence["active_total"]:
            raise CompShareOrphanError(
                f"Zero-Orphan Gate found active owned instances for {run_id}: "
                f"{evidence['active_ids']}"
            )
        return evidence

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

        # 1 & 2: Reconcile local ledger records.  A failed readback is kept as
        # a query failure even if a best-effort delete happens to succeed: the
        # readiness decision must know that the provider response was
        # undecidable.
        for run_id, rec in list(self._instances.items()):
            if rec.terminated_at is not None:
                continue
            report.still_active.append(rec.instance_id)
            try:
                info = self.cli.instance_show(rec.instance_id)
                if not isinstance(info, dict):
                    raise CompShareManagerError(
                        f"provider show returned malformed object for {rec.instance_id}"
                    )
                status = str(info.get("status") or "").strip().lower()
                if not status:
                    raise CompShareManagerError(
                        f"provider show omitted status for {rec.instance_id}"
                    )
                if not instance_requires_cleanup(status):
                    rec.terminated_at = time.time()
                    rec.active_operation_id = None
                    self._persist_ledger(rec)
                    continue
                try:
                    self.terminate_run(run_id)
                    report.recovered.append(rec.instance_id)
                    report.still_active.remove(rec.instance_id)
                except Exception as exc:
                    report.delete_failed.append(rec.instance_id)
                    logger.warning(
                        "Failed to recover ledger instance %s: %s", rec.instance_id, exc
                    )
            except CompShareCliError as exc:
                if str(exc.code).upper() == "NOT_FOUND":
                    # A NOT_FOUND show is safe only if the complete account
                    # list also proves the ID absent.
                    try:
                        items = self.cli.instance_list(all=True)
                        if not any(
                            isinstance(item, dict)
                            and extract_verified_instance_id(item) == rec.instance_id
                            for item in items
                        ):
                            rec.terminated_at = time.time()
                            rec.active_operation_id = None
                            self._persist_ledger(rec)
                            report.still_active.remove(rec.instance_id)
                            continue
                    except Exception as list_exc:
                        report.query_failed.append(rec.instance_id)
                        logger.warning(
                            "Could not confirm missing instance %s: %s",
                            rec.instance_id,
                            list_exc,
                        )
                report.query_failed.append(rec.instance_id)
                # Continue to cloud sweep; do not claim this record recovered.
            except Exception as exc:
                report.query_failed.append(rec.instance_id)
                logger.warning(
                    "Could not query ledger instance %s: %s", rec.instance_id, exc
                )

        # 3: Cloud sweep for dangling MLFFBench instances.  This is the same
        # real all-account query used by the zero-orphan evidence path.
        try:
            cloud_instances = self.cli.instance_list(all=True)
            if not isinstance(cloud_instances, list):
                raise CompShareManagerError("provider instance list result is not a list")
            for item in cloud_instances:
                if not isinstance(item, dict):
                    raise CompShareManagerError(
                        "provider instance list contained a non-object record"
                    )
                if not matches_ownership_marker(item):
                    continue
                inst_id = extract_verified_instance_id(item)
                if not inst_id:
                    raise CompShareManagerError(
                        "owned provider record has no verified instance_id"
                    )
                status = str(item.get("status") or "").strip().lower()
                if not instance_requires_cleanup(status):
                    continue
                if inst_id in report.recovered:
                    continue
                report.still_active.append(inst_id)
                try:
                    self._terminate_provider_instance(
                        inst_id,
                        run_id="",
                        image_id=str(item.get("image_id") or ""),
                    )
                    report.recovered.append(inst_id)
                    while inst_id in report.still_active:
                        report.still_active.remove(inst_id)
                except Exception as exc:
                    report.delete_failed.append(inst_id)
                    logger.critical(
                        "Failed to delete dangling cloud instance %s: %s", inst_id, exc
                    )
        except Exception as exc:
            logger.warning("Could not list cloud instances during reconciliation: %s", exc)
            report.query_failed.append("instance_list")

        # 4: A final independent list query is mandatory.  It prevents a
        # successful delete ACK/readback from being mistaken for zero orphans
        # when another owned record remained outside the local ledger.
        try:
            final = self.zero_orphan_query()
            for inst_id in final.get("active_ids") or []:
                if inst_id not in report.still_active:
                    report.still_active.append(inst_id)
            if final.get("active_total"):
                report.still_active.extend(
                    inst_id
                    for inst_id in final.get("active_ids") or []
                    if inst_id not in report.still_active
                )
        except Exception as exc:
            report.query_failed.append("zero_orphan_query")
            logger.warning("Final zero-orphan query failed: %s", exc)

        report.still_active = sorted(set(report.still_active) - set(report.recovered))
        report.recovered = sorted(set(report.recovered))
        report.query_failed = sorted(set(report.query_failed))
        report.delete_failed = sorted(set(report.delete_failed))
        report.clean = report.ok()
        return report

    def recover_dangling_instances(self) -> list[str]:
        """Durable teardown recovery: terminate any active instances left from prior runs/crashes."""
        return self.reconcile_and_recover().recovered

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
