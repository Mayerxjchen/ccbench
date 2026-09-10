"""Independent Verifier runner.

Every Verifier runs in a fresh container, never inside the Candidate:

- ``--rm``, fixed non-root ``--user 65532:65532``, ``--network none``,
  ``--read-only``, ``--cap-drop ALL``, ``no-new-privileges``, private ``/tmp``
  tmpfs (from the resolved profile, default ``64m``).

The downgraded uid works because the Verifier interpreter is image-owned and
runtime qualification proves every test runner and dependency is accessible
to that non-root uid (nothing is read through the Candidate's ``/root`` home
or the sealed submission's own venv).  ``/tests/test.sh`` selects the image's
trusted interpreter (through the common launcher where applicable); tests
must never execute an interpreter shipped in the sealed submission.  The
verifier only ever runs the repo's own ``/tests/test.sh``, never Candidate code.
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
import re
import os
import stat
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bench.contracts.result import (
    BenchmarkResult,
    FailureCode,
    ResultClass,
)
from bench.verifiers import launcher as _launcher

RESULT_JSON = "result.json"
REWARD_TXT = "reward.txt"
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_PROCESS_OUTPUT_BYTES = 1 * 1024 * 1024


def _safe_output(path: Path) -> bool:
    try:
        node = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(node.st_mode) and node.st_nlink == 1 and node.st_size <= MAX_RESULT_BYTES


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
    cpus: float = 4.0
    memory: str = "8g"
    pids_limit: int = 512
    container_name: str | None = None


def _bounded_communicate(child: subprocess.Popen[bytes], timeout_sec: float) -> tuple[bytes, bytes, str | None]:
    """Drain both pipes incrementally, bounding retained process output.

    Docker/verifier output is diagnostic only and must never be allowed to
    consume unbounded host memory.  The child is started in its own process
    group so an over-limit or timeout can terminate descendants too.
    """
    streams = {child.stdout: bytearray(), child.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    try:
        for stream in streams:
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + max(0.0, timeout_sec)
        overflow = False
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b"", b"", "timeout"
            for key, _ in selector.select(min(remaining, 0.25)):
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                buffer = streams[stream]
                if len(buffer) + len(chunk) > MAX_PROCESS_OUTPUT_BYTES:
                    overflow = True
                    # Keep a bounded diagnostic prefix; the caller kills the
                    # process group before closing the pipes.
                    buffer.extend(chunk[: max(0, MAX_PROCESS_OUTPUT_BYTES - len(buffer))])
                else:
                    buffer.extend(chunk)
                if overflow:
                    return bytes(streams[child.stdout]), bytes(streams[child.stderr]), "output_limit"
        child.wait(timeout=max(0.1, deadline - time.monotonic()))
        return bytes(streams[child.stdout]), bytes(streams[child.stderr]), None
    finally:
        selector.close()


def _terminate_process(child: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            child.kill()
        except OSError:
            pass
    try:
        child.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for stream in (child.stdout, child.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass


def _cleanup_container(name: str) -> bool:
    """Stop/remove a run-scoped container and prove it is gone."""
    try:
        subprocess.run(["docker", "stop", "-t", "2", name],
                       capture_output=True, text=True, check=False, timeout=5)
        subprocess.run(["docker", "rm", "-f", name],
                       capture_output=True, text=True, check=False, timeout=5)
        inspect = subprocess.run(["docker", "inspect", name],
                                 capture_output=True, text=True, check=False, timeout=5)
        return inspect.returncode != 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def build_verifier_command(
    spec: VerifierSpec,
    submission: Path,
    logs: Path,
) -> list[str]:
    """Build the isolated ``docker run`` argv using the common launcher.

    The launcher (``bench.verifiers.launcher``) is the single
    verifier execution path: it resolves the interpreter, runs pytest with
    CTRF output, writes result.json, and produces reward.txt compatibility.
    """
    # The launcher is invoked inside the container via `python -m
    # bench.verifiers.launcher`.  We build the docker argv that
    # mounts the sealed submission, tests, and logs, then runs the launcher.
    # The common launcher (launch()) is the single verifier execution path.
    # Production test.sh files invoke: python -m bench.verifiers.launcher
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
        "--cpus",
        str(spec.cpus),
        "--memory",
        spec.memory,
        "--pids-limit",
        str(spec.pids_limit),
    ]
    if spec.container_name:
        name = re.sub(r"[^A-Za-z0-9_.-]", "-", spec.container_name)[:80]
        cmd += ["--name", name]
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
    if logs.exists() and logs.is_symlink():
        return BenchmarkResult.infra_invalid(
            run_id or "unknown", FailureCode.VERIFIER_FAILURE,
            "verifier log directory must not be a symlink",
        )
    # Never clear a caller-provided directory: it may contain unrelated user
    # data. A verifier invocation owns a fresh, non-existent log directory;
    # stale or pre-seeded PASS/FAIL files therefore fail closed.
    if logs.exists():
        return BenchmarkResult.infra_invalid(
            run_id or "unknown", FailureCode.VERIFIER_FAILURE,
            "verifier log directory must be fresh and non-existent",
        )
    try:
        logs.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return BenchmarkResult.infra_invalid(
            run_id or "unknown", FailureCode.VERIFIER_FAILURE,
            f"cannot create fresh verifier log directory: {exc}",
        )
    cmd = build_verifier_command(spec, sealed_submission, logs)
    if runner is not None:
        try:
            completed = runner(cmd, timeout=int(spec.timeout_sec), capture_output=True, text=True, check=False)
        except subprocess.TimeoutExpired:
            return BenchmarkResult.infra_invalid(run_id or "unknown", FailureCode.VERIFIER_FAILURE,
                f"verifier timed out after {spec.timeout_sec}s")
        except OSError as exc:
            return BenchmarkResult.infra_invalid(run_id or "unknown", FailureCode.VERIFIER_FAILURE,
                f"cannot launch verifier: {exc}")
    else:
        try:
            child = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=False, start_new_session=True)
            stdout, stderr, termination = _bounded_communicate(child, float(spec.timeout_sec))
            if termination is not None:
                _terminate_process(child)
                if spec.container_name and not _cleanup_container(spec.container_name):
                    return BenchmarkResult.infra_invalid(
                        run_id or "unknown", FailureCode.VERIFIER_FAILURE,
                        "verifier cleanup failed after " + termination,
                    )
                return BenchmarkResult.infra_invalid(
                    run_id or "unknown", FailureCode.VERIFIER_FAILURE,
                    (f"verifier timed out after {spec.timeout_sec}s"
                     if termination == "timeout" else "verifier output exceeded the bounded limit"),
                )
            completed = subprocess.CompletedProcess(
                cmd, child.returncode, stdout=stdout[:MAX_PROCESS_OUTPUT_BYTES],
                stderr=stderr[:MAX_PROCESS_OUTPUT_BYTES],
            )
        except OSError as exc:
            return BenchmarkResult.infra_invalid(run_id or "unknown", FailureCode.VERIFIER_FAILURE,
                f"cannot launch verifier: {exc}")
    result_path = logs / RESULT_JSON
    reward_path = logs / REWARD_TXT
    returncode = getattr(completed, "returncode", 0)
    if returncode not in (0, None):
        # A trusted verifier may use its process status to report a scientific
        # or agent outcome (for example a failed case test writes a structured
        # AGENT_FAILURE and exits 1).  Parse that bounded, no-follow result
        # before classifying the process as infrastructure failure.  A
        # non-zero process may never smuggle a PASS result through this path.
        if _safe_output(result_path):
            structured = _parse_result_json(result_path, run_id)
            if structured.result_class is ResultClass.AGENT_FAILURE:
                return structured
            if (structured.result_class is ResultClass.VALID_RESULT
                    and structured.failure_code is FailureCode.SCIENTIFIC_FAIL):
                return structured
        return BenchmarkResult.infra_invalid(
            run_id or "unknown", FailureCode.VERIFIER_FAILURE,
            f"verifier exited with status {returncode}",
        )
    if not _safe_output(result_path) and not _safe_output(reward_path):
        return BenchmarkResult.infra_invalid(
            run_id or "unknown", FailureCode.VERIFIER_FAILURE,
            f"verifier produced neither {RESULT_JSON} nor {REWARD_TXT}",
        )
    return _parse_verifier_output(logs, run_id)


def _parse_verifier_output(logs: Path, run_id: str | None) -> BenchmarkResult:
    result_path = logs / RESULT_JSON
    if _safe_output(result_path):
        return _parse_result_json(result_path, run_id)
    reward_path = logs / REWARD_TXT
    if _safe_output(reward_path):
        try:
            fd = os.open(reward_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                lines = stream.read(MAX_RESULT_BYTES + 1).strip().splitlines()
        except OSError:
            lines = []
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
        fd = os.open(result_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            payload = json.loads(stream.read(MAX_RESULT_BYTES + 1))
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
