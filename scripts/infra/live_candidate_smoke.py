#!/usr/bin/env python3
"""Opt-in live smoke for Candidate Docker, sidecar, and model routing.

This command makes one real model request and never prints credentials.  It is
kept outside pytest so the normal suite remains deterministic and cost-free.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import stat
import tempfile
import uuid
from pathlib import Path

from bench.agents import ClaudeCodeAdapter
from bench.pilot import _load_candidate_dotenv, _publish_candidate_messages


EXPECTED_OUTPUT = b"candidate-network-ok\n"
RESULT_NAME = "smoke-result.json"
LEDGER_NAME = "resource-ledger.json"
CLEANUP_TIMEOUT_SEC = 45.0
_RUN_UID_RE = re.compile(r"^[0-9a-f]{32}$")


def _validate_run_uid(value: object) -> str:
    if not isinstance(value, str) or _RUN_UID_RE.fullmatch(value) is None:
        raise ValueError("resource ledger run_uid is not a strict 32-character hex UID")
    return value


def _atomic_json(target: Path, payload: dict[str, object], *, mode: int = 0o600) -> None:
    """Write a bounded control artifact atomically with private permissions."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, mode)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _write_ledger(path: Path, run_uid: str, *, status: str = "active", **extra: object) -> None:
    _validate_run_uid(run_uid)
    payload: dict[str, object] = {
        "schema_version": 1,
        "run_uid": run_uid,
        "status": status,
    }
    payload.update(extra)
    _atomic_json(path, payload)


def _load_ledger(path: Path) -> dict[str, object]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
        raise ValueError("cleanup ledger must be a small regular file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported resource ledger")
    _validate_run_uid(payload.get("run_uid"))
    if payload.get("status") not in {"active", "cancelled", "closed", "recovered"}:
        raise ValueError("invalid resource ledger status")
    return payload


async def _cleanup_from_ledger(path: Path) -> dict[str, object]:
    """Recover one run after an abrupt kill; never sweep unrelated resources."""
    ledger = _load_ledger(path)
    from bench.core.sidecar_topology import reconcile_run_resources

    result = await reconcile_run_resources(str(ledger["run_uid"]))
    if result.get("remaining"):
        raise RuntimeError("resource reconciliation left labelled resources")
    _write_ledger(path, str(ledger["run_uid"]), status="recovered", cleanup_ok=True)
    return {"run_uid": ledger["run_uid"], "cleanup_ok": True, "status": "recovered"}


def _install_signal_cancellation(loop: object, task: object, received_signal: list[str | None]) -> list[signal.Signals]:
    """Install a tiny, testable TERM/INT bridge which only cancels this run."""
    installed: list[signal.Signals] = []

    def request_cancel(sig: signal.Signals) -> None:
        received_signal[0] = sig.name
        task.cancel()  # type: ignore[attr-defined]

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_cancel, sig)  # type: ignore[attr-defined]
            installed.append(sig)
        except (NotImplementedError, RuntimeError):
            pass
    return installed


def _safe_preflight(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, object] = {}
    if isinstance(value.get("classification"), str):
        result["classification"] = value["classification"][:64]
    if isinstance(value.get("status"), int):
        result["status"] = value["status"]
    # The proxy only emits a type name here.  Keep it bounded and do not copy
    # arbitrary upstream error details into the smoke artifact.
    if isinstance(value.get("detail"), str):
        result["detail"] = value["detail"][:64]
    return result or None


def _safe_metrics(value: object, *, depth: int = 0) -> object:
    """Retain numeric usage diagnostics without copying arbitrary strings."""
    if depth > 2:
        return None
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in list(value.items())[:64]:
            if isinstance(key, str) and len(key) <= 64:
                safe = _safe_metrics(item, depth=depth + 1)
                if safe is not None or item is None:
                    result[key] = safe
        return result
    if isinstance(value, list):
        return [_safe_metrics(item, depth=depth + 1) for item in value[:64]]
    return None


def _safe_event_summaries(value: object, *, request: bool = False) -> list[dict[str, object]]:
    """Copy only the already-safe proxy summary fields, with hard bounds."""
    if not isinstance(value, list):
        return []
    allowed = {
        "type", "content_block_type", "tool_name", "stop_reason",
        "tool_result_is_error", "role", "blocks", "normalized",
        "tool_use_id_hmac", "protocol_conflict", "tool_use_count",
        "tool_result_count", "tool_result_error_count",
        "request_index", "request_body_hmac", "outcome",
        "new_tool_use_count", "new_tool_result_count", "duplicate_tool_snapshot",
    }
    summaries: list[dict[str, object]] = []
    for item in value[:128 if request else 256]:
        if not isinstance(item, dict):
            continue
        summary: dict[str, object] = {}
        for key in allowed:
            if key not in item:
                continue
            val = item[key]
            if key == "blocks":
                if not isinstance(val, list):
                    continue
                blocks: list[dict[str, object]] = []
                for block in val[:64]:
                    if not isinstance(block, dict):
                        continue
                    safe_block: dict[str, object] = {}
                    for block_key in ("role", "type", "tool_name", "tool_result_is_error", "tool_use_id_hmac"):
                        block_val = block.get(block_key)
                        if isinstance(block_val, str):
                            safe_block[block_key] = block_val[:128]
                        elif isinstance(block_val, bool):
                            safe_block[block_key] = block_val
                    blocks.append(safe_block)
                summary[key] = blocks
            elif isinstance(val, str):
                summary[key] = val[:128]
            elif isinstance(val, bool):
                summary[key] = val
            elif isinstance(val, (int, float)) and key in {
                "tool_use_count", "tool_result_count", "tool_result_error_count"
            }:
                summary[key] = val
        summaries.append(summary)
    return summaries


def _tool_pairing_stats(summaries: list[dict[str, object]]) -> dict[str, int]:
    uses: set[str] = set()
    results: set[str] = set()
    error_results: set[str] = set()
    for summary in summaries:
        blocks = summary.get("blocks")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            block_id = block.get("tool_use_id_hmac")
            if not isinstance(block_id, str):
                continue
            if block_type in {"tool_use", "server_tool_use"}:
                uses.add(block_id)
            elif block_type == "tool_result":
                results.add(block_id)
                if block.get("tool_result_is_error") is True:
                    error_results.add(block_id)
    return {
        "tool_use_count": len(uses),
        "tool_result_count": len(results),
        "tool_result_error_count": len(error_results),
        "tool_pairs_matched": len(uses & results),
        "tool_pairs_unmatched": len(uses - results),
        "tool_results_unmatched": len(results - uses),
    }


def _safe_response_summaries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for item in value[:128]:
        if not isinstance(item, dict):
            continue
        summary: dict[str, object] = {}
        for key in ("transport", "stop_reason", "canonical_stop", "terminal_source",
                    "request_body_hmac", "outcome"):
            if isinstance(item.get(key), str):
                summary[key] = item[key][:64]
        for key in (
            "status", "tool_use_count", "charged_delta", "request_index",
        ):
            if isinstance(item.get(key), (int, float)):
                summary[key] = item[key]
        for key in (
            "parsed", "protocol_complete", "native_terminal", "normalized_terminal",
            "forwarded", "abort", "usage_consistent",
        ):
            if isinstance(item.get(key), bool):
                summary[key] = item[key]
        usage = item.get("usage")
        safe_usage = _safe_metrics(usage)
        if isinstance(safe_usage, dict):
            summary["usage"] = safe_usage
        result.append(summary)
    return result


def _safe_request_outcomes(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for item in value[:128]:
        if not isinstance(item, dict):
            continue
        safe: dict[str, object] = {}
        for key in ("request_index", "request_body_hmac", "outcome"):
            val = item.get(key)
            if isinstance(val, (int, bool)):
                safe[key] = val
            elif isinstance(val, str):
                safe[key] = val[:128]
        result.append(safe)
    return result


def _inspect_output(path: Path) -> dict[str, object]:
    result: dict[str, object] = {
        "output_exists": False,
        "output_size": None,
        "output_sha256": None,
        "output_exact": False,
    }
    try:
        info = path.lstat()
    except FileNotFoundError:
        return result
    except OSError:
        result["output_exists"] = False
        return result
    result["output_exists"] = True
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
        return result
    try:
        data = path.read_bytes()
    except OSError:
        return result
    result["output_size"] = len(data)
    result["output_sha256"] = hashlib.sha256(data).hexdigest()
    result["output_exact"] = data == EXPECTED_OUTPUT
    return result


def _write_result(root: Path, payload: dict[str, object]) -> None:
    """Atomically publish a mode-0600 result without exposing raw diagnostics."""
    _atomic_json(root / RESULT_NAME, payload)


def _safe_logs(adapter: ClaudeCodeAdapter | None) -> dict[str, object]:
    if adapter is None:
        return {}
    try:
        logs = adapter.collect_logs()
    except Exception:
        return {}
    return logs if isinstance(logs, dict) else {}


def _summary_from_logs(
    *,
    logs: dict[str, object],
    proxy: object | None,
    model: str,
    candidate_completed: bool,
    failure_kind: str | None,
    error_type: str | None,
    cleanup_ok: bool,
    output: dict[str, object],
    prompt_digest: str,
    tool_policy_digest: str,
) -> dict[str, object]:
    gateway = logs.get("model_gateway")
    gateway = gateway if isinstance(gateway, dict) else {}
    preflight = _safe_preflight(gateway.get("preflight"))
    def _gateway_metric(name: str) -> object:
        value = gateway.get(name)
        if isinstance(value, (int, float)) or value is None and proxy is None:
            return value
        return getattr(proxy, name, None) if proxy is not None else None

    event_summaries = _safe_event_summaries(gateway.get("event_summaries"))
    request_summaries = _safe_event_summaries(gateway.get("request_summaries"), request=True)
    request_outcomes = _safe_request_outcomes(gateway.get("request_outcomes"))
    response_summaries = _safe_response_summaries(gateway.get("response_summaries"))
    usage_events = _safe_metrics(gateway.get("usage_events"))
    charged_sum = sum(
        int(item.get("charged_delta", 0))
        for item in response_summaries
        if isinstance(item.get("charged_delta"), (int, float))
    )
    if not response_summaries and isinstance(usage_events, list):
        charged_sum = sum(
            int(item.get("charged_delta", 0))
            for item in usage_events
            if isinstance(item, dict) and isinstance(item.get("charged_delta"), (int, float))
        )
    native_terminal_count = sum(1 for item in response_summaries if item.get("native_terminal") is True)
    normalized_terminal_count = sum(1 for item in response_summaries if item.get("normalized_terminal") is True)
    if not response_summaries:
        native_terminal_count = sum(
            1 for item in event_summaries
            if item.get("type") == "message_stop" and item.get("normalized") is not True
        )
        normalized_terminal_count = sum(
            1 for item in event_summaries
            if item.get("type") == "message_stop" and item.get("normalized") is True
        )
    tokens_used = _gateway_metric("tokens_used")
    protocol_ok = bool(response_summaries) and all(
        item.get("parsed") is True
        and item.get("protocol_complete") is True
        and isinstance(item.get("status"), (int, float))
        and 200 <= item["status"] < 300
        for item in response_summaries
    )
    usage_accounted = isinstance(tokens_used, (int, float)) and charged_sum == tokens_used
    # Use the adapter's persisted safe route fields, falling back to the
    # requested CLI value when preparation failed before logs were available.
    requested = logs.get("requested_model")
    cli_model = logs.get("cli_model")
    upstream = logs.get("upstream_model")
    digest = logs.get("image_digest")
    payload: dict[str, object] = {
        "ok": False,
        "failure_kind": failure_kind,
        "error_type": error_type,
        "candidate_completed": candidate_completed,
        "requested_model": requested if isinstance(requested, str) else model,
        "cli_model": cli_model if isinstance(cli_model, str) else None,
        "upstream_model": upstream if isinstance(upstream, str) else None,
        "image_digest": digest if isinstance(digest, str) else None,
        "sidecar_image": logs.get("sidecar_image") if isinstance(logs.get("sidecar_image"), str) else None,
        "sidecar_image_digest": logs.get("sidecar_image_digest") if isinstance(logs.get("sidecar_image_digest"), str) else None,
        "candidate_exit_code": logs.get("candidate_exit_code") if isinstance(logs.get("candidate_exit_code"), int) else None,
        "prompt_digest": prompt_digest,
        "tool_policy_digest": tool_policy_digest,
        "preflight": preflight,
        "turn_count": _gateway_metric("turn_count"),
        "tokens_used": tokens_used,
        "budget_exceeded_reason": gateway.get("budget_exceeded_reason")
        if gateway.get("budget_exceeded_reason") is not None or proxy is None
        else getattr(proxy, "budget_exceeded_reason", None),
        "usage": _safe_metrics(logs.get("usage")),
        "usage_events": usage_events,
        "usage_charged_sum": charged_sum,
        "usage_accounted": usage_accounted,
        "response_summaries": response_summaries,
        "protocol_ok": protocol_ok,
        "event_summaries": event_summaries,
        "request_summaries": request_summaries,
        "request_outcomes": request_outcomes,
        "native_terminal_count": native_terminal_count,
        "normalized_terminal_count": normalized_terminal_count,
        **_tool_pairing_stats(request_summaries),
        "cleanup_ok": cleanup_ok,
        **output,
    }
    payload["ok"] = bool(
        candidate_completed and cleanup_ok and output.get("output_exact") and failure_kind is None
    )
    payload["failure_code"] = {
        "agent_timeout": "AGENT_TIMEOUT",
        "signal_cancelled": "HARNESS_CANCELLED",
        "cleanup_failed": "INFRA_CLEANUP_FAILED",
        "adapter_exception": "AGENT_FAILURE",
        "artifact_mismatch": "ARTIFACT_MISMATCH",
        "configuration": "CONFIGURATION_ERROR",
    }.get(failure_kind or "")
    return payload


async def _run(
    root: Path,
    max_budget_usd: float,
    max_total_tokens: int,
    max_turns: int,
    model_override: str | None = None,
    *,
    resource_run_uid: str | None = None,
    ledger_path: Path | None = None,
) -> dict[str, object]:
    root = root.resolve()
    run_uid = resource_run_uid or uuid.uuid4().hex
    _validate_run_uid(run_uid)
    ledger_path = ledger_path or (root / LEDGER_NAME)
    _write_ledger(ledger_path, run_uid, status="active")

    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task()
    received_signal: list[str | None] = [None]
    installed_signals = _install_signal_cancellation(loop, current_task, received_signal)

    workspace = root / "candidate"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "instruction.md").write_text(
        "Create final/network-smoke.txt. Its UTF-8 bytes must be exactly "
        "candidate-network-ok followed by one LF byte (0x0A), with no other bytes.\n",
        encoding="utf-8",
    )
    prompt_digest = hashlib.sha256(
        (workspace / "instruction.md").read_bytes()
    ).hexdigest()
    tool_policy = ("Bash", "Read", "Write", "Edit", "Glob", "Grep")
    tool_policy_digest = hashlib.sha256(
        json.dumps(tool_policy, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    model = model_override or os.environ.get("BENCH_MODEL", "")
    endpoint = (
        os.environ.get("ANTHROPIC_BASE_URL")
        or os.environ.get("BENCH_BASE_URL")
        or ""
    )
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    api_key = auth_token or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("BENCH_API_KEY") or ""
    api_auth_mode = "bearer" if auth_token else "x-api-key"
    adapter: ClaudeCodeAdapter | None = None
    logs: dict[str, object] = {}
    candidate_completed = False
    cleanup_ok = True
    failure_kind: str | None = None
    error_type: str | None = None
    output_path = workspace / "final" / "network-smoke.txt"
    try:
        if not model or not endpoint or not api_key:
            failure_kind = "configuration"
            error_type = "MissingConfiguration"
        else:
            adapter = ClaudeCodeAdapter(
                model=model,
                max_turns=max_turns,
                max_total_tokens=max_total_tokens,
                threads_root=root / "threads",
                task_name="live-candidate-smoke",
                image=os.environ.get(
                    "BENCH_CANDIDATE_IMAGE", "bench-agent-claude-code:2.1.266"
                ),
                sidecar_image=os.environ.get(
                    "BENCH_SIDECAR_IMAGE", "bench-gateway-anthropic:1"
                ),
                case_dir=workspace,
                workspace=workspace,
                preserve_workspace=True,
                api_endpoint=endpoint,
                api_key=api_key,
                api_auth_mode=api_auth_mode,
                session_id=str(uuid.uuid4()),
                # Use the frozen profile surface.  Claude Code 2.1 does not expose a
                # standalone Write permission reliably; Bash/Edit makes this smoke's
                # final-file assertion executable while remaining explicitly bounded.
                tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
                effort="low",
                max_budget_usd=max_budget_usd,
                agent_timeout_sec=180,
                resource_run_uid=run_uid,
            )
            try:
                await adapter.prepare()
                await adapter.start((workspace / "instruction.md").read_text(encoding="utf-8"))
                candidate_completed = True
            except Exception as exc:
                failure_kind = (
                    "agent_timeout" if type(exc).__name__ == "AgentTimeoutError"
                    else "adapter_exception"
                )
                error_type = type(exc).__name__
            except asyncio.CancelledError:
                # A signal is a controlled harness cancellation, not a model
                # success.  Cleanup below is shielded and bounded.
                failure_kind = "signal_cancelled"
                error_type = received_signal[0] or "CancelledError"
    except asyncio.CancelledError:
        failure_kind = "signal_cancelled"
        error_type = received_signal[0] or "CancelledError"
    finally:
        logs = _safe_logs(adapter)
        if adapter is not None:
            close_task = asyncio.create_task(adapter.close())
            try:
                await asyncio.wait_for(asyncio.shield(close_task), timeout=CLEANUP_TIMEOUT_SEC)
            except Exception as exc:
                cleanup_ok = False
                if not close_task.done():
                    close_task.cancel()
                    await asyncio.gather(close_task, return_exceptions=True)
                if error_type is None:
                    error_type = type(exc).__name__
                if failure_kind is None or failure_kind == "signal_cancelled":
                    failure_kind = "cleanup_failed"
            # A timed-out/cancelled close may have stopped midway through
            # Docker teardown.  Recover only resources bearing this exact
            # run UID, and keep cleanup failed if Docker cannot prove empty.
            if not cleanup_ok and any(
                getattr(adapter, name, None)
                for name in ("container_id", "sidecar_cid", "internal_net")
            ):
                try:
                    from bench.core.sidecar_topology import reconcile_run_resources
                    reconciled = await asyncio.wait_for(
                        reconcile_run_resources(run_uid), timeout=20.0
                    )
                    cleanup_ok = bool(reconciled.get("cleanup_ok")) and not reconciled.get("remaining")
                except Exception:
                    cleanup_ok = False
        try:
            _write_ledger(
                ledger_path,
                run_uid,
                status="cancelled" if failure_kind == "signal_cancelled" else "closed",
                cleanup_ok=cleanup_ok,
            )
        finally:
            for sig in installed_signals:
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError):
                    pass
    output = _inspect_output(output_path)
    if failure_kind is None and (not candidate_completed or not output["output_exact"]):
        failure_kind = "artifact_mismatch"
    proxy = getattr(adapter, "_model_proxy", None) if adapter is not None else None
    summary = _summary_from_logs(
        logs=logs,
        proxy=proxy,
        model=model,
        candidate_completed=candidate_completed,
        failure_kind=failure_kind,
        error_type=error_type,
        cleanup_ok=cleanup_ok,
        output=output,
        prompt_digest=prompt_digest,
        tool_policy_digest=tool_policy_digest,
    )
    # Keep the raw Claude event stream alongside the smoke result, never in
    # the Candidate workspace.  The summary itself remains metadata-only.
    messages_path = _publish_candidate_messages(root, logs.get("thread_dir"))
    if messages_path:
        summary["messages_path"] = messages_path
    summary["resource_run_uid"] = run_uid
    summary["signal"] = received_signal[0]
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="acknowledge that this performs a real, potentially billable model request",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--cleanup-ledger", type=Path, default=None,
        help="recover only the exact labelled resources recorded by a prior smoke run",
    )
    parser.add_argument("--model", default=None, help="explicit requested upstream model ID")
    parser.add_argument("--max-budget-usd", type=float, default=0.20)
    parser.add_argument(
        "--max-total-tokens", type=int, default=1_000_000,
        help="gateway token ceiling for this opt-in smoke (default: 1000000)",
    )
    parser.add_argument("--max-turns", type=int, default=8)
    args = parser.parse_args()
    if args.cleanup_ledger is not None:
        try:
            result = asyncio.run(_cleanup_from_ledger(args.cleanup_ledger.resolve()))
        except Exception as exc:
            result = {
                "ok": False,
                "cleanup_ok": False,
                "failure_kind": "cleanup_failed",
                "error_type": type(exc).__name__,
            }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("cleanup_ok") else 1
    if not args.live:
        parser.error("--live is required")
    _load_candidate_dotenv()
    if args.out is None:
        with tempfile.TemporaryDirectory(prefix="bench-live-smoke-") as tmp:
            out_root = Path(tmp)
            try:
                result = asyncio.run(_run(out_root, args.max_budget_usd, args.max_total_tokens, args.max_turns, args.model))
            except Exception as exc:
                result = {"ok": False, "failure_kind": "script_exception", "error_type": type(exc).__name__}
            result["harness_rc"] = 0 if result.get("ok") else 1
            _write_result(out_root, result)
    else:
        out_root = args.out.resolve()
        try:
            result = asyncio.run(_run(out_root, args.max_budget_usd, args.max_total_tokens, args.max_turns, args.model))
        except Exception as exc:
            result = {"ok": False, "failure_kind": "script_exception", "error_type": type(exc).__name__}
        result["harness_rc"] = 0 if result.get("ok") else 1
        _write_result(out_root, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
