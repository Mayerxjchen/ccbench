"""Tests for the experiment resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from dftworld_bench.config.profiles import ProfileRegistry
from dftworld_bench.config.resolver import (
    FrozenExperiment,
    construct_experiment,
    resolve_formal,
)
from dftworld_bench.contracts.case import CaseSpec
from dftworld_bench.contracts.resolved_lock import FrozenExperimentOverrideError


@pytest.fixture
def registry():
    """A test profile registry."""
    return ProfileRegistry.from_mapping({
        "agents": {
            "formal-long": {
                "max_model_turns": 512,
                "max_total_tokens": 100000000,
                "agent_active_walltime_sec": 86400,
                "scheduler_wait_walltime_sec": 604800,
            },
            "local-standard": {
                "max_model_turns": 64,
                "max_total_tokens": 10000000,
                "agent_active_walltime_sec": 7200,
                "scheduler_wait_walltime_sec": 0,
            },
        },
        "api": {
            "default": {
                "endpoint_env": "DFTWORLD_API_ENDPOINT",
                "credential_env": "DFTWORLD_API_KEY",
                "max_retries": 3,
            },
        },
        "experiments": {
            "default": {
                "max_concurrent_runs": 4,
                "default_replicates": 1,
            },
        },
        "runtimes": {
            "local-sandbox": {
                "image": "dftworld-base:latest",
                "qualification": "local-smoke",
            },
        },
        "sites": {
            "<site-alias>": {
                "type": "hpc",
                "scheduler": "slurm",
                "capabilities": ["batch_jobs", "gpu"],
            },
        },
    })


@pytest.fixture
def case():
    """A test case spec."""
    return CaseSpec(
        case_id="test/case",
        case_version="1.0",
        schema_version="1.2",
        execution_class="local_sandbox",
        instruction_path="instruction.md",
        public_files=(),
        submission_root=".",
        candidate_image=None,
        agent_timeout_sec=600.0,
        verifier_timeout_sec=600.0,
        verifier_env={},
        candidate_resources={},
        legacy_execution_value=None,
        legacy_submission_layout=True,
    )


def test_construct_experiment(registry):
    """construct_experiment() must create a FrozenExperiment."""
    selection = {"agent": "formal-long", "api": "default"}
    experiment = construct_experiment(selection, registry)
    assert isinstance(experiment, FrozenExperiment)
    assert experiment.template_name == "formal-long-default"
    assert experiment.agent_profile["max_model_turns"] == 512


def test_construct_experiment_with_site(registry):
    """construct_experiment() must include site profile when specified."""
    selection = {"agent": "formal-long", "api": "default", "site": "<site-alias>"}
    experiment = construct_experiment(selection, registry)
    assert experiment.site_profile is not None
    assert experiment.site_profile["scheduler"] == "slurm"


def test_resolve_formal_rejects_overrides(registry, case):
    """resolve_formal() must reject overrides for formal runs."""
    experiment = construct_experiment({"agent": "formal-long", "api": "default"}, registry)
    with pytest.raises(FrozenExperimentOverrideError, match="model"):
        resolve_formal(
            experiment, case, "run-1", 1,
            model="openai/gpt-4o", benchmark_commit="abc123",
            instruction="test instruction",
            overrides={"model": "x"},
        )


def test_resolve_formal_creates_lock(registry, case):
    """resolve_formal() must create a valid ResolvedRunLock."""
    experiment = construct_experiment({"agent": "formal-long", "api": "default"}, registry)
    lock = resolve_formal(
        experiment, case, "run-1", 1,
        model="deepseek/deepseek-chat", benchmark_commit="abc123def456",
        instruction="solve the problem",
        max_turns=32,
    )
    assert lock.payload["case"]["case_id"] == "test/case"
    assert lock.payload["experiment"]["replicate"] == 1
    assert lock.payload["budgets"]["max_model_turns"] == 512
    assert lock.payload["infra"]["commit"] == "abc123def456"
    assert lock.payload["agent"]["provider"] == "deepseek"
    assert lock.payload["agent"]["model_id"] == "deepseek-chat"
    assert lock.verify() is True


def test_resolve_formal_no_placeholders(registry, case):
    """resolve_formal() must never write sha256:none — every digest is real."""
    experiment = construct_experiment({"agent": "formal-long", "api": "default"}, registry)
    lock = resolve_formal(
        experiment, case, "run-1", 1,
        model="deepseek/deepseek-chat", benchmark_commit="abc123",
        instruction="test instruction",
    )
    agent = lock.payload["agent"]
    verifier = lock.payload["verifier"]
    for key in ("prompt_digest", "sampling_digest", "context_digest",
                "skill_bundle_digest", "tool_surface_digest"):
        assert agent[key] != "sha256:none", f"placeholder found in agent.{key}"
        assert agent[key].startswith("sha256:"), f"invalid digest format: agent.{key}"
    for key in ("runtime_digest", "isolation_config_digest"):
        assert verifier[key] != "sha256:none", f"placeholder found in verifier.{key}"
        assert verifier[key].startswith("sha256:"), f"invalid digest format: verifier.{key}"


def test_resolve_formal_with_hpc(registry, case):
    """resolve_formal() must include HPC block when site is specified."""
    experiment = construct_experiment(
        {"agent": "formal-long", "api": "default", "site": "<site-alias>"},
        registry,
    )
    lock = resolve_formal(
        experiment, case, "run-1", 1,
        model="deepseek/deepseek-chat", benchmark_commit="abc123",
        instruction="test",
    )
    assert "hpc" in lock.payload
    assert lock.payload["hpc"]["scheduler"] == "slurm"
