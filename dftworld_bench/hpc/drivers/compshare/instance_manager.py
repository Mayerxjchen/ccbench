"""Run-scoped instance manager for CompShare GPU resources using CompShareCli.

Core Invariants:
1. Max 1 GPU instance per managed account scope and Run.
2. Max 1 GPU per instance.
3. Max 1 concurrent GPU operation per Run.
4. Consecutive GPU operations within the same Run reuse the active instance.
5. Different Runs NEVER share an instance.
6. Settlement guarantees: stop -> terminate.
7. Recycling failure is logged to orphan ledger and produces INFRA_INVALID.
8. Budget limits (GPU hours, instance walltime, cost) strictly enforced.
9. Durable recovery from persistent ledger on restart.
10. A known lineage is rolled back only after a readiness failure; uncertain
    provider acknowledgements remain durable and block automatic retry.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from contextlib import contextmanager
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
from dftworld_bench.hpc.drivers.compshare.state import (
    AccountScopeLock,
    AccountScopeLockTimeout,
    CompSharePolicyError,
    CompShareStateStore,
    CREATE_INTENT,
    CREATE_UNCERTAIN,
    LIFECYCLE_STATES,
    PROVISIONING,
    READY,
    SAFE_LIFECYCLE_STATES,
    TEARDOWN_FAILED,
    TEARDOWN_INTENT,
    TERMINATED,
    UNCERTAIN_LIFECYCLE_STATES,
    assert_account_capacity,
    lineage_id_for,
    list_managed_instances,
)
from dftworld_bench.hpc.runtime_resolution import is_placeholder_artifact

logger = logging.getLogger(__name__)


class CompShareManagerError(Exception):
    """General error in CompShare instance management."""


class CompShareBudgetExceededError(CompShareManagerError):
    """Budget constraint violated (GPU hours, duration, or cost)."""


class CompShareOrphanError(CompShareManagerError):
    """An instance could not be cleanly terminated, producing an orphan."""


class CompShareCreateUncertainError(CompShareManagerError):
    """A provider create outcome cannot be proven; retrying would risk a duplicate."""


@dataclass
class BudgetConfig:
    max_gpu_hours: float = 4.0
    max_instance_minutes: int = 120
    max_cost_cny: float = 150.0
    hourly_rate_cny: float = 14.0
    # Formal CompShare runs are account/run scoped to exactly one instance.
    # Keep this explicit so the SiteProfile policy cannot disappear at the
    # provider boundary.
    max_instances: int = 1
    managed_account_scope_id: str | None = None

    @property
    def max_instance_hours(self) -> float:
        """Normalized SiteProfile spelling for the existing hour limit."""
        return self.max_gpu_hours

    @property
    def max_budget_cny(self) -> float:
        """Normalized SiteProfile spelling for the existing cost limit."""
        return self.max_cost_cny


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
    # Account-scoped lifecycle state is persisted in both the state journal and
    # the compatibility ledger.  Older ledger rows default to READY/TERMINATED
    # during load so existing test fixtures remain readable.
    lifecycle_state: str = READY
    lineage_id: str = ""
    ownership_name: str = ""
    ownership_remark: str = ""


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
        state_root: Path | str | None = None,
        provider: str = "compshare",
        target_binding: str | None = None,
        account: str | None = None,
        production: bool = False,
        lock_timeout_sec: float | None = 30.0,
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
        self.audit = audit
        if state_root is not None and not str(state_root).strip():
            raise CompShareManagerError(
                "CompShare manager requires a non-empty state_root"
            )
        if self.budget.max_instances != 1:
            raise CompShareManagerError(
                "RunScopedInstanceManager requires budget max_instances=1"
            )
        if production and state_root is None:
            raise CompShareManagerError(
                "production CompShare manager requires an explicit persistent "
                "compshare_state_root"
            )
        if production and not target_binding:
            raise CompShareManagerError(
                "production CompShare manager requires an explicit target_binding"
            )
        if production and not account:
            raise CompShareManagerError(
                "production CompShare manager requires an explicit account"
            )

        # Test-mode callers historically supplied only a ledger path.  Keep
        # that ergonomic path, while production always receives an explicit
        # durable state root from the trusted SiteProfile/composition root.
        if state_root is None:
            if ledger_path is not None:
                state_root = Path(ledger_path).parent
            else:
                state_root = Path("runs")
        self.state_root = Path(state_root)
        if ledger_path is None:
            # Preserve the historical default filename for callers that use
            # the manager directly; production composition supplies its own
            # explicit path under the durable state root.
            ledger_path = self.state_root / "compshare-ledger.jsonl"
        if orphan_ledger_path is None:
            orphan_ledger_path = self.state_root / "compshare-orphans.jsonl"
        self.ledger_path = Path(ledger_path)
        self.orphan_ledger_path = Path(orphan_ledger_path)
        self.provider = provider
        self.target_binding = target_binding or "offline://compshare"
        self.account = account or "offline-account"
        self.production = production
        self.lock_timeout_sec = lock_timeout_sec
        self._instances: dict[str, RunInstanceRecord] = {}
        self._state_records: dict[str, dict[str, Any]] = {}
        self._account_lock = AccountScopeLock(
            self.state_root,
            provider=self.provider,
            target_binding=self.target_binding,
            account=self.account,
            managed_account_scope_id=self.budget.managed_account_scope_id,
            timeout_sec=self.lock_timeout_sec,
        )
        self._state_store = CompShareStateStore(
            self.state_root, self._account_lock.scope_hash
        )
        self._load_ledger()
        self._refresh_state()

    def _load_ledger(self) -> None:
        self._instances = self._read_ledger()

    def _read_ledger(self) -> dict[str, RunInstanceRecord]:
        """Read the append-only ledger and retain the latest row per run."""
        if not self.ledger_path or not self.ledger_path.exists():
            return {}
        latest: dict[str, RunInstanceRecord] = {}
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            raise ValueError("ledger row must be an object")
                        run_id = data["run_id"]
                        instance_id = data["instance_id"]
                        image_id = data["image_id"]
                        if not all(
                            isinstance(value, str) and value
                            for value in (run_id, instance_id, image_id)
                        ):
                            raise ValueError(
                                "ledger row run_id, instance_id, and image_id must be non-empty strings"
                            )
                        terminated_at = data.get("terminated_at")
                        lifecycle_state = data.get("lifecycle_state") or (
                            TERMINATED if terminated_at is not None else READY
                        )
                        if lifecycle_state not in LIFECYCLE_STATES:
                            raise ValueError(
                                f"unknown ledger lifecycle state: {lifecycle_state!r}"
                            )
                        gpu_count = data.get("gpu_count", 1)
                        if (
                            isinstance(gpu_count, bool)
                            or not isinstance(gpu_count, int)
                            or gpu_count != 1
                        ):
                            raise ValueError("ledger gpu_count must be exactly 1")
                        name, remark = make_ownership_marker(run_id)
                        rec = RunInstanceRecord(
                            run_id=run_id,
                            instance_id=instance_id,
                            image_id=image_id,
                            created_at=data["created_at"],
                            gpu_type=data.get("gpu_type", "4090"),
                            gpu_count=gpu_count,
                            terminated_at=terminated_at,
                            active_operation_id=data.get("active_operation_id"),
                            accumulated_gpu_seconds=data.get(
                                "accumulated_gpu_seconds", 0.0
                            ),
                            lifecycle_state=lifecycle_state,
                            lineage_id=data.get("lineage_id")
                            or lineage_id_for(self._account_lock.scope_hash, run_id, image_id),
                            ownership_name=data.get("ownership_name") or name,
                            ownership_remark=data.get("ownership_remark") or remark,
                        )
                        latest[rec.run_id] = rec
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as exc:
            logger.error("Failed to load instance ledger %s: %s", self.ledger_path, exc)
            raise CompShareManagerError(
                f"Ledger file {self.ledger_path} is corrupt; refusing to start in unverified state: {exc}"
            ) from exc
        return latest

    def _refresh_ledger_locked(self) -> None:
        """Refresh local records after the account lock is held."""
        self._instances = self._read_ledger()

    def _refresh_state(self) -> None:
        try:
            self._state_records = self._state_store.latest()
        except Exception as exc:
            logger.error("Failed to load CompShare state %s: %s", self._state_store.path, exc)
            raise CompShareManagerError(
                f"CompShare state {self._state_store.path} is corrupt; refusing to start: {exc}"
            ) from exc

    def _refresh_account_state_locked(self) -> None:
        """Refresh both durable projections while holding the account lock."""
        self._refresh_ledger_locked()
        self._refresh_state()

    def _persist_state_event(
        self,
        status: str,
        *,
        run_id: str,
        lineage_id: str,
        image_id: str,
        instance_id: str = "",
        ownership_name: str = "",
        ownership_remark: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "status": status,
            "run_id": run_id,
            "lineage_id": lineage_id,
            "image_id": image_id,
        }
        if instance_id:
            event["instance_id"] = instance_id
        if ownership_name:
            event["ownership_name"] = ownership_name
        if ownership_remark:
            event["ownership_remark"] = ownership_remark
        if reason:
            event["reason"] = reason
        record = self._state_store.append_event(event)
        self._state_records[lineage_id] = record
        return record

    def _state_for_run(self, run_id: str) -> list[dict[str, Any]]:
        return sorted(
            (
                record
                for record in self._state_records.values()
                if record.get("run_id") == run_id
            ),
            key=lambda record: str(record.get("lineage_id")),
        )

    def _active_uncertain_states_locked(self) -> list[dict[str, Any]]:
        return [
            record
            for record in self._state_records.values()
            if record.get("status") in UNCERTAIN_LIFECYCLE_STATES
            and record.get("status") != TERMINATED
        ]

    def _raise_if_unresolved_locked(self, operation: str) -> None:
        unresolved = self._active_uncertain_states_locked()
        if not unresolved:
            return
        details = sorted(
            f"{item.get('run_id')}:{item.get('status')}"
            for item in unresolved
        )
        raise CompShareManagerError(
            f"cannot perform {operation}; unresolved CompShare lifecycle state(s): "
            + ", ".join(details)
        )

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
        if is_placeholder_artifact(image_id):
            raise CompShareManagerError(
                f"placeholder or unassigned CompShare image is not executable: {image_id!r}"
            )

        # Every decision, including reuse, is made from a fresh durable view
        # while holding the account-wide lock.  This closes the stale-manager
        # race where two processes each believe their local ledger is empty.
        try:
            with self._account_lock:
                self._refresh_account_state_locked()
                return self._get_or_create_locked(
                    run_id,
                    image_id,
                    operation_id=operation_id,
                    gpu_type=gpu_type,
                    gpu_count=gpu_count,
                )
        except AccountScopeLockTimeout as exc:
            raise CompShareManagerError(
                f"timed out acquiring CompShare account lock: {exc}"
            ) from exc

    def _get_or_create_locked(
        self,
        run_id: str,
        image_id: str,
        *,
        operation_id: str,
        gpu_type: str,
        gpu_count: int,
    ) -> str:
        """Get/create implementation; caller must hold ``_account_lock``."""
        record = self._instances.get(run_id)
        if record is not None and record.terminated_at is None:
            if record.lifecycle_state in UNCERTAIN_LIFECYCLE_STATES:
                raise CompShareManagerError(
                    f"Run {run_id} is in unresolved lifecycle state "
                    f"{record.lifecycle_state}; reconcile before retrying"
                )
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

        # A prior durable intent/uncertain teardown blocks *all* new creates in
        # this account scope.  The operator must reconcile it explicitly; no
        # retry can safely infer whether a provider request was accepted.
        unresolved = self._active_uncertain_states_locked()
        if unresolved:
            details = sorted(
                f"{item.get('run_id')}:{item.get('status')}"
                for item in unresolved
            )
            raise CompShareManagerError(
                "CompShare account has unresolved lifecycle state(s): "
                + ", ".join(details)
            )

        # The provider inventory and capacity helper are intentionally first;
        # the stock check and offline command plan happen before any durable
        # create intent or provider mutation.
        try:
            managed = self._provider_inventory_locked()
        except CompSharePolicyError as exc:
            raise CompShareManagerError(f"account capacity preflight failed: {exc}") from exc
        try:
            assert_account_capacity(
                self.cli,
                max_instances=self.budget.max_instances,
                inventory=managed,
            )
        except CompSharePolicyError as exc:
            raise CompShareManagerError(f"account capacity preflight failed: {exc}") from exc
        # A durable READY/PROVISIONING ledger row is itself a reservation.
        # Keep it fail-closed when a provider's list is eventually consistent
        # or a test provider has process-local inventory; only an explicit
        # terminated row releases the account-wide slot.
        ledger_active = sorted(
            record.instance_id
            for record in self._instances.values()
            if record.terminated_at is None and record.run_id != run_id
        )
        if ledger_active:
            raise CompShareManagerError(
                "CompShare max_instances=1 guard found active durable "
                f"instance(s): {ledger_active}"
            )
        return self._create_instance_unlocked(
            run_id,
            image_id,
            operation_id=operation_id,
            gpu_type=gpu_type,
        )

    @contextmanager
    def _creation_lock(self):
        """Compatibility alias for the account-wide lifecycle lock."""
        with self._account_lock:
            yield

    def _provider_inventory_locked(self):
        """Return validated managed inventory; caller must hold account lock."""
        try:
            return list_managed_instances(self.cli)
        except CompSharePolicyError:
            raise
        except Exception as exc:
            raise CompSharePolicyError(
                f"provider inventory query failed: {exc}"
            ) from exc

    def plan_instance_create(
        self,
        run_id: str,
        image_id: str,
        *,
        gpu_type: str = "4090",
        count: int = 1,
        provider_dry_run: bool = False,
    ):
        """Build a pure provider command plan without calling CLI or reading credentials."""
        from dftworld_bench.hpc.drivers.compshare.cli import (
            InstanceCreateSpec,
            build_instance_create_plan,
        )

        name, remark = make_ownership_marker(run_id)
        spec = InstanceCreateSpec(
            image=image_id,
            name=name,
            remark=remark,
            region=self.region,
            zone=self.zone,
            gpu=gpu_type,
            count=count,
            cpu=self.default_cpus,
            memory=self.default_memory,
            disk=self.default_disk,
            provider_dry_run=provider_dry_run,
            image_source=self.default_image_source,
        )
        return build_instance_create_plan(spec)

    def _create_instance_unlocked(
        self,
        run_id: str,
        image_id: str,
        *,
        operation_id: str,
        gpu_type: str,
    ) -> str:
        """Create exactly one lineage while the account lock is held.

        The ordering is deliberately durable and fail-closed:
        ``stock -> offline plan -> CREATE_INTENT -> provider create ->
        PROVISIONING -> ready -> account-wide post-check``.  A provider
        exception after the real create call is treated as uncertain and is
        never retried automatically.
        """
        # Check pre-creation stock via the provider only after the complete
        # account inventory preflight performed by the caller.
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

        # Build the exact command plan after stock is known and before writing
        # an intent.  This function is pure and performs no runner call.
        self.plan_instance_create(run_id, image_id, gpu_type=gpu_type, count=1)
        instance_name, instance_remark = make_ownership_marker(run_id)
        lineage_id = lineage_id_for(self._account_lock.scope_hash, run_id, image_id)
        self._persist_state_event(
            CREATE_INTENT,
            run_id=run_id,
            lineage_id=lineage_id,
            image_id=image_id,
            ownership_name=instance_name,
            ownership_remark=instance_remark,
        )
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

        # Create new instance for run via CLI.  ``created_id`` is assigned only
        # after the provider has returned an explicit ID; an exception or
        # malformed response leaves an uncertain intent and cannot trigger a
        # second provider create.
        created_id: str | None = None
        try:
            # The provider dry-run is an online validation call; it is
            # intentionally separate from the pure offline plan API.
            try:
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
                    provider_dry_run=True,
                    image_source=self.default_image_source,
                )
            except Exception as exc:
                self._mark_create_uncertain_locked(
                    run_id,
                    lineage_id,
                    image_id,
                    reason=f"provider dry-run failed before real create: {exc}",
                )
                raise

            # Real create.
            try:
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
                    provider_dry_run=False,
                    image_source=self.default_image_source,
                )
            except Exception as exc:
                self._mark_create_uncertain_locked(
                    run_id,
                    lineage_id,
                    image_id,
                    reason=f"real create acknowledgement uncertain: {exc}",
                )
                raise CompShareCreateUncertainError(
                    f"CompShare create acknowledgement is uncertain for run {run_id}; "
                    "reconcile before retrying"
                ) from exc

            if not isinstance(inst_info, dict):
                reason = f"real create returned malformed response: {inst_info!r}"
                self._mark_create_uncertain_locked(
                    run_id, lineage_id, image_id, reason=reason
                )
                raise CompShareCreateUncertainError(
                    f"CompShare create acknowledgement is uncertain for run {run_id}; "
                    "reconcile before retrying"
                )
            raw_created_id = inst_info.get("instance_id") or inst_info.get("id")
            if not isinstance(raw_created_id, str) or not raw_created_id.strip():
                reason = f"real create returned no verified instance_id: {inst_info!r}"
                self._mark_create_uncertain_locked(
                    run_id, lineage_id, image_id, reason=reason
                )
                raise CompShareCreateUncertainError(
                    f"CompShare create acknowledgement is uncertain for run {run_id}; "
                    "reconcile before retrying"
                )
            created_id = raw_created_id.strip()

            # The ID is now known.  Persist PROVISIONING before any wait or
            # readback so a crash cannot make a subsequent manager resubmit.
            record = RunInstanceRecord(
                run_id=run_id,
                instance_id=created_id,
                image_id=image_id,
                created_at=time.time(),
                gpu_type=gpu_type,
                gpu_count=1,
                active_operation_id=operation_id,
                lifecycle_state=PROVISIONING,
                lineage_id=lineage_id,
                ownership_name=instance_name,
                ownership_remark=instance_remark,
            )
            self._persist_state_event(
                PROVISIONING,
                run_id=run_id,
                lineage_id=lineage_id,
                image_id=image_id,
                instance_id=created_id,
                ownership_name=instance_name,
                ownership_remark=instance_remark,
            )
            self._instances[run_id] = record
            self._persist_ledger(record)

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

            # Wait for instance to become RUNNING.
            try:
                self.cli.wait_instance_ready(created_id)
            except Exception as exc:
                # A returned ID plus exact marker is sufficient to authorize a
                # rollback of this lineage only.  Unknown ownership or a
                # failed readback must remain an orphan/uncertain state.
                try:
                    self._rollback_lineage_instance_locked(
                        record,
                        reason=f"Creation readiness failed ({exc})",
                    )
                except Exception as rollback_exc:
                    self._mark_teardown_failed_locked(
                        record,
                        reason=(
                            f"Creation readiness failed ({exc}); rollback failed "
                            f"({rollback_exc})"
                        ),
                    )
                raise

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

            # Read the complete account inventory while still holding the
            # same lock.  The exact marker/ID lineage must be visible and
            # active before this run is released to job submission.
            try:
                self._post_create_lineage_check_locked(record)
            except Exception as exc:
                self._mark_create_uncertain_locked(
                    run_id,
                    lineage_id,
                    image_id,
                    instance_id=created_id,
                    reason=f"post-create lineage check failed: {exc}",
                )
                raise CompShareCreateUncertainError(
                    f"CompShare create lineage is uncertain for run {run_id}; "
                    "reconcile before retrying"
                ) from exc

            record.lifecycle_state = READY
            self._persist_state_event(
                READY,
                run_id=run_id,
                lineage_id=lineage_id,
                image_id=image_id,
                instance_id=created_id,
                ownership_name=instance_name,
                ownership_remark=instance_remark,
            )
            self._persist_ledger(record)
            return created_id
        except Exception as exc:
            # Rollback is handled at the exact readiness boundary above.  In
            # particular, this outer guard never deletes a provider ID after
            # an uncertain acknowledgement or post-create inventory failure.
            raise

    def _mark_create_uncertain_locked(
        self,
        run_id: str,
        lineage_id: str,
        image_id: str,
        *,
        reason: str,
        instance_id: str = "",
    ) -> None:
        """Persist an unresolved create so no automatic second create occurs."""
        ownership_name, ownership_remark = make_ownership_marker(run_id)
        self._persist_state_event(
            CREATE_UNCERTAIN,
            run_id=run_id,
            lineage_id=lineage_id,
            image_id=image_id,
            instance_id=instance_id,
            ownership_name=ownership_name,
            ownership_remark=ownership_remark,
            reason=reason,
        )
        rec = self._instances.get(run_id)
        if rec is not None:
            rec.lifecycle_state = CREATE_UNCERTAIN
            self._persist_ledger(rec)

    def _post_create_lineage_check_locked(self, record: RunInstanceRecord) -> None:
        """Require one exact visible, non-terminal provider record for lineage."""
        managed = self._provider_inventory_locked()
        matching = [
            view
            for view in managed
            if view.instance_id == record.instance_id
            and view.item.get("name") == record.ownership_name
            and view.item.get("remark") == record.ownership_remark
        ]
        if len(matching) != 1:
            raise CompShareManagerError(
                "post-create inventory did not contain exactly one matching "
                f"lineage record for {record.instance_id}"
            )
        if matching[0].status in SAFE_DELETED_STATES:
            raise CompShareManagerError(
                f"post-create lineage {record.instance_id} is not active: {matching[0].status}"
            )

    def _rollback_lineage_instance_locked(
        self, record: RunInstanceRecord, *, reason: str
    ) -> None:
        """Rollback only an ID proven to carry this request's exact marker."""
        try:
            info = self.cli.instance_show(record.instance_id)
            if not isinstance(info, dict):
                raise CompShareManagerError("provider show returned malformed object")
            observed_id = extract_verified_instance_id(info)
            if (
                observed_id != record.instance_id
                or info.get("name") != record.ownership_name
                or info.get("remark") != record.ownership_remark
            ):
                raise CompShareManagerError(
                    "provider record ownership does not match this create lineage"
                )
            status = info.get("status")
            if not isinstance(status, str) or not status:
                raise CompShareManagerError("provider record status is malformed")
            if status.strip().lower() in SAFE_DELETED_STATES:
                record.lifecycle_state = TERMINATED
                record.terminated_at = time.time()
                record.active_operation_id = None
                self._persist_state_event(
                    TERMINATED,
                    run_id=record.run_id,
                    lineage_id=record.lineage_id,
                    image_id=record.image_id,
                    instance_id=record.instance_id,
                    ownership_name=record.ownership_name,
                    ownership_remark=record.ownership_remark,
                    reason=reason,
                )
                self._persist_ledger(record)
                return
            self._terminate_provider_instance(
                record.instance_id,
                run_id=record.run_id,
                image_id=record.image_id,
            )
            record.lifecycle_state = TERMINATED
            record.terminated_at = time.time()
            record.active_operation_id = None
            self._persist_state_event(
                TERMINATED,
                run_id=record.run_id,
                lineage_id=record.lineage_id,
                image_id=record.image_id,
                instance_id=record.instance_id,
                ownership_name=record.ownership_name,
                ownership_remark=record.ownership_remark,
                reason=reason,
            )
            self._persist_ledger(record)
        except Exception as exc:
            self._record_orphan(record, f"{reason}; rollback deletion failed ({exc})")
            raise

    def _mark_teardown_failed_locked(
        self, record: RunInstanceRecord, *, reason: str
    ) -> None:
        record.lifecycle_state = TEARDOWN_FAILED
        self._persist_state_event(
            TEARDOWN_FAILED,
            run_id=record.run_id,
            lineage_id=record.lineage_id,
            image_id=record.image_id,
            instance_id=record.instance_id,
            ownership_name=record.ownership_name,
            ownership_remark=record.ownership_remark,
            reason=reason,
        )
        self._persist_ledger(record)

    def release_operation(
        self, run_id: str, operation_id: str, *, gpu_seconds: float = 0.0
    ) -> None:
        """Release the active operation lock and accumulate billable GPU seconds."""
        try:
            with self._account_lock:
                self._refresh_account_state_locked()
                record = self._instances.get(run_id)
                if record is not None and record.active_operation_id == operation_id:
                    record.active_operation_id = None
                    record.accumulated_gpu_seconds += gpu_seconds
                    self._persist_ledger(record)
        except AccountScopeLockTimeout as exc:
            raise CompShareManagerError(
                f"timed out acquiring CompShare account lock for release: {exc}"
            ) from exc

    def terminate_run(self, run_id: str) -> bool:
        """Mandatory settlement step: stop -> delete instance for the run."""
        try:
            with self._account_lock:
                self._refresh_account_state_locked()
                record = self._instances.get(run_id)
                if record is None or record.terminated_at is not None:
                    pending = self._state_for_run(run_id)
                    if not pending or all(
                        item.get("status") in SAFE_LIFECYCLE_STATES
                        for item in pending
                    ):
                        return True
                    # A durable intent with no provider ID cannot be safely
                    # terminated by guessing; reconciliation must inspect it.
                    raise CompShareCreateUncertainError(
                        f"run {run_id} has unresolved create state; reconcile before teardown"
                    )

                if record.lifecycle_state == CREATE_UNCERTAIN:
                    raise CompShareCreateUncertainError(
                        f"run {run_id} has CREATE_UNCERTAIN state; reconcile before teardown"
                    )
                instance_id = record.instance_id
                self._persist_state_event(
                    TEARDOWN_INTENT,
                    run_id=run_id,
                    lineage_id=record.lineage_id,
                    image_id=record.image_id,
                    instance_id=instance_id,
                    ownership_name=record.ownership_name,
                    ownership_remark=record.ownership_remark,
                )
                try:
                    self._terminate_lineage_locked(record)
                    return True
                except Exception as exc:
                    self._mark_teardown_failed_locked(record, reason=str(exc))
                    self._record_orphan(record, str(exc))
                    raise CompShareOrphanError(
                        f"Failed to terminate instance {instance_id} for run {run_id}: {exc}"
                    ) from exc
        except AccountScopeLockTimeout as exc:
            raise CompShareManagerError(
                f"timed out acquiring CompShare account lock for terminate: {exc}"
            ) from exc

    def _terminate_lineage_locked(self, record: RunInstanceRecord) -> None:
        """Terminate a record only after exact ID and ownership verification."""
        info = self.cli.instance_show(record.instance_id)
        if not isinstance(info, dict):
            raise CompShareManagerError("provider show returned malformed object")
        observed_id = extract_verified_instance_id(info)
        if (
            observed_id != record.instance_id
            or info.get("name") != record.ownership_name
            or info.get("remark") != record.ownership_remark
        ):
            raise CompShareManagerError(
                "provider record ownership does not match the managed lineage"
            )
        status = info.get("status")
        if not isinstance(status, str) or not status.strip():
            raise CompShareManagerError("provider record status is malformed")
        if status.strip().lower() not in SAFE_DELETED_STATES:
            self._terminate_provider_instance(
                record.instance_id,
                run_id=record.run_id,
                image_id=record.image_id,
            )
        record.terminated_at = time.time()
        record.active_operation_id = None
        record.lifecycle_state = TERMINATED
        self._persist_state_event(
            TERMINATED,
            run_id=record.run_id,
            lineage_id=record.lineage_id,
            image_id=record.image_id,
            instance_id=record.instance_id,
            ownership_name=record.ownership_name,
            ownership_remark=record.ownership_remark,
        )
        self._persist_ledger(record)

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
        try:
            with self._account_lock:
                self._refresh_account_state_locked()
                self._raise_if_unresolved_locked("zero-orphan query")
                managed = self._provider_inventory_locked()
                observed_ids: list[str] = []
                active_ids: list[str] = []
                for view in managed:
                    if not matches_ownership_marker(view.item, run_id):
                        continue
                    observed_ids.append(view.instance_id)
                    if view.status not in SAFE_DELETED_STATES and instance_requires_cleanup(
                        view.status
                    ):
                        active_ids.append(view.instance_id)
                return {
                    "method": "instance_list(all=True)",
                    "query_complete": True,
                    "observed_ids": sorted(set(observed_ids)),
                    "active_ids": sorted(set(active_ids)),
                    "active_total": len(set(active_ids)),
                }
        except AccountScopeLockTimeout as exc:
            raise CompShareManagerError(
                f"timed out acquiring CompShare account lock for zero-orphan query: {exc}"
            ) from exc

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
        """Reconcile every managed resource while holding the account lock.

        A complete, validated inventory is obtained before any teardown.  A
        missing or ambiguous create intent is never converted into a fresh
        create; only an exact marker/ID lineage may be adopted for cleanup.
        Reconciliation and normal create/terminate paths share the same lock,
        so they cannot delete one another's in-flight resources.
        """
        report = RecoveryReport()
        try:
            with self._account_lock:
                self._refresh_account_state_locked()
                try:
                    managed = self._provider_inventory_locked()
                except Exception as exc:
                    report.query_failed.append("instance_list")
                    logger.warning("CompShare reconciliation inventory failed: %s", exc)
                    report.clean = False
                    return report

                by_id = {view.instance_id: view for view in managed}
                records = dict(self._instances)
                # A state event may be the only durable artifact after a crash
                # between provider acknowledgement and ledger append.
                for state in self._state_records.values():
                    if state.get("status") in SAFE_LIFECYCLE_STATES:
                        continue
                    run_id = str(state.get("run_id") or "")
                    if not run_id:
                        report.query_failed.append(str(state.get("lineage_id") or "state"))
                        continue
                    records.setdefault(
                        run_id, self._record_from_state_locked(state, by_id)
                    )

                handled: set[str] = set()
                for record in records.values():
                    self._reconcile_record_locked(record, by_id, report, handled)

                # A managed provider instance without a local record is still
                # safe to clean because the inventory validator proved its
                # ownership marker.  Unmanaged records are never touched.
                for view in managed:
                    if view.status in SAFE_DELETED_STATES or view.instance_id in handled:
                        continue
                    # An active local record with this ID is handled above,
                    # including a mismatched marker which must remain a query
                    # failure rather than being adopted by the sweep.
                    if any(
                        rec.terminated_at is None
                        and rec.instance_id == view.instance_id
                        for rec in records.values()
                    ):
                        continue
                    try:
                        self._terminate_provider_instance(
                            view.instance_id,
                            run_id="reconcile",
                            image_id=str(view.item.get("image_id") or ""),
                        )
                        report.recovered.append(view.instance_id)
                    except Exception as exc:
                        report.delete_failed.append(view.instance_id)
                        logger.warning(
                            "Failed to reconcile dangling %s: %s",
                            view.instance_id,
                            exc,
                        )

                # A second complete list is the final zero-orphan assertion.
                try:
                    final = self._provider_inventory_locked()
                except Exception as exc:
                    report.query_failed.append("final_instance_list")
                    logger.warning("Final reconciliation inventory failed: %s", exc)
                else:
                    report.still_active.extend(
                        view.instance_id for view in final
                        if view.status not in SAFE_DELETED_STATES
                    )
        except AccountScopeLockTimeout as exc:
            report.query_failed.append("account_lock")
            logger.warning("Reconciliation account lock timed out: %s", exc)

        report.still_active = sorted(set(report.still_active) - set(report.recovered))
        report.recovered = sorted(set(report.recovered))
        report.query_failed = sorted(set(report.query_failed))
        report.delete_failed = sorted(set(report.delete_failed))
        report.clean = report.ok()
        return report

    def _reconcile_record_locked(
        self,
        record: RunInstanceRecord,
        by_id: dict[str, Any],
        report: RecoveryReport,
        handled: set[str],
    ) -> None:
        """Reconcile one durable lineage; caller holds the account lock.

        Ledger-only rows are intentionally sent through this same path as
        state-journal rows.  This keeps recovery compatible with pre-C4b
        ledgers without duplicating the ownership and uncertain-state rules.
        """
        if record.terminated_at is not None:
            return
        key = record.instance_id or record.lineage_id or record.run_id
        state = self._state_records.get(record.lineage_id, {})
        state_status = state.get("status") or record.lifecycle_state
        view = by_id.get(record.instance_id) if record.instance_id else None

        if state_status in UNCERTAIN_LIFECYCLE_STATES:
            # An uncertain request can be recovered only when the complete
            # inventory proves both its ID and exact ownership marker.
            if view is None or not self._view_matches_record(view, record):
                report.query_failed.append(key)
                return
        if view is None:
            if state_status in UNCERTAIN_LIFECYCLE_STATES:
                report.query_failed.append(key)
                return
            self._mark_record_terminated_locked(
                record, reason="provider complete list omitted instance"
            )
            report.recovered.append(key)
            if record.instance_id:
                handled.add(record.instance_id)
            return
        if not self._view_matches_record(view, record):
            report.query_failed.append(key)
            return
        try:
            self._terminate_lineage_locked(record)
            report.recovered.append(key)
            handled.add(record.instance_id)
        except Exception as exc:
            report.delete_failed.append(key)
            self._mark_teardown_failed_locked(record, reason=str(exc))
            self._record_orphan(record, str(exc))
            logger.warning("Failed to reconcile %s: %s", key, exc)

    def _record_from_state_locked(
        self, state: dict[str, Any], by_id: dict[str, Any]
    ) -> RunInstanceRecord:
        run_id = str(state.get("run_id") or "")
        image_id = str(state.get("image_id") or "")
        name = str(state.get("ownership_name") or "")
        remark = str(state.get("ownership_remark") or "")
        if not name or not remark:
            name, remark = make_ownership_marker(run_id)
        instance_id = str(state.get("instance_id") or "").strip()
        if not instance_id:
            matches = [
                view.instance_id for view in by_id.values()
                if view.item.get("name") == name and view.item.get("remark") == remark
            ]
            if len(matches) == 1:
                instance_id = matches[0]
        return RunInstanceRecord(
            run_id=run_id,
            instance_id=instance_id,
            image_id=image_id,
            created_at=float(state.get("ts") or time.time()),
            lifecycle_state=str(state.get("status") or CREATE_UNCERTAIN),
            lineage_id=str(state.get("lineage_id") or ""),
            ownership_name=name,
            ownership_remark=remark,
        )

    @staticmethod
    def _view_matches_record(view: Any, record: RunInstanceRecord) -> bool:
        return (
            view.instance_id == record.instance_id
            and view.item.get("name") == record.ownership_name
            and view.item.get("remark") == record.ownership_remark
        )

    def _mark_record_terminated_locked(
        self, record: RunInstanceRecord, *, reason: str = ""
    ) -> None:
        record.terminated_at = record.terminated_at or time.time()
        record.active_operation_id = None
        record.lifecycle_state = TERMINATED
        self._persist_state_event(
            TERMINATED,
            run_id=record.run_id,
            lineage_id=record.lineage_id,
            image_id=record.image_id,
            instance_id=record.instance_id,
            ownership_name=record.ownership_name,
            ownership_remark=record.ownership_remark,
            reason=reason,
        )
        self._persist_ledger(record)

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
            "gpu_count": record.gpu_count,
            "created_at": record.created_at,
            "terminated_at": record.terminated_at,
            "active_operation_id": record.active_operation_id,
            "accumulated_gpu_seconds": record.accumulated_gpu_seconds,
            "lifecycle_state": record.lifecycle_state,
            "lineage_id": record.lineage_id,
            "ownership_name": record.ownership_name,
            "ownership_remark": record.ownership_remark,
        }
        try:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(data, sort_keys=True) + "\n")
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
