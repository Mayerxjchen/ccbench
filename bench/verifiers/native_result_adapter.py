"""Normalize a paper verifier's native JSON into the Bench result schema.

This file is copied into a private verifier bundle.  Native output is retained
as ``native-result.json`` for diagnosis, while ``result.json`` is deliberately
limited to :class:`BenchmarkResult` fields consumed by the harness.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _complete(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("passed"), bool):
        return True
    return isinstance(payload.get("status"), str) and bool(payload["status"].strip())


def _native_passed(payload: dict) -> bool:
    status = payload.get("status")
    if isinstance(status, str):
        return status.upper() == "PASS"
    return payload.get("passed") is True


_NOT_READY_STATUSES = {
    "NOT_READY", "CASE_NOT_READY", "UNBUILT", "UNQUALIFIED",
    "BUILT_NOT_QUALIFIED", "CASE_INVALID", "INFRA_INVALID",
}


def _native_scientific_failure(payload: dict) -> bool:
    status = payload.get("status")
    if isinstance(status, str):
        return status.upper() in {"FAIL", "SCIENTIFIC_FAIL", "FAIL_SUBMISSION", "SUBMISSION_FAILURE"}
    return payload.get("passed") is False


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_native(output: Path, stdout: bytes) -> dict | None:
    candidate = output / "result.json"
    for raw in (candidate.read_bytes() if candidate.is_file() else b"", stdout):
        if not raw:
            continue
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("/logs/verifier"))
    parser.add_argument("--submission", type=Path, default=Path("/submission"))
    parser.add_argument("native", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.native[:1] == ["--"]:
        args.native = args.native[1:]
    if not args.native:
        _write(args.output / "result.json", {
            "run_id": "unknown", "result_class": "INFRA_INVALID",
            "failure_code": "VERIFIER_FAILURE", "reason": "native verifier command missing",
            "retryable": True, "is_counted_scientifically": False,
        })
        return 2
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(args.native, cwd=args.output, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _write(args.output / "result.json", {
            "run_id": "unknown", "result_class": "INFRA_INVALID",
            "failure_code": "VERIFIER_FAILURE", "reason": f"native verifier launch failed: {type(exc).__name__}",
            "retryable": True, "is_counted_scientifically": False,
        })
        return 2
    native = _read_native(args.output, proc.stdout)
    if native is not None:
        _write(args.output / "native-result.json", native)
    run_id = "unknown"
    # A structural precheck is never a scientific result, even if a buggy
    # launcher exits zero or reports ``passed``.  Keep it retryable and out of
    # scientific scoring.
    status = str(native.get("status", "")).upper() if native is not None else ""
    precheck_only = (native is not None and
                     (native.get("scientific_ready") is False or
                      native.get("scientific_replay_implemented") is False or
                      status in _NOT_READY_STATUSES or
                      ("status" in native and status not in {"PASS", "FAIL", "SCIENTIFIC_FAIL", "FAIL_SUBMISSION", "SUBMISSION_FAILURE"})))
    if (proc.returncode == 0 and native is not None and _complete(native)
            and _native_passed(native) and not precheck_only):
        result_class, code, reason = "VALID_RESULT", "PASS", "native verifier passed"
    elif (proc.returncode in (1, 20) and native is not None and _complete(native)
          and _native_scientific_failure(native) and not precheck_only):
        result_class, code, reason = "VALID_RESULT", "SCIENTIFIC_FAIL", "native verifier reported a scientific assertion failure"
    else:
        result_class, code, reason = "INFRA_INVALID", "VERIFIER_FAILURE", (
            "native verifier produced no complete result" if native is None
            else f"native verifier exited with status {proc.returncode}"
        )
    _write(args.output / "result.json", {
        "run_id": run_id, "result_class": result_class, "failure_code": code,
        "reason": reason, "retryable": result_class == "INFRA_INVALID",
        "is_counted_scientifically": result_class == "VALID_RESULT",
    })
    return 0 if result_class == "VALID_RESULT" and _native_passed(native or {}) else 1


if __name__ == "__main__":
    raise SystemExit(main())
