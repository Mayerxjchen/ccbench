"""RoutedDriver: composite execution driver dispatching per-operation via ComputeRouter.

Invariants:
- Implements HpcDriver and HpcAdapter protocols.
- Each submission reads `compute_class = spec.get("compute_class", "cpu")`.
- Queries `ComputeRouter.route(compute_class)` to obtain target `RouteDecision`.
- Delegates execution to the driver matching `route.site_profile.scheduler` (e.g. "slurm", "compshare").
- Resolves runtime declarations in a route-aware manner with the selected `SiteProfile` and scheduler.
- Tracks ownership of jobs, markers, and operations to route queries (status, logs, fetch, cancel).
- Flexible calling signatures: supports both 1-arg `(job_id)` (adapter) and 2-arg `(run_id, job_id)` (driver).
- Settlement: delegates `settle(run_id)` to all underlying drivers, ensuring cloud instances are stopped/deleted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ccbench.hpc.compute_profile import ComputeRouter, ResolvedRoute
from ccbench.hpc.drivers.base import HpcDriver, HpcDriverError
from ccbench.hpc.runtime_resolution import ResolvedRuntime, RuntimeResolver

logger = logging.getLogger(__name__)


class RoutedDriver(HpcDriver):
    """Composite driver routing between heterogeneous backends (e.g. Slurm CPU + CompShare GPU)."""

    kind = "routed"

    def __init__(
        self,
        router: ComputeRouter,
        drivers: Mapping[str, Any],
        *,
        runtime_resolver: RuntimeResolver | None = None,
    ) -> None:
        self.router = router
        self.drivers = dict(drivers)
        self.runtime_resolver = runtime_resolver
        self._job_to_driver: dict[str, Any] = {}
        self._op_to_driver: dict[tuple[str, str], Any] = {}
        self._marker_to_driver: dict[str, Any] = {}

    def capabilities(self) -> dict[str, Any]:
        """Aggregate abstract compute capabilities per compute class without leaking driver internals."""
        combined_classes: set[str] = set(self.router.profile.routes.keys())
        classes_caps: dict[str, dict[str, Any]] = {}
        max_cpus = 0
        max_gpus = 0
        max_mem = 0
        max_wall = 0
        for c_class in sorted(combined_classes):
            route = self.router.route(c_class)
            driver = self.drivers.get(route.site_profile.scheduler)
            c = driver.capabilities() if driver and hasattr(driver, "capabilities") else {}
            c_gpus = c.get("max_gpus", 0)
            c_cpus = c.get("max_cpus", 0)
            c_mem = c.get("max_memory_gb", 0)
            c_wall = c.get("max_walltime_minutes", 0)
            classes_caps[c_class] = {
                "max_gpus": c_gpus,
                "max_cpus": c_cpus,
                "max_memory_gb": c_mem,
                "max_walltime_minutes": c_wall,
            }
            max_cpus = max(max_cpus, c_cpus)
            max_gpus = max(max_gpus, c_gpus)
            max_mem = max(max_mem, c_mem)
            max_wall = max(max_wall, c_wall)

        # Advertised runtimes: strictly QUALIFIED capabilities only
        avail_runtimes = []
        if self.runtime_resolver is not None:
            avail_runtimes = self.runtime_resolver.qualified_capabilities()

        return {
            "scheduler": "routed",
            "compute_classes": sorted(combined_classes),
            "classes": classes_caps,
            "runtimes": avail_runtimes,
            "max_gpus": max_gpus,
            "max_cpus": max_cpus,
            "max_memory_gb": max_mem,
            "max_walltime_minutes": max_wall,
        }

    def _select_driver(self, spec: dict[str, Any]) -> tuple[Any, ResolvedRoute]:
        compute_class = spec.get("compute_class")
        if not compute_class:
            raise HpcDriverError(
                "Execution request missing mandatory 'compute_class' (must explicitly declare 'cpu' or 'gpu')"
            )
        route = self.router.route(compute_class)
        scheduler = route.site_profile.scheduler
        if "scheduler" in spec and spec["scheduler"] != scheduler:
            raise HpcDriverError(
                f"Candidate cannot override scheduler ({spec['scheduler']!r} != {scheduler!r}); "
                "scheduler is strictly determined by trusted SiteProfile"
            )
        driver = self.drivers.get(scheduler)
        if driver is None:
            raise HpcDriverError(
                f"No driver registered for scheduler {scheduler!r} (compute_class={compute_class!r})"
            )
        return driver, route

    def submit(
        self,
        spec: dict[str, Any],
        *,
        run_id: str,
        operation_id: str,
        attempt: int | None = None,
        marker: str | None = None,
    ) -> dict[str, Any]:
        """Route submission to the appropriate backend driver and record lineage."""
        driver, route = self._select_driver(spec)

        # Route-aware runtime resolution if resolver is present
        if self.runtime_resolver is not None and "runtime" in spec:
            try:
                resolved = self.runtime_resolver.resolve(
                    spec["runtime"],
                    site_profile=route.site_profile,
                    provider=route.site_profile.scheduler,
                )
                spec["_resolved_runtime"] = resolved
            except Exception as exc:
                raise HpcDriverError(
                    f"Runtime resolution failed for {spec['runtime']}: {exc}"
                ) from exc

        att = attempt if attempt is not None else 1
        spec.setdefault("idempotency_key", f"{run_id}:{operation_id}:{att}")

        res = driver.submit(
            spec,
            run_id=run_id,
            operation_id=operation_id,
            attempt=attempt,
            marker=marker,
        )
        job_id = res.get("job_id")
        if job_id:
            self._job_to_driver[job_id] = driver
        self._op_to_driver[(run_id, operation_id)] = driver
        att = attempt if attempt is not None else 1
        job_marker = marker or f"drv-{run_id}-{operation_id}-{att}"
        self._marker_to_driver[job_marker] = driver

        return res

    def find(self, run_id: str, idempotency_key: str) -> str | None:
        """Find existing job across registered drivers."""
        for driver in self.drivers.values():
            if hasattr(driver, "find"):
                found = driver.find(run_id, idempotency_key)
                if found is not None:
                    self._job_to_driver[found] = driver
                    return found
        return None

    def find_by_marker(self, *args, **kwargs) -> str | None:
        """Find job by exact scheduler marker."""
        marker = args[-1] if args else kwargs.get("marker", "")
        run_id = args[0] if len(args) > 1 else kwargs.get("run_id", "")
        driver = self._marker_to_driver.get(marker)
        if driver is not None and hasattr(driver, "find_by_marker"):
            try:
                return driver.find_by_marker(run_id, marker)
            except TypeError:
                return driver.find_by_marker(marker)
        for d in self.drivers.values():
            if hasattr(d, "find_by_marker"):
                try:
                    found = d.find_by_marker(run_id, marker)
                except TypeError:
                    found = d.find_by_marker(marker)
                if found is not None:
                    self._marker_to_driver[marker] = d
                    self._job_to_driver[found] = d
                    return found
        return None

    def find_by_operation_id(self, run_id: str, operation_id: str) -> str | None:
        """Find job by logical operation ID."""
        driver = self._op_to_driver.get((run_id, operation_id))
        if driver is not None and hasattr(driver, "find_by_operation_id"):
            return driver.find_by_operation_id(run_id, operation_id)
        for d in self.drivers.values():
            if hasattr(d, "find_by_operation_id"):
                found = d.find_by_operation_id(run_id, operation_id)
                if found is not None:
                    self._op_to_driver[(run_id, operation_id)] = d
                    self._job_to_driver[found] = d
                    return found
        return None

    def status(self, *args, **kwargs) -> dict[str, Any]:
        """Query job status from owning driver (supports 1 or 2 args)."""
        job_id = args[-1] if args else kwargs.get("job_id", "")
        run_id = args[0] if len(args) > 1 else kwargs.get("run_id", "")
        driver = self._job_to_driver.get(job_id)
        if driver is not None:
            try:
                return driver.status(run_id, job_id)
            except TypeError:
                return driver.status(job_id)
        for d in self.drivers.values():
            try:
                try:
                    res = d.status(run_id, job_id)
                except TypeError:
                    res = d.status(job_id)
                self._job_to_driver[job_id] = d
                return res
            except Exception:
                continue
        raise HpcDriverError(f"Job {job_id} not owned by any active driver in RoutedDriver")

    def logs(self, *args, **kwargs) -> dict[str, Any]:
        """Query job logs from owning driver (supports 1 or 2 args)."""
        job_id = args[-1] if args else kwargs.get("job_id", "")
        run_id = args[0] if len(args) > 1 else kwargs.get("run_id", "")
        cursor = kwargs.get("cursor", 0)
        driver = self._job_to_driver.get(job_id)
        if driver is not None:
            try:
                return driver.logs(run_id, job_id, cursor=cursor)
            except TypeError:
                return driver.logs(job_id, cursor=cursor)
        for d in self.drivers.values():
            try:
                try:
                    res = d.logs(run_id, job_id, cursor=cursor)
                except TypeError:
                    res = d.logs(job_id, cursor=cursor)
                self._job_to_driver[job_id] = d
                return res
            except Exception:
                continue
        raise HpcDriverError(f"Job {job_id} not owned by any active driver in RoutedDriver")

    def fetch(self, *args, **kwargs) -> dict[str, Any]:
        """Fetch outputs from owning driver (supports 1 or 2 args)."""
        job_id = args[-1] if args else kwargs.get("job_id", "")
        run_id = args[0] if len(args) > 1 else kwargs.get("run_id", "")
        driver = self._job_to_driver.get(job_id)
        if driver is not None:
            try:
                return driver.fetch(run_id, job_id)
            except TypeError:
                return driver.fetch(job_id)
        for d in self.drivers.values():
            try:
                try:
                    res = d.fetch(run_id, job_id)
                except TypeError:
                    res = d.fetch(job_id)
                self._job_to_driver[job_id] = d
                return res
            except Exception:
                continue
        raise HpcDriverError(f"Job {job_id} not owned by any active driver in RoutedDriver")

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel job via owning driver."""
        driver = self._job_to_driver.get(job_id)
        if driver is not None:
            return driver.cancel(job_id)
        for d in self.drivers.values():
            try:
                res = d.cancel(job_id)
                self._job_to_driver[job_id] = d
                return res
            except Exception:
                continue
        raise HpcDriverError(f"Job {job_id} not owned by any active driver in RoutedDriver")

    def _adapter_instance_id(self, job_id: str) -> str:
        """Expose the concrete provider instance for Gateway lineage events.

        The Gateway owns the durable lifecycle ledger, while RoutedDriver owns
        the per-job backend map.  Forwarding this read-only identity keeps
        SUBMIT/JOB/ARTIFACT events bound to the same CompShare instance after
        route selection without allowing a caller to choose that instance.
        """
        driver = self._job_to_driver.get(job_id)
        candidates = [driver] if driver is not None else list(self.drivers.values())
        for candidate in candidates:
            resolver = getattr(candidate, "_adapter_instance_id", None)
            if callable(resolver):
                value = resolver(job_id)
                if isinstance(value, str) and value:
                    return value
            jobs = getattr(candidate, "_jobs", None)
            if not isinstance(jobs, dict):
                continue
            record = jobs.get(job_id)
            if isinstance(record, dict):
                value = record.get("instance_id")
            else:
                value = getattr(record, "instance_id", "") if record else ""
            if isinstance(value, str) and value:
                return value
        return ""

    def usage(self) -> dict[str, Any]:
        """Aggregate usage across all registered drivers."""
        combined: dict[str, Any] = {}
        for kind, driver in self.drivers.items():
            try:
                if hasattr(driver, "usage"):
                    combined[kind] = driver.usage()
            except Exception as exc:
                combined[kind] = {"error": str(exc)}
        return combined

    def settle(self, run_id: str | None = None) -> bool:
        """Run-scoped settlement: invoke settle on ALL drivers, stopping cloud instances."""
        all_ok = True
        for kind, driver in self.drivers.items():
            try:
                if hasattr(driver, "settle"):
                    try:
                        res = driver.settle(run_id)
                    except TypeError:
                        res = driver.settle()
                    if res is False:
                        all_ok = False
            except Exception as exc:
                logger.error("Error settling driver %s for run %s: %s", kind, run_id, exc)
                all_ok = False
        return all_ok
