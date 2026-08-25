"""Treatment-only lock comparator tests."""

from __future__ import annotations

import copy

import pytest

from dftworld_bench.experiments.comparison import TREATMENTS, compare_lock


@pytest.fixture
def base_lock() -> dict:
    """A fully-frozen resolved run lock payload (no-skill arm)."""
    return {
        "case": {
            "case_id": "032-matclaw-cips-curie-temperature",
            "case_version": "1.0",
            "schema_version": "1.2",
        },
        "experiment": {
            "template_name": "formal-long-default",
            "condition_id": "no-skill",
            "replicate": 1,
        },
        "agent": {
            "provider": "openai",
            "model_id": "gpt-4o",
            "deployment_id": "default",
            "provider_model_version": "2024-08-06",
            "identity_strength": "alias",
            "engine": "pagent",
            "prompt_digest": "sha256:abc",
            "sampling_digest": "sha256:def",
            "context_digest": "sha256:ghi",
            "skill_bundle_digest": "sha256:none",
            "tool_surface_digest": "sha256:jkl",
        },
        "api": {
            "api_profile_digest": "sha256:mno",
            "endpoint_env": "DFTWORLD_API_ENDPOINT",
            "credential_env": "DFTWORLD_API_KEY",
        },
        "candidate_runtime": {
            "image": "matclaw-cips-2.2.11-gpu-amd64",
            "runtime_digest": "sha256:pqr",
            "qualification_status": "passed",
        },
        "hpc": {
            "site_profile_digest": "sha256:stu",
            "scheduler": "slurm",
            "capabilities": ["batch_jobs", "gpu"],
        },
        "verifier": {
            "runtime_digest": "sha256:vwx",
            "isolation_config_digest": "sha256:yza",
        },
        "infra": {
            "version": "2.0.0",
            "commit": "aae1bec",
        },
        "budgets": {
            "max_model_turns": 512,
            "max_total_tokens": 100000000,
            "agent_active_walltime_sec": 86400,
            "scheduler_wait_walltime_sec": 604800,
        },
    }


@pytest.fixture
def ns_lock(base_lock) -> dict:
    """No-skill arm lock: condition no-skill, no skill bundle."""
    return base_lock


@pytest.fixture
def ws_lock(base_lock) -> dict:
    """With-skill arm lock: only condition_id and skill_bundle_digest differ."""
    lock = copy.deepcopy(base_lock)
    lock["experiment"]["condition_id"] = "with-skill"
    lock["agent"]["skill_bundle_digest"] = "sha256:skills-bundle-123"
    return lock


def mutate_path(lock: dict, path: str) -> None:
    """Mutate a dotted path inside a lock payload (in place)."""
    parts = path.split(".")
    target = lock
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = "MUTATED"


def test_skill_ablation_allows_only_skill_fields(ns_lock, ws_lock):
    """A valid skill-ablation pair differs only in the declared treatment."""
    diff = compare_lock(ns_lock, ws_lock, "skill_availability")
    assert diff.valid
    assert set(diff.allowed_differences) == {
        "experiment.condition_id",
        "agent.skill_bundle_digest",
    }


@pytest.mark.parametrize("path", [
    "agent.model_id",
    "agent.tool_surface_digest",
    "api.api_profile_digest",
    "budgets.max_model_turns",
    "hpc.site_profile_digest",
    "verifier.runtime_digest",
])
def test_skill_ablation_rejects_confounds(ns_lock, ws_lock, path):
    """Any non-treatment difference must be flagged as unexpected."""
    mutate_path(ws_lock, path)
    diff = compare_lock(ns_lock, ws_lock, "skill_availability")
    assert path in diff.unexpected_differences


def test_model_identity_treatment_allows_model_fields(ns_lock, base_lock):
    """Model-identity treatment allows provider/model fields to differ."""
    other = copy.deepcopy(base_lock)
    other["agent"]["provider"] = "anthropic"
    other["agent"]["model_id"] = "claude-sonnet-4"
    other["agent"]["provider_model_version"] = "2025-05-14"
    other["agent"]["identity_strength"] = "exact"
    other["experiment"]["condition_id"] = "other-condition"
    diff = compare_lock(base_lock, other, "model_identity")
    assert "agent.provider" in diff.allowed_differences
    assert "agent.model_id" in diff.allowed_differences


def test_unknown_treatment_raises(ns_lock, ws_lock):
    """Unknown treatment names must be rejected."""
    with pytest.raises(ValueError, match="unknown treatment"):
        compare_lock(ns_lock, ws_lock, "nonexistent_treatment")


def test_identical_locks_have_no_differences(ns_lock):
    """Identical locks have no differences at all."""
    diff = compare_lock(ns_lock, ns_lock, "skill_availability")
    assert diff.valid
    assert diff.allowed_differences == ()
    assert diff.unexpected_differences == ()


def test_treatment_matrix_contains_declared_treatments():
    """The treatment matrix must expose the declared treatments."""
    assert "skill_availability" in TREATMENTS
    assert "model_identity" in TREATMENTS
    assert "experiment.condition_id" in TREATMENTS["skill_availability"]
    assert "agent.skill_bundle_digest" in TREATMENTS["skill_availability"]
