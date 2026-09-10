from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import sys
import signal
from pathlib import Path

import pytest

from scripts.infra import live_candidate_smoke as smoke


class _Proxy:
    turn_count = 2
    tokens_used = 17
    budget_exceeded_reason = None


class _FakeAdapter:
    mode = "pass"
    close_error = False
    instances: list["_FakeAdapter"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._model_proxy = _Proxy()
        self.instances.append(self)

    async def prepare(self):
        return None

    async def start(self, prompt: str):
        self.prompt = prompt
        if self.mode == "exception":
            raise ValueError("adapter failed")
        output = Path(self.kwargs["workspace"]) / "final" / "network-smoke.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        if self.mode == "pass":
            output.write_bytes(smoke.EXPECTED_OUTPUT)
        elif self.mode == "mismatch":
            output.write_bytes(b"candidate-network-ok")

    def collect_logs(self):
        return {
            "requested_model": self.kwargs["model"],
            "cli_model": "claude-sonnet-4-6",
            "upstream_model": self.kwargs["model"],
            "image_digest": "sha256:candidate",
            "usage": {"total_tokens": 17, "secret": "must-not-copy"},
            "model_gateway": {
                "turn_count": 2,
                "tokens_used": 17,
                "budget_exceeded_reason": None,
                "preflight": {"classification": "ok", "status": 200},
                "usage_events": [{"input_tokens": 4, "output_tokens": 13}],
                "response_summaries": [{
                    "transport": "sse", "status": 200, "parsed": True,
                    "protocol_complete": True, "stop_reason": "end_turn",
                    "tool_use_count": 1, "native_terminal": True,
                    "normalized_terminal": False,
                    "usage": {"input_tokens": 4, "output_tokens": 13, "total_tokens": 17},
                    "charged_delta": 17,
                }],
                "event_summaries": [{
                    "type": "content_block_start",
                    "content_block_type": "tool_use",
                    "tool_name": "Bash",
                    "text": "secret prompt",
                    "input": {"command": "secret"},
                }],
                "request_summaries": [{
                    "blocks": [{"role": "assistant", "type": "tool_use", "tool_name": "Bash",
                                "tool_use_id_hmac": "a" * 64}],
                    "headers": {"x-api-key": "secret"},
                }],
            },
        }

    async def close(self):
        if self.close_error:
            raise RuntimeError("cleanup failure")


class _CancelledAdapter(_FakeAdapter):
    async def start(self, prompt: str):
        raise asyncio.CancelledError()


class _SlowCloseAdapter(_FakeAdapter):
    async def close(self):
        await asyncio.sleep(60)


def _configure(monkeypatch, mode: str = "pass", close_error: bool = False):
    _FakeAdapter.mode = mode
    _FakeAdapter.close_error = close_error
    _FakeAdapter.instances.clear()
    monkeypatch.setattr(smoke, "ClaudeCodeAdapter", _FakeAdapter)
    monkeypatch.setenv("BENCH_BASE_URL", "http://gateway.invalid")
    monkeypatch.setenv("BENCH_API_KEY", "test-only-key")
    monkeypatch.setenv("BENCH_MODEL", "dotenv-model")


def test_smoke_prompt_and_exact_lf_output(monkeypatch, tmp_path: Path):
    _configure(monkeypatch)
    result = asyncio.run(smoke._run(tmp_path, 0.20, 50_000, 8, model_override="cli-model"))
    assert result["ok"] is True
    assert _FakeAdapter.instances[0].prompt.endswith("one LF byte (0x0A), with no other bytes.\n")
    output = tmp_path / "candidate" / "final" / "network-smoke.txt"
    assert output.read_bytes() == b"candidate-network-ok\n"
    assert result["requested_model"] == "cli-model"
    assert _FakeAdapter.instances[0].kwargs["model"] == "cli-model"
    assert result["turn_count"] == 2
    assert result["tokens_used"] == 17
    assert len(result["prompt_digest"]) == 64
    assert len(result["tool_policy_digest"]) == 64
    assert result["tool_use_count"] == 1
    assert result["tool_result_count"] == 0
    assert result["tool_pairs_unmatched"] == 1
    assert result["native_terminal_count"] == 1
    assert result["normalized_terminal_count"] == 0
    assert result["protocol_ok"] is True
    assert result["usage_accounted"] is True
    ledger = tmp_path / smoke.LEDGER_NAME
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    ledger_payload = json.loads(ledger.read_text())
    assert ledger_payload["schema_version"] == 1
    assert result["resource_run_uid"] == ledger_payload["run_uid"]
    assert len(ledger_payload["run_uid"]) == 32


def test_mismatch_still_returns_safe_diagnostic_and_result_file(monkeypatch, tmp_path: Path):
    _configure(monkeypatch, mode="mismatch")
    result = asyncio.run(smoke._run(tmp_path, 0.20, 50_000, 8))
    assert result["ok"] is False
    assert result["failure_kind"] == "artifact_mismatch"
    assert result["candidate_completed"] is True
    assert result["output_exists"] is True
    assert result["output_size"] == 20
    assert result["output_exact"] is False
    assert result["output_sha256"] == hashlib.sha256(b"candidate-network-ok").hexdigest()
    rendered = json.dumps(result, sort_keys=True)
    assert "secret" not in rendered
    assert "headers" not in rendered
    assert "command" not in rendered
    smoke._write_result(tmp_path, result)
    result_file = tmp_path / smoke.RESULT_NAME
    assert stat.S_IMODE(result_file.stat().st_mode) == 0o600
    assert json.loads(result_file.read_text()) == result
    assert not list(tmp_path.glob(f".{smoke.RESULT_NAME}.*.tmp"))


def test_cleanup_failure_is_fail_closed_and_preserves_diagnostics(monkeypatch, tmp_path: Path):
    _configure(monkeypatch, close_error=True)
    result = asyncio.run(smoke._run(tmp_path, 0.20, 50_000, 8))
    assert result["ok"] is False
    assert result["failure_kind"] == "cleanup_failed"
    assert result["error_type"] == "RuntimeError"
    assert result["cleanup_ok"] is False
    assert result["turn_count"] == 2
    assert result["tokens_used"] == 17


def test_adapter_exception_still_returns_result(monkeypatch, tmp_path: Path):
    _configure(monkeypatch, mode="exception")
    result = asyncio.run(smoke._run(tmp_path, 0.20, 50_000, 8))
    assert result["ok"] is False
    assert result["failure_kind"] == "adapter_exception"
    assert result["error_type"] == "ValueError"
    assert result["candidate_completed"] is False
    assert result["cleanup_ok"] is True


@pytest.mark.parametrize("signal_name", [signal.SIGTERM, signal.SIGINT])
def test_signal_handler_marks_signal_and_cancels_task(signal_name: signal.Signals):
    class FakeLoop:
        def __init__(self):
            self.handlers = {}

        def add_signal_handler(self, sig, callback, *args):
            self.handlers[sig] = (callback, args)

    class FakeTask:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    loop = FakeLoop()
    task = FakeTask()
    received: list[str | None] = [None]
    installed = smoke._install_signal_cancellation(loop, task, received)
    callback, args = loop.handlers[signal_name]
    callback(*args)
    assert installed == [signal.SIGTERM, signal.SIGINT]
    assert received == [signal_name.name]
    assert task.cancelled is True


def test_signal_cancellation_is_classified_and_closes(monkeypatch, tmp_path: Path):
    _configure(monkeypatch)
    monkeypatch.setattr(smoke, "ClaudeCodeAdapter", _CancelledAdapter)
    result = asyncio.run(smoke._run(tmp_path, 0.20, 50_000, 8))
    assert result["ok"] is False
    assert result["failure_kind"] == "signal_cancelled"
    assert result["failure_code"] == "HARNESS_CANCELLED"
    assert result["signal"] is None
    assert result["cleanup_ok"] is True
    smoke._write_result(tmp_path, result)
    assert stat.S_IMODE((tmp_path / smoke.RESULT_NAME).stat().st_mode) == 0o600
    assert json.loads((tmp_path / smoke.LEDGER_NAME).read_text())["status"] == "cancelled"


def test_agent_timeout_has_distinct_failure_code(monkeypatch, tmp_path: Path):
    _configure(monkeypatch, mode="exception")

    class AgentTimeoutError(Exception):
        pass

    class _TimeoutAdapter(_FakeAdapter):
        async def start(self, prompt: str):
            raise AgentTimeoutError("deadline")

    monkeypatch.setattr(smoke, "ClaudeCodeAdapter", _TimeoutAdapter)
    result = asyncio.run(smoke._run(tmp_path, 0.20, 50_000, 8))
    assert result["failure_kind"] == "agent_timeout"
    assert result["failure_code"] == "AGENT_TIMEOUT"
    assert result["cleanup_ok"] is True


def test_cleanup_deadline_is_fail_closed(monkeypatch, tmp_path: Path):
    _configure(monkeypatch)
    monkeypatch.setattr(smoke, "ClaudeCodeAdapter", _SlowCloseAdapter)
    monkeypatch.setattr(smoke, "CLEANUP_TIMEOUT_SEC", 0.01)
    result = asyncio.run(smoke._run(tmp_path, 0.20, 50_000, 8))
    assert result["ok"] is False
    assert result["failure_kind"] == "cleanup_failed"
    assert result["cleanup_ok"] is False
    assert result["error_type"] == "TimeoutError"
    smoke._write_result(tmp_path, result)
    assert stat.S_IMODE((tmp_path / smoke.RESULT_NAME).stat().st_mode) == 0o600


def test_main_model_flag_overrides_dotenv(monkeypatch, tmp_path: Path, capsys):
    _configure(monkeypatch)
    monkeypatch.setattr(smoke, "_load_candidate_dotenv", lambda: None)
    monkeypatch.setattr(sys, "argv", ["live_candidate_smoke.py", "--live", "--out", str(tmp_path), "--model", "flag-model"])
    assert smoke.main() == 0
    result = json.loads((tmp_path / smoke.RESULT_NAME).read_text())
    assert result["requested_model"] == "flag-model"
    assert _FakeAdapter.instances[0].kwargs["model"] == "flag-model"
    assert result["harness_rc"] == 0
    assert capsys.readouterr().out


def test_smoke_accepts_standard_anthropic_endpoint_and_token(monkeypatch, tmp_path: Path):
    _configure(monkeypatch)
    monkeypatch.delenv("BENCH_BASE_URL")
    monkeypatch.delenv("BENCH_API_KEY")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example/anthropic")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "host-only-test-token")

    result = asyncio.run(smoke._run(tmp_path, 0.20, 50_000, 8))

    assert result["ok"] is True
    adapter = _FakeAdapter.instances[0]
    assert adapter.kwargs["api_endpoint"] == "https://gateway.example/anthropic"
    assert adapter.kwargs["api_key"] == "host-only-test-token"
    assert adapter.kwargs["api_auth_mode"] == "bearer"


def test_tool_pair_stats_deduplicate_repeated_result_snapshots():
    summary = [{"blocks": [
        {"type": "tool_use", "tool_use_id_hmac": "u"},
        {"type": "tool_result", "tool_use_id_hmac": "u", "tool_result_is_error": True},
        {"type": "tool_result", "tool_use_id_hmac": "u", "tool_result_is_error": True},
    ]}, {"blocks": [{
        "type": "tool_result", "tool_use_id_hmac": "u", "tool_result_is_error": False,
    }]}]
    assert smoke._tool_pairing_stats(summary) == {
        "tool_use_count": 1,
        "tool_result_count": 1,
        "tool_result_error_count": 1,
        "tool_pairs_matched": 1,
        "tool_pairs_unmatched": 0,
        "tool_results_unmatched": 0,
    }


def test_cleanup_ledger_is_scoped_and_idempotent(monkeypatch, tmp_path: Path):
    uid = "a" * 32
    ledger = tmp_path / smoke.LEDGER_NAME
    smoke._write_ledger(ledger, uid)
    calls: list[str] = []

    async def reconcile(run_uid: str):
        calls.append(run_uid)
        return {"run_uid": run_uid, "remaining": {}, "cleanup_ok": True}

    monkeypatch.setattr("bench.core.sidecar_topology.reconcile_run_resources", reconcile)
    first = asyncio.run(smoke._cleanup_from_ledger(ledger))
    second = asyncio.run(smoke._cleanup_from_ledger(ledger))
    assert first["cleanup_ok"] is True and second["cleanup_ok"] is True
    assert calls == [uid, uid]
    assert json.loads(ledger.read_text())["status"] == "recovered"
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


def test_cleanup_ledger_rejects_unsafe_run_uid(tmp_path: Path):
    ledger = tmp_path / smoke.LEDGER_NAME
    ledger.write_text(json.dumps({"schema_version": 1, "run_uid": "../../other", "status": "active"}))
    with pytest.raises(ValueError):
        smoke._load_ledger(ledger)
