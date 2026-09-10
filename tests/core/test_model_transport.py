"""Trusted model transport, retry taxonomy, and attempt ledger tests."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from bench.contracts.result import FailureCode
from bench.core.budgets import BudgetLedger, BudgetPolicy
from bench.core.event_store import EventStore
from bench.core.model_transport import (
    RETRYABLE_HTTP,
    HttpFailure,
    ModelResponse,
    RetryingModelClient,
    TerminalApiError,
    TimeoutUnknown,
    credential_env_names,
    full_jitter,
)

REQUEST = {"messages": [{"role": "user", "content": "solve it"}]}


class FakeTransport:
    """Scripted transport: pops the next outcome per call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[str] = []

    async def request(self, operation_id, request, timeout_sec):
        self.calls.append(operation_id)
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def client(fake_transport, **kwargs) -> RetryingModelClient:
    return RetryingModelClient(
        fake_transport,
        base_delay_sec=0.0,
        max_delay_sec=0.0,
        **kwargs,
    )


def _policy() -> BudgetPolicy:
    return BudgetPolicy(
        limits={
            "model_turns": 64,
            "logical_requests": 128,
            "api_attempts": 512,
            "tokens": 10_000_000,
            "usd_microcost": 5_000_000,
            "agent_active_walltime_ms": 86_400_000,
            "run_total_walltime_ms": 172_800_000,
            "local_tool_walltime_ms": 7_200_000,
            "api_retry_walltime_ms": 3_600_000,
            "scheduler_wait_ms": 3_600_000,
            "jobs": 16,
            "cpu_hours": 500,
            "gpu_hours": 24,
            "storage_byte_hours": 10_000_000_000,
        }
    )


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, 529])
def test_transient_status_retries(status):
    fake_transport = FakeTransport([HttpFailure(status), ModelResponse("ok")])
    result = asyncio_run(client(fake_transport, max_attempts=4).request("MODEL-1", REQUEST))
    assert result.text == "ok"
    assert [a.state for a in result.attempts] == ["known_failure", "success"]
    assert result.accepted_attempt == 2


def test_lost_response_accepts_only_retry():
    fake_transport = FakeTransport(
        [TimeoutUnknown("req-1"), ModelResponse("second", "req-2")]
    )
    result = asyncio_run(client(fake_transport).request("MODEL-1", REQUEST))
    assert result.accepted_attempt == 2
    assert result.attempts[0].state == "timeout_unknown"
    assert result.attempts[0].provider_request_id == "req-1"
    assert result.text == "second"


def test_full_jitter_bounds():
    rng = random.Random(0)
    base, cap = 1.0, 30.0
    for idx in range(8):
        for _ in range(50):
            delay = full_jitter(base, cap, idx, rng)
            assert 0.0 <= delay <= min(cap, base * (2**idx))


def test_non_retryable_status_fails_fast():
    fake_transport = FakeTransport([HttpFailure(401, body="bad key")])
    with pytest.raises(TerminalApiError) as exc:
        asyncio_run(client(fake_transport).request("MODEL-1", REQUEST))
    assert exc.value.failure_code is FailureCode.API_AUTH_CONFIG
    # fail-fast: exactly one attempt, no retries
    assert fake_transport.calls == ["MODEL-1"]


@pytest.mark.parametrize(
    "status,code",
    [
        (401, FailureCode.API_AUTH_CONFIG),
        (403, FailureCode.API_AUTH_CONFIG),
        (402, FailureCode.API_QUOTA),
        (400, FailureCode.API_CONFIGURATION),
        (422, FailureCode.API_CONFIGURATION),
        (404, FailureCode.API_CONFIGURATION),
    ],
)
def test_fail_fast_mapping(status, code):
    fake_transport = FakeTransport([HttpFailure(status)])
    with pytest.raises(TerminalApiError) as exc:
        asyncio_run(client(fake_transport).request("MODEL-1", REQUEST))
    assert exc.value.failure_code is code


def test_exhausted_transient_raises_exhausted():
    fake_transport = FakeTransport(
        [HttpFailure(503), HttpFailure(503), HttpFailure(503), HttpFailure(503)]
    )
    with pytest.raises(TerminalApiError) as exc:
        asyncio_run(client(fake_transport, max_attempts=4).request("MODEL-1", REQUEST))
    assert exc.value.failure_code is FailureCode.API_TRANSIENT_EXHAUSTED


def test_exhausted_rate_limit_raises_rate_limit():
    fake_transport = FakeTransport(
        [HttpFailure(429), HttpFailure(429), HttpFailure(429), HttpFailure(429)]
    )
    with pytest.raises(TerminalApiError) as exc:
        asyncio_run(client(fake_transport, max_attempts=4).request("MODEL-1", REQUEST))
    assert exc.value.failure_code is FailureCode.API_RATE_LIMIT_EXHAUSTED


def test_exhausted_timeout_unknown_raises_transient():
    fake_transport = FakeTransport(
        [TimeoutUnknown("r1"), TimeoutUnknown("r2"), TimeoutUnknown("r3"),
         TimeoutUnknown("r4")]
    )
    with pytest.raises(TerminalApiError) as exc:
        asyncio_run(client(fake_transport, max_attempts=4).request("MODEL-1", REQUEST))
    assert exc.value.failure_code is FailureCode.API_TRANSIENT_EXHAUSTED


def test_retry_after_honored_up_to_max_delay():
    rng = random.Random(7)
    # Retry-After of 10s with max_delay 5s → capped at 5s; sleep skipped via
    # zero delays below, so we assert the chosen delay caps through the ledger.
    fake_transport = FakeTransport(
        [HttpFailure(503, retry_after=10.0), ModelResponse("ok")]
    )
    ledger = BudgetLedger(_policy())
    cl = RetryingModelClient(
        fake_transport,
        base_delay_sec=1.0,
        max_delay_sec=5.0,
        rng=rng,
        ledger=ledger,
    )
    result = asyncio_run(cl.request("MODEL-1", REQUEST))
    assert result.text == "ok"
    retry_ms = ledger.used("api_retry_walltime_ms")
    assert 0 < retry_ms <= 5000


def test_ledger_charges_attempts_not_turns_on_retry():
    ledger = BudgetLedger(_policy())
    fake_transport = FakeTransport(
        [HttpFailure(503), HttpFailure(503), ModelResponse("ok", usage={"tokens": 120})]
    )
    result = asyncio_run(
        RetryingModelClient(
            fake_transport, base_delay_sec=0.0, max_delay_sec=0.0, ledger=ledger
        ).request("MODEL-1", REQUEST)
    )
    assert result.accepted_attempt == 3
    assert ledger.used("api_attempts") == 3
    assert ledger.used("model_turns") == 1  # charged only on accepted commit
    assert ledger.used("tokens") == 120
    assert ledger.used("logical_requests") == 0


def test_ledger_never_charges_turn_on_terminal_failure():
    ledger = BudgetLedger(_policy())
    fake_transport = FakeTransport([HttpFailure(503), HttpFailure(503)])
    with pytest.raises(TerminalApiError):
        asyncio_run(
            RetryingModelClient(
                fake_transport,
                max_attempts=2,
                base_delay_sec=0.0,
                max_delay_sec=0.0,
                ledger=ledger,
            ).request("MODEL-1", REQUEST)
        )
    assert ledger.used("api_attempts") == 2
    assert ledger.used("model_turns") == 0


def test_budget_exceeded_propagates():
    ledger = BudgetLedger(_policy())
    fake_transport = FakeTransport([ModelResponse("ok", usage={"tokens": 1})])
    cl = RetryingModelClient(
        fake_transport, base_delay_sec=0.0, max_delay_sec=0.0, ledger=ledger
    )
    ledger.charge("model_turns", 64, "MODEL-1/A0")  # exhaust the domain
    with pytest.raises(Exception, match="budget exceeded for model_turns"):
        asyncio_run(cl.request("MODEL-1", REQUEST))


def test_attempts_recorded_in_event_store(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    fake_transport = FakeTransport(
        [HttpFailure(503), ModelResponse("ok", "req-9", {"tokens": 5})]
    )
    asyncio_run(
        RetryingModelClient(
            fake_transport, base_delay_sec=0.0, max_delay_sec=0.0, events=store
        ).request("MODEL-1", REQUEST)
    )
    events = store.load_events()
    kinds = [e.kind for e in events]
    assert kinds == [
        "model_attempt",
        "model_retry_wait",
        "model_attempt",
        "model_response_committed",
    ]
    assert events[0].operation_id == "MODEL-1"
    assert events[0].payload["state"] == "known_failure"
    assert events[2].payload["state"] == "success"
    assert events[3].payload["accepted_attempt"] == 2
    assert events[3].payload["provider_request_id"] == "req-9"


def test_attempt_metadata_is_deterministic():
    transport = FakeTransport([ModelResponse("ok", "req-1")])
    first = asyncio_run(client(transport).request("MODEL-1", REQUEST))
    second = asyncio_run(client(FakeTransport([ModelResponse("ok", "req-1")])).request("MODEL-1", REQUEST))
    assert first.attempts[0].metadata_digest == second.attempts[0].metadata_digest


def test_terminal_reason_redacts_html_body():
    transport = FakeTransport([HttpFailure(503, body="<html><body>stack trace leak</body></html>")])
    with pytest.raises(TerminalApiError) as exc:
        asyncio_run(client(transport, max_attempts=1).request("MODEL-1", REQUEST))
    assert "<html>" not in exc.value.reason
    assert "redacted" in exc.value.reason


def test_circuit_breaker_blocks_new_admissions_without_switching_endpoint():
    transport = FakeTransport([HttpFailure(503), HttpFailure(503)])
    cl = RetryingModelClient(
        transport,
        max_attempts=2,
        base_delay_sec=0.0,
        max_delay_sec=0.0,
        breaker_failure_threshold=2,
        breaker_cooldown_sec=3600.0,
    )
    with pytest.raises(TerminalApiError):
        asyncio_run(cl.request("MODEL-1", REQUEST))
    with pytest.raises(TerminalApiError) as exc:
        asyncio_run(cl.request("MODEL-2", REQUEST))
    # admission blocked BEFORE any provider call
    assert transport.calls == ["MODEL-1", "MODEL-1"]
    assert exc.value.failure_code is FailureCode.API_TRANSIENT_EXHAUSTED


def test_breaker_recovers_after_cooldown_probe_success():
    transport = FakeTransport([HttpFailure(503), HttpFailure(503), ModelResponse("ok")])
    cl = RetryingModelClient(
        transport,
        max_attempts=2,
        base_delay_sec=0.0,
        max_delay_sec=0.0,
        breaker_failure_threshold=2,
        breaker_cooldown_sec=0.0,
    )
    with pytest.raises(TerminalApiError):
        asyncio_run(cl.request("MODEL-1", REQUEST))
    # cooldown is 0 → half-open probe admitted; success closes the breaker
    result = asyncio_run(cl.request("MODEL-2", REQUEST))
    assert result.text == "ok"
    assert transport.calls == ["MODEL-1", "MODEL-1", "MODEL-2"]


def test_retryable_status_set_covers_normative_statuses():
    assert RETRYABLE_HTTP == frozenset({408, 429, 500, 502, 503, 504, 529})


def test_credential_env_names_from_profiles():
    names = credential_env_names(
        {"endpoint_env": "DFTWORLD_API_ENDPOINT", "credential_env": "DFTWORLD_API_KEY"},
        {},
        {"credential_env": "OPENAI_API_KEY"},
    )
    assert names == frozenset(
        {"DFTWORLD_API_ENDPOINT", "DFTWORLD_API_KEY", "OPENAI_API_KEY"}
    )
    assert credential_env_names() == frozenset()


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
