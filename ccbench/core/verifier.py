"""Independent Verifier runner.

Every Verifier runs in a fresh container, never inside the Candidate:

- ``--rm``, fixed non-root ``--user 65532:65532``, ``--network none``,
  ``--read-only``, ``--cap-drop ALL``, ``no-new-privileges``, private ``/tmp``
  tmpfs (from the resolved profile, default ``64m``).

The downgraded uid works because the Verifier interpreter lives at
``/opt/dftworld/venv/bin/python`` — outside ``/root`` and ``/app`` — and
runtime qualification must prove every test runner and dependency is
accessible to that non-root uid (nothing is read through the Candidate's
``/root`` home or the sealed submission's own venv).  The Harness pins
``VERIFIER_PYTHON`` to that path; tests must never execute an interpreter
shipped in the sealed submission.  The verifier only ever runs the repo's own
``/tests/test.sh``, never Candidate code.
- Read-only mounts: ``/submission`` (the sealed submission, also mounted at
  the legacy-compatible ``/app``), ``/tests``, ``/reference``.
- One writable mount: ``/logs/verifier``.

The Verifier writes ``/logs/verifier/result.json`` conforming to
``schemas/result.schema.json``. Missing, malformed, or schema-invalid output
maps to ``INFRA_INVALID/VERIFIER_FAILURE`` — never to a scientific failure.
Legacy verifiers that still write ``reward.txt`` are mapped as a compatibility
fallback (1.0 -> PASS, otherwise SCIENTIFIC_FAIL) until they are migrated.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ccbench.contracts.result import (
    BenchmarkResult,
    FailureCode,
    ResultClass,
)
from ccbench.verifiers import launcher as _launcher

RESULT_JSON = "result.json"
REWARD_TXT = "reward.txt"


class VerifierError(Exception):
    """Raised when a Verifier cannot be built or launched."""


@dataclass(frozen=True)
class VerifierSpec:
    image: str
    timeout_sec: float
    env: dict[str, str]
    tests_dir: Path | None = None
    reference_dir: Path | None = None
    solution_dir: Path | None = None
    # The resolved local profile's private /tmp tmpfs (the same 64m the
    # container Candidate uses); a caller may override per spec.
    tmpfs: str = "/tmp:rw,noexec,nosuid,size=64m"


def build_verifier_command(
    spec: VerifierSpec,
    submission: Path,
    logs: Path,
) -> list[str]:
    """Build the isolated ``docker run`` argv using the common launcher.

    The launcher (``ccbench.verifiers.launcher``) is the single
    verifier execution path: it resolves the interpreter, runs pytest with
    CTRF output, writes result.json, and produces reward.txt compatibility.
    """
    # The launcher is invoked inside the container via `python -m
    # ccbench.verifiers.launcher`.  We build the docker argv that
    # mounts the sealed submission, tests, and logs, then runs the launcher.
    # The common launcher (launch()) is the single verifier execution path.
    # Production test.sh files invoke: python -m ccbench.verifiers.launcher
    # This module references the launcher to enforce the dependency chain.
    cmd = [
        "docker",
        "run",
        "--rm",
        "--user",
        "65532:65532",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        spec.tmpfs,
    ]
    for key, value in spec.env.items():
        cmd += ["--env", f"{key}={value}"]
    # docker CLI --volume does NOT resolve relative paths — it treats them as a
    # named volume and fails with "invalid characters for a local volume name".
    # Resolve to absolute so the host directory is mounted regardless of cwd.
    def _host_abs(path: Path) -> str:
        return str(path.expanduser().resolve())

    # Sealed submission is the only candidate-origin material; mounted read-only
    # at both /submission and the legacy-compatible /app.
    cmd += ["--volume", f"{_host_abs(submission)}:/submission:ro"]
    cmd += ["--volume", f"{_host_abs(submission)}:/app:ro"]
    if spec.tests_dir is not None:
        cmd += ["--volume", f"{_host_abs(spec.tests_dir)}:/tests:ro"]
    if spec.reference_dir is not None:
        cmd += ["--volume", f"{_host_abs(spec.reference_dir)}:/reference:ro"]
    if spec.solution_dir is not None:
        # Hidden reference implementation (common across MatClaw cases): the
        # test suites import `solution.*` and execute the alt solver via
        # /solution, so mount it read-only and put it on PYTHONPATH.  This is
        # the verifier's OWN reference material — never the Candidate's.
        cmd += ["--volume", f"{_host_abs(spec.solution_dir)}:/solution:ro"]
        cmd += ["--env", "PYTHONPATH=/solution"]
    cmd += ["--volume", f"{_host_abs(logs)}:/logs/verifier"]
    cmd += [spec.image, "bash", "/tests/test.sh"]
    return cmd


def run_verifier(
    spec: VerifierSpec,
    sealed_submission: Path,
    logs: Path,
    run_id: str | None = None,
    runner: Callable[..., Any] | None = None,
) -> BenchmarkResult:
    """Run the Verifier in a fresh container and classify the outcome."""
    sealed_submission = Path(sealed_submission)
    logs = Path(logs)
    logs.mkdir(parents=True, exist_ok=True)
    cmd = build_verifier_command(spec, sealed_submission, logs)
    proc = runner if runner is not None else subprocess.run
    try:
        proc(cmd, timeout=int(spec.timeout_sec), capture_output=True, text=True, check=False)
    except subprocess.TimeoutExpired:
        return BenchmarkResult.infra_invalid(
            run_id or "unknown",
            FailureCode.VERIFIER_FAILURE,
            f"verifier timed out after {spec.timeout_sec}s",
        )
    except OSError as exc:
        return BenchmarkResult.infra_invalid(
            run_id or "unknown",
            FailureCode.VERIFIER_FAILURE,
            f"cannot launch verifier: {exc}",
        )
    return _parse_verifier_output(logs, run_id)


def _parse_verifier_output(logs: Path, run_id: str | None) -> BenchmarkResult:
    result_path = logs / RESULT_JSON
    if result_path.is_file():
        return _parse_result_json(result_path, run_id)
    reward_path = logs / REWARD_TXT
    if reward_path.is_file():
        lines = reward_path.read_text(encoding="utf-8").strip().splitlines()
        raw = lines[-1] if lines else "0"
        try:
            reward = float(raw)
        except ValueError:
            reward = 0.0
        return BenchmarkResult.valid(
            run_id or "unknown",
            passed=reward >= 1.0,
            reason=f"legacy reward.txt={reward}",
        )
    return BenchmarkResult.infra_invalid(
        run_id or "unknown",
        FailureCode.VERIFIER_FAILURE,
        f"verifier produced neither {RESULT_JSON} nor {REWARD_TXT}",
    )


def _parse_result_json(result_path: Path, run_id: str | None) -> BenchmarkResult:
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except ValueError:
        return BenchmarkResult.infra_invalid(
            run_id or "unknown",
            FailureCode.VERIFIER_FAILURE,
            f"{RESULT_JSON} is malformed",
        )
    try:
        # run_id is canonicalized from the caller (the harness run), so a
        # placeholder written by the Verifier never leaks into the record.
        if run_id:
            payload["run_id"] = run_id
        return BenchmarkResult.from_dict(payload)
    except (ValueError, KeyError, TypeError) as exc:
        return BenchmarkResult.infra_invalid(
            run_id or "unknown",
            FailureCode.VERIFIER_FAILURE,
            f"{RESULT_JSON} violates result.schema.json: {exc}",
        )
