"""Structured verifier launcher (P0-F).

Common lifecycle for all 46 case verifiers:

1. Interpreter resolution — portable across container images.
2. pytest execution — with CTRF output.
3. result.json write — schema-valid, with ``failing`` field (P0-F schema).
4. reward.txt write — legacy compatibility fallback.
5. JSON-lines log — machine-parseable audit trail.

Usage (from test.sh)::

    # Delegates to the launcher for the full lifecycle.
    python -m bench.verifiers.launcher \
        --run-id 025-name2smi \
        --test-path /tests/test_outputs.py

    # Custom verifier script (031-style):
    python -m bench.verifiers.launcher \
        --run-id 031-active-distillation \
        --test-path /tests/verifier.py \
        --test-args '["verify", "--mode", "paper"]'

Classification (set automatically, never caller-controlled):

- pytest pass → VALID_RESULT / PASS
- pytest fail → AGENT_FAILURE / SCIENTIFIC_FAIL
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---- Default constants ----

DEFAULT_LOG_DIR = Path("/logs/verifier")
DEFAULT_WORK_DIR = Path("/tests")
DEFAULT_PYTHON_CANDIDATES = [
    "/opt/matclaw/bin/python",
    "/opt/ai2kit/bin/python",
    "/opt/deepmd-jax/bin/python",
    "/usr/local/bin/python3",
    "/usr/bin/python3",
]
DEFAULT_PYTEST_ARGS = ["-q", "-rA"]


# ---- result.json schema (P0-F: adds "failing" field) ----


@dataclass
class VerifierResult:
    """Structured verifier output — always written as result.json."""

    run_id: str
    result_class: str  # "VALID_RESULT" | "AGENT_FAILURE" | "INFRA_INVALID"
    failure_code: str  # "PASS" | "SCIENTIFIC_FAIL" | "INFRA_INVALID"
    reason: str
    retryable: bool = False
    is_counted_scientifically: bool = True
    failing: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "run_id": self.run_id,
                "result_class": self.result_class,
                "failure_code": self.failure_code,
                "reason": self.reason,
                "retryable": self.retryable,
                "is_counted_scientifically": self.is_counted_scientifically,
                "failing": self.failing,
            },
            indent=2,
            ensure_ascii=False,
        ) + "\n"


# ---- Interpreter resolution ----


def resolve_python(candidates: list[str] | None = None) -> str:
    """Resolve the best available Python interpreter.

    Returns the first executable from *candidates*, falling back to
    ``sys.executable`` if none are found.
    """
    for c in candidates or DEFAULT_PYTHON_CANDIDATES:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return sys.executable


# ---- Writing helpers ----


def write_result_json(result: VerifierResult, path: Path) -> None:
    """Atomically write result.json to *path*."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(result.to_json(), encoding="utf-8")
    tmp.replace(path)


def write_reward_txt(reward: int, path: Path) -> None:
    """Write legacy reward.txt (1 or 0)."""
    tmp = path.with_suffix(".txt.tmp")
    tmp.write_text(f"{reward}\n", encoding="utf-8")
    tmp.replace(path)


def write_ctrf_log(ctrf_path: Path, exit_code: int, stdout: str) -> None:
    """Write CTRF-style JSON-lines log entry (non-blocking, best-effort)."""
    try:
        entry = {"exit_code": exit_code, "stdout_tail": stdout[-2000:]}
        with open(ctrf_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # log write is best-effort


# ---- Core launcher ----


@dataclass
class LaunchConfig:
    """Configuration for a single verifier invocation."""

    run_id: str
    test_path: str  # pytest file or custom script
    test_args: list[str] | None = None  # extra args for custom script mode
    work_dir: Path = DEFAULT_WORK_DIR
    log_dir: Path = DEFAULT_LOG_DIR
    python: str | None = None  # None = auto-resolve
    pytest_args: list[str] = field(default_factory=lambda: list(DEFAULT_PYTEST_ARGS))
    extra_env: dict[str, str] = field(default_factory=dict)
    classify_on: str = "exit_code"  # "exit_code" or "json"


def launch(config: LaunchConfig) -> int:
    """Run the verifier and write result.json + reward.txt.

    Returns 0 on VALID_RESULT, 1 on AGENT_FAILURE.
    """
    log_dir = config.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    py = config.python or resolve_python()
    cwd = str(config.work_dir) if config.work_dir.exists() else None

    # Build command
    if config.test_args is not None:
        # Custom script mode: python script.py args...
        cmd = [py, config.test_path] + config.test_args
    else:
        # Pytest mode: python -m pytest file [extra pytest args]
        cmd = [py, "-m", "pytest", config.test_path] + config.pytest_args

    env = {**os.environ, **config.extra_env}

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=int(os.environ.get("VERIFIER_TIMEOUT_SEC", 3600)),
    )

    stdout = result.stdout + result.stderr
    passed = result.returncode == 0

    # Classify
    if passed:
        vr = VerifierResult(
            run_id=config.run_id,
            result_class="VALID_RESULT",
            failure_code="PASS",
            reason=f"{config.run_id} verifier passed",
            failing=[],
        )
        reward = 1
    else:
        # Extract failing test names from pytest output
        failing = _extract_failing_tests(stdout)
        vr = VerifierResult(
            run_id=config.run_id,
            result_class="AGENT_FAILURE",
            failure_code="SCIENTIFIC_FAIL",
            reason=f"{config.run_id} verifier failed",
            failing=failing,
        )
        reward = 0

    # Write outputs
    write_result_json(vr, log_dir / "result.json")
    write_reward_txt(reward, log_dir / "reward.txt")
    write_ctrf_log(log_dir / "ctrf.log", result.returncode, stdout)

    print(f"[launcher] run_id={config.run_id} result_class={vr.result_class} "
          f"failure_code={vr.failure_code} failing={len(vr.failing)}")
    return 0 if passed else 1


def _extract_failing_tests(output: str) -> list[str]:
    """Extract FAILED test names from pytest output."""
    failing = []
    for line in output.splitlines():
        if "FAILED" in line:
            # Format: FAILED path/to/test.py::test_name - ...
            parts = line.strip().split()
            if len(parts) >= 2:
                failing.append(parts[1].split("::")[-1].rstrip(":"))
    return failing


# ---- CLI entry point ----


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dftworld-bench-verifier-launcher",
        description="Common verifier lifecycle launcher (P0-F).",
    )
    parser.add_argument("--run-id", required=True, help="Run identifier (e.g. 025-name2smi)")
    parser.add_argument("--test-path", required=True, help="Pytest file or custom script")
    parser.add_argument("--test-args", default=None, help='JSON array of extra args for custom script')
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--python", default=None, help="Python interpreter (auto-resolve if omitted)")
    parser.add_argument("--pytest-args", default=None, help='JSON array of extra pytest args')
    args = parser.parse_args(argv)

    test_args = json.loads(args.test_args) if args.test_args else None
    pytest_extra = json.loads(args.pytest_args) if args.pytest_args else None
    pytest_args = list(DEFAULT_PYTEST_ARGS)
    if pytest_extra:
        pytest_args.extend(pytest_extra)

    config = LaunchConfig(
        run_id=args.run_id,
        test_path=args.test_path,
        test_args=test_args,
        work_dir=args.work_dir,
        log_dir=args.log_dir,
        python=args.python,
        pytest_args=pytest_args,
    )
    return launch(config)


if __name__ == "__main__":
    sys.exit(main())
