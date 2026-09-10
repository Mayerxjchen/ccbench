"""Unit tests for host-side ModelGatewayProxy and real-time streaming SSE budget enforcement."""

from __future__ import annotations

import asyncio
import io
import json
import urllib.error
import urllib.request
import pytest

from bench.core.budgets import BUDGET_DOMAINS, BudgetLedger, BudgetPolicy
from bench.core.model_proxy import ModelGatewayProxy


def test_anthropic_cumulative_usage_is_deduplicated_across_final_events() -> None:
    proxy = ModelGatewayProxy(real_api_endpoint="http://127.0.0.1", real_api_key="unit-test-secret-key")
    input_seen = output_seen = 0
    charged: list[tuple[int, int]] = []
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 0, "output_tokens": 0}}},
        {"type": "content_block_stop", "usage": {"input_tokens": 4, "output_tokens": 66, "total_tokens": 70}},
        {"type": "message_delta", "usage": {"input_tokens": 4, "output_tokens": 66, "total_tokens": 70}},
        {"type": "message_stop", "usage": {"input_tokens": 4, "output_tokens": 66, "total_tokens": 70}},
    ]
    for event in events:
        delta = proxy._extract_sse_usage_delta(event, input_tracker=input_seen, output_tracker=output_seen)
        charged.append(delta)
        input_seen += delta[0]
        output_seen += delta[1]
    assert charged == [(0, 0), (4, 66), (0, 0), (0, 0)]
    assert input_seen + output_seen == 70


def test_request_summary_is_bounded_and_never_keeps_prompt_or_tool_arguments() -> None:
    proxy = ModelGatewayProxy(real_api_endpoint="http://127.0.0.1", real_api_key="x")
    proxy._record_request_summary({
        "messages": [{"role": "assistant", "content": [
            {"type": "text", "text": "secret prompt"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "cat secret"}},
        ]}, {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "x", "is_error": True,
            "content": "private tool output",
        }]}],
    })
    summary = proxy.request_summaries[0]
    assert summary == {"blocks": [
        {"role": "assistant", "type": "text"},
        {"role": "assistant", "type": "tool_use", "tool_name": "Bash"},
        {"role": "user", "type": "tool_result", "tool_result_is_error": True,
         "tool_use_id_hmac": proxy._tool_id_hmac("x")},
    ], "tool_use_count": 0, "tool_result_count": 1,
        "tool_result_error_count": 1, "protocol_conflict": False}
    rendered = json.dumps(summary)
    assert "secret" not in rendered
    assert "command" not in rendered
    assert '"tool_use_id":' not in rendered
    assert '"x"' not in rendered


def test_tool_use_ids_are_hashed_and_terminal_normalization_is_marked() -> None:
    proxy = ModelGatewayProxy(real_api_endpoint="http://127.0.0.1", real_api_key="x")
    proxy._record_request_summary({"messages": [{"role": "assistant", "content": [{
        "type": "tool_use", "id": "call-secret", "name": "Bash", "input": {"command": "x"},
    }]}, {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "call-secret", "is_error": False,
    }]}]})
    proxy._record_event_summary({"type": "content_block_start", "content_block": {
        "type": "tool_use", "id": "call-secret", "name": "Bash",
    }})
    proxy._record_event_summary({"type": "message_stop"}, normalized=True)

    request = proxy.request_summaries[0]["blocks"]
    expected = proxy._tool_id_hmac("call-secret")
    assert request[0]["tool_use_id_hmac"] == expected
    assert request[1]["tool_use_id_hmac"] == expected
    assert proxy.event_summaries[0]["tool_use_id_hmac"] == expected
    assert proxy.event_summaries[0]["normalized"] is False
    assert proxy.event_summaries[1]["normalized"] is True
    rendered = json.dumps(proxy.request_summaries + proxy.event_summaries)
    assert "call-secret" not in rendered
    assert "command" not in rendered


def test_request_response_correlation_is_run_scoped_and_secret_free() -> None:
    proxy = ModelGatewayProxy(real_api_endpoint="http://127.0.0.1", real_api_key="x")
    request = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "private prompt"}],
    }
    body_hmac = proxy._request_body_hmac(request)
    proxy._record_request_summary(request, request_index=1, request_body_hmac=body_hmac)
    proxy._record_response_summary({
        "transport": "json", "status": 200, "parsed": True,
        "protocol_complete": True, "stop_reason": "end_turn",
        "charged_delta": 3,
    }, request_index=1, request_body_hmac=body_hmac)
    assert proxy.request_summaries[0]["request_index"] == 1
    assert proxy.request_summaries[0]["request_body_hmac"] == body_hmac
    assert proxy.response_summaries[0]["request_index"] == 1
    assert proxy.response_summaries[0]["request_body_hmac"] == body_hmac
    assert proxy.request_outcomes[0]["outcome"] == "complete_2xx"
    rendered = json.dumps(proxy.request_summaries + proxy.response_summaries + proxy.request_outcomes)
    assert "private prompt" not in rendered
    assert "unit-test-secret-key" not in rendered


def test_tool_snapshots_are_deduplicated_across_run_and_interrupted_retry_is_new_request() -> None:
    proxy = ModelGatewayProxy(real_api_endpoint="http://127.0.0.1", real_api_key="secret")
    request = {"messages": [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "same-id", "name": "Bash", "input": {"command": "hidden"}},
        {"type": "tool_result", "tool_use_id": "same-id", "is_error": False},
    ]}]}
    hmac1 = proxy._request_body_hmac(request)
    proxy._record_request_summary(request, request_index=1, request_body_hmac=hmac1)
    proxy._record_request_summary(request, request_index=2, request_body_hmac=hmac1)
    assert proxy.request_summaries[0]["new_tool_use_count"] == 1
    assert proxy.request_summaries[0]["new_tool_result_count"] == 1
    assert proxy.request_summaries[1]["new_tool_use_count"] == 0
    assert proxy.request_summaries[1]["new_tool_result_count"] == 0
    assert proxy.request_summaries[1]["duplicate_tool_snapshot"] is True
    proxy._record_response_summary({
        "transport": "sse", "status": 200, "parsed": True, "protocol_complete": False,
    }, request_index=1, request_body_hmac=hmac1)
    proxy._record_response_summary({
        "transport": "json", "status": 200, "parsed": True, "protocol_complete": True,
    }, request_index=2, request_body_hmac=hmac1)
    assert [row["outcome"] for row in proxy.request_outcomes] == ["interrupted", "complete_2xx"]


class DummyUpstreamServer:
    """Lightweight upstream server simulating Anthropic API streaming and non-streaming responses."""

    def __init__(self, mode: str = "sse_normal") -> None:
        self.mode = mode
        self.server: asyncio.Server | None = None
        self.port: int = 0
        self.requests_received: int = 0
        self.last_body: bytes = b""

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
        # Read request and retain the upstream body for routing assertions.
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if not line or line == b"\r\n":
                break
            if b":" in line:
                key, value = line.decode("latin-1").split(":", 1)
                headers[key.lower()] = value.strip()
        content_length = int(headers.get("content-length", "0"))
        if content_length:
            self.last_body = await reader.readexactly(content_length)

        if self.mode == "stall_before_headers":
            await asyncio.sleep(1)

        elif self.mode == "sse_normal":
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
                b'data: {"type":"message_delta","usage":{"output_tokens":20},"delta":{"stop_reason":"tool_use"}}\r\n\r\n'
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

        elif self.mode == "sse_tool_use_missing_terminal":
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Connection: close\r\n\r\n"
                b"event: message_start\r\n"
                b'data: {"type":"message_start","message":{"usage":{"input_tokens":1}}}\r\n\r\n'
                b"event: content_block_start\r\n"
                b'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","name":"Bash"}}\r\n\r\n'
                b"event: content_block_stop\r\n"
                b'data: {"type":"content_block_stop","index":0}\r\n\r\n'
            )
            await writer.drain()

        elif self.mode in {"sse_no_message_start", "sse_missing_block_stop", "sse_partial_json"}:
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Connection: close\r\n\r\n"
            )
            if self.mode != "sse_no_message_start":
                writer.write(
                    b"event: message_start\r\n"
                    b'data: {"type":"message_start","message":{"usage":{"input_tokens":1}}}\r\n\r\n'
                )
            writer.write(
                b"event: content_block_start\r\n"
                b'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","name":"Bash"}}\r\n\r\n'
            )
            if self.mode == "sse_partial_json":
                writer.write(
                    b"event: content_block_delta\r\n"
                    b'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"x\\":"}}\r\n\r\n'
                )
            elif self.mode != "sse_missing_block_stop":
                writer.write(
                    b"event: content_block_stop\r\n"
                    b'data: {"type":"content_block_stop","index":0}\r\n\r\n'
                )
            await writer.drain()

        elif self.mode == "json":
            payload = b'{"id":"ok","stop_reason":"end_turn","content":[],"usage":{"input_tokens":1,"output_tokens":1}}'
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
                + payload
            )
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
        assert proxy.usage_events[0]["input_tokens"] == 40
        assert proxy.usage_events[0]["charged_delta"] == 40
        # The stop reason can be nested under message_delta.delta in the
        # provider-compatible Anthropic stream shape.
        assert any(
            event["type"] == "message_delta" and event["stop_reason"] == "tool_use"
            for event in proxy.event_summaries
        )
        assert any(event["type"] == "message_stop" for event in proxy.event_summaries)
        assert proxy.budget_exceeded is False
        response_summary = proxy.response_summaries[0]
        assert response_summary["transport"] == "sse"
        assert response_summary["protocol_complete"] is True
        assert response_summary["native_terminal"] is False
        assert response_summary["normalized_terminal"] is True
        assert response_summary["terminal_source"] == "normalized"
        assert response_summary["usage_consistent"] is True
        rendered_summary = json.dumps(response_summary)
        assert "test" not in rendered_summary
        assert "text" not in rendered_summary
        assert response_summary["charged_delta"] == proxy.tokens_used == 60
        assert sum(event["charged_delta"] for event in proxy.usage_events) == proxy.tokens_used

        await proxy.close()
        await upstream.close()

    asyncio.run(run())


def test_model_proxy_response_deadline_fails_closed_without_waiting_for_socket() -> None:
    async def run() -> None:
        upstream = DummyUpstreamServer(mode="stall_before_headers")
        up_port = await upstream.start()
        timeout_events: list[bool] = []
        proxy = ModelGatewayProxy(
            real_api_endpoint=f"http://127.0.0.1:{up_port}",
            real_api_key="trusted-key",
            upstream_response_timeout_sec=0.05,
            on_upstream_timeout=lambda: timeout_events.append(True),
        )
        port = await proxy.start()
        body = b'{"messages":[],"stream":true}'
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            f"POST /v1/messages HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"x-api-key: {proxy.ephemeral_token}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=1)
        writer.close()
        await writer.wait_closed()

        assert b"504 Gateway Timeout" in response
        assert proxy.last_upstream_status == 504
        assert proxy.upstream_timeout_count == 1
        assert timeout_events == [True]
        assert proxy.request_outcomes[-1]["outcome"] == "upstream_timeout"

        await proxy.close()
        await upstream.close()

    asyncio.run(run())


def test_proxy_normalizes_clean_tool_use_eof() -> None:
    async def run():
        upstream = DummyUpstreamServer(mode="sse_tool_use_missing_terminal")
        up_port = await upstream.start()
        proxy = ModelGatewayProxy(
            real_api_endpoint=f"http://127.0.0.1:{up_port}", real_api_key="sk-ant-trusted",
            max_model_turns=10, max_total_tokens=1000,
        )
        p_port = await proxy.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", p_port)
        body = b'{"messages":[{"role":"user","content":"ping"}],"stream":true}'
        request = (
            f"POST /v1/messages HTTP/1.1\r\nHost: localhost\r\n"
            f"x-api-key: {proxy.ephemeral_token}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode() + body
        writer.write(request)
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert b'"stop_reason":"tool_use"' in response
        assert b'"type":"message_stop"' in response
        await proxy.close()
        await upstream.close()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["sse_no_message_start", "sse_missing_block_stop", "sse_partial_json"])
def test_proxy_does_not_normalize_truncated_sse(mode: str) -> None:
    async def run():
        upstream = DummyUpstreamServer(mode=mode)
        up_port = await upstream.start()
        proxy = ModelGatewayProxy(
            real_api_endpoint=f"http://127.0.0.1:{up_port}", real_api_key="sk-ant-trusted",
            max_model_turns=10, max_total_tokens=1000,
        )
        p_port = await proxy.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", p_port)
        body = b'{"messages":[{"role":"user","content":"ping"}],"stream":true}'
        request = (
            f"POST /v1/messages HTTP/1.1\r\nHost: localhost\r\n"
            f"x-api-key: {proxy.ephemeral_token}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n\r\n"
        ).encode() + body
        writer.write(request)
        await writer.drain()
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert b'"type":"message_delta"' not in response
        assert b'"type":"message_stop"' not in response
        summary = proxy.response_summaries[0]
        assert summary["protocol_complete"] is False
        assert summary["normalized_terminal"] is False
        assert summary["terminal_source"] == "none"
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


def test_model_proxy_validates_client_model_and_rewrites_upstream() -> None:
    async def run() -> None:
        upstream = DummyUpstreamServer(mode="json")
        up_port = await upstream.start()
        proxy = ModelGatewayProxy(
            real_api_endpoint=f"http://127.0.0.1:{up_port}",
            real_api_key="trusted-key",
            max_model_turns=5,
            max_total_tokens=1000,
            expected_client_model="claude-sonnet-4-6",
            upstream_model="deepseek-v4-pro[1M]",
        )
        port = await proxy.start()

        async def request(model: str) -> bytes:
            body = json.dumps({"model": model, "messages": []}).encode()
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(
                f"POST /v1/messages HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"x-api-key: {proxy.ephemeral_token}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()
            return response

        good = await request("claude-sonnet-4-6")
        assert b"200 OK" in good
        assert json.loads(upstream.last_body)["model"] == "deepseek-v4-pro[1M]"
        assert upstream.requests_received == 1
        assert proxy.response_summaries[0]["transport"] == "json"
        assert proxy.response_summaries[0]["protocol_complete"] is True
        assert proxy.response_summaries[0]["native_terminal"] is True
        assert proxy.response_summaries[0]["terminal_source"] == "json_stop_reason"
        assert proxy.response_summaries[0]["charged_delta"] == 2
        assert sum(event["charged_delta"] for event in proxy.usage_events) == proxy.tokens_used == 2

        bad = await request("claude-opus-4-1")
        assert b"400 Bad Request" in bad
        assert upstream.requests_received == 1

        await proxy.close()
        await upstream.close()

    asyncio.run(run())


def test_model_proxy_preflight_classifies_anthropic_failures(monkeypatch) -> None:
    proxy = ModelGatewayProxy(
        real_api_endpoint="https://gateway.example/v1",
        real_api_key="trusted-key",
        expected_client_model="claude-sonnet-4-6",
        upstream_model="deepseek-v4-pro[1M]",
    )

    class FakeResponse:
        status = 204
        def __enter__(self): return self
        def __exit__(self, *_): return False

    captured: list[urllib.request.Request] = []

    def accepted(request, **kwargs):
        captured.append(request)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", accepted)
    assert proxy._preflight_sync(1)["classification"] == "ok"
    assert captured[0].get_header("X-api-key") == "trusted-key"
    assert captured[0].get_header("Authorization") is None

    def rejected(*args, **kwargs):
        raise urllib.error.HTTPError("https://gateway.example/v1/messages", 422, "bad model", {}, io.BytesIO(b"model rejected"))

    monkeypatch.setattr("urllib.request.urlopen", rejected)
    result = proxy._preflight_sync(1)
    assert result == {"classification": "model_rejected", "status": 422}


def test_model_proxy_bearer_mode_sends_no_x_api_key(monkeypatch) -> None:
    proxy = ModelGatewayProxy(
        real_api_endpoint="https://gateway.example",
        real_api_key="trusted-token",
        upstream_auth_mode="bearer",
    )
    captured: list[urllib.request.Request] = []

    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *_): return False

    def accepted(request, **kwargs):
        captured.append(request)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", accepted)
    assert proxy._preflight_sync(1) == {"classification": "ok", "status": 200}
    assert captured[0].get_header("Authorization") == "Bearer trusted-token"
    assert captured[0].get_header("X-api-key") is None
