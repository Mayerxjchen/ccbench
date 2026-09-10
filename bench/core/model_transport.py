"""Trusted model transport, retry taxonomy, and attempt ledger.

The transport is the ONLY component that issues provider requests.  It runs in
the trusted Harness process — never inside the Candidate — so the candidate
environment carries neither endpoint nor credential.  It is the single retry
owner: nested SDK retries are disabled, only known-transient HTTP statuses are
retried with full jitter, ``Retry-After`` is honored up to the frozen max
delay, and lost/unknown responses are treated as retryable (accept-only-on-
success).  A bounded circuit breaker blocks new admissions without switching
endpoint.

Every attempt lands in the EventStore; every charge flows through the
BudgetLedger.  A model turn is charged ONLY when an accepted response is
committed — never inferred from tool calls.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Protocol

from bench.config.profiles import canonical_json, digest_bytes
from bench.contracts.result import FailureCode
from bench.core.budgets import BudgetLedger
from bench.core.event_store import EventStore

# Known-transient HTTP statuses: retry with full jitter.
RETRYABLE_HTTP = frozenset({408, 429, 500, 502, 503, 504, 529})

# Fail-fast, non-retryable statuses → terminal FailureCode.
_TERMINAL_CODES: dict[int, FailureCode] = {
    400: FailureCode.API_CONFIGURATION,
    401: FailureCode.API_AUTH_CONFIG,
    402: FailureCode.API_QUOTA,
    403: FailureCode.API_AUTH_CONFIG,
    404: FailureCode.API_CONFIGURATION,
    422: FailureCode.API_CONFIGURATION,
}

_HTML_MARKERS = ("<html", "<!doctype", "<!DOCTYPE")


def credential_env_names(*profiles: dict[str, Any]) -> frozenset[str]:
    """Env var names that must NEVER reach the Candidate.

    Each api profile references endpoint/credential via env var NAMES (never
    literal values).  Those names are exactly the keys a container_env must
    refuse to forward into the candidate — the transport resolves the values
    only inside the trusted Harness process.
    """
    names: set[str] = set()
    for profile in profiles:
        endpoint = profile.get("endpoint_env")
        credential = profile.get("credential_env")
        if endpoint:
            names.add(str(endpoint))
        if credential:
            names.add(str(credential))
    return frozenset(names)


def full_jitter(base: float, cap: float, retry_index: int, rng: random.Random) -> float:
    """Exponential backoff with full jitter: uniform in [0, min(cap, base*2^i)]."""
    return rng.uniform(0.0, min(cap, base * (2**retry_index)))


def _redact_body(body: str) -> str:
    """Redact HTML bodies from public records; truncate long plain bodies."""
    if any(marker in body.lower() for marker in _HTML_MARKERS):
        return "<redacted html body>"
    if len(body) > 300:
        return body[:300] + "…"
    return body


# -- transport outcomes -------------------------------------------------------


@dataclass(frozen=True)
class HttpFailure:
    status: int
    body: str = ""
    retry_after: float | None = None


@dataclass(frozen=True)
class TimeoutUnknown:
    """A request whose completion state is unknown (lost, timed out)."""

    request_id: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    text: str
    request_id: str | None = None
    usage: dict[str, Any] | None = None


class Transport(Protocol):
    """One raw provider round-trip. Never owned by the Candidate."""

    async def request(
        self,
        operation_id: str,
        request: dict[str, Any],
        timeout_sec: float,
    ) -> HttpFailure | ModelResponse | TimeoutUnknown: ...


# -- attempt and response contracts --------------------------------------------


@dataclass(frozen=True)
class ModelAttempt:
    """One recorded provider attempt, frozen and digest-anchored."""

    state: str  # known_failure | timeout_unknown | success
    provider_request_id: str | None
    usage: dict[str, Any] | None
    metadata_digest: str

    @classmethod
    def create(
        cls,
        state: str,
        provider_request_id: str | None,
        usage: dict[str, Any] | None,
        request_digest: str,
    ) -> "ModelAttempt":
        body = {
            "state": state,
            "provider_request_id": provider_request_id,
            "usage": usage,
            "request_digest": request_digest,
        }
        return cls(
            state=state,
            provider_request_id=provider_request_id,
            usage=usage,
            metadata_digest=digest_bytes(canonical_json(body)),
        )


@dataclass(frozen=True)
class AcceptedResponse:
    """A committed, accepted model response and its full attempt ledger."""

    text: str
    attempts: tuple[ModelAttempt, ...]
    accepted_attempt: int  # 1-indexed

    @property
    def usage(self) -> dict[str, Any] | None:
        for attempt in reversed(self.attempts):
            if attempt.state == "success":
                return attempt.usage
        return None


class TerminalApiError(RuntimeError):
    """A terminal, classified API failure (never a model turn was charged)."""

    def __init__(
        self,
        code: FailureCode,
        reason: str,
        *,
        status: int | None = None,
    ):
        super().__init__(reason)
        self.failure_code = code
        self.reason = reason
        self.status = status


# -- bounded circuit breaker ----------------------------------------------------


class CircuitBreaker:
    """Blocks new admissions after consecutive failures, without switching
    endpoint.  After the cooldown the breaker is half-open and admits one
    probe; success closes it, another failure reopens it."""

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_sec: float = 60.0,
        now: Any = time.monotonic,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        self._now = now
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open = False

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if self._half_open:
            return True
        if self._now() - self._opened_at >= self.cooldown_sec:
            self._half_open = True
            return True
        return False

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._opened_at = self._now()
            self._half_open = False

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open = False


# -- the retrying client ---------------------------------------------------------


class RetryingModelClient:
    """One retry owner. Disable nested SDK retries; retry here.

    Charges the BudgetLedger per attempt and on accepted commit, records every
    attempt in the EventStore, and normalizes terminal failures to the Task 5
    FailureCodes with HTML bodies redacted.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        max_attempts: int = 4,
        base_delay_sec: float = 1.0,
        max_delay_sec: float = 30.0,
        request_timeout_sec: float = 120.0,
        rng: random.Random | None = None,
        ledger: BudgetLedger | None = None,
        events: EventStore | None = None,
        breaker_failure_threshold: int = 5,
        breaker_cooldown_sec: float = 60.0,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self.transport = transport
        self.max_attempts = max_attempts
        self.base_delay_sec = base_delay_sec
        self.max_delay_sec = max_delay_sec
        self.request_timeout_sec = request_timeout_sec
        self.rng = rng or random.Random()
        self.ledger = ledger
        self.events = events
        self.breaker = CircuitBreaker(
            failure_threshold=breaker_failure_threshold,
            cooldown_sec=breaker_cooldown_sec,
        )

    async def request(
        self,
        operation_id: str,
        request: dict[str, Any],
        *,
        at: str = "t0",
    ) -> AcceptedResponse:
        if not self.breaker.allow():
            self._emit(
                operation_id,
                "model_admission_blocked",
                {"reason": "circuit_open"},
                at,
            )
            raise TerminalApiError(
                FailureCode.API_TRANSIENT_EXHAUSTED,
                "circuit breaker open: admission blocked without endpoint switch",
            )

        request_digest = digest_bytes(canonical_json(request))
        attempts: list[ModelAttempt] = []
        last_failure: HttpFailure | None = None
        last_timeout: TimeoutUnknown | None = None

        for attempt_index in range(1, self.max_attempts + 1):
            self._charge("api_attempts", 1, operation_id)
            outcome = await self._round_trip(operation_id, request)
            attempt_no = len(attempts) + 1

            if isinstance(outcome, ModelResponse):
                usage = dict(outcome.usage) if outcome.usage else None
                committed = ModelAttempt.create(
                    "success", outcome.request_id, usage, request_digest
                )
                attempts.append(committed)
                self._record_attempt(operation_id, attempt_no, committed, at)
                self.breaker.record_success()
                # A model turn is charged ONLY when an accepted response is
                # committed — never inferred from tool calls.
                self._charge("model_turns", 1, operation_id)
                if usage and usage.get("tokens"):
                    self._charge("tokens", int(usage["tokens"]), operation_id)
                self._emit(
                    operation_id,
                    "model_response_committed",
                    {
                        "accepted_attempt": attempt_no,
                        "provider_request_id": outcome.request_id,
                    },
                    at,
                )
                return AcceptedResponse(
                    text=outcome.text,
                    attempts=tuple(attempts),
                    accepted_attempt=attempt_no,
                )

            if isinstance(outcome, HttpFailure):
                attempt = ModelAttempt.create(
                    "known_failure", None, None, request_digest
                )
                attempts.append(attempt)
                self._record_attempt(operation_id, attempt_no, attempt, at)
                self.breaker.record_failure()
                if outcome.status not in RETRYABLE_HTTP:
                    code = _TERMINAL_CODES.get(
                        outcome.status, FailureCode.API_CONFIGURATION
                    )
                    raise TerminalApiError(
                        code,
                        f"provider failed-fast on HTTP {outcome.status}: "
                        f"{_redact_body(outcome.body)}",
                        status=outcome.status,
                    )
                last_failure = outcome
                if attempt_index < self.max_attempts:
                    await self._retry_wait(
                        operation_id, attempt_index, outcome, at
                    )
                continue

            if isinstance(outcome, TimeoutUnknown):
                attempt = ModelAttempt.create(
                    "timeout_unknown", outcome.request_id, None, request_digest
                )
                attempts.append(attempt)
                self._record_attempt(operation_id, attempt_no, attempt, at)
                self.breaker.record_failure()
                last_timeout = outcome
                if attempt_index < self.max_attempts:
                    await self._retry_wait(
                        operation_id, attempt_index, None, at
                    )
                continue

            raise TerminalApiError(
                FailureCode.API_CONFIGURATION,
                f"transport returned unknown outcome: {type(outcome).__name__}",
            )

        self.breaker.record_failure()
        if last_failure is not None and last_failure.status == 429:
            code = FailureCode.API_RATE_LIMIT_EXHAUSTED
        else:
            code = FailureCode.API_TRANSIENT_EXHAUSTED
        reason = self._exhaustion_reason(attempts, last_failure, last_timeout)
        self._emit(
            operation_id,
            "model_attempts_exhausted",
            {"attempts": len(attempts), "code": code.value},
            at,
        )
        raise TerminalApiError(code, reason)

    # -- internals -----------------------------------------------------------

    async def _round_trip(self, operation_id: str, request: dict[str, Any]):
        try:
            return await asyncio.wait_for(
                self.transport.request(
                    operation_id, request, self.request_timeout_sec
                ),
                timeout=self.request_timeout_sec,
            )
        except asyncio.TimeoutError:
            return TimeoutUnknown()

    async def _retry_wait(
        self,
        operation_id: str,
        attempt_index: int,
        failure: HttpFailure | None,
        at: str,
    ) -> None:
        if failure is not None and failure.retry_after is not None:
            delay = min(float(failure.retry_after), self.max_delay_sec)
        else:
            delay = full_jitter(
                self.base_delay_sec,
                self.max_delay_sec,
                attempt_index - 1,
                self.rng,
            )
        self._charge("api_retry_walltime_ms", int(delay * 1000), operation_id)
        self._emit(
            operation_id,
            "model_retry_wait",
            {"attempt": attempt_index + 1, "delay_ms": int(delay * 1000)},
            at,
        )
        if delay > 0:
            await asyncio.sleep(delay)

    def _charge(self, domain: str, amount: int, operation_id: str) -> None:
        if self.ledger is not None:
            self.ledger.charge(domain, amount, operation_id)

    def _emit(self, operation_id: str, kind: str, payload: dict, at: str) -> None:
        if self.events is not None:
            self.events.append(operation_id, kind, payload, at)

    def _record_attempt(
        self,
        operation_id: str,
        attempt_no: int,
        attempt: ModelAttempt,
        at: str,
    ) -> None:
        self._emit(
            operation_id,
            "model_attempt",
            {
                "attempt": attempt_no,
                "state": attempt.state,
                "provider_request_id": attempt.provider_request_id,
                "usage": attempt.usage,
                "metadata_digest": attempt.metadata_digest,
            },
            at,
        )

    def _exhaustion_reason(
        self,
        attempts: list[ModelAttempt],
        last_failure: HttpFailure | None,
        last_timeout: TimeoutUnknown | None,
    ) -> str:
        states = ",".join(a.state for a in attempts)
        if last_timeout is not None:
            return (
                f"provider completion unknown after {len(attempts)} attempts "
                f"({states}); last request {last_timeout.request_id!r} never "
                "committed"
            )
        if last_failure is not None:
            return (
                f"provider transient HTTP {last_failure.status} persisted after "
                f"{len(attempts)} attempts ({states}); body "
                f"{_redact_body(last_failure.body)}"
            )
        return f"provider attempts exhausted after {len(attempts)} attempts"
