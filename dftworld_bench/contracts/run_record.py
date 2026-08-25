"""Immutable per-attempt Run Record.

One ``RunRecord`` captures every identity needed to attribute an attempt:
case, execution class, Agent/model, treatment (condition + skill source +
content digest), image, profile, submission, verifier, platform, job, usage,
lifecycle, and the classified result. Records are written once and never
updated; ``RunStore`` refuses a second write for the same ``run_id``.

The class is deliberately NOT frozen: ``validate()`` is the enforcement point,
so harness code can assemble a record in pieces and only ever persist one that
passes. Treatment identity is frozen at validate time — a ``with-skill`` record
must carry an immutable image identity plus a content digest; a ``no-skill``
record must carry neither.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import Any

from dftworld_bench.contracts.events import EventChainError, RunEvent, validate_event_chain
from dftworld_bench.contracts.result import BenchmarkResult
from dftworld_bench.core.budgets import BUDGET_DOMAINS
from dftworld_bench.core.lifecycle import (
    InvalidTransition,
    Lifecycle,
    RunPhase,
    TERMINAL_PHASES,
)

VALID_EXECUTION_CLASSES = ("local_sandbox", "hpc_controller")
VALID_CONDITIONS = ("no-skill", "with-skill")


class RunRecordError(Exception):
    """A RunRecord violates its identity or immutability invariants."""


@dataclass
class RunRecord:
    """One classified attempt, persisted once and never updated."""

    run_id: str
    case_id: str
    execution_class: str
    agent_model: str
    condition_id: str
    skills_source: str
    skills_sha: str | None
    image: str
    benchmark_commit: str
    profile: str
    submission_root: str
    verifier: str
    platform: str
    job_id: str | None
    site_config_digest: str
    usage: dict[str, Any]
    lifecycle_events: list[dict[str, Any]]
    result: BenchmarkResult
    # aggregation / legacy compatibility (not part of scientific identity)
    experiment_id: str = "default"
    replicate: int = 1
    attempt: int = 1
    thread_dir: str | None = None
    legacy_normalized: bool = False
    schema_version: int = 1

    def validate(self) -> None:
        if not self.run_id:
            raise RunRecordError("run_id is required")
        if not self.case_id:
            raise RunRecordError("case_id is required")
        if self.execution_class not in VALID_EXECUTION_CLASSES:
            raise RunRecordError(
                f"execution_class must be one of {VALID_EXECUTION_CLASSES}, "
                f"got {self.execution_class!r}"
            )
        if self.condition_id not in VALID_CONDITIONS:
            raise RunRecordError(
                f"condition_id must be one of {VALID_CONDITIONS}, "
                f"got {self.condition_id!r}"
            )
        if self.condition_id == "no-skill":
            if self.skills_source != "none":
                raise RunRecordError(
                    "no-skill treatment requires skills_source='none', "
                    f"got {self.skills_source!r}"
                )
            if self.skills_sha is not None:
                raise RunRecordError(
                    "no-skill treatment requires skills_sha=None; got a digest"
                )
        else:  # with-skill
            if self.skills_source != "image":
                raise RunRecordError(
                    "with-skill treatment requires skills_source='image', "
                    f"got {self.skills_source!r}"
                )
            if not self.skills_sha:
                raise RunRecordError(
                    "with-skill treatment requires skills_sha (content digest); "
                    "got None"
                )
        if not isinstance(self.result, BenchmarkResult):
            raise RunRecordError("result must be a BenchmarkResult")
        if not self.lifecycle_events:
            raise RunRecordError("lifecycle_events must not be empty")
        for event in self.lifecycle_events:
            if "phase" not in event or "at" not in event:
                raise RunRecordError("lifecycle event requires both 'phase' and 'at'")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "execution_class": self.execution_class,
            "agent_model": self.agent_model,
            "experiment_id": self.experiment_id,
            "condition_id": self.condition_id,
            "skills_source": self.skills_source,
            "skills_sha": self.skills_sha,
            "image": self.image,
            "benchmark_commit": self.benchmark_commit,
            "profile": self.profile,
            "submission_root": self.submission_root,
            "verifier": self.verifier,
            "platform": self.platform,
            "job_id": self.job_id,
            "site_config_digest": self.site_config_digest,
            "replicate": self.replicate,
            "attempt": self.attempt,
            "thread_dir": self.thread_dir,
            "legacy_normalized": self.legacy_normalized,
            "usage": dict(self.usage),
            "lifecycle_events": [dict(e) for e in self.lifecycle_events],
            "result": self.result.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunRecord":
        return cls(
            run_id=str(payload["run_id"]),
            case_id=str(payload["case_id"]),
            execution_class=str(payload["execution_class"]),
            agent_model=str(payload["agent_model"]),
            experiment_id=str(payload.get("experiment_id", "default")),
            condition_id=str(payload["condition_id"]),
            skills_source=str(payload["skills_source"]),
            skills_sha=None
            if payload["skills_sha"] is None
            else str(payload["skills_sha"]),
            image=str(payload["image"]),
            benchmark_commit=str(payload["benchmark_commit"]),
            profile=str(payload["profile"]),
            submission_root=str(payload["submission_root"]),
            verifier=str(payload["verifier"]),
            platform=str(payload["platform"]),
            job_id=None if payload["job_id"] is None else str(payload["job_id"]),
            site_config_digest=str(payload["site_config_digest"]),
            replicate=int(payload.get("replicate", 1)),
            attempt=int(payload.get("attempt", 1)),
            thread_dir=None
            if payload.get("thread_dir") is None
            else str(payload["thread_dir"]),
            legacy_normalized=bool(payload.get("legacy_normalized", False)),
            usage=dict(payload["usage"]),
            lifecycle_events=[dict(e) for e in payload["lifecycle_events"]],
            result=BenchmarkResult.from_dict(payload["result"]),
        )


# --------------------------------------------------------------------------- #
# v2: monotonic one-terminal lifecycle + full run identity
# --------------------------------------------------------------------------- #

_TERMINAL_PHASE_BY_RESULT = {
    "VALID_RESULT": "COMPLETED",
    "AGENT_FAILURE": "FAILED_AGENT",
    "INFRA_INVALID": "INVALID_INFRA",
}

# v2-only identity: absent in v1, required in every v2 payload.
_V2_IDENTITY_KEYS = (
    "run_mode",
    "lock_digest",
    "event_root_digest",
    "event_chain",
    "attempts",
    "budgets",
    "runtime_identities",
    "remote_jobs",
    "seal",
)

VALID_RUN_MODES = ("smoke", "formal")

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RUNTIME_ROLES = frozenset({"candidate", "control", "compute", "verifier"})


def _require_digest(name: str, value: Any) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise RunRecordError(f"{name} must be a full sha256 digest")
    return value


def model_attempts_from_event_chain(events: list[RunEvent]) -> list[dict[str, Any]]:
    """Project model-attempt audit rows from an already validated event chain."""
    committed = {
        (event.operation_id, event.payload.get("accepted_attempt"))
        for event in events
        if event.kind == "model_response_committed"
    }
    return [
        {
            "operation_id": event.operation_id,
            **event.payload,
            "accepted": (
                event.operation_id,
                event.payload.get("attempt"),
            ) in committed,
        }
        for event in events
        if event.kind == "model_attempt"
    ]


def _validate_monotonic_timestamps(events: list[dict[str, Any]]) -> None:
    """Lifecycle ``at`` timestamps must be non-decreasing where they parse as
    ISO; unparseable values (legacy event trails) are not part of the order
    contract and are skipped rather than rejected."""
    previous = None
    for event in events:
        raw = event.get("at")
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if previous is not None and ts < previous:
            raise RunRecordError("lifecycle timestamps must be non-decreasing")
        previous = ts


@dataclass
class RunRecordV2(RunRecord):
    """v2 RunRecord: one-terminal monotonic lifecycle + full run identity.

    v1 records remain readable historical artifacts (``RunStore.load``
    dispatches on ``schema_version``).  A v2 record must replay exactly once
    through the lifecycle state machine, end in exactly one terminal phase,
    and have that terminal agree with the classified result.  The extra
    identity fields bind the record to its resolved run lock, the
    hash-chained event root, model attempts, budgets, runtime identities and
    remote HPC jobs — the full accountability trail.
    """

    lock_digest: str | None = None
    event_root_digest: str | None = None
    event_chain: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    budgets: dict[str, Any] = field(default_factory=dict)
    runtime_identities: dict[str, Any] = field(default_factory=dict)
    remote_jobs: list[dict[str, Any]] = field(default_factory=list)
    seal: dict[str, Any] | None = None
    schema_version: int = 2
    # ``smoke`` = case-construction, scripted Candidate: written with
    # counted=false / formal_eligible=false and refused by the formal
    # aggregator.  ``formal`` = counted scientifically.  Every mode still
    # passes the unconditional harness identity gates — there is no lockless
    # bypass.
    run_mode: str = "formal"

    def validate(self) -> None:
        super().validate()
        if self.run_mode not in VALID_RUN_MODES:
            raise RunRecordError(
                f"run_mode must be one of {VALID_RUN_MODES}, got {self.run_mode!r}"
            )
        _require_digest("lock_digest", self.lock_digest)
        _require_digest("event_root_digest", self.event_root_digest)
        _require_digest("site_config_digest", self.site_config_digest)
        if not self.platform.strip() or self.platform == "unknown":
            raise RunRecordError("platform must identify the real execution site")
        if not self.profile.strip() or self.profile == "unknown":
            raise RunRecordError("profile must identify the real site profile")

        try:
            session_events = [RunEvent.from_dict(event) for event in self.event_chain]
            if not session_events:
                raise RunRecordError("event_chain must contain validated session events")
            validate_event_chain(session_events)
        except (EventChainError, KeyError, TypeError, ValueError) as exc:
            raise RunRecordError(f"event chain validation failed: {exc}") from exc
        if session_events[-1].event_digest != self.event_root_digest:
            raise RunRecordError(
                "event chain root does not match event_root_digest: "
                f"{session_events[-1].event_digest!r} != {self.event_root_digest!r}"
            )

        missing_budgets = BUDGET_DOMAINS - set(self.budgets)
        extra_budgets = set(self.budgets) - BUDGET_DOMAINS
        if missing_budgets or extra_budgets:
            raise RunRecordError(
                "budgets must be a complete budget snapshot; "
                f"missing={sorted(missing_budgets)}, extra={sorted(extra_budgets)}"
            )
        if any(not isinstance(value, int) or value < 0 for value in self.budgets.values()):
            raise RunRecordError("budget snapshot values must be non-negative integers")

        actual_roles = set(self.runtime_identities)
        if actual_roles != _RUNTIME_ROLES:
            raise RunRecordError(
                "runtime identities must contain candidate, control, compute, and "
                f"verifier; got {sorted(actual_roles)}"
            )
        control = self.runtime_identities["control"]
        if self.execution_class == "local_sandbox" and control is not None:
            raise RunRecordError(
                "local_sandbox control runtime must be null by contract"
            )
        if self.execution_class == "hpc_controller" and not isinstance(control, dict):
            raise RunRecordError(
                "hpc_controller requires a real control runtime identity"
            )
        for role, identity in self.runtime_identities.items():
            if role == "control" and identity is None:
                continue
            if not isinstance(identity, dict) or identity.get("role") != role:
                raise RunRecordError(f"runtime identity {role!r} has a mismatched role")
            for key in ("profile", "image"):
                if not isinstance(identity.get(key), str) or not identity[key]:
                    raise RunRecordError(f"runtime identity {role!r} requires {key}")
            _require_digest(f"runtime identity {role!r} digest", identity.get("digest"))
        if self.runtime_identities["candidate"]["image"] != self.image:
            raise RunRecordError("candidate runtime identity does not match record image")
        if self.runtime_identities["verifier"]["image"] != self.verifier:
            raise RunRecordError("verifier runtime identity does not match record verifier")

        chained_attempts = model_attempts_from_event_chain(session_events)
        if self.attempts != chained_attempts:
            raise RunRecordError("attempts do not match the validated event chain")
        for attempt in self.attempts:
            _require_digest("model attempt metadata_digest", attempt.get("metadata_digest"))

        if self.execution_class == "hpc_controller" and not self.remote_jobs:
            raise RunRecordError("hpc_controller records require remote_jobs audit entries")
        for job in self.remote_jobs:
            required = {"job_id", "state", "resources", "artifact_hashes"}
            missing = required - set(job)
            if missing:
                raise RunRecordError(
                    f"remote_jobs entry missing required fields: {sorted(missing)}"
                )
            if not isinstance(job["job_id"], str) or not job["job_id"]:
                raise RunRecordError("remote_jobs job_id must be non-empty")
            if not isinstance(job["state"], str) or not job["state"]:
                raise RunRecordError("remote_jobs state must be non-empty")
            if not isinstance(job["resources"], dict):
                raise RunRecordError("remote_jobs resources must be an object")
            if not isinstance(job["artifact_hashes"], dict):
                raise RunRecordError("remote_jobs artifact_hashes must be an object")
            for name, digest in job["artifact_hashes"].items():
                _require_digest(f"remote job artifact {name!r}", digest)
            # Operation-attempt lineage: optional pairwise, validated when
            # present; dispatcher-produced records always carry both.
            operation_id = job.get("operation_id")
            attempt = job.get("attempt")
            if (operation_id is None) != (attempt is None):
                raise RunRecordError(
                    "remote_jobs entry requires operation_id and attempt together"
                )
            if operation_id is not None and (
                not isinstance(operation_id, str) or not operation_id
            ):
                raise RunRecordError("remote_jobs operation_id must be non-empty")
            if attempt is not None and (
                not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1
            ):
                raise RunRecordError("remote_jobs attempt must be a positive integer")

        if self.result.result_class.value == "VALID_RESULT" and self.seal is None:
            raise RunRecordError("VALID_RESULT records require a submission seal")
        if self.seal is not None:
            required = {
                "manifest_digest", "file_count", "total_bytes", "sealed_at",
                "legacy_layout", "exclusions",
            }
            missing = required - set(self.seal)
            if missing:
                raise RunRecordError(
                    f"seal missing required fields: {sorted(missing)}"
                )
            _require_digest("seal manifest_digest", self.seal.get("manifest_digest"))
            if type(self.seal["file_count"]) is not int or self.seal["file_count"] < 0:
                raise RunRecordError("seal file_count must be a non-negative integer")
            if type(self.seal["total_bytes"]) is not int or self.seal["total_bytes"] < 0:
                raise RunRecordError("seal total_bytes must be a non-negative integer")
            if not isinstance(self.seal["sealed_at"], str) or not self.seal["sealed_at"]:
                raise RunRecordError("seal sealed_at must be non-empty")
            if not isinstance(self.seal["legacy_layout"], bool):
                raise RunRecordError("seal legacy_layout must be boolean")
            if not isinstance(self.seal["exclusions"], list):
                raise RunRecordError("seal exclusions must be a list")

        if self.result.run_id != self.run_id:
            raise RunRecordError("result run_id does not match record run_id")

        phases = [str(event["phase"]) for event in self.lifecycle_events]
        terminal_values = {phase.value for phase in TERMINAL_PHASES}
        terminals = [phase for phase in phases if phase in terminal_values]
        if len(terminals) != 1:
            raise RunRecordError(
                "record must have exactly one terminal phase "
                f"(got {len(terminals)} in {phases})"
            )
        if phases[-1] != terminals[0]:
            raise RunRecordError(
                f"terminal phase {terminals[0]} must be the last lifecycle event"
            )
        expected = _TERMINAL_PHASE_BY_RESULT.get(self.result.result_class.value)
        if phases[-1] != expected:
            raise RunRecordError(
                f"terminal {phases[-1]} does not match result "
                f"{self.result.result_class.value} (expected {expected})"
            )
        # Replay the full trail through the lifecycle state machine.  CREATED
        # is the implicit birth of the Lifecycle; a trail that records it
        # explicitly (fixtures) and one that starts at PACKAGED (the harness)
        # must both replay legally.
        try:
            lifecycle = Lifecycle(run_id=self.run_id)
            first = 0
            if phases and phases[0] == RunPhase.CREATED.value:
                first = 1
            for phase in phases[first:]:
                lifecycle.transition(RunPhase(phase), datetime.now(timezone.utc))
        except (ValueError, InvalidTransition) as exc:
            raise RunRecordError(f"lifecycle violates the state machine: {exc}") from exc
        _validate_monotonic_timestamps(self.lifecycle_events)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        formal = self.run_mode == "formal"
        payload.update(
            {
                "run_mode": self.run_mode,
                # Derived flags: a record is formally eligible iff it is a
                # FORMAL run; it is counted iff it is formally eligible AND
                # scientifically valid.  SMOKE records are neither.
                "formal_eligible": formal,
                "counted": (
                    formal
                    and self.result.result_class.value == "VALID_RESULT"
                ),
                "lock_digest": self.lock_digest,
                "event_root_digest": self.event_root_digest,
                "event_chain": [dict(event) for event in self.event_chain],
                "attempts": [dict(a) for a in self.attempts],
                "budgets": dict(self.budgets),
                "runtime_identities": dict(self.runtime_identities),
                "remote_jobs": [dict(j) for j in self.remote_jobs],
                "seal": None if self.seal is None else dict(self.seal),
            }
        )
        payload["schema_version"] = 2
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunRecordV2":
        if payload.get("schema_version") != 2:
            raise RunRecordError(
                f"RunRecordV2.from_dict requires schema_version=2, "
                f"got {payload.get('schema_version')!r}"
            )
        for key in _V2_IDENTITY_KEYS:
            if key not in payload:
                raise RunRecordError(f"v2 record requires {key!r}")
        return cls(
            run_id=str(payload["run_id"]),
            case_id=str(payload["case_id"]),
            execution_class=str(payload["execution_class"]),
            agent_model=str(payload["agent_model"]),
            experiment_id=str(payload.get("experiment_id", "default")),
            condition_id=str(payload["condition_id"]),
            skills_source=str(payload["skills_source"]),
            skills_sha=None
            if payload["skills_sha"] is None
            else str(payload["skills_sha"]),
            image=str(payload["image"]),
            benchmark_commit=str(payload["benchmark_commit"]),
            profile=str(payload["profile"]),
            submission_root=str(payload["submission_root"]),
            verifier=str(payload["verifier"]),
            platform=str(payload["platform"]),
            job_id=None if payload["job_id"] is None else str(payload["job_id"]),
            site_config_digest=str(payload["site_config_digest"]),
            replicate=int(payload.get("replicate", 1)),
            attempt=int(payload.get("attempt", 1)),
            thread_dir=None
            if payload.get("thread_dir") is None
            else str(payload["thread_dir"]),
            legacy_normalized=bool(payload.get("legacy_normalized", False)),
            usage=dict(payload["usage"]),
            lifecycle_events=[dict(e) for e in payload["lifecycle_events"]],
            result=BenchmarkResult.from_dict(payload["result"]),
            lock_digest=None
            if payload["lock_digest"] is None
            else str(payload["lock_digest"]),
            event_root_digest=None
            if payload["event_root_digest"] is None
            else str(payload["event_root_digest"]),
            event_chain=[dict(event) for event in payload["event_chain"]],
            attempts=[dict(a) for a in payload["attempts"]],
            budgets=dict(payload["budgets"]),
            runtime_identities=dict(payload["runtime_identities"]),
            remote_jobs=[dict(j) for j in payload["remote_jobs"]],
            seal=None if payload["seal"] is None else dict(payload["seal"]),
            # Default "formal": v2 records written before RunMode existed were
            # all formal runs; the derived counted/formal_eligible flags in the
            # payload are recomputed, never trusted from disk.
            run_mode=str(payload.get("run_mode", "formal")),
        )
