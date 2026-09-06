"""The HpcAdapter contract: seven stable operations + scheduler identity.

An adapter maps one scheduler (Slurm, a fake site, a process runner) onto the
same seven operations the bench-hpc client and gateway speak. Adapters are
site-owned; the trusted gateway is the only component that calls them.

``operation_id`` is the durable scheduler-side identity for one logical
submission: the gateway consults ``find_by_operation_id`` BEFORE submitting so
a crashed/replayed client never double-schedules a job.

Job states follow the bench-hpc vocabulary:
``QUEUED``, ``RUNNING``, ``SUCCEEDED``, ``FAILED``, ``CANCELLED``,
``TIMEOUT``, ``LOST``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

JOB_STATES = (
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "LOST",
)

TERMINAL_STATES = ("SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT", "LOST")


class TransportUnknown(Exception):
    """The scheduler may or may not have accepted the submission.

    Raised after a transport-level loss of response: the adapter (scheduler
    side) may have created the job, but the caller never learned its ID. The
    gateway must recover via the fsynced SUBMIT_INTENT marker, never by a
    blind resubmission.
    """


@runtime_checkable
class HpcAdapter(Protocol):
    """All seven operations; every real adapter must provide all of them."""

    def capabilities(self) -> dict[str, Any]: ...

    def submit(
        self,
        spec: dict[str, Any],
        *,
        run_id: str,
        operation_id: str,
    ) -> dict[str, Any]: ...

    def find_by_operation_id(self, run_id: str, operation_id: str) -> str | None: ...

    def status(self, job_id: str) -> dict[str, Any]: ...

    def logs(self, job_id: str) -> dict[str, Any]: ...

    def fetch(self, job_id: str) -> dict[str, Any]: ...

    def cancel(self, job_id: str) -> dict[str, Any]: ...

    def usage(self) -> dict[str, Any]: ...
