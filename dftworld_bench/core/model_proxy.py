"""Model Gateway Proxy: Host-side trusted credential isolator and real-time streaming budget enforcer.

Candidate agent containers receive ONLY a short-lived, run-scoped ephemeral token
and a proxy URL pointing to this host gateway. The real Anthropic / upstream API
credentials NEVER enter the Candidate container environment.

Key capabilities:
1. Validates incoming run-scoped ephemeral tokens.
2. Forwards requests to the upstream API using trusted host credentials.
3. Parses streaming usage chunks in real-time (supporting Server-Sent Events / SSE).
4. Sole owner of model turns and token budget accounting (charges the harness BudgetLedger).
5. Real-time fail-fast budget cut-off: immediately aborts streaming/upstream upon token
   exhaustion, rejects subsequent requests (HTTP 429), and invokes on_budget_exceeded callback.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from dftworld_bench.core.budgets import BudgetExceeded

MAX_BODY_BYTES = 10 * 1024 * 1024  # 10MB limit (P0/P1 defense)


class ModelGatewayProxy:
    """Run-scoped host model proxy that shields API secrets and enforces live budgets."""

    def __init__(
        self,
        *,
        real_api_endpoint: str,
        real_api_key: str,
        budget_ledger: Any = None,
        max_model_turns: int = 128,
        max_total_tokens: int = 50_000_000,
        task_name: str = "run",
        on_budget_exceeded: Callable[[], None] | None = None,
        max_concurrency: int = 4,
    ) -> None:
        self.real_api_endpoint = real_api_endpoint.rstrip("/")
        self._real_api_key = real_api_key
        self.budget_ledger = budget_ledger
        self.max_model_turns = max_model_turns
        self.max_total_tokens = max_total_tokens
        self.task_name = task_name
        self.on_budget_exceeded = on_budget_exceeded
        self._semaphore = asyncio.Semaphore(max_concurrency)

        # Ephemeral token valid only for this run/container lifecycle
        self.ephemeral_token = f"mlff-run-{secrets.token_hex(24)}"

        self._server: asyncio.Server | None = None
        self.port: int = 0
        self.host: str = "0.0.0.0"
        self.turn_count: int = 0
        self.tokens_used: int = 0
        self.budget_exceeded: bool = False
        self._closed: bool = False

    async def start(self) -> int:
        """Start listening on an OS-assigned ephemeral port."""
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            0,  # OS assigned port
        )
        for sock in self._server.sockets:
            self.port = sock.getsockname()[1]
            break
        return self.port

    def proxy_url(self, host_alias: str = "host.docker.internal") -> str:
        """URL reachable by the candidate container."""
        return f"http://{host_alias}:{self.port}"

    async def close(self) -> None:
        """Shut down the proxy, invalidating the ephemeral token immediately."""
        self._closed = True
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def _record_tokens(self, delta: int) -> bool:
        """Record newly consumed tokens, charge ledger, and check budget cutoff.

        Returns True if budget remains healthy, False if budget was exceeded.
        """
        if delta <= 0:
            return not self.budget_exceeded

        self.tokens_used += delta
        if self.tokens_used > self.max_total_tokens:
            self.budget_exceeded = True
            if self.on_budget_exceeded:
                self.on_budget_exceeded()
            return False

        if self.budget_ledger is not None:
            try:
                self.budget_ledger.charge("tokens", delta, f"{self.task_name}/tokens")
            except BudgetExceeded:
                self.budget_exceeded = True
                if self.on_budget_exceeded:
                    self.on_budget_exceeded()
                raise

        return True

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            async with self._semaphore:
                # 1. Read request line
                req_line_bytes = await reader.readline()
                if not req_line_bytes:
                    writer.close()
                    await writer.wait_closed()
                    return

                req_line = req_line_bytes.decode("utf-8", errors="replace").strip()
                parts = req_line.split()
                if len(parts) < 2:
                    self._send_error(writer, 400, "Bad Request")
                    return
                method, path = parts[0], parts[1]

                # Strict Method & Path Whitelist (P0/P1 defense)
                if method != "POST":
                    self._send_error(writer, 405, f"Method Not Allowed: {method}")
                    return
                clean_path = path.split("?")[0].rstrip("/")
                if clean_path != "/v1/messages":
                    self._send_error(writer, 404, f"Not Found: {path}")
                    return

                # 2. Read headers
                headers: dict[str, str] = {}
                content_length = 0
                while True:
                    line_bytes = await reader.readline()
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line:
                        break
                    if ":" in line:
                        h_name, h_val = line.split(":", 1)
                        headers[h_name.strip().lower()] = h_val.strip()
                        if h_name.strip().lower() == "content-length":
                            try:
                                content_length = int(h_val.strip())
                            except ValueError:
                                content_length = 0

                # Check max payload size (10MB limit)
                if content_length > MAX_BODY_BYTES:
                    self._send_error(writer, 413, f"Payload Too Large: max {MAX_BODY_BYTES} bytes")
                    return

                # 3. Read body
                body_bytes = b""
                if content_length > 0:
                    body_bytes = await reader.readexactly(content_length)

                # 4. Authenticate ephemeral token
                auth_header = headers.get("authorization", "")
                x_api_key = headers.get("x-api-key", "")
                token_received = ""
                if x_api_key:
                    token_received = x_api_key
                elif auth_header.startswith("Bearer "):
                    token_received = auth_header[7:].strip()

                if token_received != self.ephemeral_token or self._closed:
                    self._send_error(writer, 401, "Unauthorized: invalid or expired run token")
                    return

                # 5. Live Turn Budget Enforcement (Proxy is sole owner)
                if self.budget_exceeded:
                    self._send_error(writer, 429, "Budget Exceeded: model turns or token limit reached")
                    return

                self.turn_count += 1
                if self.turn_count > self.max_model_turns:
                    self.budget_exceeded = True
                    if self.on_budget_exceeded:
                        self.on_budget_exceeded()
                    self._send_error(writer, 429, "Budget Exceeded: max_model_turns exceeded")
                    return

                if self.budget_ledger is not None:
                    try:
                        self.budget_ledger.charge("model_turns", 1, f"{self.task_name}/model-turn/{self.turn_count}")
                    except BudgetExceeded as exc:
                        self.budget_exceeded = True
                        if self.on_budget_exceeded:
                            self.on_budget_exceeded()
                        self._send_error(writer, 429, f"Budget Exceeded: {exc}")
                        return

                # 6. Forward upstream with trusted credentials
                upstream_url = f"{self.real_api_endpoint}{path}"
                fwd_headers = {
                    "Content-Type": headers.get("content-type", "application/json"),
                    "anthropic-version": headers.get("anthropic-version", "2023-06-01"),
                "x-api-key": self._real_api_key,
                "Authorization": f"Bearer {self._real_api_key}",
            }
            if "accept" in headers:
                fwd_headers["Accept"] = headers["accept"]

            await self._forward_request_streaming(method, upstream_url, fwd_headers, body_bytes, writer)

        except Exception as exc:
            try:
                self._send_error(writer, 502, f"Proxy Gateway Error: {exc}")
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    def _send_error(self, writer: asyncio.StreamWriter, status: int, message: str) -> None:
        payload = json.dumps({"error": {"message": message, "code": status}}).encode("utf-8")
        headers = [
            f"HTTP/1.1 {status} Error",
            "Content-Type: application/json",
            f"Content-Length: {len(payload)}",
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(headers).encode("utf-8") + payload)

    async def _forward_request_streaming(
        self,
        method: str,
        upstream_url: str,
        headers: dict[str, str],
        body: bytes,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        """Stream request from upstream to client with real-time SSE token accounting."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Tuple[str, Any]] = asyncio.Queue()
        abort_event = asyncio.Event()

        def do_stream():
            req = urllib.request.Request(
                upstream_url,
                data=body if method in ("POST", "PUT", "PATCH") else None,
                headers=headers,
                method=method,
            )
            ctx = ssl.create_default_context()
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                    resp_headers = dict(resp.headers)
                    loop.call_soon_threadsafe(queue.put_nowait, ("meta", (resp.status, resp_headers)))

                    while not abort_event.is_set():
                        chunk = resp.readline()
                        if not chunk:
                            break
                        loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
                    loop.call_soon_threadsafe(queue.put_nowait, ("eof", None))

            except urllib.error.HTTPError as err:
                err_body = err.read()
                loop.call_soon_threadsafe(queue.put_nowait, ("meta", (err.code, dict(err.headers))))
                loop.call_soon_threadsafe(queue.put_nowait, ("chunk", err_body))
                loop.call_soon_threadsafe(queue.put_nowait, ("eof", None))
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))

        # Launch upstream reader in thread pool
        stream_future = loop.run_in_executor(None, do_stream)

        # 1. Wait for meta
        try:
            tag, val = await queue.get()
            if tag == "error":
                self._send_error(client_writer, 502, f"Upstream error: {val}")
                return
            status, resp_headers = val

            headers_lower = {str(k).lower(): str(v) for k, v in resp_headers.items()}
            content_type = headers_lower.get("content-type", "").lower()
            is_sse = "text/event-stream" in content_type

            # Send response headers to client
            header_lines = [f"HTTP/1.1 {status} OK"]
            for k, v in resp_headers.items():
                if k.lower() not in ("connection", "content-length"):
                    header_lines.append(f"{k}: {v}")
            header_lines.append("Connection: close")
            header_lines.append("")
            header_lines.append("")
            client_writer.write("\r\n".join(header_lines).encode("utf-8"))
            await client_writer.drain()

            # State for token extraction across chunks
            accumulated_body = bytearray()
            stream_input_tokens = 0
            stream_output_tokens = 0

            # 2. Process chunks
            while True:
                tag, val = await queue.get()
                if tag == "eof":
                    break
                if tag == "error":
                    break
                if tag == "chunk":
                    chunk_bytes: bytes = val
                    accumulated_body.extend(chunk_bytes)
                    # Real-time SSE usage parser & budget cutoff BEFORE forwarding
                    forward_this_chunk = True
                    if is_sse:
                        text_chunk = chunk_bytes.decode("utf-8", errors="replace")
                        for line in text_chunk.splitlines():
                            line_str = line.strip()
                            if line_str.startswith("data:"):
                                data_part = line_str[5:].strip()
                                if data_part and data_part != "[DONE]":
                                    try:
                                        event_obj = json.loads(data_part)
                                        delta = self._extract_sse_usage_delta(
                                            event_obj,
                                            input_tracker=stream_input_tokens,
                                            output_tracker=stream_output_tokens,
                                        )
                                        if delta[0] > 0:
                                            stream_input_tokens += delta[0]
                                        if delta[1] > 0:
                                            stream_output_tokens += delta[1]

                                        total_delta = delta[0] + delta[1]
                                        if total_delta > 0:
                                            try:
                                                healthy = self._record_tokens(total_delta)
                                                if not healthy:
                                                    forward_this_chunk = False
                                                    abort_event.set()
                                                    break
                                            except BudgetExceeded:
                                                forward_this_chunk = False
                                                abort_event.set()
                                                break
                                    except (json.JSONDecodeError, UnicodeDecodeError):
                                        pass
                        if self.budget_exceeded:
                            forward_this_chunk = False

                    # Forward chunk ONLY IF budget remains healthy
                    if forward_this_chunk and not self.budget_exceeded:
                        client_writer.write(chunk_bytes)
                        await client_writer.drain()
                    else:
                        # Budget exceeded: DO NOT forward over-limit chunk!
                        # Immediately abort upstream and close client connection
                        abort_event.set()
                        break

            # 3. If non-SSE JSON response, parse total usage upon completion
            if not is_sse and not self.budget_exceeded:
                try:
                    resp_json = json.loads(accumulated_body.decode("utf-8", errors="replace"))
                    if isinstance(resp_json, dict) and "usage" in resp_json:
                        usage = resp_json["usage"]
                        total = int(usage.get("total_tokens") or 0)
                        if not total:
                            total = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
                        if total > 0:
                            self._record_tokens(total)
                except Exception:
                    pass

        finally:
            abort_event.set()
            await stream_future

    def _extract_sse_usage_delta(
        self,
        event_obj: dict[str, Any],
        *,
        input_tracker: int,
        output_tracker: int,
    ) -> Tuple[int, int]:
        """Extract incremental (input_delta, output_delta) from SSE event payload."""
        delta_in = 0
        delta_out = 0

        ev_type = event_obj.get("type", "")

        # Anthropic message_start event
        if ev_type == "message_start":
            msg = event_obj.get("message", {})
            usage = msg.get("usage", {})
            in_tok = int(usage.get("input_tokens") or 0)
            if in_tok > input_tracker:
                delta_in = in_tok - input_tracker

        # Anthropic message_delta event
        elif ev_type == "message_delta":
            usage = event_obj.get("usage", {})
            out_tok = int(usage.get("output_tokens") or 0)
            if out_tok > output_tracker:
                delta_out = out_tok - output_tracker

        # Generic / OpenAI style usage in chunk
        elif "usage" in event_obj and isinstance(event_obj["usage"], dict):
            usage = event_obj["usage"]
            tot = int(usage.get("total_tokens") or 0)
            cur = input_tracker + output_tracker
            if tot > cur:
                delta_out = tot - cur

        return delta_in, delta_out
