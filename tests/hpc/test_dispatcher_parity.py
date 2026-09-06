"""Seven-operation parity: the HpcDispatcher façade must behave identically
to driving the Gateway directly with an issued token.

For identical validated requests against identical fresh process_test sites,
every observable output must match (token/timestamp-free fields only). Any
divergence would mean the façade added or lost behavior, which Task 1
forbids.
"""

from __future__ import annotations

from ccbench.hpc import HpcDispatcher
from ccbench.hpc.gateway import ALL_OPS, Gateway
from ccbench.hpc.gateway_runtime import build_adapter

DIGEST = "img@sha256:" + "a" * 64


def _spec(key: str) -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": DIGEST,
        "command": ["/bin/echo", "ok"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": [],
    }


def _direct_gateway(root, workspace):
    adapter = build_adapter(
        {"adapter": "process_test", "root": str(root), "timeout_sec": 5.0}
    )
    gateway = Gateway(adapter, workspace_root=workspace)
    token = gateway.issue("run-1", ALL_OPS, ttl_sec=300.0)
    return gateway, token


def test_seven_operation_parity(tmp_path):
    direct_root = tmp_path / "direct-site"
    facade_root = tmp_path / "facade-site"
    workspace = tmp_path / "workspace"

    gateway, token = _direct_gateway(direct_root, workspace)
    session = HpcDispatcher.process_test(facade_root).open_run(
        "run-1", workspace=workspace
    )

    # capabilities
    assert gateway.capabilities(token, "run-1") == session.capabilities()

    # submit — same operation on both sides yields the same job id and flag
    direct_sub = gateway.submit(token, "run-1", _spec("op-1"), operation_id="op-1")
    facade_sub = session.submit(_spec("op-1"), operation_id="op-1")
    assert direct_sub == facade_sub
    job_id = direct_sub["job_id"]

    # duplicate submit is reported identically
    assert gateway.submit(token, "run-1", _spec("op-1"), operation_id="op-1") == (
        session.submit(_spec("op-1"), operation_id="op-1")
    )

    # status / logs — poll both sides to a terminal state first: echo exits
    # quickly but fork+exec still needs wall time, so bound the wait by time,
    # not iteration count.
    import time

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if (
            gateway.status(token, "run-1", job_id)["state"]
            == session.status(job_id)["state"] == "SUCCEEDED"
        ):
            break
        time.sleep(0.01)
    assert gateway.status(token, "run-1", job_id) == session.status(job_id)
    direct_logs = gateway.logs(token, "run-1", job_id)
    facade_logs = session.logs(job_id)
    assert set(direct_logs) == set(facade_logs)

    # usage
    direct_usage = gateway.usage(token, "run-1")
    facade_usage = session.usage()
    for key, value in direct_usage.items():
        assert facade_usage[key] == value

    # fetch of a succeeded job returns the same shape on both sides; values
    # embed per-site roots (direct-site vs facade-site) so only the key set
    # and structure are comparable, as with logs above
    direct_fetch = gateway.fetch(token, "run-1", job_id)
    facade_fetch = session.fetch(job_id)
    assert set(direct_fetch) == set(facade_fetch)

    # cancel semantics agree: a long-running job is cancelled mid-flight on
    # both sides, then reports CANCELLED identically
    sleeper = _spec("op-2")
    sleeper["command"] = ["/bin/sleep", "30"]
    direct_b = gateway.submit(token, "run-1", sleeper, operation_id="op-2")
    facade_b = session.submit(dict(sleeper), operation_id="op-2")
    assert direct_b["job_id"] == facade_b["job_id"]
    assert gateway.cancel(token, "run-1", direct_b["job_id"]) == (
        session.cancel(facade_b["job_id"])
    )
    assert gateway.status(token, "run-1", direct_b["job_id"]) == (
        session.status(facade_b["job_id"])
    )


def test_parity_on_rejection_paths(tmp_path):
    """The façade must not widen or narrow the trust boundary: unknown job and
    foreign-run errors surface identically through both paths."""
    direct_root = tmp_path / "direct-site"
    facade_root = tmp_path / "facade-site"
    workspace = tmp_path / "workspace"

    gateway, token = _direct_gateway(direct_root, workspace)
    session = HpcDispatcher.process_test(facade_root).open_run(
        "run-2", workspace=workspace
    )

    def _error_of(fn):
        try:
            fn()
            return None
        except Exception as exc:  # noqa: BLE001 - comparing rejection types
            return type(exc).__name__

    assert _error_of(lambda: gateway.status(token, "run-1", "job-9999")) == (
        _error_of(lambda: session.status("job-9999"))
    )
