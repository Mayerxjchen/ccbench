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
import hashlib
import hmac
import http.client
import json
import secrets
import socket
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from bench.core.budgets import BudgetExceeded

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
        expected_model: str | None = None,
        expected_client_model: str | None = None,
        upstream_model: str | None = None,
        upstream_auth_mode: str = "x-api-key",
        upstream_response_timeout_sec: float = 300.0,
        on_upstream_timeout: Callable[[], None] | None = None,
    ) -> None:
        self.real_api_endpoint = real_api_endpoint.rstrip("/")
        self._real_api_key = real_api_key
        self.budget_ledger = budget_ledger
        self.max_model_turns = max_model_turns
        self.max_total_tokens = max_total_tokens
        self.task_name = task_name
        self.on_budget_exceeded = on_budget_exceeded
        # ``expected_model`` remains a compatibility alias for callers that
        # used the pre-routing single-ID contract.  New callers distinguish
        # the locally accepted Claude model from the opaque upstream ID.
        self.expected_client_model = expected_client_model or expected_model
        self.upstream_model = upstream_model
        if upstream_auth_mode not in {"x-api-key", "bearer"}:
            raise ValueError("upstream_auth_mode must be x-api-key or bearer")
        self.upstream_auth_mode = upstream_auth_mode
        if upstream_response_timeout_sec <= 0:
            raise ValueError("upstream_response_timeout_sec must be positive")
        self.upstream_response_timeout_sec = float(upstream_response_timeout_sec)
        self.on_upstream_timeout = on_upstream_timeout
        self.expected_model = self.expected_client_model
        self._semaphore = asyncio.Semaphore(max_concurrency)

        # Ephemeral token valid only for this run/container lifecycle
        self.ephemeral_token = f"mlff-run-{secrets.token_hex(24)}"

        self._server: asyncio.Server | None = None
        self.port: int = 0
        self.host: str = "0.0.0.0"
        self.turn_count: int = 0
        self.tokens_used: int = 0
        self.budget_exceeded: bool = False
        self.budget_exceeded_reason: str | None = None
        self._closed: bool = False
        self.last_request_path: str | None = None
        self.last_client_model: str | None = None
        self.last_upstream_model: str | None = None
        self.last_upstream_status: int | None = None
        self.upstream_timeout_count: int = 0
        self.usage_events: list[dict[str, int | str]] = []
        self.event_summaries: list[dict[str, str]] = []
        self.request_summaries: list[dict[str, Any]] = []
        self.response_summaries: list[dict[str, Any]] = []
        # Request IDs are correlation material only.  A fresh in-memory key
        # prevents cross-run correlation and is never persisted or returned.
        self._tool_id_hmac_key = secrets.token_bytes(32)
        self._request_hmac_key = secrets.token_bytes(32)
        self._request_index = 0
        self.request_outcomes: list[dict[str, Any]] = []
        self._seen_run_tool_uses: set[str] = set()
        self._seen_run_tool_results: set[str] = set()

    def _upstream_auth_headers(self) -> dict[str, str]:
        """Return exactly one provider auth header for the configured credential type."""
        if self.upstream_auth_mode == "bearer":
            return {"Authorization": f"Bearer {self._real_api_key}"}
        return {"x-api-key": self._real_api_key}

    async def preflight(self, *, timeout_sec: float = 5.0) -> dict[str, str | int]:
        """Classify connectivity to the configured Anthropic-compatible route.

        This is an explicit coordinator-side check; Candidate traffic still
        requires the run token and is limited to ``POST /v1/messages``.  The
        result distinguishes DNS/TLS/auth/path/model failures so a broken
        gateway is never mistaken for a scientific failure.
        """
        return await asyncio.to_thread(self._preflight_sync, timeout_sec)

    def _preflight_sync(self, timeout_sec: float) -> dict[str, str | int]:
        endpoint = self._upstream_url("/v1/messages")
        model = self.upstream_model or self.expected_client_model or "claude-sonnet-4-6"
        body = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }).encode()
        request = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "anthropic-version": "2023-06-01",
                     **self._upstream_auth_headers()},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                return {"classification": "ok", "status": int(response.status)}
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read(4096).decode("utf-8", errors="replace").lower()
            except Exception:
                detail = ""
            if exc.code in (401, 403):
                classification = "auth"
            elif exc.code == 404:
                classification = "anthropic_messages_404"
            elif exc.code in (400, 422) and "model" in detail:
                classification = "model_rejected"
            else:
                classification = "upstream_rejected"
            return {"classification": classification, "status": int(exc.code)}
        except socket.gaierror:
            return {"classification": "dns"}
        except ssl.SSLError:
            return {"classification": "tls"}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.gaierror):
                return {"classification": "dns"}
            if isinstance(reason, ssl.SSLError):
                return {"classification": "tls"}
            return {"classification": "transport", "detail": type(reason).__name__}

    def _upstream_url(self, path: str) -> str:
        """Join an Anthropic base URL without producing ``/v1/v1``."""
        if self.real_api_endpoint.endswith("/v1") and path.startswith("/v1/"):
            return self.real_api_endpoint + path[3:]
        return self.real_api_endpoint + path

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
        if self.budget_ledger is not None:
            try:
                self.budget_ledger.charge("tokens", delta, f"{self.task_name}/tokens")
            except BudgetExceeded:
                self.budget_exceeded = True
                self.budget_exceeded_reason = "ledger"
                if self.on_budget_exceeded:
                    self.on_budget_exceeded()
                raise

        if self.tokens_used > self.max_total_tokens:
            self.budget_exceeded = True
            self.budget_exceeded_reason = "max_total_tokens"
            if self.on_budget_exceeded:
                self.on_budget_exceeded()
            return False

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
                self.last_request_path = path

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

                # Parse once for the model contract and the bounded diagnostic
                # summary.  The summary intentionally sees only block types,
                # tool names, and tool-result error flags; it never stores
                # prompts, arguments, headers, or response content.
                request_json: dict[str, Any] | None = None
                if body_bytes:
                    try:
                        parsed_request = json.loads(body_bytes.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        if self.expected_client_model is not None:
                            self._send_error(writer, 400, f"Invalid JSON request: {exc}")
                            return
                    else:
                        if isinstance(parsed_request, dict):
                            request_json = parsed_request

                # The gateway must not silently substitute a provider default.
                # For custom routes the model string is opaque, but it must
                # equal the run's requested value byte-for-byte.
                if self.expected_client_model is not None:
                    if request_json is None or request_json.get("model") != self.expected_client_model:
                        self._send_error(writer, 400, "model does not match the run-scoped client model")
                        return
                    self.last_client_model = str(request_json["model"])
                    # Claude Code has already validated the client-facing
                    # model.  Only the trusted host proxy may translate it to
                    # the user's opaque upstream gateway ID.
                    if self.upstream_model is not None:
                        request_json["model"] = self.upstream_model
                        self.last_upstream_model = self.upstream_model
                        body_bytes = json.dumps(
                            request_json, separators=(",", ":"), ensure_ascii=False
                        ).encode("utf-8")
                request_index: int | None = None
                request_body_hmac: str | None = None
                if request_json is not None:
                    self._request_index += 1
                    request_index = self._request_index
                    request_body_hmac = self._request_body_hmac(request_json)
                    self.request_outcomes.append({
                        "request_index": request_index,
                        "request_body_hmac": request_body_hmac,
                        "outcome": "in_flight",
                    })
                    self._record_request_summary(
                        request_json,
                        request_index=request_index,
                        request_body_hmac=request_body_hmac,
                    )

                # 5. Live Turn Budget Enforcement (Proxy is sole owner)
                if self.budget_exceeded:
                    self._finish_request(request_index, "budget_rejected")
                    self._send_error(writer, 429, "Budget Exceeded: model turns or token limit reached")
                    return

                self.turn_count += 1
                if self.turn_count > self.max_model_turns:
                    self.budget_exceeded = True
                    self.budget_exceeded_reason = "max_model_turns"
                    if self.on_budget_exceeded:
                        self.on_budget_exceeded()
                    self._finish_request(request_index, "budget_rejected")
                    self._send_error(writer, 429, "Budget Exceeded: max_model_turns exceeded")
                    return

                if self.budget_ledger is not None:
                    try:
                        self.budget_ledger.charge("model_turns", 1, f"{self.task_name}/model-turn/{self.turn_count}")
                    except BudgetExceeded as exc:
                        self.budget_exceeded = True
                        self.budget_exceeded_reason = "ledger"
                        if self.on_budget_exceeded:
                            self.on_budget_exceeded()
                        self._finish_request(request_index, "budget_rejected")
                        self._send_error(writer, 429, f"Budget Exceeded: {exc}")
                        return

                # 6. Forward upstream with trusted credentials
                upstream_url = self._upstream_url(path)
                fwd_headers = {
                    "Content-Type": headers.get("content-type", "application/json"),
                    "anthropic-version": headers.get("anthropic-version", "2023-06-01"),
                    **self._upstream_auth_headers(),
                }
            if "accept" in headers:
                fwd_headers["Accept"] = headers["accept"]

            await self._forward_request_streaming(
                method, upstream_url, fwd_headers, body_bytes, writer,
                request_index=request_index, request_body_hmac=request_body_hmac,
            )

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
        reason = http.client.responses.get(status, "Error")
        payload = json.dumps({"error": {"message": message, "code": status}}).encode("utf-8")
        headers = [
            f"HTTP/1.1 {status} {reason}",
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
        *,
        request_index: int | None = None,
        request_body_hmac: str | None = None,
    ) -> None:
        """Stream request from upstream to client with real-time SSE token accounting."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Tuple[str, Any]] = asyncio.Queue()
        abort_event = threading.Event()
        response_started = False
        deadline = loop.time() + self.upstream_response_timeout_sec

        def publish(tag: str, value: Any) -> None:
            # The worker is deliberately daemonized so a wedged third-party
            # socket can never keep the harness process alive after timeout.
            # Ignore late events once the run loop has already closed.
            try:
                loop.call_soon_threadsafe(queue.put_nowait, (tag, value))
            except RuntimeError:
                pass

        async def next_event() -> Tuple[str, Any]:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            return await asyncio.wait_for(queue.get(), timeout=remaining)

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
                    publish("meta", (resp.status, resp_headers))

                    while not abort_event.is_set():
                        chunk = resp.readline()
                        if not chunk:
                            break
                        publish("chunk", chunk)
                    publish("eof", None)

            except urllib.error.HTTPError as err:
                err_body = err.read()
                publish("meta", (err.code, dict(err.headers)))
                publish("chunk", err_body)
                publish("eof", None)
            except Exception as exc:
                publish("error", exc)

        # A daemon thread avoids asyncio's default-executor shutdown waiting
        # forever on a third-party socket that ignores close/timeout signals.
        threading.Thread(
            target=do_stream,
            name=f"bench-upstream-{request_index or 0}",
            daemon=True,
        ).start()

        # 1. Wait for meta
        try:
            tag, val = await next_event()
            if tag == "error":
                self._finish_request(request_index, "interrupted")
                self._send_error(client_writer, 502, f"Upstream error: {val}")
                return
            status, resp_headers = val
            self.last_upstream_status = int(status)

            headers_lower = {str(k).lower(): str(v) for k, v in resp_headers.items()}
            content_type = headers_lower.get("content-type", "").lower()
            is_sse = "text/event-stream" in content_type

            if is_sse:
                # SSE Branch: Stream chunks with real-time pre-charge and over-limit suppression
                header_lines = [f"HTTP/1.1 {status} OK"]
                for k, v in resp_headers.items():
                    if k.lower() not in ("connection", "content-length"):
                        header_lines.append(f"{k}: {v}")
                header_lines.append("Connection: close")
                header_lines.append("")
                header_lines.append("")
                client_writer.write("\r\n".join(header_lines).encode("utf-8"))
                await client_writer.drain()
                response_started = True

                stream_input_tokens = 0
                stream_output_tokens = 0
                saw_message_start = False
                saw_tool_use = False
                tool_use_count = 0
                tool_use_indices: set[int] = set()
                saw_terminal_stop = False
                saw_message_stop = False
                terminal_stop_reason: str | None = None
                normalized_terminal = False
                forwarded_any = False
                clean_upstream_eof = False
                protocol_complete = True
                open_content_blocks: set[int] = set()

                while True:
                    tag, val = await next_event()
                    if tag == "eof":
                        clean_upstream_eof = True
                        break
                    if tag == "error":
                        break
                    if tag == "chunk":
                        chunk_bytes: bytes = val
                        forward_this_chunk = True
                        text_chunk = chunk_bytes.decode("utf-8", errors="replace")
                        for line in text_chunk.splitlines():
                            line_str = line.strip()
                            if line_str.startswith("data:"):
                                data_part = line_str[5:].strip()
                                if data_part and data_part != "[DONE]":
                                    try:
                                        event_obj = json.loads(data_part)
                                        block = event_obj.get("content_block") or {}
                                        delta_obj = event_obj.get("delta") or {}
                                        block_type = block.get("type") if isinstance(block, dict) else ""
                                        delta_type = delta_obj.get("type") if isinstance(delta_obj, dict) else ""
                                        event_type = event_obj.get("type")
                                        if event_type == "message_start":
                                            saw_message_start = isinstance(event_obj.get("message"), dict)
                                            if not saw_message_start:
                                                protocol_complete = False
                                        elif event_type == "error" or isinstance(event_obj.get("error"), dict):
                                            protocol_complete = False
                                        elif event_type == "content_block_start":
                                            index = event_obj.get("index")
                                            if not isinstance(index, int) or index in open_content_blocks:
                                                protocol_complete = False
                                            else:
                                                open_content_blocks.add(index)
                                                if block_type == "tool_use" and index not in tool_use_indices:
                                                    tool_use_indices.add(index)
                                                    tool_use_count += 1
                                        elif event_type == "content_block_stop":
                                            index = event_obj.get("index")
                                            if not isinstance(index, int) or index not in open_content_blocks:
                                                protocol_complete = False
                                            else:
                                                open_content_blocks.remove(index)
                                        if block_type == "tool_use" or delta_type == "tool_use":
                                            saw_tool_use = True
                                        if event_obj.get("type") == "message_stop":
                                            saw_message_stop = True
                                        event_stop_reason = (
                                            event_obj.get("stop_reason")
                                            or ((event_obj.get("message") or {}).get("stop_reason", "") if isinstance(event_obj.get("message"), dict) else "")
                                            or ((event_obj.get("delta") or {}).get("stop_reason", "") if isinstance(event_obj.get("delta"), dict) else "")
                                        )
                                        if event_stop_reason:
                                            saw_terminal_stop = True
                                            terminal_stop_reason = str(event_stop_reason)[:64]
                                        self._record_event_summary(event_obj)
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
                                        if len(self.usage_events) < 256:
                                            usage = event_obj.get("usage")
                                            if not isinstance(usage, dict) and isinstance(event_obj.get("message"), dict):
                                                usage = event_obj["message"].get("usage")
                                            if isinstance(usage, dict):
                                                self.usage_events.append({
                                                    "type": str(event_obj.get("type", "")),
                                                    "input_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
                                                    "output_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
                                                    "total_tokens": int(usage.get("total_tokens") or 0),
                                                    "charged_delta": total_delta,
                                                })
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

                        if forward_this_chunk and not self.budget_exceeded:
                            client_writer.write(chunk_bytes)
                            await client_writer.drain()
                            forwarded_any = True
                        else:
                            abort_event.set()
                            break
                # Some Anthropic-compatible gateways close a successful SSE
                # stream after tool_use without emitting the SDK terminal
                # events.  Normalize only a clean 2xx EOF; never invent a
                # verdict after upstream error, client abort, or budget stop.
                if (clean_upstream_eof and 200 <= status < 300
                        and not abort_event.is_set() and not self.budget_exceeded
                        and saw_message_start and protocol_complete and not open_content_blocks):
                    if saw_tool_use and not saw_terminal_stop:
                        event = {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}
                        self._record_event_summary(event, normalized=True)
                        normalized_terminal = True
                        client_writer.write(
                            b"event: message_delta\n"
                            b'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}\n\n'
                        )
                        forwarded_any = True
                    # For non-tool responses we only know that a terminal
                    # message was intended when the provider supplied a stop
                    # reason; otherwise a truncated stream stays incomplete.
                    if not saw_message_stop and (saw_tool_use or saw_terminal_stop):
                        event = {"type": "message_stop"}
                        self._record_event_summary(event, normalized=True)
                        normalized_terminal = True
                        client_writer.write(
                            b"event: message_stop\n"
                            b'data: {"type":"message_stop"}\n\n'
                        )
                        forwarded_any = True
                    await client_writer.drain()
                terminal_source = (
                    "native" if saw_message_stop else
                    "normalized" if normalized_terminal else "none"
                )
                if terminal_stop_reason is None and normalized_terminal and saw_tool_use:
                    terminal_stop_reason = "tool_use"
                    terminal_source = "normalized"
                charged_delta = stream_input_tokens + stream_output_tokens
                self._record_response_summary({
                    "transport": "sse",
                    "status": int(status),
                    "parsed": bool(saw_message_start),
                    "protocol_complete": bool(
                        200 <= status < 300 and saw_message_start and protocol_complete
                        and not open_content_blocks
                        and (saw_message_stop or normalized_terminal)
                    ),
                    "stop_reason": terminal_stop_reason,
                    "canonical_stop": terminal_stop_reason,
                    "terminal_source": terminal_source,
                    "tool_use_count": tool_use_count,
                    "native_terminal": bool(saw_message_stop),
                    "normalized_terminal": normalized_terminal,
                    "forwarded": forwarded_any,
                    "abort": bool(abort_event.is_set() or self.budget_exceeded),
                    "usage": {
                        "input_tokens": stream_input_tokens,
                        "output_tokens": stream_output_tokens,
                        "total_tokens": charged_delta,
                    },
                    "charged_delta": charged_delta,
                    "usage_consistent": charged_delta == stream_input_tokens + stream_output_tokens,
                }, request_index=request_index, request_body_hmac=request_body_hmac)
            else:
                # Non-SSE Branch: BUFFER full body, parse usage, charge ledger FIRST, then send!
                accumulated_body = bytearray()
                while True:
                    tag, val = await next_event()
                    if tag in ("eof", "error"):
                        break
                    if tag == "chunk":
                        accumulated_body.extend(val)

                # Parse non-SSE JSON usage
                non_stream_tokens = 0
                non_stream_input = 0
                non_stream_output = 0
                parsed_json = False
                content_valid = False
                usage_valid = False
                usage_consistent = False
                json_stop_reason: str | None = None
                json_tool_use_count = 0
                try:
                    resp_json = json.loads(accumulated_body.decode("utf-8", errors="replace"))
                    parsed_json = isinstance(resp_json, dict)
                    if isinstance(resp_json, dict):
                        json_stop_reason = resp_json.get("stop_reason")
                        usage = resp_json.get("usage")
                        if isinstance(usage, dict):
                            non_stream_input = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                            non_stream_output = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
                            usage_total = int(usage.get("total_tokens") or 0)
                            usage_valid = non_stream_input >= 0 and non_stream_output >= 0 and (
                                usage_total >= 0 or "input_tokens" in usage or "output_tokens" in usage
                            )
                            usage_consistent = usage_total == 0 or usage_total == non_stream_input + non_stream_output
                        content = resp_json.get("content")
                        if isinstance(content, list):
                            content_valid = True
                            json_tool_use_count = sum(
                                1 for block in content
                                if isinstance(block, dict) and block.get("type") == "tool_use"
                            )
                    if isinstance(resp_json, dict) and "usage" in resp_json:
                        usage = resp_json["usage"]
                        non_stream_tokens = int(usage.get("total_tokens") or 0)
                        if not non_stream_tokens:
                            non_stream_tokens = non_stream_input + non_stream_output
                except Exception:
                    pass

                if len(self.usage_events) < 256:
                    self.usage_events.append({
                        "type": "json_response",
                        "input_tokens": non_stream_input,
                        "output_tokens": non_stream_output,
                        "total_tokens": non_stream_tokens,
                        "charged_delta": non_stream_tokens,
                    })
                response_summary = {
                    "transport": "json",
                    "status": int(status),
                    "parsed": parsed_json,
                    "protocol_complete": bool(
                        parsed_json and 200 <= status < 300 and content_valid
                        and bool(json_stop_reason) and usage_valid and usage_consistent
                    ),
                    "stop_reason": str(json_stop_reason)[:64] if json_stop_reason else None,
                    "canonical_stop": str(json_stop_reason)[:64] if json_stop_reason else None,
                    "terminal_source": "json_stop_reason" if json_stop_reason else "none",
                    "tool_use_count": json_tool_use_count,
                    "native_terminal": bool(json_stop_reason),
                    "normalized_terminal": False,
                    "forwarded": False,
                    "abort": False,
                    "usage": {
                        "input_tokens": non_stream_input,
                        "output_tokens": non_stream_output,
                        "total_tokens": non_stream_tokens,
                    },
                    "charged_delta": non_stream_tokens,
                    "usage_consistent": usage_consistent,
                }

                # Pre-flight charge before sending ANY data to client
                budget_rejected = False
                if non_stream_tokens > 0:
                    try:
                        healthy = self._record_tokens(non_stream_tokens)
                        if not healthy:
                            budget_rejected = True
                    except BudgetExceeded as exc:
                        budget_rejected = True

                if self.budget_exceeded:
                    budget_rejected = True
                if budget_rejected:
                    self._send_error(client_writer, 429, "Budget Exceeded: token budget limit reached")
                    response_summary["abort"] = True
                    self._record_response_summary(
                        response_summary,
                        request_index=request_index,
                        request_body_hmac=request_body_hmac,
                    )
                    return

                # Budget healthy: send HTTP headers and full body
                header_lines = [f"HTTP/1.1 {status} OK"]
                for k, v in resp_headers.items():
                    if k.lower() not in ("connection", "content-length"):
                        header_lines.append(f"{k}: {v}")
                header_lines.append(f"Content-Length: {len(accumulated_body)}")
                header_lines.append("Connection: close")
                header_lines.append("")
                header_lines.append("")
                client_writer.write("\r\n".join(header_lines).encode("utf-8") + bytes(accumulated_body))
                await client_writer.drain()
                response_summary["forwarded"] = True
                self._record_response_summary(
                    response_summary,
                    request_index=request_index,
                    request_body_hmac=request_body_hmac,
                )

        except asyncio.TimeoutError:
            abort_event.set()
            self.upstream_timeout_count += 1
            self._finish_request(request_index, "upstream_timeout")
            if not response_started:
                self.last_upstream_status = 504
                self._send_error(
                    client_writer,
                    504,
                    f"Upstream response exceeded {self.upstream_response_timeout_sec:g}s",
                )
            if self.on_upstream_timeout is not None:
                self.on_upstream_timeout()
        finally:
            abort_event.set()
            if request_index is not None:
                current = next(
                    (row for row in self.request_outcomes
                     if row.get("request_index") == request_index),
                    None,
                )
                if current is not None and current.get("outcome") == "in_flight":
                    self._finish_request(request_index, "interrupted")

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
            # A few Anthropic-compatible gateways report cumulative output in
            # message_start/stop too; account for it just like message_delta.
            out_tok = int(usage.get("output_tokens") or 0)
            if out_tok > output_tracker:
                delta_out = out_tok - output_tracker

        # Anthropic message_delta and compatible stop events may expose both
        # input and output cumulatively.  Difference each component separately
        # so repeated final usage events are charged exactly once.
        elif "usage" in event_obj and isinstance(event_obj["usage"], dict):
            usage = event_obj["usage"]
            in_tok = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            out_tok = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            if in_tok > input_tracker:
                delta_in = in_tok - input_tracker
            if out_tok > output_tracker:
                delta_out = out_tok - output_tracker
            if not in_tok and not out_tok:
                tot = int(usage.get("total_tokens") or 0)
                cur = input_tracker + output_tracker
                if tot > cur:
                    delta_out = tot - cur

        return delta_in, delta_out

    def _record_request_summary(
        self,
        request_json: dict[str, Any],
        *,
        request_index: int | None = None,
        request_body_hmac: str | None = None,
    ) -> None:
        """Record bounded request metadata without text, tool args, headers, or keys."""
        if len(self.request_summaries) >= 128:
            return
        messages = request_json.get("messages")
        if not isinstance(messages, list):
            return
        summary: dict[str, Any] = {"blocks": []}
        use_ids: set[str] = set()
        result_ids: set[str] = set()
        error_ids: set[str] = set()
        use_names: dict[str, str] = {}
        result_error_states: dict[str, bool] = {}
        protocol_conflict = False
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            blocks = content if isinstance(content, list) else [content]
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if not isinstance(block_type, str):
                    continue
                entry: dict[str, Any] = {"role": str(role or ""), "type": block_type[:64]}
                if block_type in {"tool_use", "server_tool_use"} and isinstance(block.get("name"), str):
                    entry["tool_name"] = block["name"][:128]
                if block_type in {"tool_use", "server_tool_use", "tool_result"}:
                    tool_use_id = block.get("id") or block.get("tool_use_id")
                    if isinstance(tool_use_id, str):
                        entry["tool_use_id_hmac"] = self._tool_id_hmac(tool_use_id)
                        if block_type in {"tool_use", "server_tool_use"}:
                            use_ids.add(entry["tool_use_id_hmac"])
                            name = str(block.get("name") or "")
                            prior_name = use_names.get(entry["tool_use_id_hmac"])
                            if prior_name is not None and prior_name != name:
                                protocol_conflict = True
                            use_names[entry["tool_use_id_hmac"]] = name
                        else:
                            result_ids.add(entry["tool_use_id_hmac"])
                if block_type == "tool_result":
                    entry["tool_result_is_error"] = bool(block.get("is_error", False))
                    result_id = entry.get("tool_use_id_hmac")
                    if isinstance(result_id, str):
                        state = bool(entry["tool_result_is_error"])
                        prior_state = result_error_states.get(result_id)
                        if prior_state is not None and prior_state != state:
                            protocol_conflict = True
                        result_error_states[result_id] = state
                    if entry.get("tool_result_is_error") is True and isinstance(entry.get("tool_use_id_hmac"), str):
                        error_ids.add(entry["tool_use_id_hmac"])
                summary["blocks"].append(entry)
                if len(summary["blocks"]) >= 256:
                    break
            if len(summary["blocks"]) >= 256:
                break
        self.request_summaries.append(summary)

        # These are per-request unique counts.  The smoke harness aggregates
        # the bounded snapshots by the same HMAC, so SDK retries do not inflate
        # result/error totals.
        summary["tool_use_count"] = len(use_ids)
        summary["tool_result_count"] = len(result_ids)
        summary["tool_result_error_count"] = len(error_ids)
        summary["protocol_conflict"] = protocol_conflict
        if request_index is not None and request_body_hmac is not None:
            if not any(row.get("request_index") == request_index for row in self.request_outcomes):
                self.request_outcomes.append({
                    "request_index": request_index,
                    "request_body_hmac": request_body_hmac,
                    "outcome": "in_flight",
                })
            new_uses = use_ids - self._seen_run_tool_uses
            new_results = result_ids - self._seen_run_tool_results
            duplicate_snapshot = bool(use_ids or result_ids) and not (new_uses or new_results)
            self._seen_run_tool_uses.update(use_ids)
            self._seen_run_tool_results.update(result_ids)
            summary.update({
                "request_index": request_index,
                "request_body_hmac": request_body_hmac,
                "new_tool_use_count": len(new_uses),
                "new_tool_result_count": len(new_results),
                "duplicate_tool_snapshot": duplicate_snapshot,
            })

    def _tool_id_hmac(self, tool_use_id: str) -> str:
        return hmac.new(self._tool_id_hmac_key, tool_use_id.encode("utf-8"), hashlib.sha256).hexdigest()

    def _request_body_hmac(self, request_json: dict[str, Any]) -> str:
        canonical = json.dumps(
            request_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
        return hmac.new(self._request_hmac_key, canonical, hashlib.sha256).hexdigest()

    def _finish_request(self, request_index: int | None, outcome: str) -> None:
        if request_index is None:
            return
        for row in self.request_outcomes:
            if row.get("request_index") == request_index:
                if row.get("outcome") == "in_flight":
                    row["outcome"] = outcome
                return

    def _record_response_summary(
        self,
        summary: dict[str, Any],
        *,
        request_index: int | None = None,
        request_body_hmac: str | None = None,
    ) -> None:
        if request_index is not None and request_body_hmac is not None:
            summary["request_index"] = request_index
            summary["request_body_hmac"] = request_body_hmac
            status = summary.get("status")
            complete = bool(
                isinstance(status, int) and 200 <= status < 300
                and summary.get("protocol_complete") is True
            )
            summary["outcome"] = "complete_2xx" if complete else "interrupted"
            self._finish_request(request_index, summary["outcome"])
        self._append_response_summary(summary)

    def _append_response_summary(self, summary: dict[str, Any]) -> None:
        if len(self.response_summaries) < 128:
            self.response_summaries.append(summary)

    def _record_event_summary(self, event_obj: dict[str, Any], *, normalized: bool = False) -> None:
        """Keep only bounded, secret-free metadata from one SSE event."""
        if len(self.event_summaries) >= 512:
            return
        block = event_obj.get("content_block") or {}
        delta_obj = event_obj.get("delta") or {}
        summary = {
            "type": str(event_obj.get("type", ""))[:64],
            "content_block_type": str(block.get("type", "") if isinstance(block, dict) else "")[:64],
            "tool_name": str(block.get("name", "") if isinstance(block, dict) else "")[:128],
            "stop_reason": str(
                event_obj.get("stop_reason")
                or ((event_obj.get("message") or {}).get("stop_reason", "") if isinstance(event_obj.get("message"), dict) else "")
                or ((event_obj.get("delta") or {}).get("stop_reason", "") if isinstance(event_obj.get("delta"), dict) else "")
            )[:64],
            "tool_result_is_error": str(
                bool(block.get("is_error", False))
                if isinstance(block, dict) and block.get("type") == "tool_result" else False
            ),
            "normalized": bool(normalized),
        }
        if isinstance(block, dict) and block.get("type") in {"tool_use", "server_tool_use", "tool_result"}:
            tool_use_id = block.get("id") or block.get("tool_use_id")
            if isinstance(tool_use_id, str):
                summary["tool_use_id_hmac"] = self._tool_id_hmac(tool_use_id)
        if not summary["content_block_type"] and isinstance(delta_obj, dict):
            summary["content_block_type"] = str(delta_obj.get("type", ""))[:64]
        self.event_summaries.append(summary)
