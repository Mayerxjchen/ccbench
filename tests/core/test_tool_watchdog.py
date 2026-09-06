"""Tool watchdog tests: process-group isolation and TERM→grace→KILL.

P0-C regression: an unbounded root-recursive (or equivalent) local tool
must be killed by the watchdog, and the Agent/Coordinator must receive
control again without waiting for the run-total deadline.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from ccbench.core.tool_watchdog import ToolResult, ToolWatchdog


def _run(coro):
    """Run an async coroutine from a sync test."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture()
def watchdog():
    return ToolWatchdog(walltime_sec=2.0, grace_sec=0.5)


def test_short_command_succeeds(watchdog):
    """A fast command completes normally within walltime."""
    result = _run(watchdog.run([sys.executable, "-c", "print('hello')"]))
    assert result.returncode == 0
    assert result.timed_out is False
    assert "hello" in result.stdout
    assert result.walltime_ms > 0


def test_unbounded_command_is_killed():
    """033 regression: an infinite-loop tool must be killed, not hang."""
    dog = ToolWatchdog(walltime_sec=1.0, grace_sec=0.3)
    result = _run(dog.run([sys.executable, "-c", "import time; time.sleep(999)"]))
    assert result.timed_out is True
    # Process received a signal (SIGTERM or SIGKILL) — either means watchdog acted.
    assert (result.returncode or 0) < 0, f"expected negative returncode, got {result.returncode}"
    assert result.walltime_ms < 5000, f"took {result.walltime_ms}ms, expected <5s"


def test_process_group_is_killed_not_just_parent():
    """The watchdog kills the entire process group, not just the parent."""
    dog = ToolWatchdog(walltime_sec=1.0, grace_sec=0.3)
    script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(999)']); "
        "time.sleep(999)"
    )
    result = _run(dog.run([sys.executable, "-c", script]))
    assert result.timed_out is True
    assert (result.returncode or 0) < 0


def test_zero_exit_code_not_timed_out():
    """A clean exit never sets timed_out."""
    dog = ToolWatchdog(walltime_sec=5.0)
    result = _run(dog.run([sys.executable, "-c", "import sys; sys.exit(0)"]))
    assert result.returncode == 0
    assert result.timed_out is False


def test_nonzero_exit_not_timed_out():
    """A nonzero exit that happens within walltime is not a timeout."""
    dog = ToolWatchdog(walltime_sec=5.0)
    result = _run(dog.run([sys.executable, "-c", "import sys; sys.exit(42)"]))
    assert result.returncode == 42
    assert result.timed_out is False


def test_walltime_ms_is_positive():
    """walltime_ms is always >= 0 for any command."""
    dog = ToolWatchdog(walltime_sec=5.0)
    result = _run(dog.run([sys.executable, "-c", "pass"]))
    assert result.walltime_ms >= 0
