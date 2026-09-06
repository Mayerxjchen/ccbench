"""ProcessTestAdapter: run jobs as local subprocesses — contract CI only.

This adapter exists so the conformance suite can exercise the full seven-op
contract without SSH, a scheduler, or Docker. Local benchmark cases never use
it. Job argv is passed straight to ``subprocess.Popen`` with ``shell=False`` —
the JobSpec already guarantees a plain argv array, so there is no shell.

Each job lives in its own ``0700`` directory under the adapter root. The
adapter owns the state machine and never lets a terminal-state job be fetched
or cancelled again.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from ccbench.hpc.adapters.base import (
    JOB_STATES,
    TERMINAL_STATES,
    TransportUnknown,
)


class AdapterError(Exception):
    """A job-state rule was violated (fetch/cancel at the wrong time)."""


class ProcessTestAdapter:
    """In-process adapter: one subprocess per job, real files on disk."""

    def __init__(self, root: Path, *, timeout_sec: float = 30.0) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._timeout_sec = timeout_sec
        self._jobs: dict[str, dict[str, Any]] = {}
        self._idem: dict[str, str] = {}
        self._ops: dict[tuple[str, str], str] = {}  # (run_id, operation_id) -> job_id
        self._seq = 0
        self._submit_count = 0
        self._fail_after_accept = False
        self._markers: dict[str, str] = {}  # marker -> job_id
        self._last_marker: str | None = None

    # -- fault injection (CI only; never a production driver) --------------

    def fail_after_scheduler_accept_once(self) -> None:
        """Next submit registers the job, then loses the response."""
        self._fail_after_accept = True

    @property
    def physical_submit_count(self) -> int:
        return self._submit_count

    def find_by_marker(self, marker: str) -> str | None:
        """Exact-match the opaque submit marker."""
        if getattr(self, "_ambiguous_marker", None) == marker:
            raise AdapterError(f"ambiguous marker {marker!r}: 2 jobs")
        job_id = self._markers.get(marker)
        if job_id is not None and job_id not in self._jobs:
            return None  # scheduler lost the job
        return job_id

    def forget_all_jobs(self) -> None:
        """Simulate the scheduler losing every job (clean node reboot)."""
        self._jobs.clear()
        self._idem.clear()
        self._ops.clear()
        self._markers.clear()

    def plant_duplicate_markers(self) -> None:
        """Model two scheduler jobs reporting the same submit marker."""
        if self._last_marker is None:
            raise AdapterError("no marker to duplicate")
        self._ambiguous_marker = self._last_marker

    def finish(self, job_id: str, *, state: str) -> None:
        """Test hook: force a job into a terminal state (scheduler truth)."""
        job = self._jobs[job_id]
        if state not in TERMINAL_STATES:
            raise AdapterError(f"{state!r} is not terminal")
        if job.get("proc") is not None and job["proc"].poll() is None:
            job["proc"].kill()
        job["state"] = state

    # -- the seven operations --------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        return {
            "adapter": "process_test",
            "states": list(JOB_STATES),
            "supports_cancel": True,
            "default_queue": "ci",
        }

    def find_by_operation_id(self, run_id: str, operation_id: str) -> str | None:
        return self._ops.get((run_id, operation_id))

    def submit(
        self,
        spec: dict[str, Any],
        *,
        run_id: str,
        operation_id: str,
        marker: str | None = None,
    ) -> dict[str, Any]:
        if marker is not None and marker in self._markers:
            # Exact-marker replay of an already-accepted submission.
            return {"job_id": self._markers[marker], "duplicate": True}
        key = spec["idempotency_key"]
        if key in self._idem:
            return {"job_id": self._idem[key], "duplicate": True}
        if marker is None:
            # v1 single-job-per-operation semantics; v2 attempts carry an
            # explicit marker and are guarded by it instead.
            prior = self._ops.get((run_id, operation_id))
            if prior is not None:
                return {"job_id": prior, "duplicate": True}
        self._seq += 1
        self._submit_count += 1
        job_id = f"job-{self._seq:04d}"
        work = self._root / job_id
        work.mkdir(mode=0o700)
        stdout = (work / "stdout.log").open("wb")
        stderr = (work / "stderr.log").open("wb")
        try:
            proc = subprocess.Popen(
                list(spec["command"]),
                cwd=str(work),
                stdout=stdout,
                stderr=stderr,
                shell=False,
            )
        except OSError as exc:
            stdout.close()
            stderr.close()
            raise AdapterError(f"cannot launch job {job_id}: {exc}") from exc
        if marker is not None:
            self._markers[marker] = job_id
            self._last_marker = marker
        # Register the accepted job BEFORE the fault point: a lost response
        # still leaves a real job at the scheduler, which recovery must find.
        self._jobs[job_id] = {
            "state": "RUNNING",
            "exit_code": None,
            "work": work,
            "proc": proc,
            "spec": spec,
            "started": time.monotonic(),
        }
        self._idem[key] = job_id
        self._ops[(run_id, operation_id)] = job_id
        if self._fail_after_accept:
            self._fail_after_accept = False
            raise TransportUnknown(
                f"job {job_id} accepted, response lost before delivery"
            )
        return {"job_id": job_id, "duplicate": False}

    def status(self, job_id: str) -> dict[str, Any]:
        job = self._jobs[job_id]
        self._poll(job)
        return {"job_id": job_id, "state": job["state"], "exit_code": job["exit_code"]}

    def _poll(self, job: dict[str, Any]) -> None:
        """Sync state with the subprocess: exit → SUCCEEDED/FAILED, overrun → TIMEOUT."""
        proc: subprocess.Popen = job["proc"]
        if job["state"] in TERMINAL_STATES:
            return
        if proc.poll() is None:
            if time.monotonic() - job["started"] > self._timeout_sec:
                proc.kill()
                job["state"] = "TIMEOUT"
                job["exit_code"] = None
            return
        code = proc.wait()
        job["exit_code"] = code
        job["state"] = "SUCCEEDED" if code == 0 else "FAILED"

    def logs(self, job_id: str) -> dict[str, Any]:
        job = self._jobs[job_id]
        return {
            "job_id": job_id,
            "stdout": _read(job["work"] / "stdout.log"),
            "stderr": _read(job["work"] / "stderr.log"),
        }

    def fetch(self, job_id: str) -> dict[str, Any]:
        job = self._jobs[job_id]
        self._poll(job)
        if job["state"] != "SUCCEEDED":
            raise AdapterError(f"fetch before success (state {job['state']})")
        files = sorted(
            path.name for path in job["work"].iterdir()
            if path.is_file() and path.suffix == ".txt"
        )
        outputs = {
            name: (job["work"] / name).read_text(encoding="utf-8")
            for name in files
        }
        return {"job_id": job_id, "state": job["state"], "files": files, "outputs": outputs}

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self._jobs[job_id]
        self._poll(job)
        if job["state"] in TERMINAL_STATES:
            raise AdapterError(f"cancel after terminal state ({job['state']})")
        job["proc"].kill()
        job["state"] = "CANCELLED"
        return {"job_id": job_id, "state": "CANCELLED"}

    def usage(self) -> dict[str, Any]:
        return {
            "jobs": len(self._jobs),
            "submitted": self._submit_count,
            "runs": len(self._idem),
        }

    # -- introspection helpers (conformance) -----------------------------

    @property
    def root(self) -> Path:
        return self._root


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
