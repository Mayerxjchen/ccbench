"""Trusted Harness: fixed orchestration, generic over the AgentAdapter.

The harness owns the phases and their order (LIFECYCLE.md) and the benchmark
infra (submission collection, quarantine, independent Verifier, immutable run
record). It never touches pagent, docker, or a scheduler — the provider half
lives in ``bench.agents`` behind the ``AgentAdapter`` protocol.

Local order is normative:

    package → candidate_start → agent_start → agent_stop → candidate_freeze →
    submission_collect → candidate_destroy → structural_validate → quarantine →
    verifier_start → record_write

The structural gate runs on the collected raw tree BEFORE quarantine/seal and
the fresh Verifier; a submission that fails the structural contract is an
AGENT_FAILURE (INVALID_SUBMISSION) and the Verifier never starts.

The candidate is destroyed BEFORE the sealed submission is quarantined and
verified in a fresh container — a Verifier never coexists with a Candidate.
Teardown (freeze + collect + destroy) is always attempted, even after an agent
timeout or an Agent-adapter exception. Timeouts and adapter crashes are
classified (AGENT_FAILURE / INFRA_INVALID), never collapsed to reward = 0.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from bench.agents import AgentAdapter
from bench.contracts.result import (
    BenchmarkResult,
    FailureCode,
    ResultClass,
)
from bench.contracts.run_record import (
    RunRecordV2,
    model_attempts_from_event_chain,
)
from bench.core.budgets import BudgetLedger, BudgetPolicy
from bench.core.event_store import EventStore
from bench.core.quarantine import (
    QuarantineError,
    QuarantineLimits,
    SubmissionSeal,
    collect_raw_submission,
    quarantine_submission,
)
from bench.core.run_store import RunStore
from bench.core.submission_contract import validate_submission
from bench.core.verifier import VerifierSpec, run_verifier

# The Verifier's own interpreter: the verifier image's venv, outside /root and
# /app.  Pinned by the Harness so case tests never execute an interpreter
# shipped in the sealed submission (which may be hostile).
VERIFIER_PYTHON = "/opt/dftworld/venv/bin/python"

# Harness phase → RunPhase for the lifecycle trail. record_write is an ACTION,
# not a phase: it is excluded from the trail and the record ends in exactly
# one terminal phase (COMPLETED / FAILED_AGENT / INVALID_INFRA) derived from
# the classified result.
_LIFECYCLE_MAP = {
    # CREATED is the implicit birth of the Lifecycle; the first harness action
    # (package) is the PACKAGED phase.
    "package": "PACKAGED",
    "candidate_start": "CANDIDATE_STARTING",
    "agent_start": "CANDIDATE_RUNNING",
    "agent_stop": "CANDIDATE_STOPPING",
    "candidate_freeze": "CANDIDATE_FROZEN",
    "submission_collect": "SUBMISSION_COLLECTED",
    "candidate_destroy": "CANDIDATE_DESTROYED",
    "structural_validate": "STRUCTURALLY_VALIDATED",
    "quarantine": "QUARANTINED",
    "sealed": "SEALED",
    "verifier_start": "VERIFYING",
}
_TERMINAL_PHASE = {
    "VALID_RESULT": "COMPLETED",
    "AGENT_FAILURE": "FAILED_AGENT",
    "INFRA_INVALID": "INVALID_INFRA",
}

from bench.config.env import get_env_int

VERIFY_MAX_FILES = get_env_int("BENCH_VERIFY_MAX_FILES", 50000)
VERIFY_MAX_SINGLE_BYTES = get_env_int("BENCH_VERIFY_MAX_SINGLE_BYTES", 2 * 1024**3)
VERIFY_MAX_TOTAL_BYTES = get_env_int("BENCH_VERIFY_MAX_TOTAL_BYTES", 20 * 1024**3)


class RunMode(str, Enum):
    """How a run may be counted.  The identity contract is strict in BOTH
    modes — there is no lockless bypass — but only ``FORMAL`` runs are
    counted scientifically and eligible for a formal comparison.

    ``SMOKE`` runs (case-construction, scripted Candidate) must still carry a
    resolved run lock, complete budgets, runtime identities, and a durable
    session; their records are written with ``counted=false`` and
    ``formal_eligible=false`` and the formal aggregator refuses them.
    """

    SMOKE = "smoke"
    FORMAL = "formal"


@dataclass(frozen=True)
class Treatment:
    """The experiment treatment under which one attempt runs (scientific ID)."""

    experiment_id: str = "default"
    condition_id: str = "no-skill"
    skills_source: str = "none"
    skill_image: str = ""
    skill_commit: str = ""
    skills_sha: str | None = None
    replicate: int = 1
    attempt: int = 1
    benchmark_commit: str = "unknown"
    agent_model: str = "unknown"


@dataclass(frozen=True)
class Profile:
    """Site / execution identity. Only the secret-free digest is stored."""

    name: str = "local"
    execution_class: str = "local_sandbox"
    platform: str = "local_docker"
    site_config_digest: str = "sha256:local"
    legacy_normalized: bool = False


@dataclass(frozen=True)
class HarnessSpec:
    """Everything the harness needs to run one case attempt."""

    case_id: str
    case_dir: Path
    image: str
    instruction: str
    run_id: str
    agent_timeout_sec: float
    threads_root: Path
    # How this run may be counted.  The identity gates below are enforced
    # unconditionally in BOTH modes; ``mode`` only changes how the record is
    # labelled (counted/formal_eligible) and what the aggregator accepts.
    mode: RunMode = RunMode.FORMAL
    gpus: int = 0
    verifier_timeout_sec: float = 600.0
    verifier_env: dict[str, str] = field(default_factory=dict)
    submission_root: str = "."
    legacy_submission_layout: bool = True
    model: str = "deepseek/deepseek-chat"
    max_turns: int = 32
    verbose: bool = False
    # Structural pre-Verifier gate (shape checks only; never executes)
    submission_contract: dict[str, Any] = field(default_factory=dict)
    # Raw budgets block from the resolved run lock (lock-domain key names).
    # When present, the run carries a frozen BudgetPolicy and a live ledger
    # whose snapshot lands in the run record. Missing formal limits are
    # rejected at run start (infra error, never an agent failure).
    budgets: dict[str, Any] | None = None
    # Resolved run-lock digest; recorded verbatim in the v2 run record so the
    # record is cryptographically pinned to the lock that authorized it.
    lock_digest: str | None = None
    # Resolved, digest-pinned identities for all four runtime roles.
    runtime_identities: dict[str, dict[str, Any] | None] | None = None
    # Gateway audit rows. Local runs keep this empty; HPC runs must provide one
    # or more complete job rows before finalization.
    remote_jobs: tuple[dict[str, Any], ...] = ()


class TrustedHarness:
    """Runs one attempt through the fixed phase order and records it once."""

    LOCAL_ORDER = [
        "package",
        "candidate_start",
        "agent_start",
        "agent_stop",
        "candidate_freeze",
        "submission_collect",
        "candidate_destroy",
        "structural_validate",
        "quarantine",
        "sealed",
        "verifier_start",
        "record_write",
    ]

    def __init__(
        self,
        agent: AgentAdapter,
        *,
        store: RunStore | None = None,
        collector: Callable[..., Any] | None = None,
        structural_validator: Callable[..., Any] | None = None,
        quarantiner: Callable[..., Any] | None = None,
        verifier: Callable[..., Any] | None = None,
        session: EventStore | None = None,
    ):
        self.agent = agent
        self.store = store or RunStore(Path("jobs"))
        self.collector = collector or collect_raw_submission
        self.structural_validator = structural_validator or validate_submission
        self.quarantiner = quarantiner or quarantine_submission
        self.verifier = verifier or run_verifier
        self.events: list[str] = []
        self.ledger: BudgetLedger | None = None
        # Durable session: the harness checkpoints at its side-effect
        # boundaries (freeze, seal, verifier) so the RunCoordinator can resume
        # exactly-once around the attempt.
        self.session = session

    def _emit(self, phase: str) -> None:
        self.events.append(phase)

    async def run(
        self,
        spec: HarnessSpec,
        treatment: Treatment,
        profile: Profile,
    ) -> BenchmarkResult:
        self.events = []
        # Freeze the run's budget policy up front. A lock that declares budgets
        # but omits formal limits is an infra error and must never be recorded
        # as an agent failure — hence outside the phase try/except.
        #
        # The identity gates are UNCONDITIONAL: every run, SMOKE or FORMAL,
        # carries a resolved lock digest, complete budget limits, resolved
        # runtime identities and a durable session.  There is no lockless
        # bypass in either mode; ``mode`` only labels how the record is counted.
        self.ledger = None
        if spec.budgets is None:
            raise ValueError(
                f"{spec.mode.value} run requires resolved budget limits"
            )
        if spec.lock_digest is None:
            raise ValueError(
                f"{spec.mode.value} run requires a resolved lock_digest"
            )
        if spec.runtime_identities is None:
            raise ValueError(
                f"{spec.mode.value} run requires resolved runtime identities"
            )
        if self.session is None:
            raise ValueError(
                f"{spec.mode.value} run requires a durable event session"
            )
        self.ledger = BudgetLedger(
            BudgetPolicy.from_lock({"budgets": spec.budgets})
        )
        # I3: the ledger is harness-owned, but model turns happen inside the
        # agent adapter's drive loop — inject the run ledger so charging is
        # wired into the production model call path.
        attach_ledger = getattr(self.agent, "attach_budget_ledger", None)
        if attach_ledger is not None:
            attach_ledger(self.ledger)
        thread_dir = Path(spec.threads_root) / spec.case_id
        workspace = thread_dir / "workspace"
        logs_dir = thread_dir / "verifier-logs"
        run_id = spec.run_id
        t0 = time.perf_counter()
        logs: dict[str, Any] = {}
        failure: tuple[FailureCode, str] | None = None

        try:
            self._emit("package")
            await self.agent.prepare()

            self._emit("candidate_start")
            self._emit("agent_start")
            try:
                await asyncio.wait_for(
                    self.agent.start(spec.instruction),
                    timeout=spec.agent_timeout_sec,
                )
            except TimeoutError:
                failure = (
                    FailureCode.AGENT_TIMEOUT,
                    f"agent timeout after {spec.agent_timeout_sec}s",
                )
            except Exception as exc:
                failure = (
                    FailureCode.HARNESS_FAILURE,
                    f"agent adapter failed: {type(exc).__name__}: {exc}",
                )
            self._emit("agent_stop")

            self._emit("candidate_freeze")
            try:
                logs = self.agent.collect_logs() or {}
            except Exception:
                logs = {}
            metainfo = self._metainfo(spec, treatment, logs, failure)
            try:
                await self.agent.stop(metainfo)
            except Exception as exc:
                failure = failure or (
                    FailureCode.HARNESS_FAILURE,
                    f"adapter stop failed: {type(exc).__name__}: {exc}",
                )
            self._session_emit(
                "HARNESS", "candidate_frozen",
                {"run_id": run_id, "case_id": spec.case_id},
            )
            self._session_checkpoint(
                phase="candidate_frozen", run_id=run_id,
                lock_digest=spec.lock_digest,
            )

            self._emit("submission_collect")
            raw_dir = thread_dir / "raw-submission"
            try:
                self.collector(
                    workspace,
                    spec.submission_root,
                    raw_dir,
                    spec.legacy_submission_layout,
                )
            except QuarantineError as exc:
                # A Candidate-origin unsafe node (symlink, hardlink, special
                # file) is the agent's fault: the frozen workspace was not a
                # clean submission.  AGENT_FAILURE / INVALID_SUBMISSION.
                failure = failure or (
                    FailureCode.INVALID_SUBMISSION,
                    f"submission collection rejected unsafe source: {exc}",
                )
            except OSError as exc:
                # The source node already passed lstat validation; an OS error
                # (vanished/swapped source, disk) is infrastructure, not the
                # Candidate.  INFRA_INVALID / HARNESS_FAILURE.
                failure = failure or (
                    FailureCode.HARNESS_FAILURE,
                    f"submission collection failed: {type(exc).__name__}: {exc}",
                )
            finally:
                self._emit("candidate_destroy")
                try:
                    await self.agent.close()
                except Exception as exc:
                    failure = (
                        FailureCode.HARNESS_FAILURE,
                        f"candidate teardown failed: {type(exc).__name__}: {exc}",
                    )

            # Structural submission gate: shape checks only, never executes.
            # Order is collection → structural validation → quarantine → Verifier.
            self._emit("structural_validate")
            if failure is None and spec.submission_contract:
                structural_errors = self.structural_validator(
                    raw_dir, spec.submission_contract
                )
                if structural_errors:
                    failure = (
                        FailureCode.INVALID_SUBMISSION,
                        "submission fails the structural contract: "
                        + "; ".join(
                            f"{e.code} {e.path}" for e in structural_errors
                        ),
                    )
        except Exception as exc:
            failure = failure or (
                FailureCode.HARNESS_FAILURE,
                f"harness phase failed: {type(exc).__name__}: {exc}",
            )

        elapsed_sec = time.perf_counter() - t0
        seal = None  # only a successful quarantine produces a seal

        if failure is not None:
            code, reason = failure
            try:
                result = BenchmarkResult.agent_failure(run_id, code, reason)
            except ValueError:
                result = BenchmarkResult.infra_invalid(run_id, code, reason)
        else:
            self._emit("quarantine")
            sealed_dir = thread_dir / "sealed-submission"
            limits = QuarantineLimits(
                max_files=VERIFY_MAX_FILES,
                max_single_bytes=VERIFY_MAX_SINGLE_BYTES,
                max_total_bytes=VERIFY_MAX_TOTAL_BYTES,
                allow_archives=False,
            )
            seal = self.quarantiner(raw_dir, sealed_dir, limits)
            self._session_emit(
                "HARNESS", "submission_sealed",
                {"run_id": run_id, "sealed_dir": str(sealed_dir)},
            )
            self._session_checkpoint(
                phase="submission_sealed", run_id=run_id,
                lock_digest=spec.lock_digest,
            )
            self._emit("sealed")
            self._emit("verifier_start")
            reference_dir = spec.case_dir / "reference"
            solution_dir = spec.case_dir / "solution"
            verifier_spec = VerifierSpec(
                image=spec.image,
                timeout_sec=spec.verifier_timeout_sec,
                # Harness-owned: the Verifier runs its own interpreter, never
                # one shipped in the sealed submission.  Case verifier_env
                # cannot override the pinned path.
                env={**spec.verifier_env, "VERIFIER_PYTHON": VERIFIER_PYTHON},
                tests_dir=spec.case_dir / "tests",
                reference_dir=reference_dir if reference_dir.is_dir() else None,
                solution_dir=solution_dir if solution_dir.is_dir() else None,
            )
            result = self.verifier(
                verifier_spec, sealed_dir, logs_dir, run_id=run_id
            )
            self._session_emit(
                "HARNESS", "verifier_result",
                {
                    "run_id": run_id,
                    "result_class": result.result_class.value,
                    "failure_code": result.failure_code.value if result.failure_code else None,
                },
            )
            self._session_checkpoint(
                phase="verifier_result", run_id=run_id,
                lock_digest=spec.lock_digest,
            )

        self._emit("record_write")
        self._session_checkpoint(
            phase="record_write", run_id=run_id, lock_digest=spec.lock_digest,
        )
        self._record(
            spec,
            treatment,
            profile,
            result,
            logs,
            elapsed_sec,
            thread_dir,
            seal=seal,
        )
        return result

    def _session_emit(self, operation_id: str, kind: str, payload: dict) -> None:
        if self.session is not None:
            self.session.append(operation_id, kind, payload)

    def _session_checkpoint(
        self, *, phase: str, run_id: str, lock_digest: str
    ) -> None:
        if self.session is not None:
            self.session.write_checkpoint(
                {
                    "run_id": run_id,
                    "phase": phase,
                    "lock_digest": lock_digest,
                    "budgets": (
                        self.ledger.snapshot() if self.ledger is not None else {}
                    ),
                }
            )

    def _metainfo(
        self,
        spec: HarnessSpec,
        treatment: Treatment,
        logs: dict[str, Any],
        failure: tuple[FailureCode, str] | None,
    ) -> dict:
        return {
            "title": spec.case_id,
            "task": spec.case_id,
            "image": spec.image,
            "model": spec.model,
            "ok": failure is None,
            "error": failure[1] if failure else "",
            "tool_calls": logs.get("tool_calls", 0),
            "tokens": logs.get("tokens", 0),
            "budget_snapshot": (
                self.ledger.snapshot() if self.ledger is not None else {}
            ),
            "elapsed_sec": 0.0,  # interim; final elapsed lands in the run record
            "experiment_id": treatment.experiment_id,
            "condition_id": treatment.condition_id,
            "replicate": treatment.replicate,
            "attempt": treatment.attempt,
            "benchmark_commit": treatment.benchmark_commit,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _record(
        self,
        spec: HarnessSpec,
        treatment: Treatment,
        profile: Profile,
        result: BenchmarkResult,
        logs: dict[str, Any],
        elapsed_sec: float,
        thread_dir: Path,
        seal: SubmissionSeal | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        # record_write is an action, not a phase: exclude it and end the trail
        # in exactly one terminal phase derived from the classified result.
        events = [
            {"phase": _LIFECYCLE_MAP.get(phase, phase), "at": now}
            for phase in self.events
            if phase != "record_write"
        ]
        events.append(
            {"phase": _TERMINAL_PHASE.get(result.result_class.value, "COMPLETED"),
             "at": now}
        )
        assert self.session is not None  # v2 preflight requires it
        checkpoint = self.session.load_checkpoint(spec.lock_digest)
        if checkpoint is None:
            raise ValueError("v2 run requires a durable session checkpoint")
        session_events = self.session.load_events()
        if not session_events:
            raise ValueError("v2 run requires a non-empty validated event chain")
        event_root = session_events[-1].event_digest
        event_chain = [event.to_dict() for event in session_events]
        attempts = model_attempts_from_event_chain(session_events)
        seal_payload = None
        if seal is not None:
            seal_payload = {
                "manifest_digest": seal.manifest_digest,
                "file_count": seal.file_count,
                "total_bytes": seal.total_bytes,
                "sealed_at": seal.sealed_at.isoformat(),
                "legacy_layout": seal.legacy_layout,
                "exclusions": list(seal.exclusions),
            }
        record = RunRecordV2(
            run_id=spec.run_id,
            run_mode=spec.mode.value,
            case_id=spec.case_id,
            execution_class=profile.execution_class,
            agent_model=treatment.agent_model or spec.model,
            condition_id=treatment.condition_id,
            skills_source=treatment.skills_source,
            skills_sha=treatment.skills_sha,
            image=spec.image,
            benchmark_commit=treatment.benchmark_commit,
            profile=profile.name,
            submission_root=spec.submission_root,
            verifier=spec.image,
            platform=profile.platform,
            job_id=None,
            site_config_digest=profile.site_config_digest,
            usage={
                "tool_calls": logs.get("tool_calls", 0),
                "tokens": logs.get("tokens", 0),
                "elapsed_sec": round(elapsed_sec, 3),
                "budgets": (
                    self.ledger.snapshot() if self.ledger is not None else {}
                ),
            },
            lifecycle_events=events,
            result=result,
            experiment_id=treatment.experiment_id,
            replicate=treatment.replicate,
            attempt=treatment.attempt,
            thread_dir=str(thread_dir),
            legacy_normalized=profile.legacy_normalized,
            lock_digest=spec.lock_digest,
            event_root_digest=event_root,
            event_chain=event_chain,
            attempts=attempts,
            budgets=self.ledger.snapshot(),
            runtime_identities={
                role: None if identity is None else dict(identity)
                for role, identity in spec.runtime_identities.items()
            },
            remote_jobs=[dict(job) for job in spec.remote_jobs],
            seal=seal_payload,
        )
        self.store.create(record)
