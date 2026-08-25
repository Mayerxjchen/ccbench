"""bench-hpc client: HTTP-only, bearer-auth, JSON-in/JSON-out.

The client must be able to reach a remote gateway through nothing but
BENCH_HPC_GATEWAY_URL + BENCH_HPC_RUN_TOKEN. It never shells out, never
interpolates job values into a local command, and every command emits stable
JSON on stdout (errors on stderr, nonzero exit).
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from dftworld_bench.hpc.client import HpcClient, HpcClientError

ROOT = Path(__file__).resolve().parents[2]

DIGEST = "img@sha256:" + "a" * 64


def _ok(payload: dict) -> tuple[int, bytes]:
    return 200, json.dumps(payload).encode()


def _fake_transport(records: list):
    def transport(method: str, path: str, payload: object, headers: dict):
        records.append((method, path, payload))
        return _ok({"ok": True, "echo": path})
    return transport


def _client(records: list) -> HpcClient:
    return HpcClient("https://gw.example.test", "tok-123", transport=_fake_transport(records))


def test_submit_posts_job_payload() -> None:
    records: list = []
    client = _client(records)
    spec = {"schema_version": 1, "idempotency_key": "k1", "runtime": DIGEST,
            "command": ["cp2k", "-i", "input.inp"],
            "resources": {"cpus": 8, "memory_gb": 32, "gpus": 0, "walltime_minutes": 60}}
    out = client.submit(spec)
    assert len(records) == 1
    method, path, payload = records[0]
    assert method == "POST"
    assert path == "/api/v1/jobs"
    assert payload == spec
    assert out == {"ok": True, "echo": path}


def test_status_fetches_job() -> None:
    records: list = []
    out = _client(records).status("job-42")
    assert records == [("GET", "/api/v1/jobs/job-42/status", None)]
    assert out["ok"] is True


def test_logs_and_fetch_and_cancel_hit_expected_paths() -> None:
    records: list = []
    client = _client(records)
    client.logs("job-42")
    client.fetch("job-42")
    client.cancel("job-42")
    assert [r[:2] for r in records] == [
        ("GET", "/api/v1/jobs/job-42/logs"),
        ("GET", "/api/v1/jobs/job-42/outputs"),
        ("POST", "/api/v1/jobs/job-42/cancel"),
    ]


def test_capabilities_and_usage_are_read_commands() -> None:
    records: list = []
    client = _client(records)
    client.capabilities()
    client.usage()
    assert [r[:2] for r in records] == [
        ("GET", "/api/v1/capabilities"),
        ("GET", "/api/v1/usage"),
    ]


def test_transport_receives_bearer_and_json_headers() -> None:
    seen: dict = {}

    def transport(method, path, payload, headers):
        seen.update(headers)
        return _ok({"ok": True})

    client = HpcClient("https://gw.example.test", "tok-123", transport=transport)
    client.submit({"idempotency_key": "k1"})
    assert seen["Authorization"] == "Bearer tok-123"
    assert seen["Content-Type"] == "application/json"
    assert "Accept" in seen


def test_http_error_surfaces_client_error() -> None:
    def transport(method, path, payload, headers):
        return 500, b'{"error": "gateway exploded"}'

    client = HpcClient("https://gw.example.test", "tok-123", transport=transport)
    with pytest.raises(HpcClientError, match="500"):
        client.status("job-42")


def test_client_error_message_from_server_payload() -> None:
    def transport(method, path, payload, headers):
        return 422, b'{"error": "walltime exceeds profile"}'

    client = HpcClient("https://gw.example.test", "tok-123", transport=transport)
    with pytest.raises(HpcClientError, match="walltime exceeds profile"):
        client.submit({"idempotency_key": "k1"})


# --- CLI ---


@pytest.fixture
def cli_env(monkeypatch) -> None:
    monkeypatch.setenv("BENCH_HPC_GATEWAY_URL", "https://gw.example.test")
    monkeypatch.setenv("BENCH_HPC_RUN_TOKEN", "tok-123")


class _capture:
    """Context-manager that replaces sys.stdout/stderr and captures text."""

    def __enter__(self):
        self._out = io.StringIO()
        self._err = io.StringIO()
        self._old = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = self._out, self._err
        return self

    def __exit__(self, *exc):
        sys.stdout, sys.stderr = self._old

    def text(self):
        return self._out.getvalue()

    def err(self):
        return self._err.getvalue()


def _run(argv: list[str], records: list) -> tuple[int, _capture]:
    from dftworld_bench.hpc.__main__ import main
    with _capture() as cap:
        rc = main(argv, transport=_fake_transport(records))
    return rc, cap


def test_cli_capabilities_emits_stable_json_on_stdout(cli_env) -> None:
    rc, cap = _run(["capabilities"], [])
    assert rc == 0
    assert json.loads(cap.text()) == {"ok": True, "echo": "/api/v1/capabilities"}
    assert cap.err() == ""


def test_cli_submit_reads_job_file(cli_env, tmp_path) -> None:
    job = tmp_path / "job.yaml"
    job.write_text(
        'schema_version: 1\n'
        'idempotency_key: "cli-submit-1"\n'
        f'runtime: "{DIGEST}"\n'
        'command: [cp2k, -i, input.inp]\n'
        'resources: {cpus: 8, memory_gb: 32, gpus: 0, walltime_minutes: 60}\n'
        'inputs: [input.inp]\n'
        'outputs: [output.out]\n',
        encoding="utf-8",
    )
    records: list = []
    rc, cap = _run(["submit", str(job)], records)
    assert rc == 0
    assert records[0][0] == "POST"
    payload = records[0][2]
    assert payload["idempotency_key"] == "cli-submit-1"
    assert payload["command"] == ["cp2k", "-i", "input.inp"]
    assert json.loads(cap.text()) == {"ok": True, "echo": "/api/v1/jobs"}


def test_cli_status_logs_fetch_cancel_usage_map_to_commands(cli_env) -> None:
    records: list = []
    for argv in (["status", "job-1"], ["logs", "job-1"], ["fetch", "job-1"],
                 ["cancel", "job-1"], ["usage"]):
        rc, cap = _run(argv, records)
        assert rc == 0
        assert cap.err() == ""
        assert json.loads(cap.text()).get("ok") is True
    assert [r[:2] for r in records] == [
        ("GET", "/api/v1/jobs/job-1/status"),
        ("GET", "/api/v1/jobs/job-1/logs"),
        ("GET", "/api/v1/jobs/job-1/outputs"),
        ("POST", "/api/v1/jobs/job-1/cancel"),
        ("GET", "/api/v1/usage"),
    ]


def test_cli_requires_gateway_and_token(monkeypatch) -> None:
    monkeypatch.delenv("BENCH_HPC_GATEWAY_URL", raising=False)
    monkeypatch.delenv("BENCH_HPC_RUN_TOKEN", raising=False)
    rc, cap = _run(["capabilities"], [])
    assert rc == 2
    assert cap.text() == ""
    assert "BENCH_HPC_GATEWAY_URL" in cap.err()


def test_cli_unknown_command_errors_on_stderr() -> None:
    rc, cap = _run(["explode"], [])
    assert rc == 2
    assert cap.text() == ""
    assert "unknown command" in cap.err()


def test_cli_unknown_command_lists_commands() -> None:
    """Unknown operations are self-describing: the error names every legal
    command so a controller operator knows what the CLI can do."""
    rc, cap = _run(["not-a-command"], [])
    assert rc == 2
    assert "capabilities" in cap.err()
    assert "submit" in cap.err()


def test_cli_help_prints_usage_on_stdout_and_exits_zero() -> None:
    rc, cap = _run(["help"], [])
    assert rc == 0
    assert "usage" in cap.text()
    assert "capabilities" in cap.text()


def test_cli_reports_client_errors_on_stderr(cli_env) -> None:
    def transport(method, path, payload, headers):
        return 502, b'{"error": "gateway unreachable"}'

    from dftworld_bench.hpc.__main__ import main
    with _capture() as cap:
        rc = main(["status", "job-1"], transport=transport)
    assert rc == 1
    assert cap.text() == ""
    assert "502" in cap.err()


def test_job_values_never_reach_local_shell() -> None:
    """The client speaks HTTP only; no job field is interpolated into a command."""
    src = (ROOT / "dftworld_bench" / "hpc" / "client.py").read_text(encoding="utf-8")
    assert "subprocess" not in src
    assert "os.system" not in src
    assert "shlex" not in src
    main_src = (ROOT / "dftworld_bench" / "hpc" / "__main__.py").read_text(encoding="utf-8")
    assert "subprocess" not in main_src
    assert "os.system" not in main_src


# --- v2: operation-attempt client + CLI ---


def test_client_v2_submit_posts_attempt_identity() -> None:
    records: list = []
    client = _client(records)
    result = client.submit_v2(
        {"schema_version": 1},
        run_id="run-a",
        operation_id="cp2k-round-01",
        attempt=2,
    )
    assert result["ok"] is True
    method, path, payload = records[0]
    assert (method, path) == ("POST", "/v2/submit")
    assert payload["operation_id"] == "cp2k-round-01"
    assert payload["attempt"] == 2
    assert payload["run_id"] == "run-a"


def test_client_v2_status_sends_optional_attempt() -> None:
    records: list = []
    client = _client(records)
    client.status_operation("run-a", "cp2k-round-01")
    client.status_operation("run-a", "cp2k-round-01", 3)
    assert records[0][2]["attempt"] is None
    assert records[1][2]["attempt"] == 3
    assert {r[1] for r in records} == {"/v2/status"}


def test_cli_v2_status_routes_operation_with_run_env(cli_env, monkeypatch) -> None:
    monkeypatch.setenv("BENCH_HPC_RUN_ID", "run-a")
    records: list = []
    rc, cap = _run(["status", "cp2k-round-01", "--attempt", "2"], records)
    assert rc == 0
    _, path, payload = records[0]
    assert path == "/v2/status"
    assert payload == {
        "run_id": "run-a",
        "operation_id": "cp2k-round-01",
        "attempt": 2,
    }


def test_cli_operation_without_run_env_fails_closed(cli_env, monkeypatch) -> None:
    monkeypatch.delenv("BENCH_HPC_RUN_ID", raising=False)
    rc, cap = _run(["status", "round-01"], [])
    assert rc == 2
    assert "BENCH_HPC_RUN_ID" in cap.err()


def test_cli_hidden_v1_job_id_compat_unchanged(cli_env, monkeypatch) -> None:
    """Historical job ids keep hitting the frozen v1 paths — never reinterpreted."""
    monkeypatch.delenv("BENCH_HPC_RUN_ID", raising=False)
    records: list = []
    rc, cap = _run(["status", "job-0001"], records)
    assert rc == 0
    assert records[0][1] == "/api/v1/jobs/job-0001/status"
    records.clear()
    rc, cap = _run(["cancel", "12345"], records)
    assert rc == 0
    assert records[0][1] == "/api/v1/jobs/12345/cancel"


def test_cli_v2_submit_requires_run_env(cli_env, tmp_path, monkeypatch) -> None:
    import io
    import sys as _sys

    from dftworld_bench.hpc.__main__ import main

    job = tmp_path / "job.yaml"
    job.write_text(
        'schema_version: 1\n'
        'idempotency_key: "cli-v2-1"\n'
        f'runtime: "{DIGEST}"\n'
        'command: [echo, hi]\n'
        'resources: {cpus: 1, memory_gb: 1, gpus: 0, walltime_minutes: 5}\n'
        'inputs: []\n'
        'outputs: []\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("BENCH_HPC_RUN_ID", raising=False)
    with _capture() as cap:
        rc = main(["submit", str(job), "op-1", "1"], transport=_fake_transport([]))
    assert rc == 2
    assert "BENCH_HPC_RUN_ID" in cap.err()
