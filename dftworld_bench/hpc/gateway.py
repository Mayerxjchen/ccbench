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
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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


class GatewayError(Exception):
    """A capability was denied at the trust boundary.

    ``status`` carries the HTTP status the common binding should reply with:
    401 for authentication failures, 403 for authorization/containment denials.
    """

    def __init__(self, message: str, *, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


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
        self._runtime_resolver = runtime_resolver
        self._now = now or time.time
        self._tokens: dict[str, Capability] = {}
        self._jobs: dict[str, dict[str, str]] = {}  # run_id -> key -> job_id
        self._run_jobs: dict[str, set[str]] = {}  # run_id -> {job_id}
        # v2 ownership lineage: run_id -> operation_id -> attempt -> scheduler job
        self._op_attempts: dict[str, dict[str, dict[int, str]]] = {}
        # Runs whose settlement has begun: no further submissions accepted.
        self._settled: set[str] = set()
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

    def authorize(self, token: str, run_id: str, operation: str) -> Capability:
        capability = self._tokens.get(token)
        if capability is None:
            raise GatewayError("unknown or revoked token", status=401)
        if capability.run_id != run_id:
            raise GatewayError(
                f"token/run mismatch: token for {capability.run_id!r} used for {run_id!r}",
                status=401,
            )
        if self._now() > capability.expires_at:
            raise GatewayError(f"token expired at {capability.expires_at:.0f}", status=401)
        if operation not in capability.operations:
            raise GatewayError(f"operation {operation!r} outside token scope")
        return capability

    # -- the seven operations (all run-scoped) ----------------------------

    def capabilities(self, token: str, run_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "capabilities")
        out = dict(self._adapter.capabilities())
        if self._runtime_resolver is not None:
            # Runtime capabilities are what Agents may name; concrete SIF
            # identities stay server-side (Architecture Freeze §3).
            out["runtime_capabilities"] = self._runtime_resolver.capabilities()
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
        if run_id in self._settled:
            raise GatewayError(
                f"run {run_id!r} is settled; no further submissions are accepted"
            )
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
            return self._submit_result(prior, duplicate=True, resolved=resolved)
        key = spec["idempotency_key"]
        existing = self._jobs.get(run_id, {}).get(key)
        if existing is not None:
            return self._submit_result(existing, duplicate=True, resolved=resolved)
        self._check_quota(run_id, spec["resources"])
        result = self._adapter.submit(spec, run_id=run_id, operation_id=operation_id)
        job_id = result["job_id"]
        self._remember(run_id, key, job_id)
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
        spec, resolved = self._resolve_runtime(spec, run_id)
        self._validate_inputs(spec)
        try:
            JobSpec._from_payload(spec, source=None)
        except JobError as exc:
            raise GatewayError(f"invalid job spec: {exc}") from exc

        attempts = self._op_attempts.setdefault(run_id, {}).setdefault(operation_id, {})
        known = attempts.get(attempt)
        if known is not None:
            return self._submit_result(known, duplicate=True, resolved=resolved)

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
            state = self._adapter.status(lower_job)["state"]
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
                found = getattr(self._adapter, "find_by_marker", lambda m: None)(marker)
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
            self._audit.append(
                {
                    "kind": "SUBMIT_ACCEPTED",
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "attempt": attempt,
                    "marker": marker,
                    "job_id": found,
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
            },
            durable=True,
        )
        result = self._adapter.submit(
            spec, run_id=run_id, operation_id=operation_id, marker=marker
        )
        job_id = result["job_id"]
        attempts[attempt] = job_id
        self._remember(run_id, spec["idempotency_key"], job_id)
        self._charge(run_id, spec["resources"])
        self._audit.append(
            {
                "kind": "SUBMIT_ACCEPTED",
                "run_id": run_id,
                "operation_id": operation_id,
                "attempt": attempt,
                "marker": marker,
                "job_id": job_id,
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
            found = getattr(self._adapter, "find_by_marker", lambda m: None)(marker)
        except Exception as exc:
            raise GatewayError(
                f"ambiguous scheduler matches while resolving attempt "
                f"{attempt} of {operation_id!r}: {exc}"
            ) from exc
        if found is not None:
            attempts[attempt] = found
            self._remember(run_id, f"adopted:{marker}", found)
            self._audit.append(
                {
                    "kind": "SUBMIT_ACCEPTED",
                    "run_id": run_id,
                    "operation_id": operation_id,
                    "attempt": attempt,
                    "marker": marker,
                    "job_id": found,
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
            if kind not in ("SUBMIT_INTENT", "SUBMIT_ACCEPTED"):
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
        states = {
            str(number): self._adapter.status(job_id)["state"]
            for number, job_id in sorted(attempts.items())
        }
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
        """Begin settlement: reject further submissions for this run."""
        self.authorize(token, run_id, "usage")
        if run_id in self._settled:
            return
        self._settled.add(run_id)
        if self._audit is not None:
            self._audit.append(
                {"kind": "SETTLEMENT_BEGIN", "run_id": run_id}, durable=True
            )

    def status(self, token: str, run_id: str, job_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "status")
        self._own(run_id, job_id)
        return self._adapter.status(job_id)

    def logs(self, token: str, run_id: str, job_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "logs")
        self._own(run_id, job_id)
        return self._adapter.logs(job_id)

    def fetch(self, token: str, run_id: str, job_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "fetch")
        self._own(run_id, job_id)
        try:
            return self._adapter.fetch(job_id)
        except Exception as exc:
            raise GatewayError(str(exc)) from exc

    def cancel(self, token: str, run_id: str, job_id: str) -> dict[str, Any]:
        self.authorize(token, run_id, "cancel")
        self._own(run_id, job_id)
        try:
            result = self._adapter.cancel(job_id)
        except Exception as exc:
            raise GatewayError(str(exc)) from exc
        self._audit_append({"kind": "cancel", "run_id": run_id, "job_id": job_id})
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
        try:
            resolved = self._runtime_resolver.resolve(decl)
        except RuntimeResolutionError as exc:
            raise GatewayError(f"runtime resolution failed: {exc}") from exc
        if resolved.declaration != decl:
            self._audit_append({
                "kind": "runtime_resolved",
                "run_id": run_id,
                "requested": decl,
                "capability": resolved.capability,
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
        if job_id not in self._run_jobs.get(run_id, set()):
            raise GatewayError(f"job {job_id!r} is not part of run {run_id!r}")

    def _remember(self, run_id: str, key: str, job_id: str) -> None:
        self._jobs.setdefault(run_id, {})[key] = job_id
        self._run_jobs.setdefault(run_id, set()).add(job_id)

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

    def _audit_append(self, event: dict[str, Any]) -> None:
        if self._audit is not None:
            self._audit.append(event)

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


def _reject_unsafe_relative(raw: str, kind: str) -> None:
    if raw == "" or raw.startswith("/") or "\\" in raw or ".." in Path(raw).parts:
        raise GatewayError(f"{kind} path must be a safe relative path: {raw!r}")
