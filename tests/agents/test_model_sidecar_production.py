"""Integration and fault-injection tests for production Model Gateway Sidecar and topology manager."""

from __future__ import annotations

import asyncio
import http.server
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any
import pytest

from dftworld_bench.agents import ClaudeCodeAdapter
from dftworld_bench.core.budgets import BUDGET_DOMAINS, BudgetLedger, BudgetPolicy
from dftworld_bench.core.model_proxy import ModelGatewayProxy
from dftworld_bench.core.sidecar_topology import (
    SidecarTopologyManager,
    TopologyCleanupError,
    TopologyError,
    TopologyRollbackError,
)

AGENT_IMAGE = "mlffbench-candidate-claude-code-sandbox:v1"
OVER_LIMIT_ATTACK_STRING = "CANARY_OVER_LIMIT_LEAK_PROBE_UNAUTHORIZED"


def _docker_available() -> bool:
    try:
        res = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5.0,
        )
        return res.returncode == 0
    except Exception:
        return False


class DummySSEHandler(http.server.BaseHTTPRequestHandler):
    req_count = 0

    def do_POST(self):
        DummySSEHandler.req_count += 1
        c = DummySSEHandler.req_count
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if c == 1:
            chunk = (
                b'event: message_start\r\ndata: {"type": "message_start", "message": {"usage": {"input_tokens": 15}}}\r\n\r\n'
                b'event: message_delta\r\ndata: {"type": "message_delta", "usage": {"output_tokens": 10}}\r\n\r\n'
            )
            self.wfile.write(chunk)
            self.wfile.flush()
        else:
            chunk1 = (
                b'event: message_start\r\ndata: {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}\r\n\r\n'
            )
            self.wfile.write(chunk1)
            self.wfile.flush()
            time.sleep(0.05)
            chunk2 = (
                f'event: message_delta\r\ndata: {{"type": "message_delta", "usage": {{"output_tokens": 30}}, "delta": {{"text": "{OVER_LIMIT_ATTACK_STRING}"}}}}\r\n\r\n'
            ).encode("utf-8")
            try:
                self.wfile.write(chunk2)
                self.wfile.flush()
            except Exception:
                pass

    def log_message(self, *args):
        pass


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not running")
def test_production_sidecar_topology_manager_lifecycle():
    """Verify Candidate internal network -> dual-homed sidecar -> host proxy managed by SidecarTopologyManager."""
    async def _test():
        async def run_cmd(cmd: list[str]) -> tuple[int, str, str]:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
            return proc.returncode or 0, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")

        DummySSEHandler.req_count = 0
        server = http.server.HTTPServer(("127.0.0.1", 0), DummySSEHandler)
        u_port = server.server_address[1]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        policy_limits = {d: 10_000 for d in BUDGET_DOMAINS}
        policy_limits["tokens"] = 50
        policy_limits["model_turns"] = 10
        ledger = BudgetLedger(BudgetPolicy(policy_limits))
        callback_called = []

        def on_exceeded():
            callback_called.append(True)

        proxy = ModelGatewayProxy(
            real_api_endpoint=f"http://127.0.0.1:{u_port}",
            real_api_key="sk-test-production-sidecar",
            max_total_tokens=50,
            budget_ledger=ledger,
            on_budget_exceeded=on_exceeded,
        )
        port = await proxy.start()
        token = proxy.ephemeral_token

        topology = SidecarTopologyManager(image=AGENT_IMAGE)
        net_name = await topology.create_network(prefix="test-net")

        try:
            sc_cid = await topology.start_sidecar(host_port=port, prefix="test-sidecar-")
            assert topology.sidecar_cid == sc_cid
            assert topology.network_name == net_name

            await asyncio.sleep(0.5)

            # 1. Verify candidate cannot bypass internal network
            _, stdout_bp, _ = await run_cmd([
                "docker", "run", "--rm",
                "--network", net_name,
                AGENT_IMAGE,
                "python3", "-c",
                '''
import socket
try:
    s = socket.create_connection(('1.1.1.1', 80), timeout=2)
    print("LEAK_CONNECTED")
except OSError:
    print("BLOCKED_OK")
''',
            ])
            assert "BLOCKED_OK" in stdout_bp

            # 2. Invalid token through sidecar -> 401
            _, stdout_401, _ = await run_cmd([
                "docker", "run", "--rm",
                "--network", net_name,
                AGENT_IMAGE,
                "curl", "-s", "-i",
                "-X", "POST",
                "-H", "x-api-key: bad-token",
                "-H", "content-type: application/json",
                "-d", "{}",
                "http://model-gateway:8080/v1/messages",
            ])
            assert "401 Unauthorized" in stdout_401 or "401" in stdout_401

            # 3. Request 1 within budget through sidecar (25 tokens)
            _, stdout_1, _ = await run_cmd([
                "docker", "run", "--rm",
                "--network", net_name,
                AGENT_IMAGE,
                "curl", "-s", "-i",
                "-X", "POST",
                "-H", f"x-api-key: {token}",
                "-H", "content-type: application/json",
                "-d", "{}",
                "http://model-gateway:8080/v1/messages",
            ])
            assert ledger.used("tokens") == 25

            # 4. Request 2 triggering budget exceeded (25 + 10 + 30 = 65 > 50)
            _, stdout_2, _ = await run_cmd([
                "docker", "run", "--rm",
                "--network", net_name,
                AGENT_IMAGE,
                "curl", "-s", "-i",
                "-X", "POST",
                "-H", f"x-api-key: {token}",
                "-H", "content-type: application/json",
                "-d", "{}",
                "http://model-gateway:8080/v1/messages",
            ])
            assert OVER_LIMIT_ATTACK_STRING not in stdout_2
            assert len(callback_called) > 0
            assert ledger.used("tokens") == 35

            # 5. Subsequent request -> 429
            _, stdout_3, _ = await run_cmd([
                "docker", "run", "--rm",
                "--network", net_name,
                AGENT_IMAGE,
                "curl", "-s", "-i",
                "-X", "POST",
                "-H", f"x-api-key: {token}",
                "-H", "content-type: application/json",
                "-d", "{}",
                "http://model-gateway:8080/v1/messages",
            ])
            assert "429 Too Many Requests" in stdout_3 or "Budget Exceeded" in stdout_3

        finally:
            await topology.close()
            await proxy.close()
            server.shutdown()

        # Check zero dangling resources
        assert topology.is_closed is True
        assert topology.network_name is None
        assert topology.sidecar_cid is None

        # Verify network is removed from host docker
        rc_net, stdout_net, _ = await run_cmd(["docker", "network", "ls", "-q", "--filter", f"name={net_name}"])
        assert rc_net == 0
        assert stdout_net.strip() == ""

    asyncio.run(_test())


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not running")
def test_sidecar_launch_failure_triggers_atomic_rollback():
    """Fault injection: sidecar launch failure must trigger atomic rollback of the internal network."""
    async def _test():
        topology = SidecarTopologyManager(image=AGENT_IMAGE)
        net_name = await topology.create_network(prefix="test-rollback-net")

        # Inject failure: supply an impossible docker arg that causes docker run to immediately exit non-zero
        with pytest.raises(TopologyError) as exc_info:
            await topology.start_sidecar(
                host_port=12345,
                prefix="test-sc-fail-",
                extra_docker_args=["--invalid-docker-flag-strictly-failing"],
            )

        assert "Failed to start model gateway sidecar" in str(exc_info.value)
        # Verify rollback wiped internal state
        assert topology.network_name is None
        assert topology.sidecar_cid is None

        # Verify network was cleaned up from Docker engine
        proc = await asyncio.create_subprocess_exec(
            "docker", "network", "ls", "-q", "--filter", f"name={net_name}",
            stdout=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        assert out.decode("utf-8").strip() == "", "Network was not rolled back upon sidecar failure!"

    asyncio.run(_test())


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not running")
def test_candidate_start_failure_triggers_adapter_rollback(tmp_path: Path):
    """Fault injection: candidate container start failure must rollback sidecar and network."""
    async def _test():
        case_dir = tmp_path / "case"
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "Dockerfile").write_text("FROM scratch\n")

        adapter = ClaudeCodeAdapter(
            model="test-model",
            threads_root=tmp_path / "threads",
            task_name="fail_task",
            case_dir=case_dir,
            image=AGENT_IMAGE,
            api_endpoint="http://127.0.0.1:9",
            api_key="test-key",
        )
        await adapter.prepare()

        # Inject failure into _start_container: make it fail
        async def _mock_failing_start(*args, **kwargs):
            raise RuntimeError("Injected Candidate Container Startup Crash")

        adapter._start_container = _mock_failing_start

        with pytest.raises(RuntimeError, match="Injected Candidate Container Startup Crash"):
            await adapter.start("test instruction")

        # Verify adapter cleaned up topology & proxy
        assert adapter._topology is None
        assert adapter.container_id is None
        assert adapter.sidecar_cid is None
        assert adapter.internal_net is None
        assert adapter._model_proxy is None

    asyncio.run(_test())


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not running")
def test_topology_close_idempotency():
    """Verify close() is safe to call multiple times without side effects or errors."""
    async def _test():
        topology = SidecarTopologyManager(image=AGENT_IMAGE)
        await topology.create_network(prefix="test-idem-net")
        # Call close multiple times
        await topology.close()
        await topology.close()
        await topology.close()
        assert topology.is_closed is True
        assert topology.network_name is None
        assert topology.sidecar_cid is None

    asyncio.run(_test())


@pytest.mark.skipif(not _docker_available(), reason="Docker daemon not running")
def test_topology_close_error_propagation():
    """Verify teardown errors are collected and raised as TopologyCleanupError rather than swallowed."""
    async def _test():
        topology = SidecarTopologyManager(image=AGENT_IMAGE)
        # Register a fake CID that cannot be stopped cleanly (or command fails)
        async def _failing_cmd(cmd: list[str]) -> tuple[int, str, str]:
            if "rm" in cmd:
                return 1, "", "Mock Docker daemon filesystem corruption"
            return 0, "", ""

        topology._run_cmd = _failing_cmd
        topology.candidate_cid = "fake-cid-123"

        with pytest.raises(TopologyCleanupError, match="Mock Docker daemon filesystem corruption"):
            await topology.close()

    asyncio.run(_test())
