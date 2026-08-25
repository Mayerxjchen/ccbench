"""Tool watchdog: per-tool process-group isolation with TERM→grace→KILL.

Every local Candidate tool invocation runs in its own process group.  On
walltime expiry the watchdog sends SIGTERM to the group, waits a bounded
grace interval, then SIGKILLs any survivors.  A structured
``tool_attempt_timed_out`` event is appended so the Agent may choose a
bounded alternative.

Repeated timeouts consume the local-tool budget; only exhaustion becomes
``AGENT_FAILURE/AGENT_BUDGET_EXHAUSTED``.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """Structured result from a tool invocation."""

    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    killed: bool = False
    walltime_ms: int = 0


@dataclass
class ToolWatchdog:
    """Watches one local tool invocation in its own process group.

    Parameters
    ----------
    walltime_sec : float
        Maximum wall-clock seconds before TERM→grace→KILL.
    grace_sec : float
        Seconds between SIGTERM and SIGKILL (default 3.0).
    """

    walltime_sec: float = 300.0
    grace_sec: float = 3.0

    async def run(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        operation_id: str = "",
    ) -> ToolResult:
        """Run *cmd* in a new process group and enforce the walltime.

        Returns a ``ToolResult``.  On timeout the process group is killed
        and ``timed_out=True``.
        """
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            start_new_session=True,  # new process group (setsid)
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.walltime_sec,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return ToolResult(
                returncode=proc.returncode,
                stdout=stdout_bytes.decode(errors="replace") if stdout_bytes else "",
                stderr=stderr_bytes.decode(errors="replace") if stderr_bytes else "",
                walltime_ms=elapsed_ms,
            )
        except asyncio.TimeoutError:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return await self._kill_group(proc, elapsed_ms)

    async def _kill_group(self, proc: asyncio.subprocess.Process, elapsed_ms: int) -> ToolResult:
        """TERM → grace → KILL the process group, return timed-out result."""
        pgid = None
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            pass

        # SIGTERM to the whole process group
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        # Grace period
        try:
            await asyncio.wait_for(proc.wait(), timeout=self.grace_sec)
            # Process exited within grace — no KILL needed
            stdout_bytes, stderr_bytes = await proc.communicate()
            elapsed_ms = int((time.monotonic() - (time.monotonic() - elapsed_ms / 1000)) * 1000)
            return ToolResult(
                returncode=proc.returncode,
                stdout=stdout_bytes.decode(errors="replace") if stdout_bytes else "",
                stderr=stderr_bytes.decode(errors="replace") if stderr_bytes else "",
                timed_out=True,
                walltime_ms=elapsed_ms,
            )
        except asyncio.TimeoutError:
            pass

        # SIGKILL the whole process group
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            await proc.wait()
        except Exception:
            pass

        return ToolResult(
            returncode=-9,
            timed_out=True,
            killed=True,
            walltime_ms=elapsed_ms,
        )
