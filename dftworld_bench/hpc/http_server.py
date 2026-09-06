"""[DEPRECATED] Common HTTP binding for the trusted Gateway.

.. deprecated:: 2.0
    The external HTTP server is deprecated.
    Public architecture:
        Harness -> HpcDispatcher
    Current internal compatibility:
        HpcDispatcher -> GatewayRuntime -> Gateway -> backend
    Target Phase 3:
        HpcDispatcher -> Driver

The Candidate talks to this server only; the scheduler lives behind it.  One
:class:`Gateway` per run, a run-scoped bearer token in ``Authorization``, and
the run id in the JSON body — the server never trusts a client-supplied run id
beyond what the token already authorizes.  Every operation POSTs JSON to its
path; errors are ``{"error": <message>}`` with a 4xx status.
"""

from __future__ import annotations

import http.server
import json
import threading
from typing import Any

from dftworld_bench.hpc.gateway import Gateway, GatewayError

_JSON = "application/json"


class HttpGatewayServer:
    """Serves one Gateway's seven operations over loopback HTTP."""

    def __init__(self, gateway: Gateway, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self._gateway = gateway
        self._host = host
        self._port = port
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""

    @property
    def port(self) -> int:
        """The bound port after ``start`` (0 before)."""
        return self._port

    def start(self) -> str:
        """Bind a loopback port, serve in a daemon thread, return the base URL."""
        handler = self._handler()
        self._httpd = http.server.ThreadingHTTPServer((self._host, self._port), handler)
        self._port = self._httpd.server_address[1]
        self.url = f"http://{self._host}:{self._port}"
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="hpc-gateway"
        )
        self._thread.start()
        return self.url

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def __enter__(self) -> "HttpGatewayServer":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _handler(self):
        def handler_class_factory(*, server_self: "HttpGatewayServer"):
            class _Handler(http.server.BaseHTTPRequestHandler):
                server_version = "hpc-gateway/1"

                def log_message(self, fmt: str, *args: Any) -> None:
                    pass  # the daemon stays quiet; the audit is the record

                def _token(self) -> str | None:
                    auth = self.headers.get("Authorization", "")
                    if auth.startswith("Bearer "):
                        return auth[len("Bearer "):].strip()
                    return None

                def do_POST(self) -> None:  # noqa: N802
                    op = _OPS.get(self.path)
                    if op is None:
                        self._fail(404, f"unknown op path: {self.path!r}")
                        return
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        raw = self.rfile.read(length) if length else b"{}"
                        payload = json.loads(raw.decode("utf-8")) if raw else {}
                    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                        self._fail(400, "malformed JSON body")
                        return
                    token = self._token()
                    if token is None:
                        self._fail(401, "missing bearer token")
                        return
                    try:
                        result = server_self._dispatch(token, op, payload)
                    except GatewayError as exc:
                        self._fail(exc.status, str(exc))
                        return
                    body = json.dumps(result, sort_keys=True).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", _JSON)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def _fail(self, code: int, message: str) -> None:
                    body = json.dumps({"error": message}).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", _JSON)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            return _Handler

        return handler_class_factory(server_self=self)

    def _dispatch(self, token: str, op: str, payload: dict) -> dict[str, Any]:
        run_id = _require(payload, "run_id")
        if op == "submit":
            spec = payload.get("spec")
            if not isinstance(spec, dict):
                raise GatewayError("submit requires a spec object")
            return self._gateway.submit(
                token, run_id, spec,
                operation_id=_require(payload, "operation_id"),
            )
        if op == "submit_v2":
            spec = payload.get("spec")
            if not isinstance(spec, dict):
                raise GatewayError("submit requires a spec object")
            return self._gateway.submit(
                token, run_id, spec,
                operation_id=_require(payload, "operation_id"),
                attempt=_opt_attempt(payload),
            )
        if op in ("operation_status", "operation_logs", "operation_fetch", "operation_cancel"):
            operation_id = _require(payload, "operation_id")
            attempt = _opt_attempt(payload)
            if op == "operation_status":
                return self._gateway.operation_status(token, run_id, operation_id, attempt)
            method_name = op.split("_")[1]
            job_id = self._gateway.resolve_operation_attempt(
                token, run_id, operation_id, attempt
            )
            return getattr(self._gateway, method_name)(token, run_id, job_id)
        if op == "status":
            return self._gateway.status(token, run_id, _require(payload, "job_id"))
        if op == "logs":
            return self._gateway.logs(token, run_id, _require(payload, "job_id"))
        if op == "fetch":
            return self._gateway.fetch(token, run_id, _require(payload, "job_id"))
        if op == "cancel":
            return self._gateway.cancel(token, run_id, _require(payload, "job_id"))
        if op == "capabilities":
            return self._gateway.capabilities(token, run_id)
        if op == "usage":
            return self._gateway.usage(token, run_id)
        raise GatewayError(f"unknown operation {op!r}")


_OPS = {
    "/submit": "submit",
    "/status": "status",
    "/logs": "logs",
    "/fetch": "fetch",
    "/cancel": "cancel",
    "/capabilities": "capabilities",
    "/usage": "usage",
    # v2: explicit (run, operation, attempt) identity; /v1 paths stay frozen.
    "/v2/submit": "submit_v2",
    "/v2/status": "operation_status",
    "/v2/logs": "operation_logs",
    "/v2/fetch": "operation_fetch",
    "/v2/cancel": "operation_cancel",
}


def _opt_attempt(payload: dict) -> int | None:
    value = payload.get("attempt")
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise GatewayError("attempt must be a positive integer", status=400)
    return value


def _require(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value == "":
        raise GatewayError(f"missing required field: {key}", status=400)
    return value
