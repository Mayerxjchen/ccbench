"""Tests for CCBench Experiment v2 specification, matrix expansion, and locks."""

from __future__ import annotations

from pathlib import Path
import pytest

from dftworld_bench.contracts.experiment_v2 import (
    ExperimentError,
    ExperimentSpecV2,
    build_experiment_lock,
    validate_experiment_lock,
    validate_experiment_spec,
    validate_run_lock,
)

ROOT = Path(__file__).resolve().parents[2]


def test_main_experiment_loads_and_expands_correctly():
    path = ROOT / "experiments" / "main.toml"
    assert path.is_file()
    spec = ExperimentSpecV2.from_file(path)
    assert spec.schema_version == 2
    assert spec.experiment_id == "main-v1"
    assert spec.cases == ["001", "002", "003", "004", "005"]
    assert spec.models == ["deepseek-v4-pro"]
    assert spec.skills == ["none", "hpc-submit"]
    assert spec.repeats == 3
    assert spec.budget.max_model_turns == 1024
    assert spec.budget.max_total_tokens == 100000000

    matrix = spec.expand_matrix()
    # 5 cases * 1 model * 2 skills * 3 repeats = 30 individual execution items
    assert len(matrix) == 30
    assert matrix[0] == {"case": "001", "model": "deepseek-v4-pro", "skill": "hpc-submit", "repeat": 1}
    assert matrix[-1] == {"case": "005", "model": "deepseek-v4-pro", "skill": "none", "repeat": 3}


def test_smoke_experiment_loads_and_expands():
    path = ROOT / "experiments" / "smoke.toml"
    assert path.is_file()
    spec = ExperimentSpecV2.from_file(path)
    assert spec.schema_version == 2
    assert spec.experiment_id == "smoke-v1"
    assert spec.cases == ["001"]
    assert spec.budget.max_model_turns == 64
    matrix = spec.expand_matrix()
    assert len(matrix) == 1
    assert matrix[0] == {"case": "001", "model": "deepseek-v4-pro", "skill": "none", "repeat": 1}


def test_build_experiment_lock_is_deterministic_and_valid():
    path = ROOT / "experiments" / "main.toml"
    spec = ExperimentSpecV2.from_file(path)
    commit = "a" * 40
    lock1 = build_experiment_lock(spec, commit)
    lock2 = build_experiment_lock(spec, commit)
    assert lock1["spec_digest"] == lock2["spec_digest"]
    assert lock1["total_runs"] == 30
    assert lock1["ccbench_commit"] == commit
    validate_experiment_lock(lock1)


def test_validate_run_lock_passes_valid_payload():
    valid_run_lock = {
        "schema_version": 2,
        "run_id": "run-test-123",
        "experiment_id": "main-v1",
        "case": "001-matclaw-cips-active-distillation",
        "model": "deepseek-v4-pro",
        "skill": "hpc-submit",
        "repeat": 1,
        "budget": {
            "max_model_turns": 1024,
            "max_total_tokens": 100000000,
            "agent_active_walltime_sec": 86400.0,
            "scheduler_wait_walltime_sec": 604800.0,
        },
        "model_identity": {
            "provider": "deepseek",
            "requested_model": "deepseek-v4-pro",
            "resolved_model": "deepseek-v4-pro",
            "identity_strength": "alias-only",
        },
        "candidate_digest": "sha256:" + "0" * 64,
        "compute_profile_digest": "sha256:" + "1" * 64,
        "runtime_digests": {
            "matclaw-gpu": "sha256:" + "2" * 64,
        },
        "verifier_digest": "sha256:" + "3" * 64,
        "ccbench_commit": "4" * 40,
        "created_at": "2026-09-06T16:00:00Z",
    }
    validate_run_lock(valid_run_lock)


def test_validate_run_lock_rejects_tampered_or_invalid():
    bad_commit = {
        "schema_version": 2,
        "run_id": "run-test-123",
        "experiment_id": "main-v1",
        "case": "001",
        "model": "deepseek-v4-pro",
        "skill": "none",
        "repeat": 1,
        "budget": {
            "max_model_turns": 1024,
            "max_total_tokens": 100000000,
            "agent_active_walltime_sec": 86400.0,
            "scheduler_wait_walltime_sec": 604800.0,
        },
        "model_identity": {
            "provider": "deepseek",
            "requested_model": "deepseek-v4-pro",
            "identity_strength": "alias-only",
        },
        "candidate_digest": "sha256:" + "0" * 64,
        "verifier_digest": "sha256:" + "3" * 64,
        "ccbench_commit": "short_sha",  # 非法 40 位 SHA
    }
    with pytest.raises(ExperimentError, match="does not match"):
        validate_run_lock(bad_commit)


def test_invalid_experiment_spec_rejects():
    invalid_spec = {
        "schema_version": 1,  # 必须是 2
        "experiment_id": "test",
        "cases": ["001"],
        "models": ["deepseek"],
        "skills": ["none"],
        "repeats": 0,  # 必须 >= 1
        "budget": {},
    }
    with pytest.raises(ExperimentError):
        validate_experiment_spec(invalid_spec)


def test_models_registry_loads_models_toml():
    from dftworld_bench.contracts.experiment_v2 import ModelRegistry

    path = ROOT / "experiments" / "models.toml"
    assert path.is_file()
    registry = ModelRegistry.from_file(path)
    default = registry.require("default")
    assert default.model_id == "deepseek-v4-pro"
    assert default.provider == "deepseek"

    ds = registry.require("deepseek-v4-pro")
    assert ds.provider == "deepseek"

    gpt = registry.require("gpt-4o")
    assert gpt.provider == "openai"

    claude = registry.require("claude-sonnet")
    assert claude.provider == "anthropic"

    with pytest.raises(KeyError, match="unknown model"):
        registry.require("non-existent-model")


def test_build_run_lock_v2():
    from dftworld_bench.contracts.experiment_v2 import (
        ExperimentBudget,
        ModelEntry,
        build_run_lock_v2,
    )

    budget = ExperimentBudget(
        max_model_turns=1024,
        max_total_tokens=100000000,
        agent_active_walltime_sec=86400.0,
        scheduler_wait_walltime_sec=604800.0,
    )
    model_entry = ModelEntry(
        name="claude-sonnet",
        provider="anthropic",
        model_id="claude-sonnet-4-20250514",
        identity_strength="alias",
    )
    doc = build_run_lock_v2(
        run_id="run-1",
        experiment_id="main-v1",
        case="001",
        model="claude-sonnet",
        skill="hpc-submit",
        repeat=1,
        budget=budget,
        model_entry=model_entry,
        candidate_digest="sha256:" + "0" * 64,
        verifier_digest="sha256:" + "1" * 64,
        ccbench_commit="2" * 40,
    )
    assert doc["schema_version"] == 2
    assert doc["model_identity"]["provider"] == "anthropic"
    assert doc["repeat"] == 1
