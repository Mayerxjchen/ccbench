"""HPC JobSpec: the only thing a benchmark site hands to the bench-hpc client.

A JobSpec is parsed from a local YAML file, validated against
``schemas/hpc-job.schema.json``, then hardened with security rules the schema
cannot express:

- ``command`` is pure argv — a shell string is a schema error;
- ``inputs``/``outputs`` are relative and never contain ``..``;
- resources are within the platform's resource profile;
- output globs never escape the job workspace.

Validation failures raise :class:`JobError`. The trusted gateway and every
adapter re-validate a submitted spec; a client's own validation is a first
line of defence, not the boundary.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "hpc-job.schema.json"
RESOURCE_PROFILE_SCHEMA = (
    Path(__file__).resolve().parents[2] / "schemas" / "resource-profile.schema.json"
)

KNOWN_RESOURCES = ("cpus", "memory_gb", "gpus", "walltime_minutes")


class JobError(Exception):
    """A JobSpec (or resource profile) violates the contract."""


@dataclass(frozen=True)
class JobResources:
    """Resources for one single-node job.

    ``memory_gb`` is memory per allocated node and renders as Slurm
    ``--mem=<N>G``. Multi-node jobs require a future explicit contract rather
    than silently reinterpreting this field as total job memory.
    """

    cpus: int
    memory_gb: int
    gpus: int = 0
    walltime_minutes: int = 60

    def to_dict(self) -> dict[str, int]:
        return {
            "cpus": self.cpus,
            "memory_gb": self.memory_gb,
            "gpus": self.gpus,
            "walltime_minutes": self.walltime_minutes,
        }


@dataclass(frozen=True)
class JobSpec:
    """Immutable, self-identifying compute request."""

    schema_version: int = 1
    idempotency_key: str = ""
    runtime: str = ""
    command: tuple[str, ...] = ()
    resources: JobResources = field(default_factory=JobResources)
    environment: dict[str, str] = field(default_factory=dict)
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    source: Path | None = None

    RUNTIME_RE = re.compile(r"^[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$")

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        platform_profile: dict[str, Any] | None = None,
        workspace_root: Path | None = None,
    ) -> "JobSpec":
        """Parse and validate a job file (YAML or JSON).

        ``platform_profile`` is a resource-profile dict whose maxima the job's
        resources must stay within. ``workspace_root`` is the directory the
        gateway will treat as the job workspace; output globs must resolve
        inside it.
        """
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise JobError(f"cannot read job file {path}: {exc}") from exc
        try:
            payload = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise JobError(f"job file {path} is not valid YAML/JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise JobError("job file must contain a mapping")
        spec = cls._from_payload(payload, source=path)
        spec.validate(platform_profile=platform_profile, workspace_root=workspace_root)
        return spec

    @classmethod
    def _from_payload(cls, payload: dict[str, Any], *, source: Path | None) -> "JobSpec":
        try:
            jsonschema.Draft202012Validator(_load_schema(SCHEMA_PATH)).iter_errors(payload)
        except jsonschema.SchemaError as exc:
            raise JobError(f"hpc-job schema is malformed: {exc}") from exc
        errors = sorted(
            jsonschema.Draft202012Validator(_load_schema(SCHEMA_PATH)).iter_errors(payload),
            key=lambda err: err.path,
        )
        if errors:
            first = errors[0]
            where = ".".join(str(p) for p in first.path) or "$"
            raise JobError(f"job violates hpc-job.schema.json at {where}: {first.message}")
        if not cls.RUNTIME_RE.match(payload["runtime"]):
            raise JobError(
                "runtime must be pinned to a digest: image@sha256:<64 hex>, got "
                f"{payload['runtime']!r}"
            )
        res = payload["resources"]
        return cls(
            schema_version=payload["schema_version"],
            idempotency_key=payload["idempotency_key"],
            runtime=payload["runtime"],
            command=tuple(payload["command"]),
            resources=JobResources(
                cpus=res["cpus"],
                memory_gb=res["memory_gb"],
                gpus=res.get("gpus", 0),
                walltime_minutes=res["walltime_minutes"],
            ),
            environment=dict(payload.get("environment", {})),
            inputs=tuple(payload.get("inputs", [])),
            outputs=tuple(payload.get("outputs", [])),
            source=source,
        )

    def validate(
        self,
        *,
        platform_profile: dict[str, Any] | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        _reject_unsafe_paths(self.inputs, "input")
        _reject_unsafe_paths(self.outputs, "output")
        if platform_profile is not None:
            self._check_profile(platform_profile)
        if workspace_root is not None:
            _reject_escaping_output_globs(self.outputs, workspace_root)

    def _check_profile(self, profile: dict[str, Any]) -> None:
        try:
            jsonschema.Draft202012Validator(_load_schema(RESOURCE_PROFILE_SCHEMA)).iter_errors(
                profile
            )
        except jsonschema.SchemaError as exc:
            raise JobError(f"resource-profile schema is malformed: {exc}") from exc
        errors = list(
            jsonschema.Draft202012Validator(_load_schema(RESOURCE_PROFILE_SCHEMA)).iter_errors(
                profile
            )
        )
        if errors:
            raise JobError(f"invalid resource profile: {errors[0].message}")
        ceiling = {
            "cpus": profile["max_cpus"],
            "memory_gb": profile["max_memory_gb"],
            "gpus": profile["max_gpus"],
            "walltime_minutes": profile["max_walltime_minutes"],
        }
        for name in KNOWN_RESOURCES:
            requested = getattr(self.resources, name)
            if requested > ceiling[name]:
                raise JobError(
                    f"resource {name}={requested} exceeds profile maximum "
                    f"{ceiling[name]} ({profile['name']})"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "idempotency_key": self.idempotency_key,
            "runtime": self.runtime,
            "command": list(self.command),
            "resources": self.resources.to_dict(),
            "environment": dict(self.environment),
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
        }


def _load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _reject_unsafe_paths(paths: tuple[str, ...], kind: str) -> None:
    for raw in paths:
        if not isinstance(raw, str) or raw == "":
            raise JobError(f"{kind} path must be a non-empty string: {raw!r}")
        if raw.startswith("/") or "\\" in raw:
            raise JobError(f"{kind} path must be relative (no absolute/backslash): {raw!r}")
        parts = Path(raw).parts
        if ".." in parts:
            raise JobError(f"{kind} path must not contain '..': {raw!r}")


def _reject_escaping_output_globs(outputs: tuple[str, ...], workspace_root: Path) -> None:
    """Reject output globs whose literal prefix would escape the workspace.

    Only the leading literal (up to the first glob metacharacter) is checked:
    a pattern like ``results/*.out`` is anchored at ``workspace/results`` and is
    fine; ``../outside/*.out`` is rejected by both the ``..`` rule and here.
    """
    workspace_root = Path(workspace_root).resolve()
    for raw in outputs:
        literal = raw.split("*", 1)[0].split("?", 1)[0].split("[", 1)[0].rstrip("/")
        if literal == "":
            continue  # pattern anchored at workspace root, e.g. "*.out"
        anchor = (workspace_root / literal).resolve()
        if not anchor.is_relative_to(workspace_root):
            raise JobError(f"output glob escapes job workspace: {raw!r}")
