"""Independent budget accounting tests."""

from __future__ import annotations

import pytest

from bench.core.budgets import (
    BUDGET_DOMAINS,
    BudgetExceeded,
    BudgetLedger,
    BudgetPolicy,
)


@pytest.fixture
def policy() -> BudgetPolicy:
    limits = {
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
    return BudgetPolicy(limits)


def _lock_with_budgets(budgets: dict[str, int]) -> dict[str, int]:
    return {"budgets": budgets}


def test_all_domains_are_chargeable(policy):
    ledger = BudgetLedger(policy)
    for domain in sorted(BUDGET_DOMAINS):
        ledger.charge(domain, 1, "OP-1")
    assert ledger.used("model_turns") == 1


def test_api_retry_does_not_consume_turn(policy):
    ledger = BudgetLedger(policy)
    ledger.charge("api_attempts", 1, "MODEL-1/A1")
    ledger.charge("api_retry_walltime_ms", 900, "MODEL-1/A1")
    assert ledger.used("model_turns") == 0


def test_external_wait_consumes_total_not_active(policy):
    ledger = BudgetLedger(policy)
    ledger.charge("scheduler_wait_ms", 3_600_000, "JOB-1")
    ledger.charge("run_total_walltime_ms", 3_600_000, "JOB-1")
    assert ledger.used("agent_active_walltime_ms") == 0


def test_turn_charged_only_on_accepted_commit(policy):
    ledger = BudgetLedger(policy)
    # A rejected attempt is not an accepted turn.
    ledger.charge("api_attempts", 3, "MODEL-1/A1")
    ledger.charge("model_turns", 1, "MODEL-1/A1")
    assert ledger.used("api_attempts") == 3
    assert ledger.used("model_turns") == 1


def test_charge_over_limit_raises_budget_exceeded(policy):
    ledger = BudgetLedger(policy)
    ledger.charge("model_turns", 63, "OP-1")
    with pytest.raises(BudgetExceeded) as exc:
        ledger.charge("model_turns", 2, "OP-1")
    assert exc.value.domain == "model_turns"
    assert exc.value.used == 65
    assert exc.value.limit == 64
    # The failed charge did not land.
    assert ledger.used("model_turns") == 63


def test_unknown_domain_rejected(policy):
    ledger = BudgetLedger(policy)
    with pytest.raises(KeyError, match="unknown charge domain"):
        ledger.charge("bananas", 1, "OP-1")


def test_negative_charge_rejected(policy):
    ledger = BudgetLedger(policy)
    with pytest.raises(ValueError, match="negative charge"):
        ledger.charge("tokens", -1, "OP-1")


def test_snapshot_is_independent_copy(policy):
    ledger = BudgetLedger(policy)
    ledger.charge("jobs", 2, "JOB-1")
    snap = ledger.snapshot()
    assert snap["jobs"] == 2
    ledger.charge("jobs", 1, "JOB-2")
    assert snap["jobs"] == 2
    snap["jobs"] = 999
    assert ledger.used("jobs") == 3


def test_policy_from_lock_accepts_aliases():
    lock = {
        "budgets": {
            "max_model_turns": 64,
            "max_total_tokens": 10_000_000,
            "agent_active_walltime_sec": 86_400,
            "scheduler_wait_walltime_sec": 3_600,
            "logical_requests": 128,
            "api_attempts": 512,
            "usd_microcost": 5_000_000,
            "run_total_walltime_ms": 172_800_000,
            "local_tool_walltime_ms": 7_200_000,
            "api_retry_walltime_ms": 3_600_000,
            "jobs": 16,
            "cpu_hours": 500,
            "gpu_hours": 24,
            "storage_byte_hours": 10_000_000_000,
        }
    }
    policy = BudgetPolicy.from_lock(lock)
    assert policy.require("model_turns") == 64
    assert policy.require("tokens") == 10_000_000
    assert policy.require("agent_active_walltime_ms") == 86_400_000
    assert policy.require("scheduler_wait_ms") == 3_600_000


def test_policy_from_lock_rejects_missing_formal_limits():
    with pytest.raises(ValueError, match="missing budget limits"):
        BudgetPolicy.from_lock({"budgets": {"max_model_turns": 64}})


def test_policy_from_lock_rejects_unknown_domain():
    with pytest.raises(ValueError, match="unknown budget domain"):
        BudgetPolicy.from_lock(
            {
                "budgets": {
                    **{d: 1 for d in sorted(BUDGET_DOMAINS)},
                    "turbo_mode": 1,
                }
            }
        )


def test_policy_from_lock_rejects_absent_budgets_block():
    with pytest.raises(ValueError, match="missing budget limits"):
        BudgetPolicy.from_lock({})


def test_is_exhausted_reflects_limit(policy):
    ledger = BudgetLedger(policy)
    assert not ledger.is_exhausted("jobs")
    for _ in range(16):
        ledger.charge("jobs", 1, "JOB")
    assert ledger.is_exhausted("jobs")
