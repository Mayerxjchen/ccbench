"""ProcessDriver: CI and fault-injection only; never a Formal driver."""

from __future__ import annotations

from typing import Any

from ccbench.hpc.adapters.process_test import ProcessTestAdapter


class ProcessDriver:
    """Delegation wrapper over the local subprocess adapter."""

    kind = "process_test"

    def __init__(self, adapter: ProcessTestAdapter) -> None:
        self._adapter = adapter

    @property
    def adapter(self) -> ProcessTestAdapter:
        """Test/fault-injection access to the underlying adapter."""
        return self._adapter

    def capabilities(self) -> dict[str, Any]:
        return self._adapter.capabilities()

    def submit(
        self,
        spec: dict[str, Any],
        *,
        run_id: str,
        operation_id: str,
        attempt: int | None = None,
        marker: str | None = None,
    ) -> dict[str, Any]:
        if marker is None and attempt is not None:
            marker = f"drv-{run_id}-{operation_id}-{attempt}"
        return self._adapter.submit(
            spec, run_id=run_id, operation_id=operation_id, marker=marker
        )

    def find(self, run_id: str, operation_id: str) -> str | None:
        return self._adapter.find_by_operation_id(run_id, operation_id)

    def find_by_marker(self, marker: str) -> str | None:
        return self._adapter.find_by_marker(marker)

    def status(self, job_id: str) -> dict[str, Any]:
        return self._adapter.status(job_id)

    def logs(self, job_id: str) -> dict[str, Any]:
        return self._adapter.logs(job_id)

    def fetch(self, job_id: str) -> dict[str, Any]:
        return self._adapter.fetch(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._adapter.cancel(job_id)

    def usage(self) -> dict[str, Any]:
        return self._adapter.usage()

    def settle(self) -> dict[str, Any]:
        # v1: a usage snapshot; Task 9 seals the immutable settlement report.
        return self._adapter.usage()
