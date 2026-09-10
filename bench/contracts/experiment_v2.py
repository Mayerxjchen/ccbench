"""Bench Experiment v2 contract and matrix resolution.

Defines the Case x Model x Skill x Repeat experiment model, backed by
formal schemas:
- schemas/experiment-spec.v2.schema.json
- schemas/experiment-lock.v2.schema.json
- schemas/run-lock.v2.schema.json
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
SPEC_SCHEMA_PATH = SCHEMA_DIR / "experiment-spec.v2.schema.json"
LOCK_SCHEMA_PATH = SCHEMA_DIR / "experiment-lock.v2.schema.json"
RUN_LOCK_SCHEMA_PATH = SCHEMA_DIR / "run-lock.v2.schema.json"


class ExperimentError(Exception):
    """Raised when an experiment specification or lock fails contract validation."""


@dataclass(frozen=True)
class ExperimentBudget:
    max_model_turns: int
    max_total_tokens: int
    agent_active_walltime_sec: float
    scheduler_wait_walltime_sec: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelEntry:
    name: str
    provider: str
    model_id: str
    deployment_id: str = "default"
    identity_strength: str = "alias"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelRegistry:
    """Registry of models backed by experiments/models.toml."""

    def __init__(self, models: dict[str, ModelEntry]) -> None:
        self.models = models

    @classmethod
    def from_file(cls, path: Path) -> ModelRegistry:
        if not path.is_file():
            raise FileNotFoundError(f"Models file not found: {path}")
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        raw_models = data.get("models", {})
        entries: dict[str, ModelEntry] = {}
        for name, info in raw_models.items():
            entries[name] = ModelEntry(
                name=name,
                provider=info["provider"],
                model_id=info["model_id"],
                deployment_id=info.get("deployment_id", "default"),
                identity_strength=info.get("identity_strength", "alias"),
            )
        return cls(entries)

    def get(self, name: str) -> ModelEntry | None:
        return self.models.get(name)

    def require(self, name: str) -> ModelEntry:
        if name not in self.models:
            raise KeyError(
                f"unknown model {name!r}, available: {sorted(self.models.keys())}"
            )
        return self.models[name]


@dataclass(frozen=True)
class ExperimentSpecV2:
    schema_version: int
    experiment_id: str
    description: str
    mode: str
    cases: list[str]
    models: list[str]
    skills: list[str]
    repeats: int
    budget: ExperimentBudget
    max_concurrent_runs: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentSpecV2:
        validate_experiment_spec(data)
        b = data["budget"]
        budget = ExperimentBudget(
            max_model_turns=int(b["max_model_turns"]),
            max_total_tokens=int(b["max_total_tokens"]),
            agent_active_walltime_sec=float(b["agent_active_walltime_sec"]),
            scheduler_wait_walltime_sec=float(b["scheduler_wait_walltime_sec"]),
        )
        runner = data.get("runner", {})
        return cls(
            schema_version=data["schema_version"],
            experiment_id=data["experiment_id"],
            description=data.get("description", ""),
            mode=data.get("mode", "formal"),
            cases=list(data["cases"]),
            models=list(data["models"]),
            skills=list(data["skills"]),
            repeats=int(data["repeats"]),
            budget=budget,
            max_concurrent_runs=int(runner.get("max_concurrent_runs", 1)),
        )

    @classmethod
    def from_file(cls, path: Path) -> ExperimentSpecV2:
        if not path.is_file():
            raise FileNotFoundError(f"Experiment spec file not found: {path}")
        content = path.read_text(encoding="utf-8")
        data = tomllib.loads(content)
        return cls.from_dict(data)

    def expand_matrix(self) -> list[dict[str, Any]]:
        """Expand into the exhaustive list of individual execution items.
        Order: Case -> Model -> Skill -> Repeat.
        """
        matrix: list[dict[str, Any]] = []
        for case in sorted(self.cases):
            for model in sorted(self.models):
                for skill in sorted(self.skills):
                    for rep in range(1, self.repeats + 1):
                        matrix.append({
                            "case": case,
                            "model": model,
                            "skill": skill,
                            "repeat": rep,
                        })
        return matrix


def _load_schema(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Required schema file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_experiment_spec(data: dict[str, Any]) -> None:
    schema = _load_schema(SPEC_SCHEMA_PATH)
    try:
        jsonschema.Draft202012Validator(schema).validate(data)
    except jsonschema.ValidationError as exc:
        raise ExperimentError(f"Invalid experiment spec: {exc.message}") from exc


def validate_experiment_lock(data: dict[str, Any]) -> None:
    schema = _load_schema(LOCK_SCHEMA_PATH)
    try:
        jsonschema.Draft202012Validator(schema).validate(data)
    except jsonschema.ValidationError as exc:
        raise ExperimentError(f"Invalid experiment lock: {exc.message}") from exc


def validate_run_lock(data: dict[str, Any]) -> None:
    schema = _load_schema(RUN_LOCK_SCHEMA_PATH)
    try:
        jsonschema.Draft202012Validator(schema).validate(data)
    except jsonschema.ValidationError as exc:
        raise ExperimentError(f"Invalid run lock: {exc.message}") from exc


def _validate_hex_digest(
    name: str,
    value: Any,
    expected_len: int = 64,
    prefix: str = "sha256:",
    allow_placeholders: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ExperimentError(f"{name} must be a string, got {type(value).__name__}")
    if prefix and not value.startswith(prefix):
        raise ExperimentError(f"{name} must start with {prefix!r}, got {value!r}")
    hex_part = value[len(prefix):] if prefix else value
    if len(hex_part) != expected_len or not all(c in "0123456789abcdefABCDEF" for c in hex_part):
        raise ExperimentError(f"{name} must be a {expected_len}-char hex string, got {value!r}")
    if not allow_placeholders:
        if hex_part == "0" * expected_len:
            raise ExperimentError(f"{name} cannot be a zero-placeholder: {value!r}")
        if value.lower() in ("unknown", "null", "none", ""):
            raise ExperimentError(f"{name} cannot be empty or placeholder: {value!r}")
    return value


def canonical_run_lock_digest(run_lock_doc: dict[str, Any]) -> str:
    """Compute sha256 digest of canonical json serialization of RunLockV2."""
    raw = json.dumps(run_lock_doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def build_experiment_lock(
    spec: ExperimentSpecV2,
    bench_commit: str,
    allow_placeholders: bool = False,
) -> dict[str, Any]:
    """Generate and validate an immutable experiment lock dictionary."""
    _validate_hex_digest("bench_commit", bench_commit, expected_len=40, prefix="", allow_placeholders=allow_placeholders)
    matrix = spec.expand_matrix()
    canonical_spec_json = json.dumps(
        {
            "schema_version": spec.schema_version,
            "experiment_id": spec.experiment_id,
            "mode": spec.mode,
            "cases": sorted(spec.cases),
            "models": sorted(spec.models),
            "skills": sorted(spec.skills),
            "repeats": spec.repeats,
            "budget": spec.budget.to_dict(),
        },
        sort_keys=True,
    )
    spec_digest = "sha256:" + hashlib.sha256(canonical_spec_json.encode("utf-8")).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock_data: dict[str, Any] = {
        "schema_version": 2,
        "experiment_id": spec.experiment_id,
        "mode": spec.mode,
        "spec_digest": spec_digest,
        "bench_commit": bench_commit,
        "total_runs": len(matrix),
        "budget": spec.budget.to_dict(),
        "matrix": matrix,
        "created_at": stamp,
    }
    validate_experiment_lock(lock_data)
    return lock_data


def build_run_lock_v2(
    *,
    run_id: str,
    experiment_id: str,
    case: str,
    model: str,
    skill: str,
    repeat: int,
    budget: ExperimentBudget,
    model_entry: ModelEntry,
    candidate_digest: str,
    verifier_digest: str,
    bench_commit: str,
    compute_profile_digest: str | None = None,
    runtime_digests: dict[str, str] | None = None,
    created_at: str | None = None,
    allow_placeholders: bool = False,
) -> dict[str, Any]:
    """Construct and validate an immutable RunLockV2 dictionary."""
    _validate_hex_digest("bench_commit", bench_commit, expected_len=40, prefix="", allow_placeholders=allow_placeholders)
    _validate_hex_digest("candidate_digest", candidate_digest, expected_len=64, prefix="sha256:", allow_placeholders=allow_placeholders)
    _validate_hex_digest("verifier_digest", verifier_digest, expected_len=64, prefix="sha256:", allow_placeholders=allow_placeholders)
    if compute_profile_digest:
        _validate_hex_digest("compute_profile_digest", compute_profile_digest, expected_len=64, prefix="sha256:", allow_placeholders=allow_placeholders)
    if runtime_digests:
        for k, v in runtime_digests.items():
            _validate_hex_digest(f"runtime_digests[{k}]", v, expected_len=64, prefix="sha256:", allow_placeholders=allow_placeholders)

    stamp = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    strength = model_entry.identity_strength
    if strength not in ("alias", "alias-only", "exact-snapshot", "pinned-digest"):
        strength = "alias-only"
    doc: dict[str, Any] = {
        "schema_version": 2,
        "run_id": run_id,
        "experiment_id": experiment_id,
        "case": case,
        "model": model,
        "skill": skill,
        "repeat": repeat,
        "budget": budget.to_dict(),
        "model_identity": {
            "provider": model_entry.provider,
            "requested_model": model_entry.model_id,
            "identity_strength": strength,
        },
        "candidate_digest": candidate_digest,
        "verifier_digest": verifier_digest,
        "bench_commit": bench_commit,
        "created_at": stamp,
    }
    if compute_profile_digest:
        doc["compute_profile_digest"] = compute_profile_digest
    if runtime_digests:
        doc["runtime_digests"] = runtime_digests
    validate_run_lock(doc)
    return doc
