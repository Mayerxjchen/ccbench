#!/usr/bin/env python3
"""Slurm transport abstraction for dftworld2 case 034 (DESIGN skeleton).

The agent is ONLY the control layer.  Its Slurm-facing surface is abstracted
behind this transport so that two backends are swappable without changing the
agent's decision loop, the verifier, or the reward path:

  * PseudoSlurmTransport  — Mac test-bed.  Executes sbatch/squeue/sacct/scancel
        via ``docker exec`` into the case container (or directly when the
        transport itself runs inside that container).  The workspace is the
        /app bind-mount, which is shared between the control layer and the jobs.

  * SshSlurmTransport     — HPC, GATEWAY-INTERNAL ONLY.  Executes the same
        commands via ``ssh <login> "..."`` against a real Slurm login node.
        Since the bench-hpc refactor, SshSlurmTransport is used exclusively
        through ``dftworld_bench.hpc.adapters.slurm.SlurmAdapter`` inside the
        trusted gateway (which runs on the site host with the site operator's
        own credentials).  It must never be imported by Candidate-facing code;
        the controller image no longer ships an SSH client or rsync.

Job states are normalized to the canonical Slurm vocabulary: PENDING / RUNNING /
COMPLETED / FAILED / CANCELLED (plus UNKNOWN for "no record / query failed").

Both backends are implemented for real: PseudoSlurmTransport execs the
sbatch/squeue/sacct/scancel commands via ``docker exec`` (or directly in Shape A)
and SshSlurmTransport execs the same commands over ``ssh <login>`` against a real
Slurm login node.  Job lifecycle follows the shared contract: sacct-authoritative
with squeue fallback; a query failure maps to UNKNOWN (keep waiting), never a
terminal state; ``log()`` is best-effort and returns "" for a missing file;
``cancel()`` is idempotent.

python3.11-compatible, standard library only.
"""

from __future__ import annotations

import abc
import dataclasses
import enum
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

__version__ = "0.2.0"


# --------------------------------------------------------------------------- #
# Canonical job states
# --------------------------------------------------------------------------- #


class JobState(str, enum.Enum):
    """Canonical states the agent reasons about (the Slurm vocabulary)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    @property
    def is_terminal(self) -> bool:
        return self in (
            JobState.COMPLETED,
            JobState.FAILED,
            JobState.CANCELLED,
        )


# sacct full names / squeue letters / slurm codes -> canonical.
# Design decisions (see DESIGN.md §3.7):
#   * COMPLETING (CG) stays RUNNING (the job is still active).
#   * PREEMPTED (RV) maps to FAILED (matches oh-my-batch 0.7.6).
#   * CANCELLED is kept distinct from FAILED, even though omb's squeue fallback
#     folds CA->FAILED; the transport must let the agent observe a cancellation.
_STATE_ALIASES: Dict[str, JobState] = {
    # sacct full names
    "PENDING": JobState.PENDING,
    "RUNNING": JobState.RUNNING,
    "COMPLETING": JobState.RUNNING,
    "CONFIGURING": JobState.PENDING,
    "COMPLETED": JobState.COMPLETED,
    "FAILED": JobState.FAILED,
    "TIMEOUT": JobState.FAILED,
    "OUT_OF_MEMORY": JobState.FAILED,
    "NODE_FAIL": JobState.FAILED,
    "BOOT_FAIL": JobState.FAILED,
    "DEADLINE": JobState.FAILED,
    "REVOKED": JobState.FAILED,
    "PREEMPTED": JobState.FAILED,
    "CANCELLED": JobState.CANCELLED,
    "SUSPENDED": JobState.PENDING,
    "REQUEUED": JobState.PENDING,
    "RESIZING": JobState.PENDING,
    # squeue letters
    "PD": JobState.PENDING,
    "R": JobState.RUNNING,
    "CG": JobState.RUNNING,
    "CF": JobState.PENDING,
    "CD": JobState.COMPLETED,
    "F": JobState.FAILED,
    "TO": JobState.FAILED,
    "NF": JobState.FAILED,
    "OOM": JobState.FAILED,
    "BF": JobState.FAILED,
    "DL": JobState.FAILED,
    "RV": JobState.FAILED,
    "CA": JobState.CANCELLED,
    "SE": JobState.PENDING,
    "RD": JobState.PENDING,
}


def normalize_state(raw: Optional[str]) -> JobState:
    """Map a raw scheduler token (full name or squeue letter) to a JobState."""
    if not raw:
        return JobState.UNKNOWN
    # Slurm may append ``+`` when the State column is truncated and may render
    # cancellation as ``CANCELLED by <uid>``.  The first normalized token is
    # the actual state; the suffix/reason is diagnostic metadata.
    token = raw.strip().upper().split()[0].rstrip("+")
    return _STATE_ALIASES.get(token, JobState.UNKNOWN)


# --------------------------------------------------------------------------- #
# Errors and submit options
# --------------------------------------------------------------------------- #


class TransportError(RuntimeError):
    """Raised when a scheduler operation fails in a way the agent must notice."""


@dataclasses.dataclass
class SubmitOpts:
    """Resource / booking options for :meth:`SlurmTransport.submit`.

    Fields map to sbatch flags; ``parsable`` prints only the integer job id.
    ``extra`` passes through raw argv tokens verbatim (e.g. ``--gres=gpu:1``).
    """

    job_name: Optional[str] = None
    ntasks: Optional[str] = None
    cpus_per_task: Optional[str] = None
    # Slurm --mem is memory per allocated node. The public JobSpec currently
    # supports one-node jobs, so memory_gb maps here without ambiguity.
    memory_per_node: Optional[str] = None
    nodes: Optional[str] = None
    partition: Optional[str] = None
    gres: Optional[str] = None
    time: Optional[str] = None
    output: Optional[str] = None
    error: Optional[str] = None
    chdir: Optional[str] = None
    account: Optional[str] = None
    qos: Optional[str] = None
    dependency: Optional[str] = None
    parsable: bool = True
    extra: Tuple[str, ...] = ()

    def validate(self) -> None:
        """Fail-closed on log paths that cannot be resolved per job.

        An HPC job must not write to a bare relative log (``slurm.out``):
        parallel jobs collide on it and ``log()`` cannot locate it afterwards.
        Require an absolute path, or a relative path carrying the ``%j``/``%J``
        job-id token so each job's log is resolvable.  Raised from :meth:`to_argv`
        so both backends reject the same way.
        """
        for flag, value in (("--output", self.output), ("--error", self.error)):
            if value is None:
                continue
            if not Path(value).is_absolute() and "%j" not in value and "%J" not in value:
                raise TransportError(
                    f"{flag} must be absolute or %j-resolvable (got {value!r}); "
                    "a bare relative log collides across jobs and cannot be located"
                )

    def to_argv(self) -> List[str]:
        """Render these options as an sbatch argv fragment (no script operand)."""
        self.validate()
        argv: List[str] = []
        pairs: List[Tuple[str, Optional[str]]] = [
            ("--job-name", self.job_name),
            ("--ntasks", self.ntasks),
            ("--cpus-per-task", self.cpus_per_task),
            ("--mem", self.memory_per_node),
            ("--nodes", self.nodes),
            ("--partition", self.partition),
            ("--gres", self.gres),
            ("--time", self.time),
            ("--output", self.output),
            ("--error", self.error),
            ("--chdir", self.chdir),
            ("--account", self.account),
            ("--qos", self.qos),
            ("--dependency", self.dependency),
        ]
        for flag, value in pairs:
            if value is not None:
                argv.extend([flag, str(value)])
        if self.parsable:
            argv.append("--parsable")
        argv.extend(self.extra)
        return argv


# --------------------------------------------------------------------------- #
# Pure parsing helpers (implemented for real — they define the contract)
# --------------------------------------------------------------------------- #


_JOB_ID_RE = re.compile(r"\d+")


def parse_job_id(stdout: str) -> str:
    """Extract the job id from ``sbatch`` stdout.

    Accepts ``Submitted batch job 42`` or a bare ``42`` (``--parsable``).
    Raises TransportError when no integer is present.
    """
    match = _JOB_ID_RE.search(stdout)
    if not match:
        raise TransportError(f"sbatch produced no job id in stdout: {stdout!r}")
    return match.group(0)


def parse_sacct_state(stdout: str, job_id: str) -> JobState:
    """Parse ``sacct -X -P --format=JobID,State -j <id>`` CSV.

    Header ``JobID|State`` on row 1; one row per known job.  The PARENT job row
    (``42``) is authoritative and wins over step rows (``42.batch`` / ``42.0``)
    and array children (``42_1``), which are ignored — a child's COMPLETED must
    never mark the whole job done early, and a child's FAILED must never mark a
    healthy parent failed.  Unknown ids are omitted (caller falls back to
    squeue).  Returns UNKNOWN when no parent row matches (fail-closed: never
    guess a terminal state from a partial record).
    """
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line == "JobID|State" or "|" not in line:
            continue
        fields = [f.strip() for f in line.split("|")]
        if len(fields) >= 2 and fields[0] == job_id:
            return normalize_state(fields[1])
    return JobState.UNKNOWN


def parse_squeue_state(stdout: str, job_id: str) -> JobState:
    """Parse ``squeue -h -o "%A %t"`` output (``JobID <letter>`` lines)."""
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == job_id:
            return normalize_state(parts[1])
    # A clean, empty squeue result means the job is no longer queued/running;
    # the caller must confirm the terminal state via sacct.
    return JobState.UNKNOWN


def resolve_output_path(
    submit_dir: Union[str, os.PathLike[str]],
    job_id: str,
    output_opt: Optional[str] = None,
) -> str:
    """Resolve a job's log file path (Slurm ``%j`` token convention).

    Relative ``--output`` paths are anchored at the submit dir; the default is
    ``slurm-<jobid>.out``.  Only the ``%j``/``%J`` tokens are substituted here;
    real Slurm substitutes more, but keeping output paths ``%j``-based makes the
    transport's log lookup uniform across backends.
    """
    if output_opt:
        name = output_opt.replace("%j", str(job_id)).replace("%J", str(job_id))
    else:
        name = f"slurm-{job_id}.out"
    p = Path(name)
    if p.is_absolute():
        return str(p)
    return str(Path(submit_dir) / name)


# --------------------------------------------------------------------------- #
# Transport interface
# --------------------------------------------------------------------------- #


class SlurmTransport(abc.ABC):
    """Interface for the Slurm-facing operations the 034 agent uses.

    The agent's decision loop talks ONLY to this object.  Concrete backends
    (pseudo-slurm-in-container vs ssh-to-real-Slurm) swap without changing the
    agent, the verifier, or the reward path.
    """

    # -- job lifecycle --------------------------------------------------------

    @abc.abstractmethod
    def submit(
        self,
        script: Union[str, os.PathLike[str]],
        opts: Optional[Union[SubmitOpts, Dict[str, Any]]] = None,
    ) -> str:
        """Submit a batch script and return the job id as a string.

        ``script`` may carry ``#SBATCH`` headers; ``opts`` (a SubmitOpts or a
        plain dict) override/supplement them.  Blocks until the scheduler has
        ACCEPTED the job (an id is returned) — not until it runs.  Raises
        TransportError on rejection.
        """

    @abc.abstractmethod
    def status(self, job_id: str) -> JobState:
        """Return the canonical job state (see JobState).

        UNKNOWN means no record or the query failed — not terminal, retry.
        """

    @abc.abstractmethod
    def log(self, job_id: str, tail: Optional[int] = None) -> str:
        """Return the job's merged stdout/stderr, optionally tail-limited.

        Best-effort: return "" when the log file does not exist yet.  Never
        raise on a missing log.
        """

    @abc.abstractmethod
    def cancel(self, job_id: str) -> None:
        """Request cancellation.  Idempotent.  status() then converges to
        CANCELLED."""

    # -- workspace sync -------------------------------------------------------

    @abc.abstractmethod
    def stage(
        self,
        local_paths: Iterable[Union[str, os.PathLike[str]]],
        remote_dir: str,
    ) -> List[str]:
        """Make local inputs visible to the compute side before submit.

        Pseudo-slurm: no-op (the /app bind-mount already exposes the control
        workspace).  SSH: rsync/scp up to the HPC shared filesystem.  Returns
        the remote paths.
        """

    @abc.abstractmethod
    def fetch(
        self,
        remote_paths: Iterable[str],
        local_dir: Union[str, os.PathLike[str]],
    ) -> List[Path]:
        """Pull job outputs back into the control-layer workspace (the tree the
        verifier grades).

        Pseudo-slurm: no-op.  SSH: rsync -a -c down, optional SHA-256 verify.
        Returns the local paths written.
        """

    def sync_workspace(
        self,
        direction: str,
        workspace: Optional[Union[str, os.PathLike[str]]] = None,
    ) -> None:
        """Whole-stage convenience over stage()/fetch().

        ``direction`` is ``"stage"`` (inputs up) or ``"fetch"`` (outputs down).
        Backends override with the sanest default for their workspace binding.
        """
        raise NotImplementedError(
            f"{type(self).__name__}.sync_workspace is not implemented"
        )

    # -- capabilities ---------------------------------------------------------

    def remote_arch(self) -> str:
        """Return the architecture jobs actually run on (``uname -m`` output,
        e.g. ``x86_64`` or ``aarch64``).

        The controller compares this to the locked SIF's platform before every
        smoke/paper submit so an un-runnable image (arm64 SIF vs amd64 cluster)
        is rejected pre-submit instead of failing on the node.  Backends that
        cannot answer must raise TransportError (the controller fails closed).
        """
        raise NotImplementedError(
            f"{type(self).__name__}.remote_arch is not implemented"
        )

    # -- convenience ----------------------------------------------------------

    def wait(
        self,
        job_id: str,
        poll: float = 60.0,
        timeout: Optional[float] = None,
    ) -> JobState:
        """Poll status() until a terminal state.

        60 s is the HPC polling default (matches ``WAIT_JOB_INTERVAL`` in the
        HPC env); pseudo-slurm callers pass a short poll explicitly.

        A transient query failure is "unknown, keep waiting" — never "done"
        (the wait_for_job.sh rule).  Only a terminal state returned by the
        scheduler exits.  A controller timeout CANCELS the job (never leaves an
        orphan allocation) and raises TimeoutError; missing records never count
        as success.
        """
        import time

        deadline = None if timeout is None else time.monotonic() + timeout
        last = JobState.UNKNOWN
        while True:
            try:
                last = self.status(job_id)
            except TransportError:
                last = JobState.UNKNOWN
            if last.is_terminal:
                return last
            if deadline is not None and time.monotonic() >= deadline:
                # A controller timeout must not leave an orphaned allocation.
                # cancel() is idempotent for both backends.
                self.cancel(job_id)
                raise TimeoutError(
                    f"job {job_id} not terminal after {timeout}s; cancellation requested "
                    f"(last={last.value})"
                )
            time.sleep(poll)


# --------------------------------------------------------------------------- #
# Backend 1 — pseudo-slurm inside the case container (Mac test-bed)
# --------------------------------------------------------------------------- #


class PseudoSlurmTransport(SlurmTransport):
    """Executes sbatch/squeue/sacct/scancel via ``docker exec`` into the case
    container, or directly when the transport itself runs inside that container.

    Workspace = the /app bind-mount, shared between the control layer and the
    jobs: stage()/fetch() are path-mapping no-ops.

    ``container``   docker container id to exec into (Shape B).  When None the
                    binaries are executed directly (Shape A, in-container).
    ``workspace``   the control-layer graded workspace (host path in Shape B).
                    Default "/app".
    ``state_dir``   optional PSEUDO_SLURM_DIR override injected into the exec
                    env so all calls share one job database.
    """

    def __init__(
        self,
        *,
        container: Optional[str] = None,
        workspace: Union[str, os.PathLike[str]] = "/app",
        state_dir: Optional[str] = None,
    ) -> None:
        self.container = container
        self.workspace = str(workspace)
        self.state_dir = state_dir
        # job_id -> container-side submit dir (for resolving output logs)
        self._submit_dirs: Dict[str, str] = {}

    def _to_container_path(self, p: Union[str, os.PathLike[str]]) -> str:
        """Map a control-workspace path to the container's /app path.

        The /app bind-mount is the shared workspace: a path under ``workspace``
        maps to ``/app/<rel>``.  Paths outside the workspace are passed through
        unchanged (Shape A runs inside the container and uses /app directly).
        """
        if not self.container:
            return str(p)
        try:
            rel = os.path.relpath(str(p), self.workspace)
        except ValueError:  # different drive (Windows) — not on macOS, keep safe
            return str(p)
        if rel == ".":
            return "/app"
        if rel.startswith(".."):
            return str(p)
        return f"/app/{rel}"

    # -- executor -------------------------------------------------------------

    def _run_slurm(self, argv: List[str]) -> subprocess.CompletedProcess:
        """Run an slurm argv fragment against the scheduler."""
        quoted = shlex.join(argv)
        if self.container:
            cmd = ["docker", "exec"]
            if self.state_dir:
                cmd += ["-e", f"PSEUDO_SLURM_DIR={self.state_dir}"]
            cmd += [self.container, "bash", "-lc", quoted]
        else:
            # Shape A: the transport is inside the container; exec directly.
            cmd = ["bash", "-lc", quoted]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def remote_arch(self) -> str:
        """``uname -m`` of the pseudo-slurm container (Shape B) or the in-container
        host (Shape A).  aarch64 on the Mac build box → amd64 SIFs fail closed."""
        proc = self._run_slurm(["uname", "-m"])
        return proc.stdout.strip()

    # -- SlurmTransport -------------------------------------------------------

    def submit(
        self,
        script: Union[str, os.PathLike[str]],
        opts: Optional[Union[SubmitOpts, Dict[str, Any]]] = None,
    ) -> str:
        """docker-exec ``sbatch --parsable [opts] <script>``, parse id.

        Script path is translated from the control workspace to the container
        /app path (same inode via the bind mount).  Preserve PSEUDO_SLURM_DIR.
        """
        opt = opts if isinstance(opts, SubmitOpts) else SubmitOpts(**(opts or {}))
        argv = ["sbatch"] + opt.to_argv() + [self._to_container_path(script)]
        proc = self._run_slurm(argv)
        if proc.returncode != 0:
            raise TransportError(
                f"sbatch failed (rc={proc.returncode}): "
                f"{proc.stderr or proc.stdout or ''}"
            )
        job_id = parse_job_id(proc.stdout)
        self._submit_dirs[job_id] = str(Path(self._to_container_path(script)).parent)
        return job_id

    def status(self, job_id: str) -> JobState:
        """docker-exec ``sacct -X -P --format=JobID,State -j <id>`` then fall
        back to ``squeue -h -o '%A %t'``; normalize via the helpers."""
        proc = self._run_slurm(
            ["sacct", "-X", "-P", "--format=JobID,State", "-j", str(job_id)]
        )
        state = (
            parse_sacct_state(proc.stdout, str(job_id))
            if proc.returncode == 0
            else JobState.UNKNOWN
        )
        if state is not JobState.UNKNOWN:
            return state
        # sacct is authoritative; a live squeue row keeps the job visible.
        proc2 = self._run_slurm(["squeue", "-h", "-o", "%A %t"])
        state2 = (
            parse_squeue_state(proc2.stdout, str(job_id))
            if proc2.returncode == 0
            else JobState.UNKNOWN
        )
        return state2

    def log(self, job_id: str, tail: Optional[int] = None) -> str:
        """Read the job's merged stdout/stderr from the shared workspace.

        Resolves candidates in order: the default ``slurm-<id>.out`` at the
        submit dir, then ``slurm.out`` / ``output`` (script-declared names).
        Missing file -> "" (best-effort, never raises).
        """
        submit_dir = self._submit_dirs.get(str(job_id))
        candidates: List[str] = []
        if submit_dir:
            candidates += [
                resolve_output_path(submit_dir, job_id),
                f"{submit_dir}/slurm.out",
                f"{submit_dir}/output",
            ]
        candidates += [resolve_output_path(self.workspace, job_id)]
        for cpath in candidates:
            args = ["tail", "-n", str(int(tail))] if tail else ["cat"]
            args += [cpath]
            proc = self._run_slurm(args)
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
        return ""

    def cancel(self, job_id: str) -> None:
        """docker-exec ``scancel <id>``.  Idempotent: a nonzero rc (already-gone
        job) is treated as success — status() converges to CANCELLED."""
        proc = self._run_slurm(["scancel", str(job_id)])
        if proc.returncode != 0:
            raise TransportError(
                f"scancel failed (rc={proc.returncode}): "
                f"{proc.stderr or proc.stdout or ''}"
            )

    def stage(
        self,
        local_paths: Iterable[Union[str, os.PathLike[str]]],
        remote_dir: str,
    ) -> List[str]:
        """No-op: the /app bind-mount is the shared workspace.  Returns the
        container-side paths (control workspace path maps to /app)."""
        return [str(p) for p in local_paths]

    def fetch(
        self,
        remote_paths: Iterable[str],
        local_dir: Union[str, os.PathLike[str]],
    ) -> List[Path]:
        """No-op: the /app bind-mount already exposes job outputs at the control
        workspace.  Returns the local paths."""
        return [Path(p) for p in remote_paths]

    def sync_workspace(
        self,
        direction: str,
        workspace: Optional[Union[str, os.PathLike[str]]] = None,
    ) -> None:
        # Bind mount: nothing to copy in either direction.
        return None


# --------------------------------------------------------------------------- #
# Backend 2 — ssh to a real Slurm login node (HPC)
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class SshConfig:
    """Connection facts for SshSlurmTransport.

    Credential handling is intentionally external (ssh-agent / key path): the
    transport never performs password auth and never writes secrets to
    agent-visible files.
    """

    host: str
    user: Optional[str] = None
    key: Optional[Union[str, os.PathLike[str]]] = None
    port: Optional[int] = None
    options: Dict[str, str] = dataclasses.field(default_factory=dict)

    def target(self) -> str:
        if self.user:
            return f"{self.user}@{self.host}"
        return self.host

    def to_ssh_argv(self, remote_command: str) -> List[str]:
        argv = ["ssh"]
        if self.key:
            argv += ["-i", str(self.key)]
        if self.port:
            argv += ["-p", str(self.port)]
        for k, v in self.options.items():
            argv += ["-o", f"{k}={v}"]
        argv += [self.target(), remote_command]
        return argv


class SshSlurmTransport(SlurmTransport):
    """Executes sbatch/squeue/sacct/scancel via ``ssh <login> "<cmd>"`` against
    a real Slurm login node.

    Workspace strategies (``sync``):
      ``shared_fs``  — the control workspace already lives on a filesystem the
                       HPC also sees; stage/fetch are path-mapping no-ops.
      ``sync_back``  — stage inputs up per stage and fetch outputs down after
                       COMPLETED (rsync -a -c), so the verifier-graded tree
                       stays complete.

    ``remote_workspace`` is the HPC-side path corresponding to ``workspace``.
    """

    def __init__(
        self,
        *,
        ssh: SshConfig,
        workspace: Union[str, os.PathLike[str]] = "/app",
        remote_workspace: str = "/app",
        sync: str = "shared_fs",
    ) -> None:
        self.ssh = ssh
        self.workspace = str(workspace)
        self.remote_workspace = remote_workspace
        if sync not in ("shared_fs", "sync_back"):
            raise ValueError(f"unknown sync strategy: {sync!r}")
        self.sync = sync
        # job_id -> remote-side submit dir (for resolving output logs)
        self._submit_dirs: Dict[str, str] = {}

    def _to_remote_path(self, p: Union[str, os.PathLike[str]]) -> str:
        """Map a control-workspace path to the HPC-side remote_workspace path."""
        try:
            rel = os.path.relpath(str(p), self.workspace)
        except ValueError:
            return str(p)
        if rel == ".":
            return self.remote_workspace
        if rel.startswith(".."):
            return str(p)
        return f"{self.remote_workspace}/{rel}"

    # -- remote executor ------------------------------------------------------

    def _run_remote(self, remote_command: str) -> subprocess.CompletedProcess:
        """Run a command on the login node.  Single choke point: swap this for a
        persistent remote runner (tmux-based) when the cluster provides one —
        one-shot ssh loses remote cwd/env between calls.

        Transient connection failures (ssh exit 255 / timeouts) are retried
        with backoff: scheduler state lives on the site, so a dropped
        monitoring connection must never surface as a job failure."""
        cmd = self.ssh.to_ssh_argv(remote_command)
        last_exc: Optional[Exception] = None
        for attempt in range(4):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=300)
            except subprocess.TimeoutExpired as exc:
                last_exc = exc
            else:
                if proc.returncode != 255:
                    return proc
                last_exc = TransportError(
                    f"ssh rc=255: {proc.stderr or proc.stdout or ''}"
                )
            time.sleep(min(45.0, 5.0 * (2 ** attempt)))
        raise last_exc if last_exc is not None else RuntimeError("unreachable")

    def _check(self, proc: subprocess.CompletedProcess, what: str) -> None:
        if proc.returncode != 0:
            raise TransportError(
                f"{what} failed (rc={proc.returncode}): "
                f"{proc.stderr or proc.stdout or ''}"
            )

    def remote_arch(self) -> str:
        """``uname -m`` of the HPC login node (compute nodes share its arch).

        One cheap round-trip per submit; the controller uses it to reject an
        un-runnable SIF before sbatch.  Failure to answer fails closed.
        """
        proc = self._run_remote("uname -m")
        self._check(proc, "remote uname -m")
        return proc.stdout.strip()

    def _rsync_argv(self, source: str, dest: str) -> List[str]:
        """Build a single rsync invocation honouring this ssh config."""
        ssh_cmd = ["ssh"]
        if self.ssh.key:
            ssh_cmd += ["-i", str(self.ssh.key)]
        if self.ssh.port:
            ssh_cmd += ["-p", str(self.ssh.port)]
        for k, v in self.ssh.options.items():
            ssh_cmd += ["-o", f"{k}={v}"]
        return [
            "rsync",
            "-a",
            "-c",
            "--partial",
            "-e",
            shlex.join(ssh_cmd),
            source,
            dest,
        ]

    def _rsync(self, source: str, dest: str, what: str) -> None:
        proc = subprocess.run(
            self._rsync_argv(source, dest),
            capture_output=True,
            text=True,
            timeout=600,
        )
        self._check(proc, what)

    # -- SlurmTransport -------------------------------------------------------

    def submit(
        self,
        script: Union[str, os.PathLike[str]],
        opts: Optional[Union[SubmitOpts, Dict[str, Any]]] = None,
    ) -> str:
        """Stage the script to remote_workspace, then ssh ``sbatch --parsable
        [opts] <remote-script>`` and parse the id."""
        opt = opts if isinstance(opts, SubmitOpts) else SubmitOpts(**(opts or {}))
        remote_script = self._to_remote_path(script)
        if self.sync == "sync_back":
            self._run_remote(f"mkdir -p {shlex.quote(str(Path(remote_script).parent))}")
            self._rsync(str(script), f"{self.ssh.target()}:{remote_script}", "stage script")
        if opt.chdir:
            # The batch script's #SBATCH --chdir target must exist before
            # sbatch: a missing dir kills the job instantly (rc=1, no
            # output, NonZeroExitCode) before bash ever runs.
            self._run_remote(f"mkdir -p {shlex.quote(str(opt.chdir))}")
        cmd = "sbatch " + shlex.join(opt.to_argv() + [remote_script])
        proc = self._run_remote(cmd)
        self._check(proc, "sbatch")
        job_id = parse_job_id(proc.stdout)
        self._submit_dirs[job_id] = str(Path(remote_script).parent)
        return job_id

    def status(self, job_id: str) -> JobState:
        """ssh ``sacct -X -P --format=JobID,State -j <id>`` then fall back to
        ``squeue -h -o '%A %t'``; normalize via the helpers.  A query failure
        must map to UNKNOWN (keep waiting), never to a terminal state."""
        proc = self._run_remote(
            f"sacct -X -P --format=JobID,State -j {shlex.quote(str(job_id))} 2>/dev/null"
        )
        state = (
            parse_sacct_state(proc.stdout, str(job_id))
            if proc.returncode == 0
            else JobState.UNKNOWN
        )
        if state is not JobState.UNKNOWN:
            return state
        proc2 = self._run_remote("squeue -h -o '%A %t' 2>/dev/null")
        state2 = (
            parse_squeue_state(proc2.stdout, str(job_id))
            if proc2.returncode == 0
            else JobState.UNKNOWN
        )
        return state2

    def log(self, job_id: str, tail: Optional[int] = None) -> str:
        """ssh ``tail -n <tail> <remote-log>`` (resolve_output_path on the
        remote side).  Return "" when the file is not there yet."""
        submit_dir = self._submit_dirs.get(str(job_id))
        candidates: List[str] = []
        if submit_dir:
            candidates += [
                resolve_output_path(submit_dir, job_id),
                f"{submit_dir}/slurm.out",
                f"{submit_dir}/output",
            ]
        candidates += [resolve_output_path(self.remote_workspace, job_id)]
        for rpath in candidates:
            tail_cmd = f"tail -n {int(tail)} {shlex.quote(rpath)}" if tail else f"cat {shlex.quote(rpath)}"
            proc = self._run_remote(f"{tail_cmd} 2>/dev/null")
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
        return ""

    def cancel(self, job_id: str) -> None:
        """ssh ``scancel <id>``.  Idempotent (already-gone job -> no error)."""
        proc = self._run_remote(f"scancel {shlex.quote(str(job_id))} 2>/dev/null")
        if proc.returncode != 0:
            raise TransportError(
                f"scancel failed (rc={proc.returncode}): "
                f"{proc.stderr or proc.stdout or ''}"
            )

    def accounting(self, job_id: str) -> Dict[str, str]:
        """Authoritative terminal facts for one job from ``sacct``.

        One ssh round-trip returns the raw accounting line set; callers embed
        these strings verbatim into qualification receipts so the verifier can
        strictly re-parse them offline.  Unknown/missing fields come back as
        "" rather than guessed values.
        """
        fmt = "JobID,State,ExitCode,Partition,NodeList,ReqTRES,AllocTRES"
        source = f"sacct -X -P -n --format={fmt} -j {shlex.quote(str(job_id))}"
        proc = self._run_remote(f"{source} 2>/dev/null")
        self._check(proc, "sacct accounting")
        fields: Dict[str, str] = {
            "raw_state": "",
            "exit_code_raw": "",
            "partition": "",
            "node_list": "",
            "req_tres": "",
            "alloc_tres": "",
            "source": source,
        }
        for line in proc.stdout.splitlines():
            parts = line.split("|")
            if len(parts) < 7 or parts[0] != str(job_id):
                continue  # skip .batch/.extern rows
            fields["raw_state"] = parts[1]
            fields["exit_code_raw"] = parts[2]
            fields["partition"] = parts[3]
            fields["node_list"] = parts[4]
            fields["req_tres"] = parts[5]
            fields["alloc_tres"] = parts[6]
            break
        return fields

    def alloc_tres(self, job_id: str) -> str:
        """AllocTRES from sacct accounting ("" while pending/unallocated).

        Backs the adapter's C4 reconciliation, which previously only worked
        with test transports that duck-typed this method.
        """
        return self.accounting(str(job_id))["alloc_tres"]

    def stage(
        self,
        local_paths: Iterable[Union[str, os.PathLike[str]]],
        remote_dir: str,
    ) -> List[str]:
        """sync_back: rsync -a -c each local path up to
        <remote_workspace>/<remote_dir>.  shared_fs is a path-mapping no-op."""
        if self.sync == "shared_fs":
            return [self._to_remote_path(str(p)) for p in local_paths]
        dest_dir = f"{self.remote_workspace}/{remote_dir}"
        self._run_remote(f"mkdir -p {shlex.quote(dest_dir)}")
        for p in local_paths:
            self._rsync(str(p), f"{self.ssh.target()}:{dest_dir}/", "stage input")
        return [f"{dest_dir}/{Path(p).name}" for p in local_paths]

    def fetch(
        self,
        remote_paths: Iterable[str],
        local_dir: Union[str, os.PathLike[str]],
    ) -> List[Path]:
        """sync_back: rsync -a -c each remote path down to local_dir.
        shared_fs is a path-mapping no-op."""
        if self.sync == "shared_fs":
            return [Path(p) for p in remote_paths]
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        written: List[Path] = []
        for rpath in remote_paths:
            local = Path(local_dir) / Path(rpath).name
            self._rsync(f"{self.ssh.target()}:{rpath}", str(local), "fetch output")
            written.append(local)
        return written

    def sync_workspace(
        self,
        direction: str,
        workspace: Optional[Union[str, os.PathLike[str]]] = None,
    ) -> None:
        """Whole-stage sync.  stage -> rsync the local workspace tree up (trailing
        slash preserves structure); fetch -> pull the remote tree back down."""
        local = self.workspace if workspace is None else str(workspace)
        if direction == "stage":
            self._rsync(f"{local}/", f"{self.ssh.target()}:{self.remote_workspace}/", "sync_workspace stage")
        elif direction == "fetch":
            Path(local).mkdir(parents=True, exist_ok=True)
            self._rsync(f"{self.ssh.target()}:{self.remote_workspace}/", f"{local}/", "sync_workspace fetch")
        else:
            raise ValueError(f"sync_workspace direction must be 'stage'|'fetch', got {direction!r}")


# --------------------------------------------------------------------------- #
# Factory + CLI
# --------------------------------------------------------------------------- #


def make_transport(
    backend: str,
    *,
    container: Optional[str] = None,
    workspace: str = "/app",
    state_dir: Optional[str] = None,
    ssh: Optional[SshConfig] = None,
    remote_workspace: str = "/app",
    sync: str = "shared_fs",
) -> SlurmTransport:
    """Build a transport by name (``pseudo`` or ``ssh``)."""
    if backend == "pseudo":
        return PseudoSlurmTransport(
            container=container,
            workspace=workspace,
            state_dir=state_dir,
        )
    if backend == "ssh":
        if ssh is None:
            raise ValueError("SshSlurmTransport requires an SshConfig")
        return SshSlurmTransport(
            ssh=ssh,
            workspace=workspace,
            remote_workspace=remote_workspace,
            sync=sync,
        )
    raise ValueError(f"unknown transport backend: {backend!r}")


def main(argv: Optional[List[str]] = None) -> int:
    """Print usage.  Performs no scheduler operations on its own."""
    print("slurm_transport.py — 034 HPC transport (implemented)")
    print()
    print("Backends:")
    print("  PseudoSlurmTransport  Mac test-bed: sbatch/squeue/sacct/scancel via")
    print("                        docker exec into the case container (or direct")
    print("                        when in-container); workspace = /app bind-mount.")
    print("  SshSlurmTransport     HPC: same commands via ssh <login>; workspace =")
    print("                        HPC shared filesystem or per-stage rsync sync-back.")
    print()
    print("Interface (SlurmTransport):")
    print("  submit(script, opts) -> job_id")
    print("  status(job_id)       -> JobState (PENDING/RUNNING/COMPLETED/FAILED/")
    print("                           CANCELLED/UNKNOWN)")
    print("  log(job_id, tail)    -> output text")
    print("  cancel(job_id)       -> None")
    print("  stage(...) / fetch(...) / sync_workspace(direction, workspace)")
    print("  wait(job_id, poll, timeout) -> JobState")
    print()
    print("Job lifecycle follows the shared contract: sacct-authoritative with")
    print("squeue fallback; query failure -> UNKNOWN (never terminal); log() is")
    print("best-effort; cancel() is idempotent.")
    print()
    print("Usage examples:")
    print("  t = make_transport('pseudo', container='034-ref-run-01', workspace='/app')")
    print("  jid = t.submit('/app/work/geopt/geopt.slurm')")
    print("  t.wait(jid, poll=10, timeout=21600) ; t.log(jid, tail=50)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
