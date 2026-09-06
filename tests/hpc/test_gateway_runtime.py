"""GatewayRuntime: per-run lease lifecycle.

One run gets one token + one pair of network names. Closing the lease revokes
the token and tears down the networks idempotently — a second close is a no-op.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ccbench.hpc.gateway import ALL_OPS, GatewayError
from ccbench.hpc.gateway_runtime import GatewayRuntime, GatewayRuntimeError


def _config(root: Path) -> dict:
    return {"adapter": "process_test", "root": str(root), "timeout_sec": 5.0}


def _fake_networks(run_id: str):
    return (f"net-{run_id}-cand", f"net-{run_id}-egress")


def test_start_issues_run_scoped_lease(tmp_path: Path) -> None:
    runtime = GatewayRuntime(networks_factory=_fake_networks)
    lease = runtime.start("run-1", _config(tmp_path))
    assert lease.run_id == "run-1"
    assert lease.token
    assert lease.networks == ("net-run-1-cand", "net-run-1-egress")
    cap = lease.gateway.authorize(lease.token, "run-1", "submit")
    assert cap.run_id == "run-1"
    assert set(ALL_OPS) <= cap.operations


def test_close_revokes_token_and_removes_networks(tmp_path: Path) -> None:
    runtime = GatewayRuntime(networks_factory=_fake_networks)
    lease = runtime.start("run-1", _config(tmp_path))
    lease.close()
    assert lease.closed is True
    assert lease.networks == ()
    with pytest.raises(GatewayError):
        lease.gateway.authorize(lease.token, "run-1", "submit")


def test_close_is_idempotent(tmp_path: Path) -> None:
    runtime = GatewayRuntime(networks_factory=_fake_networks)
    lease = runtime.start("run-1", _config(tmp_path))
    lease.close()
    lease.close()  # second close must not raise
    assert lease.closed is True


def test_start_rejects_unknown_adapter(tmp_path: Path) -> None:
    runtime = GatewayRuntime(networks_factory=_fake_networks)
    with pytest.raises(GatewayRuntimeError, match="adapter"):
        runtime.start("run-1", {"adapter": "no-such-adapter"})


def test_start_rejects_bare_runtime_lock_dir_without_catalog(tmp_path: Path) -> None:
    """Runtime locks are not authority; only the composition root may inject a Catalog."""
    runtime = GatewayRuntime(networks_factory=_fake_networks)
    with pytest.raises(GatewayRuntimeError, match="runtime_lock_dir alone"):
        runtime.start(
            "run-1",
            {**_config(tmp_path), "runtime_lock_dir": str(tmp_path / "locks")},
        )


def test_second_lease_for_same_run_replaces_first(tmp_path: Path) -> None:
    runtime = GatewayRuntime(networks_factory=_fake_networks)
    first = runtime.start("run-1", _config(tmp_path))
    second = runtime.start("run-1", _config(tmp_path))
    assert first.token != second.token
    with pytest.raises(GatewayError):
        first.gateway.authorize(first.token, "run-1", "submit")
    assert second.gateway.authorize(second.token, "run-1", "submit").run_id == "run-1"


def test_runtime_passes_workspace_root_to_gateway(tmp_path: Path) -> None:
    """The run workspace root flows into the gateway so bound inputs are
    contained; a host-absolute path is rejected at the lease gateway."""
    runtime = GatewayRuntime(networks_factory=_fake_networks)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "input.dat").write_text("staged")
    lease = runtime.start("run-1", {**_config(tmp_path), "workspace_root": str(ws)})

    spec = {
        "schema_version": 1,
        "idempotency_key": "key-1",
        "runtime": "img@sha256:" + "a" * 64,
        "command": ["/bin/echo", "ok"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": ["input.dat"],
        "outputs": [],
    }
    job = lease.gateway.submit(lease.token, "run-1", spec, operation_id="OP-1")
    assert job["duplicate"] is False

    # A host-absolute path in inputs is rejected by the workspace containment.
    hostile = {**spec, "idempotency_key": "key-2",
               "inputs": ["/etc/passwd"]}
    with pytest.raises(GatewayError):
        lease.gateway.submit(lease.token, "run-1", hostile, operation_id="OP-2")
