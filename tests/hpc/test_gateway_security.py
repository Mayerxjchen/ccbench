"""Adversarial gateway security: containment, job ownership, operation identity.

The Candidate can stage only what already lives in its run workspace (never an
arbitrary gateway-host path), can never touch another run's jobs, and
re-submitting the same logical operation returns the prior job instead of
double-scheduling.  Every trust-boundary mutation lands in the hash-chained
GatewayAudit; the common HTTP binding enforces the same rules over the wire.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from bench.hpc.audit import GatewayAudit
from bench.hpc.gateway import ALL_OPS, Gateway, GatewayError
from bench.hpc.http_server import HttpGatewayServer

DIGEST = "img@sha256:" + "a" * 64


def valid_job_spec(**overrides) -> dict:
    spec = {
        "schema_version": 1,
        "idempotency_key": "key-1",
        "runtime": DIGEST,
        "command": ["/bin/echo", "ok"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": [],
    }
    spec.update(overrides)
    return spec


class _OwnedAdapter:
    """Adapter whose job ownership is keyed by (run, operation) identity."""

    def __init__(self):
        self.submits: list[dict] = []
        self._jobs: dict[str, dict] = {}
        self._idem: dict[str, str] = {}
        self._ops: dict[tuple[str, str], str] = {}
        self._seq = 0

    def capabilities(self):
        return {"adapter": "fake", "supports_cancel": True}

    def submit(self, spec, *, run_id, operation_id):
        self.submits.append(spec)
        key = spec["idempotency_key"]
        if key in self._idem:
            return {"job_id": self._idem[key], "duplicate": True}
        prior = self._ops.get((run_id, operation_id))
        if prior is not None:
            return {"job_id": prior, "duplicate": True}
        self._seq += 1
        job_id = f"job-{self._seq:04d}"
        self._jobs[job_id] = {"state": "SUCCEEDED", "exit_code": 0}
        self._idem[key] = job_id
        self._ops[(run_id, operation_id)] = job_id
        return {"job_id": job_id, "duplicate": False}

    def find_by_operation_id(self, run_id, operation_id):
        return self._ops.get((run_id, operation_id))

    def status(self, job_id):
        return {"job_id": job_id, **self._jobs[job_id]}

    def logs(self, job_id):
        return {"job_id": job_id, "stdout": "", "stderr": ""}

    def fetch(self, job_id):
        return {"job_id": job_id, "files": []}

    def cancel(self, job_id):
        self._jobs[job_id]["state"] = "CANCELLED"
        return {"job_id": job_id, "state": "CANCELLED"}

    def usage(self):
        return {"jobs": len(self._jobs)}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace" / "run-1"
    ws.mkdir(parents=True)
    (ws / "input.dat").write_text("staged")
    (ws / "nested").mkdir()
    (ws / "nested" / "extra.dat").write_text("nested")
    return ws


@pytest.fixture
def adapter() -> _OwnedAdapter:
    return _OwnedAdapter()


@pytest.fixture
def audit(tmp_path: Path) -> GatewayAudit:
    return GatewayAudit(tmp_path / "audit.jsonl")


@pytest.fixture
def gateway(workspace, adapter, audit) -> Gateway:
    return Gateway(adapter, workspace_root=workspace, audit=audit)


@pytest.fixture
def token(gateway: Gateway) -> str:
    return gateway.issue("run-1", ALL_OPS)


# -- Step 1: adversarial path + ownership rejection -------------------------


@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "/Users/example/.ssh/config",
    "/app/../secret",
    "../other-run",
])
def test_gateway_rejects_non_workspace_paths(gateway, token, path):
    spec = valid_job_spec(inputs=[{"source": path, "destination": "input.dat"}])
    with pytest.raises(GatewayError, match="workspace"):
        gateway.submit(token, "run-1", spec, operation_id="OP-ws-reject")


def test_token_cannot_touch_other_run_job(gateway, token):
    job = gateway.submit(token, "run-1", valid_job_spec(), operation_id="OP-job-touch")
    run_b = gateway.issue("run-b", ALL_OPS)
    with pytest.raises(GatewayError, match="not part of run"):
        gateway.status(run_b, "run-b", job["job_id"])


# -- Step 2: canonical containment -----------------------------------------


def test_symlink_input_rejected(gateway, token, workspace):
    link = workspace / "link.dat"
    link.symlink_to(workspace / "input.dat")
    spec = valid_job_spec(inputs=[{"source": str(link), "destination": "input.dat"}])
    with pytest.raises(GatewayError, match="symlink"):
        gateway.submit(token, "run-1", spec, operation_id="OP-symlink-reject")


def test_contained_input_is_accepted(gateway, token, workspace):
    spec = valid_job_spec(inputs=["input.dat"])
    result = gateway.submit(token, "run-1", spec, operation_id="OP-contained")
    assert result["duplicate"] is False


def test_nested_contained_input_is_accepted(gateway, token, workspace):
    spec = valid_job_spec(inputs=["nested/extra.dat"])
    assert gateway.submit(token, "run-1", spec, operation_id="OP-nested")["duplicate"] is False


def test_containment_without_workspace_root_fails_closed(adapter):
    """A gateway with no run workspace root must not accept bound inputs."""
    gw = Gateway(adapter)
    tok = gw.issue("run-1", ALL_OPS)
    spec = valid_job_spec(inputs=[{"source": "/tmp", "destination": "input.dat"}])
    with pytest.raises(GatewayError, match="workspace"):
        gw.submit(tok, "run-1", spec, operation_id="OP-no-root")


# -- Step 3: scheduler operation identity ----------------------------------


def test_same_operation_id_returns_prior_job(gateway, token, adapter):
    first = gateway.submit(token, "run-1", valid_job_spec(), operation_id="OP-1")
    second = gateway.submit(
        token, "run-1", valid_job_spec(idempotency_key="key-2"), operation_id="OP-1"
    )
    assert first["job_id"] == second["job_id"]
    assert second["duplicate"] is True
    assert len(adapter.submits) == 1  # launched once, never twice


def test_operation_identity_survives_gateway_restart(workspace, adapter):
    """After a gateway crash its in-memory idempotency map is gone; the adapter
    still reconciles the same operation to the previously scheduled job."""
    first_gw = Gateway(adapter, workspace_root=workspace)
    tok1 = first_gw.issue("run-1", ALL_OPS)
    first = first_gw.submit(tok1, "run-1", valid_job_spec(), operation_id="OP-1")

    revived = Gateway(adapter, workspace_root=workspace)
    tok2 = revived.issue("run-1", ALL_OPS)
    second = revived.submit(tok2, "run-1", valid_job_spec(), operation_id="OP-1")
    assert second["job_id"] == first["job_id"]
    assert second["duplicate"] is True


def test_operation_identity_is_run_scoped(gateway, token):
    job_a = gateway.submit(token, "run-1", valid_job_spec(), operation_id="OP-1")
    run_b = gateway.issue("run-b", ALL_OPS)
    job_b = gateway.submit(
        run_b, "run-b", valid_job_spec(idempotency_key="key-b"), operation_id="OP-1"
    )
    assert job_a["job_id"] != job_b["job_id"]
    assert job_b["duplicate"] is False


def test_malformed_operation_id_rejected(gateway, token):
    with pytest.raises(GatewayError, match="operation_id"):
        gateway.submit(token, "run-1", valid_job_spec(), operation_id="bad op; rm -rf")


# -- GatewayAudit -----------------------------------------------------------


def test_audit_chains_entries(audit):
    d1 = audit.append({"kind": "token_issued", "run_id": "run-1"})
    d2 = audit.append({"kind": "submit", "run_id": "run-1", "job_id": "job-0001"})
    entries = audit.entries()
    assert entries[1]["prev"] == d1
    assert d1 != d2
    assert audit.verify() == []


def test_audit_chain_detects_file_tampering(tmp_path):
    path = tmp_path / "audit.jsonl"
    GatewayAudit(path).append({"kind": "submit", "job_id": "job-0001"})
    GatewayAudit(path).append({"kind": "cancel", "job_id": "job-0001"})
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["event"]["job_id"] = "job-0002"  # attacker rewrites the first entry
    path.write_text(
        "\n".join(
            json.dumps(entry, sort_keys=True)
            for entry in [first, json.loads(lines[1])]
        )
        + "\n"
    )
    reloaded = GatewayAudit(path)
    assert reloaded.verify() != []


def test_gateway_appends_audit_events(gateway, token, audit):
    gateway.submit(token, "run-1", valid_job_spec(), operation_id="OP-1")
    kinds = [e["event"]["kind"] for e in audit.entries()]
    assert kinds == ["token_issued", "submit"]
    assert audit.verify() == []


# -- common HTTP binding ----------------------------------------------------


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _post(url: str, token: str, op: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url + f"/{op}",
        data=json.dumps(payload).encode("utf-8"),
        headers=_headers(token),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_http_submit_and_status(gateway, token):
    server = HttpGatewayServer(gateway)
    url = server.start()
    try:
        job = _post(url, token, "submit", {
            "run_id": "run-1", "operation_id": "OP-1", "spec": valid_job_spec(),
        })
        assert job["duplicate"] is False
        state = _post(url, token, "status", {
            "run_id": "run-1", "job_id": job["job_id"],
        })
        assert state["state"] == "SUCCEEDED"
        dup = _post(url, token, "submit", {
            "run_id": "run-1", "operation_id": "OP-1", "spec": valid_job_spec(),
        })
        assert dup["job_id"] == job["job_id"]
        assert dup["duplicate"] is True
    finally:
        server.close()


def test_http_wrong_token_rejected(gateway):
    server = HttpGatewayServer(gateway)
    url = server.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(url, "not-the-token", "status", {
                "run_id": "run-1", "job_id": "job-0001",
            })
        assert exc.value.code == 401
    finally:
        server.close()


def test_http_unknown_path_404(gateway, token):
    server = HttpGatewayServer(gateway)
    url = server.start()
    try:
        req = urllib.request.Request(
            url + "/nope",
            data=b"{}",
            headers=_headers(token),
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 404
    finally:
        server.close()


def test_http_missing_run_id_400(gateway, token):
    server = HttpGatewayServer(gateway)
    url = server.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(url, token, "status", {"job_id": "job-0001"})
        assert exc.value.code == 400
    finally:
        server.close()


def test_http_escape_path_rejected(gateway, token):
    server = HttpGatewayServer(gateway)
    url = server.start()
    try:
        spec = valid_job_spec(
            inputs=[{"source": "/etc/passwd", "destination": "input.dat"}]
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(url, token, "submit", {"run_id": "run-1", "operation_id": "OP-1", "spec": spec})
        body = json.loads(exc.value.read().decode("utf-8"))
        assert "workspace" in body["error"]
    finally:
        server.close()
