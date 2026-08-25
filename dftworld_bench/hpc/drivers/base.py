"""Driver boundary: one contract in front of every execution backend.

Drivers are thin delegation wrappers around the existing adapters — no
implementation moves, no scheduler behavior changes. They translate method
names/types and carry operation-attempt identity. Formal runs may only resolve
a qualified SlurmDriver; ProcessDriver exists for CI and fault injection.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class DriverSelectionError(Exception):
    """A driver kind was requested that the run mode forbids."""


@runtime_checkable
class HpcDriver(Protocol):
    """The nine methods every execution backend must expose."""

    def capabilities(self) -> dict[str, Any]: ...

    def submit(
        self,
        spec: dict[str, Any],
        *,
        run_id: str,
        operation_id: str,
        attempt: int | None = None,
        marker: str | None = None,
    ) -> dict[str, Any]: ...

    def find(self, run_id: str, operation_id: str) -> str | None: ...

    def find_by_marker(self, marker: str) -> str | None: ...

    def status(self, job_id: str) -> dict[str, Any]: ...

    def logs(self, job_id: str) -> dict[str, Any]: ...

    def fetch(self, job_id: str) -> dict[str, Any]: ...

    def cancel(self, job_id: str) -> dict[str, Any]: ...

    def usage(self) -> dict[str, Any]: ...

    def settle(self) -> dict[str, Any]: ...


def resolve_driver(*, mode: str, kind: str) -> None:
    """Admission check: Formal rejects any driver that is not qualified Slurm.

    Smoke and Pilot may select ProcessDriver through an explicit non-formal
    profile. This gate runs before any Candidate startup.
    """
    if mode == "formal" and kind != "slurm":
        raise DriverSelectionError(
            f"Formal runs require a qualified SlurmDriver; got {kind!r}"
        )
