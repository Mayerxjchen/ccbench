"""Typed, immutable configuration for one benchmark campaign."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


SUPPORTED_PROVIDERS = frozenset(
    {"deepseek", "openai", "anthropic", "kimi", "longcat", "mimo", "ollama", "vllm", "sglang"}
)
EXECUTION_CLASSES = frozenset({"local_sandbox", "hpc_controller"})


class RunConfigError(ValueError):
    """The campaign configuration is absent, malformed, or unsafe."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelConfig(StrictModel):
    provider: Literal["deepseek", "openai", "anthropic", "kimi", "longcat", "mimo", "ollama", "vllm", "sglang"]
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
    """Load one YAML or TOML config and reject all ambiguity."""
    path = Path(path)
    if not path.is_file():
        raise RunConfigError(f"run config not found: {path}")
    raw_text = path.read_text(encoding="utf-8")
    if path.suffix == ".toml" or "schema_version = 2" in raw_text or "experiment_id" in raw_text:
        try:
            import tomllib
            payload = tomllib.loads(raw_text)
        except Exception:
            try:
                payload = yaml.safe_load(raw_text)
            except Exception as exc:
                raise RunConfigError(f"run config unreadable: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RunConfigError("run config must be a mapping")
        if payload.get("schema_version") == 2 or "experiment_id" in payload:
            from dftworld_bench.contracts.experiment_v2 import ExperimentSpecV2, ModelRegistry

            spec = ExperimentSpecV2.from_dict(payload)
            root = Path(__file__).resolve().parents[2]
            models_path = root / "experiments" / "models.toml"
            model_reg = ModelRegistry.from_file(models_path) if models_path.is_file() else None
            chosen_model_name = spec.models[0] if spec.models else "deepseek-v4-pro"
            if model_reg and chosen_model_name in model_reg.models:
                m_entry = model_reg.require(chosen_model_name)
                provider = m_entry.provider
                model_id = m_entry.model_id
                deployment_id = m_entry.deployment_id
                identity_strength = "alias-only" if m_entry.identity_strength == "alias-only" else "exact"
            else:
                provider = "deepseek"
                model_id = chosen_model_name
                deployment_id = "default"
                identity_strength = "alias-only"

            endpoint_env = "CCBENCH_BASE_URL" if os.getenv("CCBENCH_BASE_URL") else f"{provider.upper()}_BASE_URL"
            credential_env = "CCBENCH_API_KEY" if os.getenv("CCBENCH_API_KEY") else f"{provider.upper()}_API_KEY"
            if not os.getenv("CCBENCH_BASE_URL") and provider == "deepseek":
                endpoint_env = "DEEPSEEK_BASE_URL"
            if not os.getenv("CCBENCH_API_KEY") and provider == "deepseek":
                credential_env = "DEEPSEEK_API_KEY"
            elif not os.getenv("CCBENCH_BASE_URL") and provider == "openai":
                endpoint_env = "OPENAI_BASE_URL"
            elif not os.getenv("CCBENCH_API_KEY") and provider == "openai":
                credential_env = "OPENAI_API_KEY"
            elif not os.getenv("CCBENCH_BASE_URL") and provider == "anthropic":
                endpoint_env = "ANTHROPIC_BASE_URL"
            elif not os.getenv("CCBENCH_API_KEY") and provider == "anthropic":
                credential_env = "ANTHROPIC_API_KEY"

            adapted = {
                "schema_version": 1,
                "run_config_id": spec.experiment_id,
                "mode": "formal",
                "model": {
                    "provider": provider,
                    "model_id": model_id,
                    "deployment_id": deployment_id,
                    "identity_strength": identity_strength,
                },
                "api": {
                    "endpoint_env": endpoint_env,
                    "credential_env": credential_env,
                    "max_attempts": 4,
                    "retry_base_delay_sec": 1.0,
                    "retry_max_delay_sec": 30.0,
                    "request_timeout_sec": 180.0,
                    "input_usd_micros_per_million_tokens": 0,
                    "output_usd_micros_per_million_tokens": 0,
                },
                "agent_by_execution_class": {
                    "local_sandbox": {
                        "max_model_turns": 64,
                        "max_total_tokens": 10000000,
                        "active_walltime_sec": 7200.0,
                        "scheduler_wait_walltime_sec": 0.0,
                    },
                    "hpc_controller": {
                        "max_model_turns": spec.budget.max_model_turns,
                        "max_total_tokens": spec.budget.max_total_tokens,
                        "active_walltime_sec": spec.budget.agent_active_walltime_sec,
                        "scheduler_wait_walltime_sec": spec.budget.scheduler_wait_walltime_sec,
                    },
                },
                "treatments": {
                    "no-skill": {"skills_enabled": False},
                    "with-skill": {"skills_enabled": True},
                },
            }
            return RunConfig.model_validate(adapted)
    else:
        try:
            payload = yaml.safe_load(raw_text)
        except (OSError, yaml.YAMLError) as exc:
            raise RunConfigError(f"run config unreadable: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RunConfigError("run config must be a YAML mapping")
    try:
        return RunConfig.model_validate(payload)
    except ValidationError as exc:
        raise RunConfigError(str(exc)) from exc
