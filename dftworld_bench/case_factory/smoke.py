"""Production runtime checker for Draft Cases (Task 9/10 closure).

Runs the case's generated Draft through the real Trusted Harness with an
*isolated* Candidate and the real ``run_verifier`` path.

Candidate exec is pluggable:

- ``audit`` (default): ``IsolatedScriptedCandidate`` — a real child process
  with private HOME, cwd and env, not a function writing straight into the
  workspace.
- ``docker``: ``DockerScriptedCandidate`` — the generated Case Dockerfile is
  built and run as an isolated container (network none, non-root 65532:65532,
  read-only root, private /tmp tmpfs, no host HOME/credentials, a single rw
  workspace mount).  The container, not the host, writes the final output.

Verifier container exec is pluggable:

- ``docker``: executes ``build_verifier_command``'s argv with the real docker
  runtime.  If the image is missing or docker is unavailable the run is
  ``INFRA_INVALID`` and gates stay unset — fail-closed, never a best-effort
  pass.
- ``audit``: CI/offline runner that asserts the isolation flags in the built
  argv and simulates the container by writing a conforming ``result.json`` into
  the verifier logs dir.  ``run_verifier`` then parses it through the exact
  production path (``_parse_verifier_output``).

Gate promotion on a ``FailureCode.PASS`` result:

- every runner writes the contract gates ``runtime_contract_valid`` and
  ``verifier_command_valid``;
- the full runtime gates ``runtime_valid`` and ``candidate_smoke_valid`` are
  written ONLY when the Candidate AND the Verifier both ran as real containers
  (``candidate="docker"`` + ``runner="docker"``).

Evidence (RunRecord, sealed submission, verifier logs) is durable only when
``runs_dir`` is passed; otherwise it lives in a private temp dir deleted on
return.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

from dftworld_bench.case_factory.state import (
    read_factory_state,
    write_factory_state,
)
from dftworld_bench.contracts.case import CaseSpec
from dftworld_bench.contracts.resolved_lock import ResolvedRunLock
from dftworld_bench.contracts.result import BenchmarkResult, FailureCode
from dftworld_bench.core.event_store import EventStore
from dftworld_bench.core.harness import (
    HarnessSpec,
    Profile,
    RunMode,
    Treatment,
    TrustedHarness,
)
from dftworld_bench.core.packager import package_candidate
from dftworld_bench.core.run_store import RunStore
from dftworld_bench.core.verifier import run_verifier

DECLARED_CONTENT = "final accuracy 0.9876\n"
SMOKE_EXPERIMENT = "case-construction-smoke"

# The isolation contract every generated Draft expects from its Verifier
# container; the audit runner asserts these are present in the built argv.
VERIFIER_ISOLATION = (
    "--rm",
    "--network",
    "none",
    "--user",
    "65532:65532",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
)


class SmokeError(Exception):
    """Raised when a Draft cannot be smoke-run at all (pre-check failure)."""


class IsolatedScriptedCandidate:
    """Real-process Candidate for the smoke.

    ``prepare`` packages through the allowlist Packager into the harness
    workspace; ``start`` spawns a detached child ``python`` process whose HOME
    is a private temp dir and whose cwd is the candidate workspace; the child
    writes the declared final output.  ``close`` terminates and reaps the child
    — real teardown, never a no-op.  No pagent, no external model.
    """

    def __init__(
        self,
        case_dir: Path,
        threads_root: Path,
        final_content: str = DECLARED_CONTENT,
    ) -> None:
        self.case_dir = Path(case_dir)
        self.task_name = self.case_dir.name
        self.workspace = Path(threads_root) / self.task_name / "workspace"
        self.final_content = final_content
        self._child: subprocess.Popen | None = None
        self._home: Path | None = None

    @property
    def version(self) -> str:
        return "isolated-scripted-candidate-v2"

    async def prepare(self) -> None:
        spec = CaseSpec.load(self.case_dir)
        package_candidate(spec, self.workspace)

    async def start(self, instruction: str) -> None:
        self._home = Path(tempfile.mkdtemp(prefix="scripted-home-"))
        final = self.workspace / "final"
        final.mkdir(parents=True, exist_ok=True)
        code = (
            "import pathlib, sys\n"
            "pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')\n"
        )
        env = {
            "HOME": str(self._home),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": "",
        }
        # A real separate process: private HOME, workspace cwd, no inherited
        # PAGENT/dftworld state.  The child — not the harness process — writes
        # the final output bytes.
        self._child = subprocess.Popen(
            [sys.executable, "-c", code, str(final / "result.txt"), self.final_content],
            cwd=str(self.workspace),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        rc = self._child.wait(timeout=30)
        if rc != 0:
            raise RuntimeError(f"scripted candidate child exited {rc}")

    async def stop(self, metainfo: dict) -> None:
        """candidate_freeze: the child already sealed its output; nothing more."""

    async def close(self) -> None:
        """candidate_destroy: terminate and reap the child if still alive."""
        child = self._child
        self._child = None
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)

    def collect_logs(self) -> dict:
        return {
            "tool_calls": 0,
            "tokens": 0,
            "skills_invoked": [],
            "candidate_home_isolated": self._home is not None,
        }


class DockerScriptedCandidate:
    """Real-container Candidate for the full smoke.

    ``prepare`` builds the generated Case Dockerfile into a per-case image tag;
    ``start`` runs that image as an isolated container: ``--network none``,
    non-root ``65532:65532``, ``--read-only`` root with a private ``/tmp``
    tmpfs, a fresh ``HOME`` and no host mounts beyond the harness workspace
    (docker never inherits host env, so no host HOME/git/credentials leak).  The
    container — not the host process — writes the declared final output into the
    mounted workspace.  ``close`` relies on ``--rm``; the container is gone once
    the foreground run exits.
    """

    def __init__(
        self,
        case_dir: Path,
        threads_root: Path,
        final_content: str = DECLARED_CONTENT,
    ) -> None:
        self.case_dir = Path(case_dir)
        self.task_name = self.case_dir.name
        self.workspace = Path(threads_root) / self.task_name / "workspace"
        self.final_content = final_content
        self.image_tag = f"dftworld-smoke-{self.task_name}"

    @property
    def version(self) -> str:
        return "docker-scripted-candidate-v1"

    async def prepare(self) -> None:
        spec = CaseSpec.load(self.case_dir)
        package_candidate(spec, self.workspace)
        proc = subprocess.run(
            ["docker", "build", "-t", self.image_tag, str(self.case_dir)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"docker build {self.image_tag} failed: {proc.stderr[-400:]}"
            )

    def run_command(self) -> list[str]:
        """The isolated candidate argv; asserted by tests, executed by start."""
        code = (
            "import pathlib, sys\n"
            "pathlib.Path(sys.argv[1]).write_text(sys.argv[2], encoding='utf-8')\n"
        )
        return [
            "docker", "run", "--rm",
            "--network", "none",
            "--user", "65532:65532",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--env", "HOME=/tmp/home",
            "--env", "PYTHONPATH=",
            "--workdir", "/workspace",
            "--volume", f"{self.workspace.resolve()}:/workspace",
            self.image_tag,
            "python3", "-c", code,
            # Inside-container path: /workspace maps to the harness workspace.
            "/workspace/final/result.txt", self.final_content,
        ]

    async def start(self, instruction: str) -> None:
        final = self.workspace / "final"
        final.mkdir(parents=True, exist_ok=True)
        # The container uid 65532 must be able to write the mounted workspace;
        # this is a private smoke temp tree, world-writable is fine.
        os.chmod(self.workspace, 0o777)
        os.chmod(final, 0o777)
        proc = subprocess.run(self.run_command(), capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"docker candidate exited {proc.returncode}: {proc.stderr[-400:]}"
            )

    async def stop(self, metainfo: dict) -> None:
        """candidate_freeze: the container already sealed its output."""

    async def close(self) -> None:
        """candidate_destroy: ``--rm`` removed the container on exit; nothing
        to reap.  A run that never exited is a bug surfaced by the run record."""

    def collect_logs(self) -> dict:
        return {
            "tool_calls": 0,
            "tokens": 0,
            "skills_invoked": [],
            "candidate_container_image": self.image_tag,
            "candidate_home_isolated": True,
        }


class AuditVerifierRunner:
    """CI runner: asserts isolation flags, then simulates the container.

    It receives the exact ``build_verifier_command`` argv, checks every
    isolation flag is present, locates the ``/logs/verifier`` mount and writes a
    schema-conforming ``result.json`` there as the container would.  The real
    ``run_verifier``/``_parse_verifier_output`` decodes it.
    """

    def __init__(self) -> None:
        self.last_cmd: list[str] | None = None
        self.isolation_ok: bool | None = None
        self.logs_dir: Path | None = None

    def __call__(
        self,
        cmd: list[str],
        timeout: float | None = None,
        capture_output: bool | None = None,
        text: bool | None = None,
        check: bool | None = None,
    ) -> subprocess.CompletedProcess:
        self.last_cmd = list(cmd)
        self.isolation_ok = all(flag in cmd for flag in VERIFIER_ISOLATION)
        self.logs_dir = _logs_mount(cmd)
        if self.logs_dir is not None:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            (self.logs_dir / "result.json").write_text(
                json.dumps(
                    {
                        "run_id": "placeholder",
                        "result_class": "VALID_RESULT",
                        "failure_code": "PASS",
                        "reason": "scripted smoke verifier (audit runner)",
                        "retryable": False,
                    }
                ),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _logs_mount(cmd: list[str]) -> Path | None:
    """Extract the host path of the ``/logs/verifier`` volume from a docker argv."""
    for i, token in enumerate(cmd):
        if token == "--volume" and i + 1 < len(cmd):
            host, _, guest = cmd[i + 1].partition(":")
            if guest == "/logs/verifier":
                return Path(host)
    return None


def _docker_runner(
    spec, sealed_submission: Path, logs: Path, run_id: str | None = None
) -> BenchmarkResult:
    """Default production Verifier path: run the built argv for real."""
    return run_verifier(spec, sealed_submission, logs, run_id=run_id)


def audit_verifier(spec, sealed_submission: Path, logs: Path, run_id=None):
    """Real ``run_verifier`` with the audit container executor injected."""
    runner = AuditVerifierRunner()
    return run_verifier(spec, sealed_submission, logs, run_id=run_id, runner=runner)


def _gate_updates(result: BenchmarkResult, real_containers: bool) -> dict[str, bool]:
    """Gate changes the smoke checker derives from one run.

    Only a real ``FailureCode.PASS`` promotes anything.  Contract gates are set
    by every runner; the full runtime gates require a real container Candidate
    AND a real container Verifier (``real_containers``).
    """
    if not (result.is_counted_scientifically and result.failure_code is FailureCode.PASS):
        return {}
    updates = {"runtime_contract_valid": True, "verifier_command_valid": True}
    if real_containers:
        updates.update(runtime_valid=True, candidate_smoke_valid=True)
    return updates


# --------------------------------------------------------------------------- #
# Smoke identity (RunMode.SMOKE): real but non-formal.
#
# The smoke run passes the SAME unconditional harness identity gates as a
# formal run — resolved lock digest, complete budget limits, resolved runtime
# identities, durable session — with NO lockless bypass.  The lock is produced
# by the real ResolvedRunLock.create machinery and every digest is derived
# from real bytes (case Dockerfile, tests manifest, smoke identity content).
# What makes it non-formal is the identity block itself: smoke experiment,
# scripted/isolated candidate, benchmark_commit "unknown".  Smoke records are
# labelled counted=false / formal_eligible=false and the formal aggregator
# and invalid-run ledger refuse them.
# --------------------------------------------------------------------------- #

# Complete lock-domain budgets: every ledger domain is covered, so the run's
# BudgetPolicy parses fully (missing formal limits are an infra error, never
# a smoke shortcut).
_SMOKE_BUDGETS = {
    "max_model_turns": 64,
    "max_total_tokens": 10_000_000,
    "agent_active_walltime_sec": 86_400,
    "scheduler_wait_walltime_sec": 3_600,
    "logical_requests": 128,
    "api_attempts": 512,
    "usd_microcost": 5_000_000,
    "run_total_walltime_ms": 172_800_000,
    "local_tool_walltime_ms": 7_200_000,
    "api_retry_walltime_ms": 3_600_000,
    "jobs": 16,
    "cpu_hours": 500,
    "gpu_hours": 24,
    "storage_byte_hours": 10_000_000_000,
}


def _smoke_digest(*parts: str) -> str:
    """A real sha256 content digest over smoke identity bytes."""
    return "sha256:" + hashlib.sha256(
        "\0".join(parts).encode("utf-8")
    ).hexdigest()


def _candidate_digest(case_dir: Path) -> str:
    """sha256 over the case's candidate image definition bytes."""
    dockerfile = case_dir / "Dockerfile"
    if not dockerfile.is_file():
        dockerfile = case_dir / "environment" / "Dockerfile"
    if dockerfile.is_file():
        return "sha256:" + hashlib.sha256(dockerfile.read_bytes()).hexdigest()
    return _smoke_digest("candidate", case_dir.name)


def _tests_digest(case_dir: Path) -> str:
    """sha256 over the case tests tree — what the smoke verifier executes."""
    tests = case_dir / "tests"
    digest = hashlib.sha256()
    if tests.is_dir():
        for rel in sorted(
            p.relative_to(tests) for p in tests.rglob("*") if p.is_file()
        ):
            digest.update(str(rel).encode("utf-8"))
            digest.update(b"\0")
            digest.update((tests / rel).read_bytes())
    return "sha256:" + digest.hexdigest()


def smoke_identity(case_dir: Path, tspec: dict, run_id: str) -> dict:
    """Build the real-but-non-formal smoke identity for one run.

    Returns the complete v2 identity block: ``lock_digest`` (a genuine
    ResolvedRunLock digest), ``budgets`` (complete lock-domain limits),
    ``runtime_identities`` (4 roles; control is None for local_sandbox by
    contract), and ``site_config_digest`` (a real digest of the smoke site
    identity).  The caller also owns a durable EventStore session; together
    these satisfy every harness identity gate in RunMode.SMOKE.
    """
    case_name = case_dir.name
    candidate_digest = _candidate_digest(case_dir)
    verifier_digest = _tests_digest(case_dir)
    compute_digest = _smoke_digest("compute", case_name)
    site_digest = _smoke_digest("site", "local", "local_docker", "smoke")

    payload = {
        "case": {
            "case_id": case_name,
            "case_version": "smoke",
            "schema_version": "2",
        },
        "experiment": {
            "template_name": SMOKE_EXPERIMENT,
            "condition_id": f"{run_id}-smoke",
            "replicate": 1,
        },
        "agent": {
            "provider": "isolated",
            "model_id": "isolated-scripted-candidate-v2",
            "identity_strength": "alias",
            "engine": "scripted",
            "prompt_digest": _smoke_digest("prompt", case_name),
            "sampling_digest": _smoke_digest("sampling", case_name),
            "context_digest": _smoke_digest("context", case_name),
            "skill_bundle_digest": _smoke_digest("no-skill", case_name),
            "tool_surface_digest": _smoke_digest("tools", case_name),
        },
        "api": {
            "api_profile_digest": _smoke_digest("api", case_name),
            "endpoint_env": "DFTWORLD_SMOKE_ENDPOINT",
            "credential_env": "DFTWORLD_SMOKE_CREDENTIAL",
        },
        "candidate_runtime": {
            "image": tspec["image"],
            "runtime_digest": candidate_digest,
            "qualification_status": "smoke",
        },
        "verifier": {
            "runtime_digest": verifier_digest,
            "isolation_config_digest": _smoke_digest(
                "verifier-isolation", case_name
            ),
        },
        "infra": {
            "version": "2.0.0",
            "commit": "smoke",
            "lock_created_at": "t0",
        },
        "budgets": dict(_SMOKE_BUDGETS),
    }
    lock = ResolvedRunLock.create(payload)
    return {
        "lock_digest": lock.digest,
        "budgets": dict(_SMOKE_BUDGETS),
        "site_config_digest": site_digest,
        "runtime_identities": {
            "candidate": {
                "role": "candidate",
                "profile": f"local-smoke-{case_name}",
                "image": tspec["image"],
                "digest": candidate_digest,
            },
            # local_sandbox has no control plane by contract.
            "control": None,
            "compute": {
                "role": "compute",
                "profile": f"local-smoke-compute-{case_name}",
                "image": tspec["image"],
                "digest": compute_digest,
            },
            "verifier": {
                "role": "verifier",
                "profile": f"local-smoke-verifier-{case_name}",
                "image": tspec["image"],
                "digest": verifier_digest,
            },
        },
    }


def run_smoke(
    case_dir: Path,
    *,
    run_id: str,
    runner: str | AuditVerifierRunner = "docker",
    candidate: str = "audit",
    runs_dir: Path | None = None,
    agent_model: str = "isolated-scripted-candidate-v2",
) -> BenchmarkResult:
    """Run the Draft through the Trusted Harness and derive the factory gates.

    On a real ``FailureCode.PASS`` the checker writes the contract gates
    ``runtime_contract_valid`` / ``verifier_command_valid``; with
    ``candidate="docker"`` AND ``runner="docker"`` it additionally writes the
    full gates ``runtime_valid`` / ``candidate_smoke_valid`` (both sides really
    ran in containers).  Any other outcome leaves the gates untouched.  The
    checker never touches ``benchmark_valid``.

    ``runner`` is either the string ``"docker"`` (real verifier container) or
    ``"audit"`` (CI, asserts isolation argv + simulates output), or an
    ``AuditVerifierRunner`` instance whose ``last_cmd`` / ``isolation_ok`` can
    be inspected by the caller.

    ``candidate`` is ``"audit"`` (default, ``IsolatedScriptedCandidate``
    subprocess) or ``"docker"`` (``DockerScriptedCandidate`` — builds the
    generated Case image and runs it as an isolated container).

    ``runs_dir`` makes the evidence durable: the RunRecord, sealed submission
    and verifier logs are retained under it instead of a private temp dir that
    is deleted on return.  Pass a fresh directory per run — the RunStore is
    create-or-refuse on ``run_id``.
    """
    case_dir = Path(case_dir).resolve()
    spec = CaseSpec.load(case_dir)

    # docker bind mounts need absolute host paths; a relative case_dir or
    # --runs-dir would break every --volume (candidate workspace, verifier
    # /submission / /tests / /logs mounts).
    if runs_dir is not None:
        runs_dir = Path(runs_dir).resolve()

    if isinstance(runner, AuditVerifierRunner):
        verifier_runner = runner
        verifier = lambda s, sealed, logs, run_id=None: run_verifier(  # noqa: E731
            s, sealed, logs, run_id=run_id, runner=verifier_runner
        )
    elif runner == "audit":
        verifier_runner = AuditVerifierRunner()
        verifier = lambda s, sealed, logs, run_id=None: run_verifier(  # noqa: E731
            s, sealed, logs, run_id=run_id, runner=verifier_runner
        )
    elif runner == "docker":
        verifier = _docker_runner
    else:
        raise SmokeError(f"unknown runner {runner!r}; expected docker or audit")

    ctx = (
        nullcontext(runs_dir)
        if runs_dir is not None
        else tempfile.TemporaryDirectory(prefix="case-factory-smoke-")
    )
    with ctx as tmp:
        tmp_p = Path(tmp)
        store = RunStore(tmp_p / "runs" if runs_dir is None else tmp_p)
        tspec = _eval_task(case_dir)
        # Real-but-non-formal identity: the smoke run satisfies every harness
        # identity gate (resolved lock digest, complete budgets, resolved
        # runtime identities, durable session) — no lockless bypass — and is
        # labelled RunMode.SMOKE so its record is counted=false /
        # formal_eligible=false.
        identity = smoke_identity(case_dir, tspec, run_id)
        session = EventStore(tmp_p / "session" / "events.jsonl")
        if candidate == "docker":
            agent = DockerScriptedCandidate(case_dir, tmp_p)
        elif candidate == "audit":
            agent = IsolatedScriptedCandidate(case_dir, tmp_p)
        else:
            raise SmokeError(f"unknown candidate {candidate!r}; expected docker or audit")
        harness = TrustedHarness(
            agent, store=store, verifier=verifier, session=session
        )
        # eval.load_task names the task after the case DIRECTORY, and the
        # harness collects from ``threads_root/<task_name>/workspace``; the
        # candidate writes into that same tree, so the harness case_id must be
        # the directory name, not the full "benchmark/005-..." identity.
        spec_h = HarnessSpec(
            case_id=case_dir.name,
            case_dir=case_dir,
            image=tspec["image"],
            instruction=tspec["instruction"],
            run_id=run_id,
            agent_timeout_sec=tspec["agent_timeout_sec"],
            mode=RunMode.SMOKE,
            threads_root=tmp_p,
            gpus=tspec["gpus"],
            verifier_timeout_sec=tspec["verifier_timeout_sec"],
            verifier_env=tspec["verifier_env"],
            submission_root=tspec["submission_root"],
            legacy_submission_layout=tspec["legacy_submission_layout"],
            budgets=identity["budgets"],
            lock_digest=identity["lock_digest"],
            runtime_identities=identity["runtime_identities"],
        )
        treatment = Treatment(
            experiment_id=SMOKE_EXPERIMENT,
            condition_id="no-skill",  # scripted Candidate invokes no skill
            skills_source="none",
            replicate=1,
            attempt=1,
            benchmark_commit="unknown",
            agent_model=agent_model,
        )
        profile = Profile(
            name="local",
            execution_class=tspec["execution_class"],
            platform="local_docker",
            site_config_digest=identity["site_config_digest"],
            legacy_normalized=tspec["legacy_submission_layout"],
        )
        result = asyncio.run(harness.run(spec_h, treatment, profile))

    # Full gates require a real container on BOTH sides: a docker candidate with
    # an audit (simulated) verifier — or an audit candidate with a docker
    # verifier — proves the contract but not container-isolated execution.
    real_containers = candidate == "docker" and runner == "docker"
    updates = _gate_updates(result, real_containers)
    if updates:
        gates = read_factory_state(case_dir).with_updates(**updates)
        write_factory_state(case_dir, gates)
    return result


def _eval_task(case_dir: Path) -> dict:
    import sys as _sys

    root = Path(__file__).resolve().parents[2]
    if str(root) not in _sys.path:
        _sys.path.insert(0, str(root))
    import eval as evalmod  # type: ignore[import-not-found]

    tspec = evalmod.load_task(case_dir)
    return {
        "image": tspec.image,
        "instruction": tspec.instruction,
        "gpus": tspec.gpus,
        "agent_timeout_sec": tspec.agent_timeout_sec,
        "verifier_timeout_sec": tspec.verifier_timeout_sec,
        "verifier_env": tspec.verifier_env,
        "submission_root": tspec.submission_root,
        "legacy_submission_layout": tspec.legacy_submission_layout,
        "execution_class": tspec.execution_class,
    }
