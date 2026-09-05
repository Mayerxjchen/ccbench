"""Integration tests for the production Model Gateway Sidecar and internal network topology."""

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

from dftworld_bench.agents import _SIDECAR_TCP_FORWARDER_PY
from dftworld_bench.core.budgets import BUDGET_DOMAINS, BudgetLedger, BudgetPolicy
from dftworld_bench.core.model_proxy import ModelGatewayProxy

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
def test_production_sidecar_internal_network_topology():
    """Verify Candidate internal network -> dual-homed sidecar -> host proxy -> upstream."""
    async def _test():
        async def run_cmd(cmd: list[str]) -> tuple[int, str, str]:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
            return proc.returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")

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

        run_uid = uuid.uuid4().hex[:8]
        net_name = f"test-net-{run_uid}"
        sidecar_name = f"test-sidecar-{run_uid}"

        await run_cmd(["docker", "network", "create", "--internal", net_name])

        try:
            _, sc_out, _ = await run_cmd([
                "docker", "run", "-d", "--rm",
                "--name", sidecar_name,
                "--network", "bridge",
                "--add-host", "host.docker.internal:host-gateway",
                AGENT_IMAGE,
                "python3", "-c", _SIDECAR_TCP_FORWARDER_PY,
                "host.docker.internal", str(port), "8080",
            ])
            sc_cid = sc_out.strip()

            await run_cmd([
                "docker", "network", "connect", "--alias", "model-gateway", net_name, sc_cid,
            ])

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
            await run_cmd(["docker", "rm", "-f", sidecar_name])
            await run_cmd(["docker", "network", "rm", net_name])
            await proxy.close()
            server.shutdown()

    asyncio.run(_test())
