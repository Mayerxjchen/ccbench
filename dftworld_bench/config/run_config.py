"""Typed, immutable configuration for one benchmark campaign."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


SUPPORTED_PROVIDERS = frozenset(
    {"deepseek", "openai", "kimi", "longcat", "mimo", "ollama", "vllm", "sglang"}
)
EXECUTION_CLASSES = frozenset({"local_sandbox", "hpc_controller"})


class RunConfigError(ValueError):
    """The campaign configuration is absent, malformed, or unsafe."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelConfig(StrictModel):
    provider: Literal["deepseek", "openai", "kimi", "longcat", "mimo", "ollama", "vllm", "sglang"]
    model_id: str = Field(min_length=1)
    deployment_id: str = Field(min_length=1)
    identity_strength: Literal["exact", "alias-only"]


class ApiConfig(StrictModel):
    endpoint_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    credential_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    max_attempts: int = Field(ge=1, le=20)
    retry_base_delay_sec: float = Field(ge=0)
    retry_max_delay_sec: float = Field(ge=0)
    request_timeout_sec: float = Field(gt=0)
    input_usd_micros_per_million_tokens: int = Field(ge=0)
    output_usd_micros_per_million_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def delay_order(self) -> "ApiConfig":
        if self.retry_max_delay_sec < self.retry_base_delay_sec:
            raise ValueError("retry_max_delay_sec must be >= retry_base_delay_sec")
        return self


class AgentBudget(StrictModel):
    max_model_turns: int = Field(ge=1)
    max_total_tokens: int = Field(ge=1)
    active_walltime_sec: float = Field(gt=0)
    scheduler_wait_walltime_sec: float = Field(ge=0)


class TreatmentConfig(StrictModel):
    skills_enabled: bool


class ResolvedTreatment(StrictModel):
    name: Literal["no-skill", "with-skill"]
    skills_enabled: bool


class RunConfig(StrictModel):
    schema_version: Literal[1]
    run_config_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    mode: Literal["smoke", "discovery", "pilot", "formal"]
    model: ModelConfig
    api: ApiConfig
    agent_by_execution_class: dict[str, AgentBudget]
    treatments: dict[str, TreatmentConfig]

    @model_validator(mode="after")
    def exact_contract_keys(self) -> "RunConfig":
        execution = set(self.agent_by_execution_class)
        if execution != EXECUTION_CLASSES:
            raise ValueError(
                "agent_by_execution_class must contain exactly "
                f"{sorted(EXECUTION_CLASSES)}; got {sorted(execution)}"
            )
        treatments = set(self.treatments)
        expected = {"no-skill", "with-skill"}
        if treatments != expected:
            raise ValueError(
                f"treatments must contain exactly {sorted(expected)}; got {sorted(treatments)}"
            )
        if self.treatments["no-skill"].skills_enabled is not False:
            raise ValueError("no-skill must set skills_enabled=false")
        if self.treatments["with-skill"].skills_enabled is not True:
            raise ValueError("with-skill must set skills_enabled=true")
        return self

    def budget_for(self, execution_class: str) -> AgentBudget:
        try:
            return self.agent_by_execution_class[execution_class]
        except KeyError as exc:
            raise RunConfigError(f"unsupported execution class: {execution_class!r}") from exc

    def treatment_for(self, *, skills_enabled: bool) -> ResolvedTreatment:
        name = "with-skill" if skills_enabled else "no-skill"
        return ResolvedTreatment(name=name, skills_enabled=skills_enabled)

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def load_run_config(path: Path) -> RunConfig:
    """Load one YAML config and reject all ambiguity."""
    path = Path(path)
    if not path.is_file():
        raise RunConfigError(f"run config not found: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RunConfigError(f"run config unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RunConfigError("run config must be a YAML mapping")
    try:
        return RunConfig.model_validate(payload)
    except ValidationError as exc:
        raise RunConfigError(str(exc)) from exc
