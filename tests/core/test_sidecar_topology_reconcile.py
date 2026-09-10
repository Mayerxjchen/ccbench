import asyncio
import json

import pytest

from bench.core.sidecar_topology import (
    CANDIDATE_ROLE_LABEL,
    NETWORK_ROLE_LABEL,
    SIDECAR_ROLE_LABEL,
    SidecarTopologyManager,
    TopologyCleanupError,
    validate_resource_run_uid,
)


def test_resource_uid_is_strict():
    uid = "0123456789abcdef" * 2
    assert validate_resource_run_uid(uid) == uid
    for value in ("short", "../" + "a" * 30, "A" * 32, "a" * 31 + "!"):
        with pytest.raises(ValueError):
            validate_resource_run_uid(value)


def test_reconcile_uses_exact_labels_and_dependency_order(monkeypatch):
    uid = "b" * 32
    role_values = {
        "candidate-agent": "candidate-id",
        "model-gateway-sidecar": "sidecar-id",
        "internal-network": "network-id",
    }
    labels = {
        "candidate-id": {"bench.run_id": uid, "bench.role": "candidate-agent"},
        "sidecar-id": {"bench.run_id": uid, "bench.role": "model-gateway-sidecar"},
        "network-id": {"bench.run_id": uid, "bench.role": "internal-network"},
    }
    commands: list[list[str]] = []
    deleted: list[str] = []
    active = set(labels)

    async def fake_run(self, cmd):
        commands.append(cmd)
        if cmd[:3] == ["docker", "ps", "-aq"]:
            role = next(x.rsplit("=", 1)[1] for x in cmd if x.startswith("label=bench.role="))
            rid = role_values[role]
            return (0, rid + "\n" if rid in active else "", "")
        if cmd[:3] == ["docker", "network", "ls"]:
            rid = role_values["internal-network"]
            return (0, rid + "\n" if rid in active else "", "")
        if cmd[:2] == ["docker", "inspect"]:
            rid = cmd[-1]
            return (0, json.dumps(labels[rid]), "")
        if cmd[:3] == ["docker", "rm", "-f"]:
            rid = cmd[-1]
            active.discard(rid)
            deleted.append(rid)
            return (0, "", "")
        if cmd[:3] == ["docker", "network", "rm"]:
            rid = cmd[-1]
            active.discard(rid)
            deleted.append(rid)
            return (0, "", "")
        raise AssertionError(cmd)

    monkeypatch.setattr(SidecarTopologyManager, "_run_cmd", fake_run)
    manager = SidecarTopologyManager(image="unused", run_uid=uid)
    result = asyncio.run(manager.reconcile_run())
    assert result["cleanup_ok"] is True
    assert deleted == ["candidate-id", "sidecar-id", "network-id"]
    assert not any("prune" in arg or arg in {"-v", "--volume", "--network=host"} for cmd in commands for arg in cmd)
    # A second recovery is a no-op and cannot touch another run.
    second = asyncio.run(manager.reconcile_run())
    assert second["remaining"] == {}


def test_reconcile_refuses_mismatched_inspected_label(monkeypatch):
    uid = "c" * 32

    async def fake_run(self, cmd):
        if cmd[:3] == ["docker", "ps", "-aq"]:
            return 0, "other-id\n", ""
        if cmd[:3] == ["docker", "network", "ls"]:
            return 0, "", ""
        if cmd[:2] == ["docker", "inspect"]:
            return 0, json.dumps({"bench.run_id": "d" * 32, "bench.role": "candidate-agent"}), ""
        raise AssertionError(cmd)

    monkeypatch.setattr(SidecarTopologyManager, "_run_cmd", fake_run)
    with pytest.raises(TopologyCleanupError, match="unverified"):
        asyncio.run(SidecarTopologyManager(image="unused", run_uid=uid).reconcile_run())
