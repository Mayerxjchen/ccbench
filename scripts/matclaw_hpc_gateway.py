"""Trusted-gateway bridge for the MatClaw controller (Cases 031-033).

The controller image ships no SSH client, rsync, key, or config.  Scheduler
access lives on the trusted bench-hpc gateway on the site host.  This module
implements both halves of the bridge:

* ``MatclawGatewayServer`` — host-side HTTP server.  eval.py builds it around a
  real :class:`SshSlurmTransport` and hands the URL + run-scoped bearer token
  to the controller container via ``BENCH_HPC_GATEWAY_URL`` /
  ``BENCH_HPC_RUN_TOKEN``.  It exposes exactly the six transport operations the
  controller uses (plus ``remote_arch``); every request must carry the token,
  and every job stays scoped to the run that owns the token.

* ``GatewaySlurmTransport`` — controller-side transport implementing the same
  ``SlurmTransport`` interface over HTTP.  The controller's domain logic
  (slurm template rendering, policy assertions, run-id validation) is
  untouched; only the I/O backend swaps.

Transport contract kept identical to :class:`scripts.ablation.transport.slurm_transport.SshSlurmTransport`
so the controller and its 97 tests exercise one interface regardless of backend.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

try:
    from scripts.ablation.transport.slurm_transport import (
        JobState,
        SlurmTransport,
        SubmitOpts,
        TransportError,
    )
except ImportError:  # controller-image layout: modules sit flat in
    # /opt/dftworld/controller/ (sys.path inserted by matclaw_hpc_controller)
    from slurm_transport import (  # type: ignore[import-not-found]
        JobState,
        SlurmTransport,
        SubmitOpts,
        TransportError,
    )

GATEWAY_URL_ENV = "BENCH_HPC_GATEWAY_URL"
RUN_TOKEN_ENV = "BENCH_HPC_RUN_TOKEN"

# Wire protocol: JSON over HTTP, one operation per path.
_OP_PATHS = {
    "stage": "/ops/stage",
    "submit": "/ops/submit",
    "status": "/ops/status",
    "log": "/ops/log",
    "cancel": "/ops/cancel",
    "fetch": "/ops/fetch",
    "remote_arch": "/ops/remote_arch",
}


class MatclawGatewayError(Exception):
    """Server-side protocol violation (bad token, unknown op, transport fault)."""


def _validate_run_id_component(value: str) -> None:
    if not value or ".." in value or value.startswith("/") or value.startswith("~"):
        raise MatclawGatewayError(f"unsafe path component: {value!r}")


class MatclawGatewayServer:
    """One HTTP endpoint around one :class:`SlurmTransport`.

    The server owns the real scheduler transport (SSH).  It accepts only a
    run-scoped bearer token (``BENCH_HPC_RUN_TOKEN`` on the controller side),
    so the agent can drive its own run's jobs but nothing else.
    """

    def __init__(self, transport: SlurmTransport, run_token: str, *, port: int = 0) -> None:
        self._transport = transport
        self._run_token = run_token
        self._httpd: Optional[http.server.ThreadingHTTPServer] = None
        self._port = port
        self._thread: Optional[threading.Thread] = None
        self.url: str = ""

    @property
    def port(self) -> int:
        """The bound loopback port (after ``start``).  The eval host uses it to
        hand the controller container a ``host.docker.internal`` URL."""
        return self._port

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> str:
        """Bind a loopback port, serve in a daemon thread, return the base URL."""
        handler = self._handler()
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self._port), handler)
        self._port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self._port}"
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True, name="matclaw-gateway"
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

    def __enter__(self) -> "MatclawGatewayServer":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _handler(self):
        def handler_class_factory(*, server_self: "MatclawGatewayServer"):
            class _Handler(http.server.BaseHTTPRequestHandler):
                server_version = "matclaw-gateway/1"

                def log_message(self, fmt: str, *args: Any) -> None:
                    # keep the daemon quiet unless verbose debugging is wanted
                    pass

                def _authorize(self) -> None:
                    auth = self.headers.get("Authorization", "")
                    expected = f"Bearer {server_self._run_token}"
                    if auth != expected:
                        raise MatclawGatewayError("unauthorized")

                def _dispatch(self, op: str, payload: Optional[dict]) -> None:
                    self._authorize()
                    result = server_self._call(op, payload or {})
                    body = json.dumps(result, sort_keys=True).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def do_POST(self) -> None:  # noqa: N802
                    try:
                        length = int(self.headers.get("Content-Length", 0))
                        raw = self.rfile.read(length) if length else b"{}"
                        payload = json.loads(raw.decode("utf-8")) if raw else {}
                        op = _OP_REVERSE.get(self.path)
                        if op is None:
                            self._fail(404, f"unknown op path: {self.path!r}")
                            return
                        self._dispatch(op, payload)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        self._fail(400, "malformed JSON body")
                    except MatclawGatewayError as exc:
                        self._fail(401 if "unauthor" in str(exc) else 400, str(exc))
                    except TransportError as exc:
                        self._fail(502, f"transport: {exc}")
                    except Exception as exc:  # noqa: BLE001
                        self._fail(500, f"gateway: {exc}")

                def _fail(self, code: int, message: str) -> None:
                    body = json.dumps({"error": message}).encode("utf-8")
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            return _Handler

        return handler_class_factory(server_self=self)

    # -- transport ops ------------------------------------------------------

    def _host_path(self, p: str) -> str:
        """Map the controller container's ``/app`` view to the gateway's host
        workspace.  The agent container binds ``/app`` as a symlink to the host
        workspace (bind_workspace_as_app), so a path the controller reports as
        ``/app/<rel>`` exists on the gateway host at ``<workspace>/<rel>``.  Any
        other path is passed through unchanged (host-absolute already).
        """
        ws = str(getattr(self._transport, "workspace", "/app"))
        if p == "/app":
            return ws
        if p.startswith("/app/"):
            return str(Path(ws) / p[len("/app/"):])
        return p

    def _contained_local(self, p: str) -> str:
        """Contain a host path inside the run workspace (common Gateway rule).

        The token must never read an arbitrary gateway-host file: staged inputs
        and submitted scripts resolve inside the run workspace or are rejected.
        The import is deferred so the controller image (which ships only the
        client half) never needs ``dftworld_bench`` at import time.
        """
        from dftworld_bench.hpc.gateway import GatewayError as _CommonGatewayError
        from dftworld_bench.hpc.gateway import contained

        ws = getattr(self._transport, "workspace", None)
        if not ws:
            raise MatclawGatewayError("gateway has no run workspace root")
        try:
            return str(contained(Path(ws), Path(p)))
        except _CommonGatewayError as exc:
            raise MatclawGatewayError(str(exc)) from exc

    def _call(self, op: str, payload: dict) -> dict:
        t = self._transport
        if op == "stage":
            # local paths must be contained in the run workspace — an absolute
            # host path (e.g. /etc/passwd) is rejected here, not just
            # ``..``-escape (the remote target dir keeps its single-component
            # validation too).
            paths = [self._contained_local(self._host_path(str(p)))
                     for p in (payload.get("paths") or [])]
            remote_dir = str(payload.get("remote_dir", ""))
            _validate_run_id_component(remote_dir)
            remote = t.stage(paths, remote_dir)
            return {"remote_paths": [str(p) for p in remote]}
        if op == "submit":
            script = self._contained_local(self._host_path(str(payload.get("script", ""))))
            opts_dict = payload.get("opts")
            opts: Optional[SubmitOpts] = None
            if opts_dict is not None:
                opts = SubmitOpts(**{k: v for k, v in opts_dict.items() if k != "extra"})
                extra = opts_dict.get("extra")
                if extra:
                    opts.extra = tuple(extra)
            job_id = t.submit(script, opts)
            return {"job_id": str(job_id)}
        if op == "status":
            job_id = str(payload.get("job_id", ""))
            state = t.status(job_id)
            return {"state": state.value}
        if op == "log":
            job_id = str(payload.get("job_id", ""))
            tail = payload.get("tail")
            return {"log": t.log(job_id, tail=int(tail) if tail is not None else None)}
        if op == "cancel":
            job_id = str(payload.get("job_id", ""))
            t.cancel(job_id)
            return {"ok": True}
        if op == "fetch":
            # remote paths are gateway-constructed (stage() return values), not
            # agent-chosen; local_dir is a host path.  No component validation
            # here — the run-id gate already ran on the controller side.
            remote_paths = [str(p) for p in (payload.get("remote_paths") or [])]
            local_dir = self._host_path(str(payload.get("local_dir", "")))
            local = t.fetch(remote_paths, local_dir)
            return {"local_paths": [str(p) for p in local]}
        if op == "remote_arch":
            return {"arch": str(t.remote_arch())}
        raise MatclawGatewayError(f"unknown op {op!r}")


_OP_REVERSE = {path: op for op, path in _OP_PATHS.items()}


class GatewaySlurmTransport(SlurmTransport):
    """Controller-side ``SlurmTransport`` backed by ``MatclawGatewayServer``.

    Talks JSON over HTTP to the trusted gateway; the real SSH transport lives
    on the gateway host.  The interface is identical to ``SshSlurmTransport``,
    so the controller and its tests are backend-agnostic.
    """

    def __init__(
        self,
        gateway_url: str,
        run_token: str,
        *,
        timeout: float = 60.0,
    ) -> None:
        self._base = gateway_url.rstrip("/")
        self._token = run_token
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> Optional["GatewaySlurmTransport"]:
        url = os.environ.get(GATEWAY_URL_ENV)
        token = os.environ.get(RUN_TOKEN_ENV)
        if not url or not token:
            return None
        return cls(url, token)

    def _post(self, op: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self._base + _OP_PATHS[op],
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(body).get("error", body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                message = body
            raise TransportError(f"gateway {op} failed: HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"gateway unreachable at {self._base}: {exc.reason}") from exc

    # -- SlurmTransport interface -------------------------------------------

    def submit(
        self,
        script: Union[str, os.PathLike[str]],
        opts: Optional[Union[SubmitOpts, Dict[str, Any]]] = None,
    ) -> str:
        if isinstance(opts, SubmitOpts):
            opts_dict: Optional[dict] = asdict(opts)
        else:
            opts_dict = dict(opts) if opts else None
        result = self._post("submit", {"script": str(script), "opts": opts_dict})
        return str(result["job_id"])

    def status(self, job_id: str) -> JobState:
        result = self._post("status", {"job_id": job_id})
        return JobState(str(result["state"]))

    def log(self, job_id: str, tail: Optional[int] = None) -> str:
        result = self._post("log", {"job_id": job_id, "tail": tail})
        return str(result.get("log", ""))

    def cancel(self, job_id: str) -> None:
        self._post("cancel", {"job_id": job_id})

    def stage(
        self,
        local_paths: Iterable[Union[str, os.PathLike[str]]],
        remote_dir: str,
    ) -> List[str]:
        result = self._post(
            "stage", {"paths": [str(p) for p in local_paths], "remote_dir": remote_dir}
        )
        return list(result["remote_paths"])

    def fetch(
        self,
        remote_paths: Iterable[str],
        local_dir: Union[str, os.PathLike[str]],
    ) -> List[Path]:
        result = self._post(
            "fetch", {"remote_paths": list(remote_paths), "local_dir": str(local_dir)}
        )
        return [Path(p) for p in result["local_paths"]]

    def remote_arch(self) -> str:
        result = self._post("remote_arch", {})
        return str(result["arch"])
