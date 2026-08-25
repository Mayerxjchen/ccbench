"""Independent finite budget accounting.

Each run carries a fixed, frozen budget policy.  The ledger charges DOMAINS
independently — accepted model turns, logical requests, API attempts, tokens,
cost, active time, total time, local tool time, API retry time, scheduler wait,
jobs, CPU-hours, GPU-hours, storage-byte-hours.  API retry and external
scheduler wait never consume accepted model turns; the model transport charges
a turn ONLY when an accepted response is committed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class BudgetExceeded(ValueError):
    """Raised when a domain budget is exhausted."""

    def __init__(self, domain: str, used: int, limit: int):
        super().__init__(f"budget exceeded for {domain}: used {used}, limit {limit}")
        self.domain = domain
        self.used = used
        self.limit = limit


# All chargeable domains.  Missing formal limits are rejected at construction.
BUDGET_DOMAINS = frozenset({
    "model_turns",
    "logical_requests",
    "api_attempts",
    "tokens",
    "usd_microcost",
    "agent_active_walltime_ms",
    "run_total_walltime_ms",
    "local_tool_walltime_ms",
    "api_retry_walltime_ms",
    "scheduler_wait_ms",
    "jobs",
    "cpu_hours",
    "gpu_hours",
    "storage_byte_hours",
})

# Domain names in the resolved run lock's budgets block that map to ledger
# domains.  Wall-time lock keys are denominated in SECONDS; ledger domains are
# in MILLISECONDS, so the policy multiplies by 1000 on mapping.
_LOCK_DOMAIN_ALIASES = {
    "max_model_turns": ("model_turns", 1),
    "max_total_tokens": ("tokens", 1),
    "agent_active_walltime_sec": ("agent_active_walltime_ms", 1000),
    "scheduler_wait_walltime_sec": ("scheduler_wait_ms", 1000),
}


@dataclass(frozen=True)
class BudgetPolicy:
    """Fixed per-run budget limits keyed by domain."""

    limits: dict[str, int]

    @classmethod
    def from_lock(cls, lock: dict[str, Any]) -> "BudgetPolicy":
        """Build a policy from a resolved run lock's budgets block.

        Rejects a lock with missing formal limits or unknown domain names.
        """
        budgets = lock.get("budgets") or {}
        limits: dict[str, int] = {}
        unknown: list[str] = []
        for key, value in budgets.items():
            alias = _LOCK_DOMAIN_ALIASES.get(key)
            if alias is not None:
                domain, scale = alias
            else:
                domain, scale = key, 1
            if domain not in BUDGET_DOMAINS:
                unknown.append(key)
                continue
            limits[domain] = int(value * scale)
        if unknown:
            raise ValueError(f"unknown budget domain in lock: {unknown}")
        missing = sorted(BUDGET_DOMAINS - set(limits))
        if missing:
            raise ValueError(f"formal lock is missing budget limits: {missing}")
        return cls(limits)

    def require(self, domain: str) -> int:
        if domain not in self.limits:
            raise KeyError(f"no budget limit for domain {domain!r}")
        return self.limits[domain]


@dataclass
class BudgetLedger:
    """Independent per-domain accumulator that enforces the policy."""

    policy: BudgetPolicy
    _counts: dict[str, int] = field(
        default_factory=lambda: {d: 0 for d in BUDGET_DOMAINS}
    )

    def charge(self, domain: str, amount: int, operation_id: str) -> None:
        """Charge ``amount`` to ``domain``, raising BudgetExceeded if over."""
        if domain not in BUDGET_DOMAINS:
            raise KeyError(f"unknown charge domain: {domain!r}")
        if amount < 0:
            raise ValueError(f"negative charge to {domain}: {amount}")
        limit = self.policy.require(domain)
        current = self._counts[domain]
        new_total = current + amount
        if new_total > limit:
            raise BudgetExceeded(domain, new_total, limit)
        self._counts[domain] = new_total

    def used(self, domain: str) -> int:
        """Current usage for one domain."""
        if domain not in BUDGET_DOMAINS:
            raise KeyError(f"unknown charge domain: {domain!r}")
        return self._counts[domain]

    def snapshot(self) -> dict[str, int]:
        """Return a copy of the current usage across all domains."""
        return dict(self._counts)

    def is_exhausted(self, domain: str) -> bool:
        return self._counts[domain] >= self.policy.require(domain)
