"""The trusted gateway: every HPC capability a Candidate can reach.

The gateway is the trust boundary between the Candidate and the site's
adapter/scheduler:

- tokens are issued per run and scoped to that run's job ids;
- each operation is checked against the token's scope;
- idempotency keys deduplicate submissions before the adapter ever sees them,
  and scheduler operation identity survives a gateway restart
  (``find_by_operation_id`` is consulted before every submit);
- bound inputs are contained inside the run workspace — an absolute host path
  or a symlink that leaves the workspace is rejected;
- a per-run quota ledger rejects resource requests beyond the site's budget;
- a run can never read, fetch, or cancel another run's jobs;
- every mutation lands in an append-only, hash-chained :class:`GatewayAudit`.

The gateway holds the adapter object and the quota ledger; the Candidate holds
only a URL and a token. ``Gateway`` is transport-agnostic — the HTTP binding is
a thin layer elsewhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

from dftworld_bench.hpc.adapters.base import TERMINAL_STATES, HpcAdapter
from dftworld_bench.hpc.audit import GatewayAudit
from dftworld_bench.hpc.job import JobSpec, JobError
from dftworld_bench.hpc.runtime_resolution import (
    RuntimeResolutionError,
    RuntimeResolver,
    split_runtime,
)

ALL_OPS = ("capabilities", "submit", "status", "logs", "fetch", "cancel", "usage")

# Operation ids travel inside scheduler directives (``--comment=...``) and
# marker files, so they must be single safe argv tokens.
_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9._/:-]+$")


class GatewayErrorCode:
    """Structured error codes for GatewayError."""
    TOKEN_REVOKED = "TOKEN_REVOKED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_RUN_MISMATCH = "TOKEN_RUN_MISMATCH"
    ADAPTER_SETTLEMENT_FAILED = "ADAPTER_SETTLEMENT_FAILED"
    AUTH_FAILURE = "AUTH_FAILURE"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    CONTAINMENT_VIOLATION = "CONTAINMENT_VIOLATION"
    OTHER = "OTHER"


class GatewayError(Exception):
    """A capability was denied at the trust boundary.

    ``status`` carries the HTTP status the common binding should reply with:
    401 for authentication failures, 403 for authorization/containment denials.

    ``error_code`` is a structured code for programmatic classification:
    - TOKEN_REVOKED: token was explicitly revoked (safe to skip teardown)
    - TOKEN_EXPIRED: token TTL expired (teardown still needed via trusted path)
    - TOKEN_RUN_MISMATCH: token/run_id mismatch
    - ADAPTER_SETTLEMENT_FAILED: adapter settle() returned failure
    - AUTH_FAILURE: generic authentication failure
    - AUTHORIZATION_DENIED: operation outside token scope
    - CONTAINMENT_VIOLATION: path/symlink escape
    - OTHER: unclassified
    """

    def __init__(self, message: str, *, status: int = 403, error_code: str = GatewayErrorCode.OTHER) -> None:
        super().__init__(message)
        self.status = status
        self.error_code = error_code


def contained(root: Path, candidate: Path) -> Path:
    """Canonical containment of ``candidate`` inside the run workspace ``root``.

    Both paths resolve to their canonical real paths (symlinks followed), so
    ``..`` escapes, absolute host paths, and symlinks that leave the workspace
    are all rejected.  Raises :class:`GatewayError` on violation.
    """
    root_real = root.resolve(strict=True)
    try:
        value = candidate.resolve(strict=True)
    except OSError as exc:
        raise GatewayError(
            f"input cannot be resolved inside the run workspace: {candidate}"
        ) from exc
    if value != root_real and root_real not in value.parents:
        raise GatewayError(f"path escapes run workspace: {candidate}")
    if candidate.is_symlink():
        raise GatewayError(f"symlink input rejected: {candidate}")
    return value


@dataclass(frozen=True)
class Capability:
    """What one token may do, for one run, until ``expires_at``."""

    run_id: str
    operations: frozenset[str]
    expires_at: float
    token: str


class Gateway:
    """Owns tokens, quotas, and job ownership for one adapter."""

    def __init__(
        self,
        adapter: HpcAdapter,
        *,
        quota: dict[str, Any] | None = None,
        now: Callable[[], float] | None = None,
        audit: GatewayAudit | None = None,
        workspace_root: Path | None = None,
        runtime_resolver: RuntimeResolver | None = None,
    ) -> None:
        self._adapter = adapter
        self._quota = quota
        # Resolver authority is injected by the trusted composition root.  An
        # adapter is not an authority and may not smuggle a resolver into the
        # gateway (or replace the Catalog selected by the harness).
        self._runtime_resolver = runtime_resolver
        self._now = now or time.time
        self._tokens: dict[str, Capability] = {}
        self._jobs: dict[str, dict[str, str]] = {}  # run_id -> key -> job_id
        self._run_jobs: dict[str, set[str]] = {}  # run_id -> {job_id}
        # Scheduler lineage carried by lifecycle events.  This is deliberately
        # separate from the idempotency table: a restart can reconstruct the
        # event identity from the durable audit even when the in-memory tables
        # are empty.
        self._job_metadata: dict[str, dict[str, Any]] = {}
        # v2 ownership lineage: run_id -> operation_id -> attempt -> scheduler job
        self._op_attempts: dict[str, dict[str, dict[int, str]]] = {}
        # Runs whose settlement has begun: no further submissions accepted.
        self._settled: set[str] = set()
        self._settlement_states: dict[str, str] = {}  # run_id -> ACTIVE | SETTLING | SETTLED | SETTLEMENT_FAILED
        self._ledger: dict[str, dict[str, int]] = {}  # run_id -> resource sums
        self._audit = audit
        self._workspace_root = Path(workspace_root) if workspace_root is not None else None

    # -- token lifecycle --------------------------------------------------

    def issue(self, run_id: str, operations: tuple[str, ...] | list[str], *, ttl_sec: float = 300.0) -> str:
        ops = frozenset(operations)
        unknown = ops - set(ALL_OPS)
        if unknown:
            raise GatewayError(f"unknown operations: {sorted(unknown)}")
        token = secrets.token_hex(16)
        self._tokens[token] = Capability(
            run_id=run_id,
            operations=ops,
            expires_at=self._now() + ttl_sec,
            token=token,
        )
        self._audit_append({
            "kind": "token_issued",
            "run_id": run_id,
            "operations": sorted(ops),
            "token_digest": _digest(token),  # the token itself never lands in the log
            "ttl_sec": ttl_sec,
        })
        return token

    def revoke(self, token: str) -> None:
        self._tokens.pop(token, None)
        self._audit_append({"kind": "token_revoked", "token_digest": _digest(token)})

    def authorize(
        self,
        token: str,
        run_id: str,
        operation: str,
        *,
        allow_expired: bool = False,
    ) -> Capability:
        capability = self._tokens.get(token)
        if capability is None:
            raise GatewayError("unknown or revoked token", status=401, error_code=GatewayErrorCode.TOKEN_REVOKED)
        if capability.run_id != run_id:
            raise GatewayError(
                f"token/run mismatch: token for {capability.run_id!r} used for {run_id!r}",
                status=401,
                error_code=GatewayErrorCode.TOKEN_RUN_MISMATCH,
            )
        if not allow_expired and self._now() > capability.expires_at:
            raise GatewayError(f"token expired at {capability.expires_at:.0f}", status=401, error_code=GatewayErrorCode.TOKEN_EXPIRED)
        if operation not in capability.operations:
            raise GatewayError(f"operation {operation!r} outside token scope", error_code=GatewayErrorCode.AUTHORIZATION_DENIED)
        return capability

    # -- the seven operations (all run-scoped) ----------------------------

    def capabilities(self, token: str, run_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "capabilities")
        out = dict(self._adapter.capabilities())
        if self._runtime_resolver is not None:
            # Runtime capabilities are what Agents may name; concrete SIF
            # identities stay server-side (Architecture Freeze §3).
            out["runtime_capabilities"] = self._runtime_resolver.qualified_capabilities()
        return out

    def submit(
        self,
        token: str,
        run_id: str,
        spec: dict[str, Any],
        *,
        operation_id: str,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        self.authorize(token, run_id, "submit")
        if not _OPERATION_ID_RE.match(operation_id):
            raise GatewayError(f"unsafe operation_id: {operation_id!r}")
        state = self._settlement_states.get(run_id, "ACTIVE")
        if state != "ACTIVE" or run_id in self._settled:
            raise GatewayError(
                f"run {run_id!r} is settled or settling (state {state}); no further submissions are accepted"
            )
        # Cleanse any client-forged _resolved_runtime
        spec = {k: v for k, v in spec.items() if k != "_resolved_runtime"}
        if attempt is not None:
            return self._submit_v2(token, run_id, spec, operation_id=operation_id, attempt=attempt)
        spec, resolved = self._resolve_runtime(spec, run_id)
        self._validate_inputs(spec)
        # Typed validation: parse through JobSpec unconditionally.
        # The gateway is the trust boundary; every spec must satisfy the
        # hpc-job schema before reaching the adapter.
        try:
            JobSpec._from_payload(spec, source=None)
        except JobError as exc:
            raise GatewayError(f"invalid job spec: {exc}") from exc
        # Scheduler operation identity: consulted BEFORE submit so a gateway
        # restart (or a replayed client) never double-schedules a logical op.
        finder = getattr(self._adapter, "find_by_operation_id", None)
        if finder is None:
            raise GatewayError("adapter lacks operation identity support")
        prior = finder(run_id, operation_id)
        if prior is not None:
            self._remember(run_id, spec["idempotency_key"], prior)
            self._audit_append({
                "kind": "submit", "run_id": run_id, "job_id": prior,
                "operation_id": operation_id, "duplicate": True,
            })
            self._remember_job_metadata(
                run_id, operation_id, 1, prior, spec=spec, resolved=resolved
            )
            return self._submit_result(prior, duplicate=True, resolved=resolved)
        key = spec["idempotency_key"]
        existing = self._jobs.get(run_id, {}).get(key)
        if existing is not None:
            return self._submit_result(existing, duplicate=True, resolved=resolved)
        self._check_quota(run_id, spec["resources"])
        spec_for_adapter = dict(spec)
        if resolved is not None:
            spec_for_adapter["_resolved_runtime"] = resolved
        result = self._adapter.submit(spec_for_adapter, run_id=run_id, operation_id=operation_id)
        job_id = result["job_id"]
        self._remember(run_id, key, job_id)
        self._remember_job_metadata(
            run_id, operation_id, 1, job_id, spec=spec, resolved=resolved
        )
        self._charge(run_id, spec["resources"])
        self._audit_append({
            "kind": "submit", "run_id": run_id, "job_id": job_id,
            "operation_id": operation_id, "duplicate": False,
        })
        return self._submit_result(job_id, duplicate=False, resolved=resolved)

    # -- v2: operation-attempt identity ------------------------------------

    def _submit_v2(
        self,
        token: str,
        run_id: str,
        spec: dict[str, Any],
        *,
        operation_id: str,
        attempt: int,
    ) -> dict[str, Any]:
        """Submit under explicit (run_id, operation_id, attempt) identity.

        Durable protocol: an fsynced SUBMIT_INTENT carrying an opaque
        exact-match scheduler marker precedes ``sbatch``; SUBMIT_ACCEPTED
        lands only once the scheduler ID is known. A crash in between is
        recovered by adopting exactly one marker match — never a blind second
        submission.
        """
        if self._audit is None:
            raise GatewayError("v2 submit requires a durable audit trail")
        # Cleanse any client-forged _resolved_runtime
        spec = {k: v for k, v in spec.items() if k != "_resolved_runtime"}
        spec, resolved = self._resolve_runtime(spec, run_id)
        self._validate_inputs(spec)
        try:
            JobSpec._from_payload(spec, source=None)
        except JobError as exc:
            raise GatewayError(f"invalid job spec: {exc}") from exc

        attempts = self._op_attempts.setdefault(run_id, {}).setdefault(operation_id, {})
        known = attempts.get(attempt)
        if known is not None:
            self._remember_job_metadata(
                run_id, operation_id, attempt, known, spec=spec, resolved=resolved
            )
            return self._submit_result(known, duplicate=True, resolved=resolved)

        # A fresh gateway may have lost its in-memory lineage while the
        # durable accepted event survived.  Adopt that exact identity before
        # considering a new marker or asking the adapter to submit.
        accepted = self._accepted_submission(run_id, operation_id, attempt)
        if accepted is not None:
            accepted_job, accepted_marker = accepted
            attempts[attempt] = accepted_job
            self._remember(run_id, spec["idempotency_key"], accepted_job)
            self._remember_job_metadata(
                run_id, operation_id, attempt, accepted_job,
                spec=spec, resolved=resolved, marker=accepted_marker,
            )
            return self._submit_result(accepted_job, duplicate=True, resolved=resolved)

        # Monotonic, gap-free lineage: every lower attempt exists and is
        # scheduler-terminal before the next one may be created. A predecessor
        # whose intent is provably absent from the scheduler (definitive
        # zero-match query) is voided — it never existed scientifically and
        # must not wedge the lineage.
        for lower in range(1, attempt):
            lower_job = attempts.get(lower)
            if lower_job is None:
                lower_job = self._resolve_predecessor(
                    run_id, operation_id, lower, attempts
                )
            if lower_job is None:
                continue  # voided predecessor counts as satisfied
            state = self._call_job_adapter("status", run_id, lower_job)["state"]
            if state not in TERMINAL_STATES:
                raise GatewayError(
                    f"attempt {lower} of {operation_id!r} is not terminal yet "
                    f"(state {state}); only a terminal predecessor admits "
                    f"attempt {attempt}"
                )

        unresolved = self._unresolved_intent(run_id, operation_id, attempt)
        if len(unresolved) > 1:
            raise GatewayError(
                f"ambiguous recovery for ({run_id!r}, {operation_id!r}, "
                f"attempt {attempt}): multiple unresolved intents"
            )
        if unresolved:
            marker = unresolved[0]
            try:
                found = self._find_by_marker(run_id, marker)
            except Exception as exc:
                raise GatewayError(
                    f"ambiguous scheduler matches for intent marker "
                    f"({operation_id!r}, attempt {attempt}); refusing "
                    f"adoption and resubmission: {exc}"
                ) from exc
            if found is None:
                raise GatewayError(
                    f"unresolved SUBMIT_INTENT marker {marker[:24]}… has no "
                    "scheduler job; refusing both adoption and resubmission"
                )
            attempts[attempt] = found
            self._remember(run_id, spec["idempotency_key"], found)
            self._remember_job_metadata(
                run_id,
                operation_id,
                attempt,
                found,
                spec=spec,
                resolved=resolved,
                marker=marker,
            )
            self._audit.append(
                {
                    "kind": "SUBMIT_ACCEPTED",
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "attempt": attempt,
                    "marker": marker,
                    "job_id": found,
                    "image_id": _image_identity(spec, resolved),
                    "runtime_decl": spec.get("runtime", ""),
                    "instance_id": self._adapter_instance_id(found),
                    "adopted": True,
                },
                durable=True,
            )
            return self._submit_result(found, duplicate=True, resolved=resolved)

        self._check_quota(run_id, spec["resources"])
        marker = (
            f"dftworld-{run_id}-{operation_id}-{attempt}-"
            + secrets.token_hex(8)
        )
        self._audit.append(
            {
                "kind": "SUBMIT_INTENT",
                "run_id": run_id,
                "operation_id": operation_id,
                "attempt": attempt,
                "idempotency_key": spec["idempotency_key"],
                "marker": marker,
                "image_id": _image_identity(spec, resolved),
                "runtime_decl": spec.get("runtime", ""),
            },
            durable=True,
        )
        spec_for_adapter = dict(spec)
        if resolved is not None:
            spec_for_adapter["_resolved_runtime"] = resolved
        result = self._adapter.submit(
            spec_for_adapter, run_id=run_id, operation_id=operation_id, marker=marker
        )
        job_id = result["job_id"]
        attempts[attempt] = job_id
        self._remember(run_id, spec["idempotency_key"], job_id)
        self._remember_job_metadata(
            run_id,
            operation_id,
            attempt,
            job_id,
            spec=spec,
            resolved=resolved,
            marker=marker,
        )
        self._charge(run_id, spec["resources"])
        self._audit.append(
            {
                "kind": "SUBMIT_ACCEPTED",
                "run_id": run_id,
                "operation_id": operation_id,
                "attempt": attempt,
                "marker": marker,
                "job_id": job_id,
                "image_id": _image_identity(spec, resolved),
                "runtime_decl": spec.get("runtime", ""),
                "instance_id": self._adapter_instance_id(job_id),
                "adopted": False,
            },
            durable=True,
        )
        return self._submit_result(job_id, duplicate=False, resolved=resolved)

    def _resolve_predecessor(
        self,
        run_id: str,
        operation_id: str,
        attempt: int,
        attempts: dict[int, str],
    ) -> str | None:
        """Fill one predecessor slot before the gap gate.

        An adoptable intent (scheduler holds the job) is adopted; a
        definitive zero-match intent is voided with a durable audit event —
        the scheduler answered "absent", so retrying that attempt is not a
        blind resubmission. Ambiguity and non-voidable unresolved intents
        stay fail-closed.
        """
        unresolved = self._unresolved_intent(run_id, operation_id, attempt)
        if len(unresolved) > 1:
            raise GatewayError(
                f"ambiguous recovery for ({run_id!r}, {operation_id!r}, "
                f"attempt {attempt})"
            )
        if not unresolved:
            raise GatewayError(
                f"attempt gap for {operation_id!r}: attempt {attempt} was "
                f"never submitted and cannot be skipped"
            )
        marker = unresolved[0]
        try:
            found = self._find_by_marker(run_id, marker)
        except Exception as exc:
            raise GatewayError(
                f"ambiguous scheduler matches while resolving attempt "
                f"{attempt} of {operation_id!r}: {exc}"
            ) from exc
        if found is not None:
            attempts[attempt] = found
            self._remember(run_id, f"adopted:{marker}", found)
            intent = self._intent_event(run_id, operation_id, attempt, marker) or {}
            self._remember_job_metadata(
                run_id,
                operation_id,
                attempt,
                found,
                spec={"runtime": intent.get("runtime_decl", ""),
                      "image_id": intent.get("image_id", "")},
                marker=marker,
            )
            self._audit.append(
                {
                    "kind": "SUBMIT_ACCEPTED",
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "attempt": attempt,
                    "marker": marker,
                    "job_id": found,
                    "image_id": intent.get("image_id", ""),
                    "runtime_decl": intent.get("runtime_decl", ""),
                    "instance_id": self._adapter_instance_id(found),
                    "adopted": True,
                },
                durable=True,
            )
            return found
        # Definitive zero-match: close the intent as voided.
        self._audit.append(
            {
                "kind": "SUBMIT_VOIDED",
                "run_id": run_id,
                "operation_id": operation_id,
                "attempt": attempt,
                "marker": marker,
            },
            durable=True,
        )
        return None

    def _unresolved_intent(
        self, run_id: str, operation_id: str, attempt: int
    ) -> list[str]:
        """Markers of SUBMIT_INTENT entries never closed by SUBMIT_ACCEPTED."""
        intents: dict[str, bool] = {}  # marker -> still unresolved?
        triple = (run_id, operation_id, attempt)
        for entry in self._audit.entries():
            event = entry.get("event", {})
            kind = event.get("kind")
            if kind not in ("SUBMIT_INTENT", "SUBMIT_ACCEPTED", "SUBMIT_VOIDED"):
                continue
            key = (
                event.get("run_id"),
                event.get("operation_id"),
                event.get("attempt"),
            )
            if key != triple or "marker" not in event:
                continue
            if kind == "SUBMIT_INTENT":
                intents[event["marker"]] = True
            else:  # SUBMIT_ACCEPTED or SUBMIT_VOIDED closes the intent
                intents.pop(event["marker"], None)
        return [marker for marker, open_ in intents.items() if open_]

    def resolve_operation_attempt(
        self,
        token: str,
        run_id: str,
        operation_id: str,
        attempt: int | None = None,
    ) -> str:
        """Map (operation, attempt?) to its scheduler job ID."""
        self.authorize(token, run_id, "status")
        attempts = self._op_attempts.get(run_id, {}).get(operation_id, {})
        if not attempts:
            raise GatewayError(f"unknown operation {operation_id!r} in run {run_id!r}")
        if attempt is None:
            return attempts[max(attempts)]
        job_id = attempts.get(attempt)
        if job_id is None:
            raise GatewayError(
                f"no attempt {attempt} recorded for operation {operation_id!r}"
            )
        return job_id

    def operation_status(
        self,
        token: str,
        run_id: str,
        operation_id: str,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        self.authorize(token, run_id, "status")
        attempts = self._op_attempts.get(run_id, {}).get(operation_id, {})
        if not attempts:
            raise GatewayError(f"unknown operation {operation_id!r} in run {run_id!r}")
        states = {}
        for number, job_id in sorted(attempts.items()):
            observed = self._observe_status(
                run_id,
                job_id,
                operation_id=operation_id,
                attempt=number,
            )
            states[str(number)] = observed["state"]
        latest = max(attempts)
        chosen = latest if attempt is None else attempt
        if chosen not in attempts:
            raise GatewayError(
                f"no attempt {chosen} recorded for operation {operation_id!r}"
            )
        return {
            "operation_id": operation_id,
            "attempts": states,
            "latest_attempt": latest,
            "latest_state": states[str(latest)],
            "state": states[str(chosen)],
            "resolved_attempt": chosen,
            "job_id": attempts[chosen],
        }

    def attempts(self, token: str, run_id: str) -> list[dict[str, Any]]:
        """The full owned lineage: one entry per (operation, attempt)."""
        self.authorize(token, run_id, "status")
        entries = []
        for operation_id, attempts in sorted(
            self._op_attempts.get(run_id, {}).items()
        ):
            for attempt, job_id in sorted(attempts.items()):
                entries.append(
                    {
                        "operation_id": operation_id,
                        "attempt": attempt,
                        "job_id": job_id,
                    }
                )
        return entries

    def freeze(self, token: str, run_id: str) -> None:
        """Begin settlement: reject further submissions for this run.

        This only freezes submissions — it does NOT tear down cloud resources.
        Call ``teardown_resources()`` after all jobs have been queried, cancelled,
        and evidence fetched.
        """
        self.authorize(token, run_id, "usage", allow_expired=True)
        self.trusted_freeze(run_id)

    def trusted_freeze(self, run_id: str) -> None:
        """Trusted administrative/watchdog freeze without requiring a client token."""
        current_state = self._settlement_states.get(run_id, "ACTIVE")
        if current_state in ("SETTLED", "SETTLING"):
            return

        # The audit is the restart-safe source of truth.  Do not append a
        # second begin marker when a watchdog or a new Gateway instance
        # repeats the same freeze request.
        if self._has_event("SETTLEMENT_COMPLETE", {"run_id": run_id}):
            self._settlement_states[run_id] = "SETTLED"
            self._settled.add(run_id)
            return

        self._settlement_states[run_id] = "SETTLING"
        self._settled.add(run_id)
        if not self._has_event("SETTLEMENT_BEGIN", {"run_id": run_id}):
            self._audit_append(
                {"kind": "SETTLEMENT_BEGIN", "run_id": run_id}, durable=True
            )

    def settlement_state(self, run_id: str) -> str:
        """Return current settlement state: ACTIVE, SETTLING, SETTLED, or TEARDOWN_FAILED."""
        return self._settlement_states.get(run_id, "ACTIVE")

    def teardown_resources(self, token: str, run_id: str) -> None:
        """Tear down cloud resources (stop/delete instances) for a run.

        Must be called AFTER all jobs have been queried, cancelled, and evidence
        fetched.  Called by the dispatcher during settlement, not by freeze().
        Allows retry when previous attempt resulted in SETTLEMENT_FAILED / TEARDOWN_FAILED.
        Supports expired-token teardown per Gate A1 requirements.
        """
        self.authorize(token, run_id, "usage", allow_expired=True)
        self._perform_teardown(run_id)

    def trusted_teardown(self, run_id: str) -> None:
        """Trusted administrative/watchdog teardown without requiring a client token."""
        self._perform_teardown(run_id)

    def _record_orphan_ledger(self, run_id: str, reason: str) -> None:
        orphan_path = (self._workspace_root / "orphan-ledger.jsonl") if self._workspace_root else Path("runs/compshare-orphans.jsonl")
        try:
            orphan_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "run_id": run_id,
                "reason": reason,
                "timestamp": time.time(),
            }
            with open(orphan_path, "a", encoding="utf-8") as f:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(entry) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception as exc:
            logger.critical("Failed to write gateway orphan ledger: %s", exc)

    def _perform_teardown(self, run_id: str) -> None:
        current_state = self._settlement_states.get(run_id, "ACTIVE")
        if current_state == "SETTLED":
            return
        if self._has_event("SETTLEMENT_COMPLETE", {"run_id": run_id}):
            self._settlement_states[run_id] = "SETTLED"
            self._settled.add(run_id)
            return
        if current_state not in ("SETTLING", "SETTLEMENT_FAILED", "TEARDOWN_FAILED"):
            # Ensure submissions are frozen before resource teardown
            self._settlement_states[run_id] = "SETTLING"
            self._settled.add(run_id)

        if hasattr(self._adapter, "settle"):
            try:
                res = self._adapter.settle(run_id)
                if res is False:
                    self._settlement_states[run_id] = "TEARDOWN_FAILED"
                    self._record_orphan_ledger(run_id, "Adapter settlement reported failure")
                    raise GatewayError(
                        f"Adapter settlement reported failure for run {run_id}; "
                        "cloud instances may remain active/billing",
                        error_code=GatewayErrorCode.ADAPTER_SETTLEMENT_FAILED,
                    )
            except GatewayError:
                self._settlement_states[run_id] = "TEARDOWN_FAILED"
                self._record_orphan_ledger(run_id, "Gateway error during teardown")
                raise
            except Exception as exc:
                self._settlement_states[run_id] = "TEARDOWN_FAILED"
                self._record_orphan_ledger(run_id, f"Adapter settlement error: {exc}")
                raise GatewayError(
                    f"Adapter settlement error for run {run_id}: {exc}",
                    error_code=GatewayErrorCode.ADAPTER_SETTLEMENT_FAILED,
                ) from exc

        self._settlement_states[run_id] = "SETTLED"
        self._audit_append(
            {"kind": "SETTLEMENT_COMPLETE", "run_id": run_id}, durable=True
        )

    def status(self, token: str, run_id: str, job_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "status")
        self._own(run_id, job_id)
        return self._observe_status(run_id, job_id)

    def logs(self, token: str, run_id: str, job_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "logs")
        self._own(run_id, job_id)
        return self._adapter.logs(job_id)

    def fetch(self, token: str, run_id: str, job_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "fetch")
        self._own(run_id, job_id)
        try:
            result = self._call_job_adapter("fetch", run_id, job_id)
        except Exception as exc:
            raise GatewayError(str(exc)) from exc
        if result.get("state") in TERMINAL_STATES:
            self._emit_job_terminal(run_id, job_id, result)
        self._emit_artifact_fetched(run_id, job_id, result)
        return result

    def cancel(self, token: str, run_id: str, job_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "cancel")
        self._own(run_id, job_id)
        try:
            result = self._call_job_adapter("cancel", run_id, job_id)
        except Exception as exc:
            raise GatewayError(str(exc)) from exc
        try:
            observed = self._call_job_adapter("status", run_id, job_id)
        except Exception as exc:
            raise GatewayError(
                f"cancel acknowledgement could not be verified for {job_id!r}: {exc}"
            ) from exc
        if observed.get("state") not in TERMINAL_STATES:
            raise GatewayError(
                f"cancel acknowledgement for {job_id!r} is non-terminal: "
                f"{observed.get('state')!r}"
            )
        self._audit_append({"kind": "cancel", "run_id": run_id, "job_id": job_id})
        self._emit_job_terminal(run_id, job_id, observed)
        return result

    def usage(self, token: str, run_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "usage")
        return self._adapter.usage()

    # -- internal ----------------------------------------------------------

    def _resolve_runtime(
        self, spec: dict[str, Any], run_id: str
    ) -> tuple[dict[str, Any], Any]:
        """Seal the Agent's runtime declaration into the digest form.

        Capability tokens are resolved against locked infra truth; the
        declared digest (legacy compat form) is verified as an assertion.
        Returns ``(spec, resolved_or_None)`` — ``None`` when nothing was
        resolved (resolver-less compat operation), so responses stay
        byte-identical for sites that have not adopted resolution yet.
        """
        decl = spec.get("runtime")
        if not isinstance(decl, str):
            return spec, None  # _from_payload reports the shape error
        try:
            name, digest = split_runtime(decl)
        except RuntimeResolutionError as exc:
            raise GatewayError(f"invalid runtime declaration: {exc}") from exc
        if self._runtime_resolver is None:
            if digest is None:
                raise GatewayError(
                    f"runtime {name!r} names a capability, but this gateway "
                    "has no runtime resolver configured; either compose the "
                    "site with one or submit a digest-pinned declaration"
                )
            return spec, None
        site_profile = None
        provider = None
        if hasattr(self._adapter, "router") and self._adapter.router is not None:
            compute_class = spec.get("compute_class", "cpu")
            try:
                route = self._adapter.router.route(compute_class)
                site_profile = route.site_profile
                provider = route.site_profile.scheduler
            except Exception as exc:
                logger.warning("Router resolution in gateway: %s", exc)
        try:
            resolved = self._runtime_resolver.resolve(
                decl,
                site_profile=site_profile,
                provider=provider,
            )
        except RuntimeResolutionError as exc:
            raise GatewayError(f"runtime resolution failed: {exc}") from exc
        if resolved.declaration != decl:
            self._audit_append({
                "kind": "runtime_resolved",
                "run_id": run_id,
                "requested": decl,
                "capability": resolved.capability,
                "provider": resolved.provider,
                "site_profile_id": resolved.site_profile_id,
                "runtime_profile_id": resolved.runtime_profile_id,
                "artifact_kind": resolved.artifact_kind,
                "artifact_id_or_path": resolved.artifact_path_or_id,
                "digest": resolved.digest,
                "software_versions": resolved.software_versions,
                "qualification": resolved.qualification,
                "sif_sha256": resolved.sif_sha256,
                "runtime_profile_digest": resolved.runtime_profile_digest,
            })
        return {**spec, "runtime": resolved.declaration}, resolved

    def _submit_result(
        self, job_id: str, *, duplicate: bool, resolved: Any
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"job_id": job_id, "duplicate": duplicate}
        if resolved is not None:
            out["resolved_runtime"] = resolved.to_response()
        return out

    def _own(self, run_id: str, job_id: str) -> None:
        self._hydrate_job_metadata(run_id, job_id)
        if job_id not in self._run_jobs.get(run_id, set()):
            raise GatewayError(f"job {job_id!r} is not part of run {run_id!r}")

    def _remember(self, run_id: str, key: str, job_id: str) -> None:
        self._jobs.setdefault(run_id, {})[key] = job_id
        self._run_jobs.setdefault(run_id, set()).add(job_id)

    def _remember_job_metadata(
        self,
        run_id: str,
        operation_id: str,
        attempt: int,
        job_id: str,
        *,
        spec: dict[str, Any] | None = None,
        resolved: Any | None = None,
        marker: str | None = None,
    ) -> None:
        """Bind a scheduler job to its exact run/operation/attempt lineage."""
        current = self._job_metadata.get(job_id, {})
        runtime = spec.get("runtime", "") if isinstance(spec, dict) else ""
        image_id = _image_identity(spec or {}, resolved)
        if not image_id:
            image_id = str(current.get("image_id") or "")
        instance_id = self._adapter_instance_id(job_id) or str(
            current.get("instance_id") or ""
        )
        self._job_metadata[job_id] = {
            **current,
            "run_id": run_id,
            "operation_id": operation_id,
            "attempt": attempt,
            "marker": marker or current.get("marker", ""),
            "runtime_decl": runtime or current.get("runtime_decl", ""),
            "image_id": image_id,
            "instance_id": instance_id,
        }

    def _accepted_submission(
        self, run_id: str, operation_id: str, attempt: int
    ) -> tuple[str, str] | None:
        """Return one durable accepted job for a lineage, or fail closed."""
        matches: list[tuple[str, str]] = []
        if self._audit is None:
            return None
        for entry in self._audit.entries():
            event = entry.get("event") or {}
            if (
                event.get("kind") == "SUBMIT_ACCEPTED"
                and event.get("run_id") == run_id
                and event.get("operation_id") == operation_id
                and event.get("attempt") == attempt
                and isinstance(event.get("job_id"), str)
                and isinstance(event.get("marker"), str)
            ):
                matches.append((event["job_id"], event["marker"]))
        if not matches:
            return None
        if len(set(matches)) != 1:
            raise GatewayError(
                f"conflicting durable SUBMIT_ACCEPTED lineage for "
                f"({run_id!r}, {operation_id!r}, attempt {attempt})"
            )
        return matches[0]

    def _intent_event(
        self, run_id: str, operation_id: str, attempt: int, marker: str
    ) -> dict[str, Any] | None:
        if self._audit is None:
            return None
        for entry in self._audit.entries():
            event = entry.get("event") or {}
            if (
                event.get("kind") == "SUBMIT_INTENT"
                and event.get("run_id") == run_id
                and event.get("operation_id") == operation_id
                and event.get("attempt") == attempt
                and event.get("marker") == marker
            ):
                return event
        return None

    def _has_event(self, kind: str, identity: dict[str, Any]) -> bool:
        if self._audit is None:
            return False
        for entry in self._audit.entries():
            event = entry.get("event") or {}
            if event.get("kind") != kind:
                continue
            if all(event.get(key) == value for key, value in identity.items()):
                return True
        return False

    def _event_for(self, kind: str, identity: dict[str, Any]) -> dict[str, Any] | None:
        if self._audit is None:
            return None
        for entry in self._audit.entries():
            event = entry.get("event") or {}
            if event.get("kind") != kind:
                continue
            if all(event.get(key) == value for key, value in identity.items()):
                return event
        return None

    def _job_event_fields(self, run_id: str, job_id: str) -> dict[str, Any]:
        self._hydrate_job_metadata(run_id, job_id)
        metadata = self._job_metadata.get(job_id, {})
        return {
            "run_id": run_id,
            "operation_id": metadata.get("operation_id", ""),
            "attempt": metadata.get("attempt", 1),
            "job_id": job_id,
            "marker": metadata.get("marker", ""),
            "image_id": metadata.get("image_id", ""),
            "instance_id": metadata.get("instance_id", ""),
            "runtime_decl": metadata.get("runtime_decl", ""),
        }

    def _hydrate_job_metadata(self, run_id: str, job_id: str) -> None:
        """Rebuild event lineage from an accepted audit entry after restart."""
        if job_id in self._job_metadata or self._audit is None:
            return
        for entry in self._audit.entries():
            event = entry.get("event") or {}
            if (
                event.get("kind") == "SUBMIT_ACCEPTED"
                and event.get("run_id") == run_id
                and event.get("job_id") == job_id
            ):
                self._job_metadata[job_id] = {
                    "run_id": run_id,
                    "operation_id": event.get("operation_id", ""),
                    "attempt": event.get("attempt", 1),
                    "marker": event.get("marker", ""),
                    "runtime_decl": event.get("runtime_decl", ""),
                    "image_id": event.get("image_id", ""),
                    "instance_id": event.get("instance_id", ""),
                }
                return

    def _find_by_marker(self, run_id: str, marker: str) -> str | None:
        finder = getattr(self._adapter, "find_by_marker", None)
        if finder is None:
            return None
        try:
            return finder(marker)
        except TypeError:
            return finder(run_id, marker)

    def _call_job_adapter(
        self, method_name: str, run_id: str, job_id: str
    ) -> dict[str, Any]:
        """Call adapters supporting either v1 ``(job_id)`` or site-driver
        ``(run_id, job_id)`` signatures without changing the public Gateway.
        """
        method = getattr(self._adapter, method_name)
        try:
            return method(job_id)
        except TypeError as first:
            try:
                return method(run_id, job_id)
            except TypeError:
                raise first

    def _adapter_instance_id(self, job_id: str) -> str:
        jobs = getattr(self._adapter, "_jobs", None)
        if not isinstance(jobs, dict):
            return ""
        record = jobs.get(job_id)
        if record is None:
            return ""
        if isinstance(record, dict):
            value = record.get("instance_id")
        else:
            value = getattr(record, "instance_id", "")
        return value if isinstance(value, str) else ""

    def _observe_status(
        self,
        run_id: str,
        job_id: str,
        *,
        operation_id: str | None = None,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        if operation_id is not None and attempt is not None:
            self._remember_job_metadata(
                run_id, operation_id, attempt, job_id, marker=None
            )
        try:
            result = self._call_job_adapter("status", run_id, job_id)
        except Exception as exc:
            raise GatewayError(str(exc)) from exc
        if result.get("state") in TERMINAL_STATES:
            self._emit_job_terminal(run_id, job_id, result)
        return result

    def _emit_job_terminal(
        self, run_id: str, job_id: str, result: dict[str, Any]
    ) -> None:
        state = result.get("state")
        if state not in TERMINAL_STATES:
            return
        fields = self._job_event_fields(run_id, job_id)
        identity = {
            key: fields[key]
            for key in ("run_id", "operation_id", "attempt", "job_id")
        }
        existing = self._event_for("JOB_TERMINAL", identity)
        exit_code = result.get("exit_code")
        if existing is not None and "exit_code" not in result:
            exit_code = existing.get("exit_code")
        event = {
            "kind": "JOB_TERMINAL",
            **fields,
            "state": state,
            "exit_code": exit_code,
        }
        if existing is not None:
            if any(existing.get(key) != event.get(key) for key in ("state", "exit_code")):
                raise GatewayError(
                    f"conflicting JOB_TERMINAL event for job {job_id!r}"
                )
            return
        self._audit_append(event, durable=True)

    def _emit_artifact_fetched(
        self, run_id: str, job_id: str, result: dict[str, Any]
    ) -> None:
        records: list[dict[str, Any]] = []
        outputs = result.get("outputs")
        if isinstance(outputs, dict):
            for name, content in sorted(outputs.items(), key=lambda pair: str(pair[0])):
                if isinstance(content, str):
                    raw = content.encode("utf-8")
                elif isinstance(content, (bytes, bytearray)):
                    raw = bytes(content)
                else:
                    raw = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
                records.append({
                    "path": str(name),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                })
        else:
            files = result.get("files")
            if isinstance(files, list) and all(isinstance(name, str) for name in files):
                records = [{"path": name} for name in sorted(files)]
        manifest_digest = _digest(
            json.dumps(records, sort_keys=True, separators=(",", ":"))
        )
        event = {
            "kind": "ARTIFACT_FETCHED",
            **self._job_event_fields(run_id, job_id),
            "artifact_digest": "sha256:" + manifest_digest,
            "artifact_count": len(records),
            "artifacts": records,
        }
        identity = {
            key: event[key]
            for key in ("run_id", "operation_id", "attempt", "job_id")
        }
        existing = self._event_for("ARTIFACT_FETCHED", identity)
        if existing is not None:
            if existing.get("artifact_digest") != event["artifact_digest"]:
                raise GatewayError(
                    f"conflicting ARTIFACT_FETCHED event for job {job_id!r}"
                )
            return
        self._audit_append(event, durable=True)

    def _validate_inputs(self, spec: dict[str, Any]) -> None:
        """Bound inputs stay inside the run workspace (or are plain relative
        names); an absolute host path, ``..``, or a symlink is rejected."""
        for raw in spec.get("inputs", []):
            if isinstance(raw, str):
                _reject_unsafe_relative(raw, "input")
                continue
            if isinstance(raw, dict):
                source = raw.get("source")
                destination = raw.get("destination")
                if not isinstance(source, str) or not isinstance(destination, str):
                    raise GatewayError(
                        f"input binding must carry source and destination: {raw!r}"
                    )
                if self._workspace_root is None:
                    raise GatewayError(
                        "input containment requires a run workspace root"
                    )
                _reject_unsafe_relative(destination, "input destination")
                contained(self._workspace_root, Path(source))
                continue
            raise GatewayError(f"malformed input binding: {raw!r}")

    def _audit_append(self, event: dict[str, Any], *, durable: bool = False) -> None:
        if self._audit is not None:
            self._audit.append(event, durable=durable)

    def _check_quota(self, run_id: str, resources: dict[str, int]) -> None:
        if not self._quota:
            return
        used = self._ledger.setdefault(run_id, {"cpus": 0, "memory_gb": 0, "walltime_minutes": 0})
        for name in ("cpus", "memory_gb", "walltime_minutes"):
            requested = resources.get(name, 0)
            ceiling = self._quota.get(f"max_{name}", 2**62)
            if used[name] + requested > ceiling:
                raise GatewayError(
                    f"quota exceeded for run {run_id!r}: {name} {used[name] + requested} "
                    f"> {ceiling}"
                )

    def _charge(self, run_id: str, resources: dict[str, int]) -> None:
        used = self._ledger.setdefault(run_id, {"cpus": 0, "memory_gb": 0, "walltime_minutes": 0})
        for name in ("cpus", "memory_gb", "walltime_minutes"):
            used[name] += resources.get(name, 0)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _image_identity(spec: dict[str, Any], resolved: Any | None) -> str:
    """Return the concrete image identity bound to a lifecycle event.

    Runtime capability names are not enough for qualification evidence.  When
    the trusted resolver has materialized a concrete image, prefer that
    identity; digest-pinned legacy declarations remain a compatibility form.
    """
    if isinstance(spec, dict):
        explicit = spec.get("image_id") or spec.get("image")
        if isinstance(explicit, str) and explicit:
            return explicit
    if resolved is not None:
        for attr in ("image_id", "artifact_path_or_id"):
            value = getattr(resolved, attr, "")
            if isinstance(value, str) and value:
                return value
    runtime = spec.get("runtime", "") if isinstance(spec, dict) else ""
    if isinstance(runtime, str) and runtime:
        return runtime.split("@", 1)[0]
    return ""


def _reject_unsafe_relative(raw: str, kind: str) -> None:
    if raw == "" or raw.startswith("/") or "\\" in raw or ".." in Path(raw).parts:
        raise GatewayError(f"{kind} path must be a safe relative path: {raw!r}")
