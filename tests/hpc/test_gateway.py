"""Trust boundary: the Gateway owns tokens, quotas, and job ownership.

The Candidate never talks to the scheduler; it talks only to the Gateway with
a run-scoped bearer token. These tests pin the rejection cases a misbehaving
or mis-scoped run must hit:
"""

from __future__ import annotations

import pytest

from dftworld_bench.hpc.gateway import ALL_OPS, Gateway, GatewayError

DIGEST = "img@sha256:" + "a" * 64


def _spec(key: str, *, cpus: int = 1) -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": DIGEST,
        "command": ["/bin/echo", "ok"],
        "resources": {"cpus": cpus, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": [],
    }


class _FakeAdapter:
    """Adapter with a tiny state machine; fetch/cancel enforce terminal rules."""

    TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT", "LOST")

    def __init__(self):
        self.submits: list[dict] = []
        self._jobs: dict[str, dict] = {}
        self._idem: dict[str, str] = {}
        self._ops: dict[tuple[str, str], str] = {}

    def capabilities(self):
        return {"adapter": "fake", "states": list(self.TERMINAL), "supports_cancel": True}

    def find_by_operation_id(self, run_id, operation_id):
        return self._ops.get((run_id, operation_id))

    def submit(self, spec, *, run_id, operation_id):
        self.submits.append(spec)
        key = spec["idempotency_key"]
        if key in self._idem:
            return {"job_id": self._idem[key], "duplicate": True}
        prior = self._ops.get((run_id, operation_id))
        if prior is not None:
            return {"job_id": prior, "duplicate": True}
        job_id = f"job-{len(self._jobs) + 1}"
        self._jobs[job_id] = {"state": "SUCCEEDED", "exit_code": 0}
        self._idem[key] = job_id
        self._ops[(run_id, operation_id)] = job_id
        return {"job_id": job_id, "duplicate": False}

    def status(self, job_id):
        return {"job_id": job_id, **self._jobs[job_id]}

    def logs(self, job_id):
        return {"job_id": job_id, "stdout": "", "stderr": ""}

    def fetch(self, job_id):
        if self._jobs[job_id]["state"] != "SUCCEEDED":
            raise _FakeAdapter._TerminalRule("fetch before success")
        return {"job_id": job_id, "files": []}

    def cancel(self, job_id):
        if self._jobs[job_id]["state"] in self.TERMINAL:
            raise _FakeAdapter._TerminalRule("cancel after terminal state")
        self._jobs[job_id]["state"] = "CANCELLED"
        return {"job_id": job_id, "state": "CANCELLED"}

    def usage(self):
        return {"jobs": len(self._jobs), "submitted": len(self.submits)}

    def set_state(self, job_id, state):
        self._jobs[job_id]["state"] = state

    class _TerminalRule(Exception):
        pass


def _gateway(**kwargs):
    adapter = _FakeAdapter()
    gateway = Gateway(adapter, quota=kwargs.get("quota"), now=kwargs.get("now"))
    return gateway, adapter


def _token(gateway: Gateway, run: str = "run-A", ops=ALL_OPS, ttl: float = 60) -> str:
    return gateway.issue(run, ops, ttl_sec=ttl)


def test_authorize_valid_token() -> None:
    gateway, _ = _gateway()
    tok = _token(gateway)
    cap = gateway.authorize(tok, "run-A", "status")
    assert cap.run_id == "run-A"
    assert "status" in cap.operations


def test_token_run_mismatch_rejected() -> None:
    gateway, _ = _gateway()
    tok = _token(gateway, run="run-A")
    with pytest.raises(GatewayError, match="run"):
        gateway.authorize(tok, "run-B", "status")


def test_expired_token_rejected() -> None:
    clock = [0.0]

    def now():
        return clock[0]

    gateway, _ = _gateway(now=now)
    tok = gateway.issue("run-A", ALL_OPS, ttl_sec=10)
    gateway.authorize(tok, "run-A", "status")  # t=0, valid
    clock[0] = 11.0
    with pytest.raises(GatewayError, match="expired"):
        gateway.authorize(tok, "run-A", "status")


def test_revoked_token_rejected() -> None:
    gateway, _ = _gateway()
    tok = _token(gateway)
    gateway.revoke(tok)
    with pytest.raises(GatewayError, match="token"):
        gateway.authorize(tok, "run-A", "status")


def test_operation_outside_scope_rejected() -> None:
    gateway, _ = _gateway()
    tok = _token(gateway, ops=("capabilities",))
    gateway.capabilities(tok, "run-A")  # in scope
    with pytest.raises(GatewayError, match="scope"):
        gateway.status(tok, "run-A", "job-1")


def test_duplicate_idempotency_key_returns_original() -> None:
    gateway, adapter = _gateway()
    tok = _token(gateway)
    first = gateway.submit(tok, "run-A", _spec("key-1"), operation_id="OP-dup-1")
    second = gateway.submit(tok, "run-A", _spec("key-1"), operation_id="OP-dup-2")
    assert first["job_id"] == second["job_id"]
    assert second["duplicate"] is True
    assert len(adapter.submits) == 1  # launched once, never twice


def test_path_access_to_another_run_rejected() -> None:
    gateway, _ = _gateway()
    tok_a = _token(gateway, run="run-A")
    tok_b = _token(gateway, run="run-B")
    submitted = gateway.submit(tok_a, "run-A", _spec("key-a"), operation_id="OP-path")
    with pytest.raises(GatewayError, match="run"):
        gateway.status(tok_b, "run-B", submitted["job_id"])


def test_quota_excess_rejected() -> None:
    gateway, _ = _gateway(
        quota={"max_cpus": 2, "max_memory_gb": 8, "max_walltime_minutes": 60}
    )
    tok = _token(gateway)
    gateway.submit(tok, "run-A", _spec("key-1", cpus=2), operation_id="OP-quota-1")
    with pytest.raises(GatewayError, match="quota"):
        gateway.submit(tok, "run-A", _spec("key-2", cpus=1), operation_id="OP-quota-2")  # 2 + 1 > 2


def test_cancel_after_terminal_state_rejected() -> None:
    gateway, adapter = _gateway()
    tok = _token(gateway)
    submitted = gateway.submit(tok, "run-A", _spec("key-1"), operation_id="OP-cancel")
    adapter.set_state(submitted["job_id"], "SUCCEEDED")
    with pytest.raises(GatewayError, match="terminal"):
        gateway.cancel(tok, "run-A", submitted["job_id"])


def test_fetch_before_success_rejected() -> None:
    gateway, adapter = _gateway()
    tok = _token(gateway)
    submitted = gateway.submit(tok, "run-A", _spec("key-1"), operation_id="OP-fetch")
    adapter.set_state(submitted["job_id"], "RUNNING")
    with pytest.raises(GatewayError, match="before success"):
        gateway.fetch(tok, "run-A", submitted["job_id"])


def test_usage_ledger_tracks_submissions() -> None:
    gateway, _ = _gateway()
    tok = _token(gateway)
    gateway.submit(tok, "run-A", _spec("key-1"), operation_id="OP-usage-1")
    gateway.submit(tok, "run-A", _spec("key-2"), operation_id="OP-usage-2")
    usage = gateway.usage(tok, "run-A")
    assert usage["jobs"] == 2


def test_submit_without_submit_scope_rejected() -> None:
    gateway, _ = _gateway()
    tok = _token(gateway, ops=("capabilities",))
    with pytest.raises(GatewayError, match="scope"):
        gateway.submit(tok, "run-A", _spec("key-1"), operation_id="OP-scope")
