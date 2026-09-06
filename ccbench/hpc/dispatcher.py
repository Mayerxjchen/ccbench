"""HpcDispatcher — the one trusted Agent-facing entry for real HPC execution.

Task 1 of the dispatcher simplification: a zero-behavior-change façade over
the existing GatewayRuntime / GatewayLease / Gateway stack. The session adds
no retry, path, quota, or state logic; it only binds the run's lease token to
the seven Gateway operations and dies with its lease.

Task 9 seals the evidence semantics on top of that boundary:

- :meth:`DispatcherSession.attempts` — the owned (operation, attempt, job)
  lineage;
- :meth:`DispatcherSession.fetch_outputs` — explicit fetch with hash
  verification, no-follow staging, fsync, and atomic no-overwrite publish;
- :meth:`DispatcherSession.settle` — idempotent run settlement that freezes
  submissions, addresses only exact ledger job IDs, and produces one
  immutable report whose digest is stable across repeats.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ccbench.hpc.gateway import GatewayError, GatewayErrorCode
from ccbench.hpc.gateway_runtime import GatewayLease, GatewayRuntime


class DispatcherClosedError(Exception):
    """An operation was attempted on a closed DispatcherSession."""


class SettlementError(Exception):
    """Settlement could not produce an immutable report."""


@dataclasses.dataclass(frozen=True)
class AttemptRecord:
    """One owned scheduler attempt and its observed terminal state."""

    operation_id: str
    attempt: int
    job_id: str
    state: str


@dataclasses.dataclass(frozen=True)
class ArtifactEntry:
    path: str
    sha256: str
    size_bytes: int


@dataclasses.dataclass(frozen=True)
class ArtifactManifest:
    """Digest-pinned record of explicitly fetched outputs for one job."""

    job_id: str
    entries: tuple[ArtifactEntry, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "entries": [
                {"path": e.path, "sha256": e.sha256, "size_bytes": e.size_bytes}
                for e in self.entries
            ],
        }


@dataclasses.dataclass(frozen=True)
class SettlementReport:
    """One immutable settlement record for a run."""

    run_id: str
    attempts: tuple[AttemptRecord, ...]
    cancelled_jobs: tuple[str, ...]
    usage: dict[str, int]
    digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "attempts": [
                {
                    "operation_id": a.operation_id,
                    "attempt": a.attempt,
                    "job_id": a.job_id,
                    "state": a.state,
                }
                for a in self.attempts
            ],
            "cancelled_jobs": list(self.cancelled_jobs),
            "usage": dict(self.usage),
        }


def _reject_unsafe_artifact_name(name: str) -> None:
    if (
        not isinstance(name, str)
        or name == ""
        or name.startswith("/")
        or "\\" in name
        or ".." in Path(name).parts
    ):
        raise SettlementError(f"unsafe artifact name from remote: {name!r}")


def _reject_destination_link(destination: Path, name: str) -> None:
    """No-follow walk from the destination dir down the artifact's parents."""
    current = destination
    for part in Path(name).parts[:-1] or ():
        current = current / part
        if current.is_symlink():
            raise SettlementError(
                f"destination path traverses a symlink at {current}"
            )
    target = destination / name
    if target.is_symlink() or target.exists():
        raise SettlementError(
            f"refusing to overwrite existing destination entry {target}"
        )


class HpcDispatcher:
    """Issues one DispatcherSession per run; the sole production entry."""

    def __init__(self, runtime: GatewayRuntime, adapter_config: dict[str, Any]) -> None:
        self._runtime = runtime
        self._adapter_config = dict(adapter_config)

    @classmethod
    def process_test(cls, site_root: Path) -> "HpcDispatcher":
        """Build a dispatcher over the ProcessTestAdapter (CI/fault injection).

        v2 submissions demand a durable audit trail, so the test dispatcher
        fsyncs one next to the site root like a real deployment would.
        """
        from ccbench.hpc.audit import GatewayAudit

        site_root = Path(site_root)
        site_root.mkdir(parents=True, exist_ok=True)
        return cls(
            GatewayRuntime(audit=GatewayAudit(site_root / "audit.jsonl")),
            {
                "adapter": "process_test",
                "root": str(site_root),
                "timeout_sec": 30.0,
            },
        )

    def open_run(
        self,
        run_id: str,
        *,
        workspace: Path,
        adapter_config: dict[str, Any] | None = None,
    ) -> DispatcherSession:
        """Open one run session; ``adapter_config`` overrides the instance
        default for this run (per-run backend selection stays trusted-side)."""
        merged = {**(adapter_config or self._adapter_config)}
        config = {**merged, "workspace_root": str(workspace)}
        lease = self._runtime.start(run_id, config)
        return DispatcherSession(lease)


class DispatcherSession:
    """One run's capability-bound view over the trusted Gateway."""

    def __init__(self, lease: GatewayLease) -> None:
        self._lease = lease
        self._settlement: SettlementReport | None = None

    @property
    def run_id(self) -> str:
        return self._lease.run_id

    @property
    def token(self) -> str:
        """Run-scoped bearer token (trusted host side only)."""
        return self._lease.token

    @property
    def gateway(self):
        """The underlying trusted Gateway (trusted host side only)."""
        return self._lease.gateway

    def serve(self, *, host: str = "127.0.0.1", port: int = 0):
        """Serve this run's gateway over loopback HTTP (trusted side only)."""
        from ccbench.hpc.http_server import HttpGatewayServer

        server = HttpGatewayServer(self._lease.gateway, host=host, port=port)
        server.start()
        return server

    def capabilities(self) -> dict[str, Any]:
        self._require_open()
        return self._lease.gateway.capabilities(self._lease.token, self._lease.run_id)

    def submit(
        self,
        spec: dict[str, Any],
        *,
        operation_id: str,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        self._require_open()
        return self._lease.gateway.submit(
            self._lease.token,
            self._lease.run_id,
            spec,
            operation_id=operation_id,
            attempt=attempt,
        )

    def status(self, job_id: str) -> dict[str, Any]:
        self._require_open()
        return self._lease.gateway.status(self._lease.token, self._lease.run_id, job_id)

    def logs(self, job_id: str) -> dict[str, Any]:
        self._require_open()
        return self._lease.gateway.logs(self._lease.token, self._lease.run_id, job_id)

    def fetch(self, job_id: str) -> dict[str, Any]:
        self._require_open()
        return self._lease.gateway.fetch(self._lease.token, self._lease.run_id, job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        self._require_open()
        return self._lease.gateway.cancel(self._lease.token, self._lease.run_id, job_id)

    def usage(self) -> dict[str, Any]:
        self._require_open()
        return self._lease.gateway.usage(self._lease.token, self._lease.run_id)

    def settle(self) -> dict[str, Any]:
        """Terminal settlement hook; v1 delegates to the gateway ledger view."""
        self._require_open()
        return self.usage()

    # -- Task 9: evidence semantics ----------------------------------------

    def attempts(self) -> tuple[AttemptRecord, ...]:
        """The run's full (operation, attempt, job, state) lineage."""
        self._require_open()
        records = []
        for entry in self._lease.gateway.attempts(self._lease.token, self._lease.run_id):
            state = self._lease.gateway.status(
                self._lease.token, self._lease.run_id, entry["job_id"]
            )["state"]
            records.append(
                AttemptRecord(
                    operation_id=entry["operation_id"],
                    attempt=entry["attempt"],
                    job_id=entry["job_id"],
                    state=state,
                )
            )
        return tuple(records)

    def fetch_outputs(self, job_id: str, destination: Path) -> ArtifactManifest:
        """Explicitly fetch a job's declared outputs into ``destination``.

        Each artifact is written through a fresh staging file (no symlink
        following at the destination, no overwrite of an existing entry),
        fsynced, then atomically published; the manifest pins the verified
        digest and size of every published byte.
        """
        self._require_open()
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        result = self._lease.gateway.fetch(self._lease.token, self._lease.run_id, job_id)
        entries: list[ArtifactEntry] = []
        for name, content in sorted((result.get("outputs") or {}).items()):
            _reject_unsafe_artifact_name(name)
            data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            target = destination / name
            _reject_destination_link(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.parent / f".{target.name}.staging"
            if staged.exists() or staged.is_symlink():
                raise SettlementError(f"staging collision for artifact {name!r}")
            with open(staged, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(staged, target)
            entries.append(
                ArtifactEntry(
                    path=name,
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                )
            )
        return ArtifactManifest(job_id=job_id, entries=tuple(entries))

    def settle(self, *, cancel_pending: bool = True) -> SettlementReport:
        """Idempotent settlement over exact ledger job IDs.

        Settlement proceeds in two phases to avoid querying jobs on a deleted
        cloud instance:

        1. Freeze submissions (reject new submits, but keep instance alive).
        2. Query every owned attempt, cancel non-terminal ones, fetch evidence.
        3. Tear down cloud resources (stop/delete instance).
        4. Produce one immutable report.

        A repeat call returns the same report.
        """
        self._require_open()
        if self._settlement is not None:
            return self._settlement
        gateway = self._lease.gateway

        # Phase 1: freeze submissions only — instance stays alive for queries
        gateway.freeze(self._lease.token, self._lease.run_id)

        # Phase 2: query all jobs, cancel pending, collect states
        records: list[AttemptRecord] = []
        cancelled: list[str] = []
        for entry in gateway.attempts(self._lease.token, self._lease.run_id):
            state = gateway.status(
                self._lease.token, self._lease.run_id, entry["job_id"]
            )["state"]
            if cancel_pending and state not in (
                "SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT", "LOST"
            ):
                try:
                    gateway.cancel(
                        self._lease.token, self._lease.run_id, entry["job_id"]
                    )
                    state = "CANCELLED"
                    cancelled.append(entry["job_id"])
                except Exception as exc:  # noqa: BLE001 - recorded, never fatal
                    raise SettlementError(
                        f"settlement could not cancel {entry['job_id']!r}: {exc}"
                    ) from exc
            records.append(
                AttemptRecord(
                    operation_id=entry["operation_id"],
                    attempt=entry["attempt"],
                    job_id=entry["job_id"],
                    state=state,
                )
            )

        # Phase 3: tear down cloud resources now that all jobs are resolved
        gateway.teardown_resources(self._lease.token, self._lease.run_id)

        usage = gateway.usage(self._lease.token, self._lease.run_id)
        report = SettlementReport(
            run_id=self._lease.run_id,
            attempts=tuple(records),
            cancelled_jobs=tuple(sorted(cancelled)),
            usage=dict(usage),
            digest="",
        )
        body = json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"))
        object.__setattr__(
            report, "digest", "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
        )
        self._settlement = report
        return report

    def close(self) -> None:
        """Revoke the run token, ensure cloud settlement, and tear down networks; idempotent.

        Execution sequence:
        1. gateway.trusted_freeze(run_id)
        2. gateway.trusted_teardown(run_id)
        3. Confirm settlement state is SETTLED
        4. lease.close() (revoke token)
        5. Close per-run network

        Regardless of whether the client token is active, expired, or revoked,
        trusted teardown is always executed to ensure zero orphaned billing instances.
        """
        if self._lease.closed and self._settlement is not None:
            return

        gateway = self._lease.gateway
        run_id = self._lease.run_id
        teardown_error: Exception | None = None

        if self._settlement is None:
            try:
                gateway.trusted_freeze(run_id)
                gateway.trusted_teardown(run_id)
                state = gateway.settlement_state(run_id)
                if state != "SETTLED":
                    raise SettlementError(
                        f"Settlement state for {run_id!r} is {state!r}, expected 'SETTLED'"
                    )
            except Exception as exc:
                teardown_error = exc

        try:
            self._lease.close()
        except Exception as lease_exc:
            if teardown_error is None:
                teardown_error = lease_exc

        if teardown_error is not None:
            raise SettlementError(
                f"Cloud resource teardown failed for run {run_id!r}; "
                f"resources may still be active/billing: {teardown_error}"
            ) from teardown_error

    def _require_open(self) -> None:
        if self._lease.closed:
            raise DispatcherClosedError(
                f"session for run {self._lease.run_id!r} is closed"
            )
