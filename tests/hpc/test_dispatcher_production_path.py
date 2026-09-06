"""Production reaches HPC execution only through HpcDispatcher.

Source scans pin the structural invariant: executors, coordinator, and eval
never construct Gateways, adapters, or transports directly — only the trusted
eval composition root assembles the runtime stack and injects a dispatcher.
Behavior tests prove the injected path serves real runs, and that external
queue waits never charge model turns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Composition root privilege: these modules may name GatewayRuntime /
# HpcDispatcher, but never an adapter class or the HTTP server.
_FORBIDDEN_ANYWHERE = (
    "ProcessTestAdapter(",
    "SlurmAdapter(",
    "HttpGatewayServer(",
)
# Strict: no runtime-stack construction at all outside the composition root.
_FORBIDDEN_STRICT = _FORBIDDEN_ANYWHERE + ("GatewayRuntime(", "Gateway(")


def _src(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_executor_builds_no_runtime_stack():
    src = _src("ccbench/executors/hpc.py")
    for forbidden in _FORBIDDEN_STRICT:
        assert forbidden not in src, f"executor constructs {forbidden!r}"


def test_coordinator_never_imports_gateway_or_adapters():
    src = _src("ccbench/core/coordinator.py")
    for marker in ("gateway", "adapters", "site_profile", "transport"):
        assert f"hpc.{marker}" not in src
    for forbidden in _FORBIDDEN_STRICT:
        assert forbidden not in src


def test_eval_composition_root_constructs_only_dispatcher_and_runtime():
    src = _src("eval.py")
    for forbidden in _FORBIDDEN_ANYWHERE:
        assert forbidden not in src, f"eval constructs {forbidden!r}"
    # The composition root is where the dispatcher dependency is born.
    # (GatewayRuntime takes audit= since the durable-ledger work; the
    # zero-arg literal predates that and only the prefix is structural.)
    assert "HpcDispatcher(GatewayRuntime(" in src


def test_registry_injects_dispatcher_into_hpc_executor(tmp_path):
    import asyncio

    from ccbench.executors.registry import resolve
    from ccbench.executors.base import ExecutionContext
    from ccbench.hpc.dispatcher import HpcDispatcher
    from ccbench.hpc.gateway_runtime import GatewayRuntime

    dispatcher = HpcDispatcher(
        GatewayRuntime(),
        {"adapter": "process_test", "root": str(tmp_path / "site")},
    )
    executor = resolve("hpc_controller", dispatcher=dispatcher)
    context = ExecutionContext(
        task=_FakeTask(),
        threads_root=tmp_path,
        model="m",
        max_turns=4,
        verbose=False,
        run_id="run-1",
        workspace=tmp_path / "ws",
    )
    context.adapter_config = {
        "adapter": "process_test",
        "root": str(tmp_path / "site"),
    }
    asyncio.run(executor.prepare(context))
    try:
        assert context.container_env["BENCH_HPC_RUN_TOKEN"]
        assert "host.docker.internal" in context.container_env["BENCH_HPC_GATEWAY_URL"]
        assert context.local_gpus == 0
    finally:
        asyncio.run(executor.close(context))


def test_queue_wait_charges_no_model_turns(tmp_path):
    """The durable external wait charges scheduler_wait_ms — never turns."""
    import asyncio

    from ccbench.core.budgets import BUDGET_DOMAINS, BudgetLedger, BudgetPolicy
    from ccbench.core.coordinator import RunCoordinator
    from ccbench.core.event_store import EventStore

    ledger = BudgetLedger(
        BudgetPolicy(
            {
                d: (10_000_000 if d == "scheduler_wait_ms" else 10_000)
                for d in BUDGET_DOMAINS
            }
        )
    )

    class ChargingWaiter:
        async def wait(self, job_ids, deadline, wait_ledger):
            wait_ledger.charge("scheduler_wait_ms", 60_000, "ext-wait")
            return {"job-1": "SUCCEEDED"}

    coordinator = RunCoordinator(
        run_id="run-1",
        lock_digest="sha256:" + "1" * 64,
        executor=None,
        events=EventStore(tmp_path / "events.jsonl"),
        hpc_waiter=ChargingWaiter(),
    )
    coordinator.ledger = ledger

    asyncio.run(coordinator.yield_external(("job-1",), 100.0))

    assert ledger.used("scheduler_wait_ms") == 60_000
    assert ledger.used("model_turns") == 0


class _FakeTask:
    name = "case-x"
    execution_class = "hpc_controller"
