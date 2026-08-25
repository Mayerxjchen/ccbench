"""Fake-adapter matrix: all five HPC cases through the generic stack.

Full chain under test per case:

    CaseSpec → RuntimeRegistry → HpcExecutor → GatewayRuntime →
    HttpGatewayServer → ProcessTestAdapter → submit/status/fetch/settle → token revoke

Additional negative assertions:
- No Docker, no API, no SSH/Slurm, no CASE_POLICIES, no hidden solution.
- Same operation_id generates only one job (idempotency).
- One Executor close does not affect another run.

All tests run against the ProcessTestAdapter; no real cluster access.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path

import pytest

from dftworld_bench.contracts.case import CaseSpec
from dftworld_bench.executors import HpcExecutor, resolve
from dftworld_bench.executors.base import ExecutionContext
from dftworld_bench.hpc.gateway_runtime import GatewayRuntime

ROOT = Path(__file__).resolve().parents[2]
HPC_CASE_PREFIXES = ("031-", "032-", "033-", "034-", "042-")


def _find_case(prefix: str) -> Path:
    matches = sorted(p for p in ROOT.iterdir() if p.name.startswith(prefix))
    assert len(matches) == 1, f"expected exactly one {prefix}* case, got {matches}"
    return matches[0]


def _dispatcher():
    """A process-test dispatcher as the composition root would inject."""
    from dftworld_bench.hpc.dispatcher import HpcDispatcher
    from dftworld_bench.hpc.gateway_runtime import GatewayRuntime

    return HpcDispatcher(GatewayRuntime(), {})


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_context(spec: CaseSpec, tmp: Path, run_id: str) -> ExecutionContext:
    """Build an ExecutionContext with a ProcessTestAdapter config."""
    workspace = tmp / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    adapter_root = tmp / "adapter"
    adapter_root.mkdir(parents=True, exist_ok=True)
    return ExecutionContext(
        task=spec,
        model="test-model",
        max_turns=32,
        threads_root=tmp,
        adapter_config={
            "adapter": "process_test",
            "root": str(adapter_root),
            "workspace_root": str(workspace),
        },
        run_id=run_id,
        workspace=workspace,
    )


def _submit_and_fetch(executor, ctx, *, operation_id: str = "op-test-001"):
    """Full lifecycle: submit → poll → fetch through the HTTP gateway."""
    token = ctx.container_env["BENCH_HPC_RUN_TOKEN"]
    url = ctx.container_env["BENCH_HPC_GATEWAY_URL"]
    # The container env uses host.docker.internal; the actual server is on 127.0.0.1
    real_url = url.replace("host.docker.internal", "127.0.0.1")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Submit
    spec = {
        "schema_version": 1,
        "idempotency_key": f"idem-{operation_id}",
        "runtime": "test-image@sha256:" + "a" * 64,
        "command": ["echo", "hello from fake adapter"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 1},
        "inputs": [],
        "outputs": [],
    }
    body = json.dumps({"run_id": ctx.run_id, "spec": spec, "operation_id": operation_id}).encode()
    req = urllib.request.Request(f"{real_url}/submit", data=body, headers=headers, method="POST")
    resp = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
    job_id = resp["job_id"]

    # Poll until terminal
    for _ in range(20):
        body = json.dumps({"run_id": ctx.run_id, "job_id": job_id}).encode()
        req = urllib.request.Request(f"{real_url}/status", data=body, headers=headers, method="POST")
        status = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        if status["state"] in ("SUCCEEDED", "FAILED", "TIMEOUT", "CANCELLED"):
            break

    # Fetch
    body = json.dumps({"run_id": ctx.run_id, "job_id": job_id}).encode()
    req = urllib.request.Request(f"{real_url}/fetch", data=body, headers=headers, method="POST")
    fetch = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
    return job_id, status, fetch


# ---- per-case tests ----

@pytest.mark.parametrize("prefix", HPC_CASE_PREFIXES)
def test_fake_adapter_full_lifecycle(prefix, tmp_path):
    """Each HPC case: submit → status → fetch → settle through the full stack."""
    spec = CaseSpec.load(_find_case(prefix))
    assert spec.execution_class == "hpc_controller"
    assert HpcExecutor(dispatcher=_dispatcher()) is not None

    ctx = _make_context(spec, tmp_path, f"run-{prefix[:3]}")
    executor = HpcExecutor(dispatcher=_dispatcher())
    try:
        _run(executor.prepare(ctx))

        job_id, status, fetch = _submit_and_fetch(executor, ctx)
        assert status["state"] == "SUCCEEDED"
        assert fetch["state"] == "SUCCEEDED"
        assert job_id.startswith("job-")
    finally:
        _run(executor.close(ctx))


@pytest.mark.parametrize("prefix", HPC_CASE_PREFIXES)
def test_fake_adapter_token_revoked_on_close(prefix, tmp_path):
    """After executor.close(), the gateway server is fully shut down."""
    spec = CaseSpec.load(_find_case(prefix))
    ctx = _make_context(spec, tmp_path, f"run-revoke-{prefix[:3]}")
    executor = HpcExecutor(dispatcher=_dispatcher())
    _run(executor.prepare(ctx))

    token = ctx.container_env["BENCH_HPC_RUN_TOKEN"]
    url = ctx.container_env["BENCH_HPC_GATEWAY_URL"].replace("host.docker.internal", "127.0.0.1")
    _run(executor.close(ctx))

    # Server fully shut down — connection refused, not 403
    body = json.dumps({"run_id": ctx.run_id}).encode()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    req = urllib.request.Request(f"{url}/capabilities", data=body, headers=headers, method="POST")
    with pytest.raises((urllib.error.URLError, ConnectionRefusedError, OSError)):
        urllib.request.urlopen(req, timeout=5)


@pytest.mark.parametrize("prefix", HPC_CASE_PREFIXES)
def test_fake_adapter_token_revoked_returns_403(prefix, tmp_path):
    """Explicitly revoking a token while server is alive produces 401."""
    spec = CaseSpec.load(_find_case(prefix))
    ctx = _make_context(spec, tmp_path, f"run-revoke403-{prefix[:3]}")
    executor = HpcExecutor(dispatcher=_dispatcher())
    try:
        _run(executor.prepare(ctx))

        token = ctx.container_env["BENCH_HPC_RUN_TOKEN"]
        url = ctx.container_env["BENCH_HPC_GATEWAY_URL"].replace("host.docker.internal", "127.0.0.1")

        # Revoke the token via the gateway object directly
        ctx.gateway_server._gateway.revoke(token)

        # Now request with the revoked token → 403
        body = json.dumps({"run_id": ctx.run_id}).encode()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        req = urllib.request.Request(f"{url}/capabilities", data=body, headers=headers, method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code in (401, 403)  # auth rejection
    finally:
        _run(executor.close(ctx))


@pytest.mark.parametrize("prefix", HPC_CASE_PREFIXES)
def test_same_operation_id_generates_one_job(prefix, tmp_path):
    """Submitting the same operation_id twice returns the same job_id."""
    spec = CaseSpec.load(_find_case(prefix))
    ctx = _make_context(spec, tmp_path, f"run-idem-{prefix[:3]}")
    executor = HpcExecutor(dispatcher=_dispatcher())
    try:
        _run(executor.prepare(ctx))
        token = ctx.container_env["BENCH_HPC_RUN_TOKEN"]
        url = ctx.container_env["BENCH_HPC_GATEWAY_URL"].replace("host.docker.internal", "127.0.0.1")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        spec_body = {
            "schema_version": 1,
            "idempotency_key": "idem-dup-test",
            "runtime": "test-image@sha256:" + "a" * 64,
            "command": ["echo", "first"],
            "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 1},
            "inputs": [],
            "outputs": [],
        }
        op_id = "op-dup-test"
        body = json.dumps({"run_id": ctx.run_id, "spec": spec_body, "operation_id": op_id}).encode()
        req = urllib.request.Request(f"{url}/submit", data=body, headers=headers, method="POST")
        r1 = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())

        body = json.dumps({"run_id": ctx.run_id, "spec": spec_body, "operation_id": op_id}).encode()
        req = urllib.request.Request(f"{url}/submit", data=body, headers=headers, method="POST")
        r2 = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())

        assert r1["job_id"] == r2["job_id"]
        assert r2["duplicate"] is True
    finally:
        _run(executor.close(ctx))


def test_two_executors_are_independent(tmp_path):
    """Closing one executor does not affect another run's gateway."""
    prefix = "031-"
    spec = CaseSpec.load(_find_case(prefix))

    ctx1 = _make_context(spec, tmp_path / "run1", "run-independent-1")
    ctx2 = _make_context(spec, tmp_path / "run2", "run-independent-2")
    exec1 = HpcExecutor(dispatcher=_dispatcher())
    exec2 = HpcExecutor(dispatcher=_dispatcher())
    try:
        _run(exec1.prepare(ctx1))
        _run(exec2.prepare(ctx2))

        # Both can submit
        _submit_and_fetch(exec1, ctx1, operation_id="op-e1")
        _submit_and_fetch(exec2, ctx2, operation_id="op-e2")

        # Close exec1 — exec2 still works
        _run(exec1.close(ctx1))
        token2 = ctx2.container_env["BENCH_HPC_RUN_TOKEN"]
        url2 = ctx2.container_env["BENCH_HPC_GATEWAY_URL"].replace("host.docker.internal", "127.0.0.1")
        body = json.dumps({"run_id": ctx2.run_id}).encode()
        headers = {"Authorization": f"Bearer {token2}", "Content-Type": "application/json"}
        req = urllib.request.Request(f"{url2}/capabilities", data=body, headers=headers, method="POST")
        resp = json.loads(urllib.request.urlopen(req, timeout=5).read().decode())
        assert resp.get("adapter") == "process_test" or "capabilities" in resp
    finally:
        _run(exec1.close(ctx1))
        _run(exec2.close(ctx2))


# ---- negative assertions ----

def test_no_docker_no_ssh_no_slurm(tmp_path):
    """The fake adapter path never imports Docker, SSH, or Slurm modules."""
    import dftworld_bench.hpc.adapters.process_test as mod
    src = mod.__file__
    content = Path(src).read_text(encoding="utf-8")
    assert "import docker" not in content
    assert "import paramiko" not in content
    assert "import slurm" not in content.lower() or "slurm" not in content.split("import")[0]


def test_no_case_policies_in_gateway_runtime():
    """GatewayRuntime never references CASE_POLICIES."""
    import inspect
    from dftworld_bench.hpc import gateway_runtime as mod
    src = inspect.getsource(mod)
    assert "CASE_POLICIES" not in src
    assert "case_policies" not in src.lower()


def test_no_hidden_solution_in_adapter():
    """ProcessTestAdapter never stages or references hidden solution/."""
    import inspect
    from dftworld_bench.hpc.adapters import process_test as mod
    src = inspect.getsource(mod)
    assert "solution/" not in src
    assert "hidden" not in src.lower()
