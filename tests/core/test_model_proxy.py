"""Unit tests for host-side ModelGatewayProxy and real-time streaming SSE budget enforcement."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
import pytest

from ccbench.core.budgets import BUDGET_DOMAINS, BudgetLedger, BudgetPolicy
from ccbench.core.model_proxy import ModelGatewayProxy


class DummyUpstreamServer:
    """Lightweight upstream server simulating Anthropic API streaming and non-streaming responses."""

    def __init__(self, mode: str = "sse_normal") -> None:
        self.mode = mode
        self.server: asyncio.Server | None = None
        self.port: int = 0
        self.requests_received: int = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        for s in self.server.sockets:
            self.port = s.getsockname()[1]
            break
        return self.port

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.requests_received += 1
        # Read request
        while True:
            line = await reader.readline()
            if not line or line == b"\r\n":
                break

        if self.mode == "sse_normal":
            # Stream 3 chunks with SSE usage events
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Connection: close\r\n\r\n"
            )
            await writer.drain()

            # Chunk 1: message_start with input_tokens = 40
            evt1 = (
                b"event: message_start\r\n"
                b'data: {"type":"message_start","message":{"usage":{"input_tokens":40}}}\r\n\r\n'
            )
            writer.write(evt1)
            await writer.drain()
            await asyncio.sleep(0.01)

            # Chunk 2: content delta
            evt2 = (
                b"event: content_block_delta\r\n"
                b'data: {"type":"content_block_delta","delta":{"text":"test"}}\r\n\r\n'
            )
            writer.write(evt2)
            await writer.drain()
            await asyncio.sleep(0.01)

            # Chunk 3: message_delta with output_tokens = 20
            evt3 = (
                b"event: message_delta\r\n"
                b'data: {"type":"message_delta","usage":{"output_tokens":20}}\r\n\r\n'
            )
            writer.write(evt3)
            await writer.drain()

        elif self.mode == "sse_overflow":
            # Stream that will exceed budget on second chunk
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Connection: close\r\n\r\n"
            )
            await writer.drain()

            # Chunk 1: input tokens 30
            evt1 = (
                b"event: message_start\r\n"
                b'data: {"type":"message_start","message":{"usage":{"input_tokens":30}}}\r\n\r\n'
            )
            writer.write(evt1)
            await writer.drain()
            await asyncio.sleep(0.01)

            # Chunk 2: massive output tokens 500 (exceeds budget 100)
            evt2 = (
                b"event: message_delta\r\n"
                b'data: {"type":"message_delta","usage":{"output_tokens":500}}\r\n\r\n'
            )
            writer.write(evt2)
            await writer.drain()

        writer.close()
        await writer.wait_closed()


def test_model_proxy_token_authentication() -> None:
    async def run():
        proxy = ModelGatewayProxy(
            real_api_endpoint="https://api.anthropic.com",
            real_api_key="sk-ant-trusted-secret-do-not-leak",
            max_model_turns=5,
            max_total_tokens=1000,
        )
        port = await proxy.start()
        assert port > 0
        assert proxy.ephemeral_token.startswith("mlff-run-")
        assert "sk-ant-trusted" not in proxy.ephemeral_token

        loop = asyncio.get_running_loop()
        url = f"http://127.0.0.1:{port}/v1/messages"
        req = urllib.request.Request(
            url,
            data=b'{"messages": []}',
            headers={"Content-Type": "application/json", "x-api-key": "invalid-token"},
            method="POST",
        )

        def do_call():
            try:
                urllib.request.urlopen(req)
                return 200
            except urllib.error.HTTPError as e:
                return e.code

        code = await loop.run_in_executor(None, do_call)
        assert code == 401

        await proxy.close()

    asyncio.run(run())


def test_model_proxy_sse_streaming_realtime_accounting() -> None:
    async def run():
        upstream = DummyUpstreamServer(mode="sse_normal")
        up_port = await upstream.start()

        ledger = BudgetLedger(BudgetPolicy({d: 10_000 for d in BUDGET_DOMAINS}))
        proxy = ModelGatewayProxy(
            real_api_endpoint=f"http://127.0.0.1:{up_port}",
            real_api_key="sk-ant-trusted",
            budget_ledger=ledger,
            max_model_turns=10,
            max_total_tokens=1000,
        )
        p_port = await proxy.start()

        reader, writer = await asyncio.open_connection("127.0.0.1", p_port)
        req = (
            f"POST /v1/messages HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{p_port}\r\n"
            f"x-api-key: {proxy.ephemeral_token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: 41\r\n"
            f"\r\n"
            f'{{"model":"claude-3-7-sonnet","stream":true}}'
        ).encode()
        writer.write(req)
        await writer.drain()

        body = await reader.read()
        writer.close()
        await writer.wait_closed()

        assert b"content_block_delta" in body
        assert proxy.turn_count == 1
        # 40 in + 20 out = 60 tokens
        assert proxy.tokens_used == 60
        assert proxy.budget_exceeded is False

        await proxy.close()
        await upstream.close()

    asyncio.run(run())


def test_model_proxy_sse_streaming_cutoff_midstream() -> None:
    async def run():
        upstream = DummyUpstreamServer(mode="sse_overflow")
        up_port = await upstream.start()

        cutoff_triggered = []

        def on_budget_exceeded():
            cutoff_triggered.append(True)

        ledger = BudgetLedger(BudgetPolicy({d: 10_000 for d in BUDGET_DOMAINS}))
        proxy = ModelGatewayProxy(
            real_api_endpoint=f"http://127.0.0.1:{up_port}",
            real_api_key="sk-ant-trusted",
            budget_ledger=ledger,
            max_model_turns=10,
            max_total_tokens=100,  # 100 limit, upstream will send 30 + 500 = 530 tokens
            on_budget_exceeded=on_budget_exceeded,
        )
        p_port = await proxy.start()

        reader, writer = await asyncio.open_connection("127.0.0.1", p_port)
        req = (
            f"POST /v1/messages HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{p_port}\r\n"
            f"x-api-key: {proxy.ephemeral_token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: 15\r\n"
            f"\r\n"
            f'{{"stream":true}}'
        ).encode()
        writer.write(req)
        await writer.drain()

        # Stream reading: cut-off should happen when output tokens exceed 100
        stream_body = await reader.read()
        writer.close()
        await writer.wait_closed()

        # Invariant: First chunk (30 tokens) arrived, but over-limit chunk (500 tokens) was NOT forwarded
        assert b"message_start" in stream_body
        assert b"500" not in stream_body
        assert proxy.budget_exceeded is True
        assert len(cutoff_triggered) == 1

        # Subsequent request is immediately rejected with HTTP 429
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", p_port)
        writer2.write(req)
        await writer2.drain()
        resp2 = await reader2.read()
        writer2.close()
        await writer2.wait_closed()

        assert b"429" in resp2

        await proxy.close()
        await upstream.close()

    asyncio.run(run())


def test_model_proxy_strict_request_guards() -> None:
    """Verify ModelGatewayProxy rejects invalid methods, paths, and oversized bodies."""
    async def run():
        proxy = ModelGatewayProxy(
            real_api_endpoint="https://api.anthropic.com",
            real_api_key="sk-ant-test",
            max_model_turns=5,
            max_total_tokens=1000,
        )
        port = await proxy.start()

        # 1. Non-POST method (GET) -> 405 Method Not Allowed
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            f"GET /v1/messages HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nx-api-key: {proxy.ephemeral_token}\r\n\r\n".encode()
        )
        await writer.drain()
        resp_get = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert b"405" in resp_get

        # 2. Non-messages path -> 404 Not Found
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            f"POST /admin/keys HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nx-api-key: {proxy.ephemeral_token}\r\nContent-Length: 2\r\n\r\n{{}}".encode()
        )
        await writer.drain()
        resp_path = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert b"404" in resp_path

        # 3. Oversized body (>10MB) -> 413 Payload Too Large
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        huge_len = 11 * 1024 * 1024
        writer.write(
            f"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nx-api-key: {proxy.ephemeral_token}\r\nContent-Length: {huge_len}\r\n\r\n".encode()
        )
        await writer.drain()
        resp_huge = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert b"413" in resp_huge

        await proxy.close()

    asyncio.run(run())

