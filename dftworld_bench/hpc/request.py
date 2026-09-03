"""ExecutionRequestV2: the Agent-facing unit of HPC work.

Identity is the triple ``(run_id, operation_id, attempt)``. The scheduler job
ID stays internal provenance; scientific retries are explicit attempt
increments by the Agent, and only bounded transport retries are automatic.

Hardening beyond the schema: relative slash-free paths, no parent traversal,
argv-only commands, capability-named runtimes (the legacy digest-pinned form
is a hidden compat path verified against locked infra), unknown fields
rejected.
:class:`AttemptLedger` enforces monotonic, gap-free, one-in-flight attempts
per operation; Task 3 wires it into the gateway ownership maps.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from dftworld_bench.hpc.job import JobError, JobResources

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "execution-request.schema.json"
)

_OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


class RequestError(Exception):
    """An ExecutionRequest violates the v2 contract."""


@dataclass(frozen=True)
class InputEntry:
    """One content-addressed input file."""

    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ExecutionRequestV2:
    """Immutable, validated request identified by (run_id-scoped) operation+attempt."""

    schema_version: int = 2
    operation_id: str = ""
    attempt: int = 1
    compute_class: str = ""
    runtime: str = ""
    command: tuple[str, ...] = ()
    resources: JobResources = field(default_factory=JobResources)
    environment: dict[str, str] = field(default_factory=dict)
    input_entries: tuple[InputEntry, ...] = ()
    outputs: tuple[str, ...] = ()
    # Provenance of compute_class: only "explicit" is admissible from an
    # agent; "legacy-inferred" is stamped by the v1 compat bridge below and
    # exists so old internal callers keep working while P10 deletes them.
    compute_class_source: str = "explicit"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExecutionRequestV2":
        if not isinstance(payload, dict):
            raise RequestError("request payload must be a mapping")
        schema = _load_schema()
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(payload),
            key=lambda err: list(err.path),
        )
        if errors:
            first = errors[0]
            where = ".".join(str(p) for p in first.path) or "$"
            raise RequestError(
                f"request violates execution-request.schema.json at {where}: "
                f"{first.message}"
            )
        try:
            resources = JobResources(
                cpus=payload["resources"]["cpus"],
                memory_gb=payload["resources"]["memory_gb"],
                gpus=payload["resources"]["gpus"],
                walltime_minutes=payload["resources"]["walltime_minutes"],
            )
        except (KeyError, TypeError) as exc:
            raise RequestError(f"invalid typed resources: {exc}") from exc
        inputs = []
        for raw in payload["inputs"]:
            entry = InputEntry(
                path=raw["path"],
                sha256=raw["sha256"],
                size_bytes=int(raw["size_bytes"]),
            )
            _reject_unsafe_path(entry.path, "input")
            inputs.append(entry)
        outputs = tuple(payload["outputs"])
        for raw in outputs:
            _reject_unsafe_path(raw, "output")
        operation_id = payload["operation_id"]
        if not _OPERATION_ID_RE.match(operation_id) or "\\" in operation_id:
            raise RequestError(f"unsafe operation_id: {operation_id!r}")
        compute_class = payload["compute_class"]
        # The schema's allOf already gates the mechanical rule; this
        # defense-in-depth check keeps it true even if the schema evolves.
        _check_compute_class_consistency(compute_class, resources)
        return cls(
            schema_version=2,
            operation_id=operation_id,
            attempt=int(payload["attempt"]),
            compute_class=compute_class,
            runtime=payload["runtime"],
            command=tuple(payload["command"]),
            resources=resources,
            environment=dict(payload.get("environment", {})),
            input_entries=tuple(inputs),
            outputs=outputs,
        )

    def idempotency_key(self, run_id: str) -> str:
        return f"{run_id}:{self.operation_id}:{self.attempt}"

    def with_attempt(self, attempt: int) -> "ExecutionRequestV2":
        return ExecutionRequestV2(
            schema_version=self.schema_version,
            operation_id=self.operation_id,
            attempt=attempt,
            compute_class=self.compute_class,
            runtime=self.runtime,
            command=self.command,
            resources=self.resources,
            environment=dict(self.environment),
            input_entries=self.input_entries,
            outputs=self.outputs,
            compute_class_source=self.compute_class_source,
        )

    def with_input(self, path: str, *, sha256: str, size_bytes: int) -> "ExecutionRequestV2":
        _reject_unsafe_path(path, "input")
        entry = InputEntry(path=path, sha256=sha256, size_bytes=size_bytes)
        entries = tuple(e for e in self.input_entries if e.path != path) + (entry,)
        return ExecutionRequestV2(
            schema_version=self.schema_version,
            operation_id=self.operation_id,
            attempt=self.attempt,
            compute_class=self.compute_class,
            runtime=self.runtime,
            command=self.command,
            resources=self.resources,
            environment=dict(self.environment),
            input_entries=tuple(sorted(entries, key=lambda e: e.path)),
            outputs=self.outputs,
            compute_class_source=self.compute_class_source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation_id": self.operation_id,
            "attempt": self.attempt,
            "compute_class": self.compute_class,
            "runtime": self.runtime,
            "command": list(self.command),
            "resources": self.resources.to_dict(),
            "environment": dict(self.environment),
            "inputs": [
                {
                    "path": e.path,
                    "sha256": e.sha256,
                    "size_bytes": e.size_bytes,
                }
                for e in self.input_entries
            ],
            "outputs": list(self.outputs),
        }

    def to_job_spec_payload(self) -> dict[str, Any]:
        """v1 wire shape for adapters that still speak JobSpec dicts."""
        return {
            "schema_version": 1,
            "idempotency_key": f"{self.operation_id}:{self.attempt}",
            "runtime": self.runtime,
            "command": list(self.command),
            "resources": self.resources.to_dict(),
            "environment": dict(self.environment),
            "inputs": [e.path for e in self.input_entries],
            "outputs": list(self.outputs),
        }


class AttemptLedger:
    """Monotonic, gap-free, one-in-flight attempt admission per operation."""

    def __init__(self) -> None:
        # operation_id -> {attempt: "in_flight" | "terminal"}
        self._states: dict[str, dict[int, str]] = {}

    def admit(self, request: ExecutionRequestV2) -> None:
        states = self._states.setdefault(request.operation_id, {})
        attempt = request.attempt
        if attempt in states:
            state = states[attempt]
            if state == "in_flight":
                raise RequestError(
                    f"attempt {attempt} of {request.operation_id!r} is already "
                    "in flight; concurrent attempts are rejected"
                )
            raise RequestError(
                f"attempt {attempt} of {request.operation_id!r} has already "
                "run; attempts never repeat"
            )
        if attempt == 1:
            states[1] = "in_flight"
            return
        predecessor = states.get(attempt - 1)
        if predecessor is None:
            raise RequestError(
                f"attempt gap: attempt 1 must precede attempt {attempt} "
                f"of {request.operation_id!r}"
            )
        if predecessor != "terminal":
            raise RequestError(
                f"attempt {attempt - 1} of {request.operation_id!r} is not "
                f"terminal yet; only a scheduler-terminal predecessor admits "
                f"attempt {attempt}"
            )
        states[attempt] = "in_flight"

    def mark_terminal(self, operation_id: str, attempt: int) -> None:
        states = self._states.get(operation_id, {})
        if states.get(attempt) != "in_flight":
            raise RequestError(
                f"cannot mark ({operation_id!r}, attempt {attempt}) terminal: "
                "not in flight"
            )
        states[attempt] = "terminal"


def _reject_unsafe_path(raw: str, kind: str) -> None:
    if not isinstance(raw, str) or raw == "":
        raise RequestError(f"{kind} path must be a non-empty string: {raw!r}")
    if raw.startswith("/") or "\\" in raw:
        raise RequestError(
            f"{kind} path must be relative (no absolute/backslash): {raw!r}"
        )
    parts = Path(raw).parts
    if ".." in parts:
        raise RequestError(f"{kind} path must not contain '..': {raw!r}")


def _check_compute_class_consistency(compute_class: str, resources: JobResources) -> None:
    """Mechanical rule: cpu means zero GPUs, gpu means at least one."""
    if compute_class == "cpu" and resources.gpus != 0:
        raise RequestError(
            f"compute_class=cpu requires gpus=0, request carries gpus="
            f"{resources.gpus}"
        )
    if compute_class == "gpu" and resources.gpus < 1:
        raise RequestError(
            f"compute_class=gpu requires gpus>=1, request carries gpus="
            f"{resources.gpus}"
        )


def request_from_legacy_v1_spec(spec: dict[str, Any]) -> ExecutionRequestV2:
    """Hidden v1-compat bridge: infer compute_class from gpus, marked legacy.

    A v2 agent payload must state compute_class explicitly — ``from_dict``
    rejects one that does not.  Internal callers still on the v1 JobSpec wire
    enter here; the inference is mechanical (gpus==0 -> cpu, else gpu) and
    the result is stamped ``compute_class_source="legacy-inferred"`` so
    audit/evidence consumers can see it was never an agent decision. This
    bridge is deleted with the v1 protocol in P10.
    """
    try:
        resources = JobResources(
            cpus=spec["resources"]["cpus"],
            memory_gb=spec["resources"]["memory_gb"],
            gpus=spec["resources"]["gpus"],
            walltime_minutes=spec["resources"]["walltime_minutes"],
        )
    except (KeyError, TypeError) as exc:
        raise RequestError(f"invalid legacy v1 resources: {exc}") from exc
    compute_class = "cpu" if resources.gpus == 0 else "gpu"
    operation_id = str(spec.get("operation_id") or "legacy-operation")
    if not _OPERATION_ID_RE.match(operation_id):
        raise RequestError(f"unsafe legacy operation_id: {operation_id!r}")
    return ExecutionRequestV2(
        schema_version=2,
        operation_id=operation_id,
        attempt=int(spec.get("attempt") or 1),
        compute_class=compute_class,
        runtime=str(spec.get("runtime", "")),
        command=tuple(spec.get("command", ())),
        resources=resources,
        environment=dict(spec.get("environment", {})),
        compute_class_source="legacy-inferred",
    )


def _load_schema() -> dict[str, Any]:
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JobError(f"execution-request schema unreadable: {exc}") from exc
