"""[DEPRECATED] bench-hpc client: talk to a trusted gateway over plain HTTP(S).

.. deprecated:: 2.0
    Direct HTTP client access is deprecated.
    Public architecture:
        Harness -> HpcDispatcher
    Current internal compatibility:
        HpcDispatcher -> GatewayRuntime -> Gateway -> backend
    Target Phase 3:
        HpcDispatcher -> Driver

The client is deliberately dumb:

- it knows one gateway URL and one bearer token (``BENCH_HPC_GATEWAY_URL``,
  ``BENCH_HPC_RUN_TOKEN``);
- it exchanges JSON over HTTP and never touches the local scheduler or SSH;
- job fields are payload data, never interpolated into a local command.

The injected ``transport`` hook keeps the whole API testable without a
network: ``transport(method, path, payload, headers) -> (status, body_bytes)``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

Transport = Callable[[str, str, object | None, dict[str, str]], tuple[int, bytes]]


class HpcClientError(Exception):
    """A gateway call failed: unreachable, non-2xx, or malformed reply."""


class HpcClient:
    """HTTP client for one benchmark HPC gateway."""

    def __init__(
        self,
        gateway_url: str,
        run_token: str,
        *,
        transport: Transport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base = gateway_url.rstrip("/")
        self._token = run_token
        self._timeout = timeout
        self._transport = transport or self._http_transport

    # -- commands ---------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        return self._call("GET", "/api/v1/capabilities")

    def submit(self, spec: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", "/api/v1/jobs", spec)

    def status(self, job_id: str) -> dict[str, Any]:
        return self._call("GET", self._job_path(job_id) + "/status")

    def logs(self, job_id: str) -> dict[str, Any]:
        return self._call("GET", self._job_path(job_id) + "/logs")

    def fetch(self, job_id: str) -> dict[str, Any]:
        return self._call("GET", self._job_path(job_id) + "/outputs")

    def cancel(self, job_id: str) -> dict[str, Any]:
        return self._call("POST", self._job_path(job_id) + "/cancel")

    def usage(self) -> dict[str, Any]:
        return self._call("GET", "/api/v1/usage")

    # -- v2: operation-attempt commands ------------------------------------

    def submit_v2(
        self,
        spec: dict[str, Any],
        *,
        run_id: str,
        operation_id: str,
        attempt: int,
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v2/submit",
            {
                "run_id": run_id,
                "spec": spec,
                "operation_id": operation_id,
                "attempt": attempt,
            },
        )

    def status_operation(
        self,
        run_id: str,
        operation_id: str,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "POST", "/v2/status",
            {"run_id": run_id, "operation_id": operation_id, "attempt": attempt},
        )

    def logs_operation(
        self,
        run_id: str,
        operation_id: str,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "POST", "/v2/logs",
            {"run_id": run_id, "operation_id": operation_id, "attempt": attempt},
        )

    def fetch_operation(
        self,
        run_id: str,
        operation_id: str,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "POST", "/v2/fetch",
            {"run_id": run_id, "operation_id": operation_id, "attempt": attempt},
        )

    def cancel_operation(
        self,
        run_id: str,
        operation_id: str,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        return self._call(
            "POST", "/v2/cancel",
            {"run_id": run_id, "operation_id": operation_id, "attempt": attempt},
        )

    # -- plumbing ---------------------------------------------------------

    @staticmethod
    def _job_path(job_id: str) -> str:
        return "/api/v1/jobs/" + urllib.parse.quote(job_id, safe="")

    def _call(self, method: str, path: str, payload: object | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            status, body = self._transport(method, path, payload, headers)
        except HpcClientError:
            raise
        except Exception as exc:  # urllib, DNS, TLS, socket — all infrastructure
            raise HpcClientError(f"cannot reach gateway: {exc}") from exc
        if not 200 <= status < 300:
            raise HpcClientError(_server_error(status, body))
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HpcClientError(f"gateway returned malformed JSON (HTTP {status}): {exc}") from exc

    def _http_transport(self, method: str, path: str, payload: object | None,
                        headers: dict[str, str]) -> tuple[int, bytes]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise HpcClientError(f"cannot reach gateway at {self._base}: {exc.reason}") from exc


def _server_error(status: int, body: bytes) -> str:
    try:
        message = json.loads(body.decode("utf-8")).get("error")
    except (UnicodeDecodeError, json.JSONDecodeError):
        message = None
    return f"HTTP {status}: {message}" if message else f"HTTP {status}"
