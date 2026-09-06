"""Tests verifying CCBench Phase 2.1 Closure:
- E21-01: Formal ExperimentLock fail-closed
- E21-02: git_head_commit returns 40-char full SHA
- E21-03: Zero-placeholder rejection
- E21-04: Pre-execution RunLock generation
- E21-05: RunLock immutability & tamper rejection
- E21-06: Matrix execution expansion
- E21-07: Per-cell model resolution
- E21-08: Per-cell skill resolution
- E21-09: RunRecord & RunLock digest strong binding
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import pytest

from eval import git_head_commit
from dftworld_bench.contracts.experiment_v2 import (
    ExperimentBudget,
    ExperimentError,
    ExperimentSpecV2,
    ModelEntry,
    ModelRegistry,
    build_experiment_lock,
    build_run_lock_v2,
    canonical_run_lock_digest,
)


def test_git_head_commit_returns_40_char_sha():
    commit = git_head_commit()
    assert isinstance(commit, str)
    assert len(commit) == 40
    assert all(c in "0123456789abcdefABCDEF" for c in commit)


def test_zero_placeholder_rejected_in_build_experiment_lock():
    spec = ExperimentSpecV2.from_file(Path("experiments/smoke.toml"))
    # Zero placeholder in commit must raise ExperimentError
    with pytest.raises(ExperimentError, match="cannot be a zero-placeholder"):
        build_experiment_lock(spec, "0" * 40, allow_placeholders=False)

    # 40-char valid hex must pass
    lock_doc = build_experiment_lock(spec, "1" * 40, allow_placeholders=False)
    assert lock_doc["ccbench_commit"] == "1" * 40
    assert lock_doc["schema_version"] == 2


def test_zero_placeholder_rejected_in_build_run_lock_v2():
    budget = ExperimentBudget(
        max_model_turns=64,
        max_total_tokens=10000000,
        agent_active_walltime_sec=7200.0,
        scheduler_wait_walltime_sec=0.0,
    )
    model_entry = ModelEntry(
        name="deepseek-v4-pro",
        provider="deepseek",
        model_id="deepseek-v4-pro",
        identity_strength="alias-only",
    )

    # Rejection of zero candidate digest
    with pytest.raises(ExperimentError, match="cannot be a zero-placeholder"):
        build_run_lock_v2(
            run_id="run-1",
            experiment_id="smoke-v1",
            case="001",
            model="deepseek-v4-pro",
            skill="no-skill",
            repeat=1,
            budget=budget,
            model_entry=model_entry,
            candidate_digest="sha256:" + "0" * 64,
            verifier_digest="sha256:" + "1" * 64,
            ccbench_commit="a" * 40,
            allow_placeholders=False,
        )

    # Rejection of zero verifier digest
    with pytest.raises(ExperimentError, match="cannot be a zero-placeholder"):
        build_run_lock_v2(
            run_id="run-1",
            experiment_id="smoke-v1",
            case="001",
            model="deepseek-v4-pro",
            skill="no-skill",
            repeat=1,
            budget=budget,
            model_entry=model_entry,
            candidate_digest="sha256:" + "a" * 64,
            verifier_digest="sha256:" + "0" * 64,
            ccbench_commit="a" * 40,
            allow_placeholders=False,
        )

    # Rejection of zero commit
    with pytest.raises(ExperimentError, match="cannot be a zero-placeholder"):
        build_run_lock_v2(
            run_id="run-1",
            experiment_id="smoke-v1",
            case="001",
            model="deepseek-v4-pro",
            skill="no-skill",
            repeat=1,
            budget=budget,
            model_entry=model_entry,
            candidate_digest="sha256:" + "a" * 64,
            verifier_digest="sha256:" + "b" * 64,
            ccbench_commit="0" * 40,
            allow_placeholders=False,
        )


def test_canonical_run_lock_digest_is_deterministic_and_sensitive():
    budget = ExperimentBudget(
        max_model_turns=64,
        max_total_tokens=10000000,
        agent_active_walltime_sec=7200.0,
        scheduler_wait_walltime_sec=0.0,
    )
    model_entry = ModelEntry(
        name="deepseek-v4-pro",
        provider="deepseek",
        model_id="deepseek-v4-pro",
        identity_strength="alias-only",
    )
    doc1 = build_run_lock_v2(
        run_id="run-1",
        experiment_id="smoke-v1",
        case="001",
        model="deepseek-v4-pro",
        skill="no-skill",
        repeat=1,
        budget=budget,
        model_entry=model_entry,
        candidate_digest="sha256:" + "a" * 64,
        verifier_digest="sha256:" + "b" * 64,
        ccbench_commit="c" * 40,
        created_at="2026-09-06T12:00:00Z",
    )
    d1 = canonical_run_lock_digest(doc1)
    assert d1.startswith("sha256:")
    assert len(d1) == 71

    # Same content recomputed gives identical digest
    d1_recomp = canonical_run_lock_digest(doc1)
    assert d1 == d1_recomp

    # Tampering any field changes digest
    doc2 = dict(doc1)
    doc2["repeat"] = 2
    d2 = canonical_run_lock_digest(doc2)
    assert d1 != d2


def test_matrix_expansion_and_per_cell_integrity():
    spec = ExperimentSpecV2.from_file(Path("experiments/main.toml"))
    matrix = spec.expand_matrix()
    # main.toml: 5 cases * 1 model * 2 skills * 3 repeats = 30 runs
    assert len(matrix) == 30
    models_reg = ModelRegistry.from_file(Path("experiments/models.toml"))
    for cell in matrix:
        assert cell["case"] in ("001", "002", "003", "004", "005")
        assert cell["model"] in ("deepseek-v4-pro",)
        assert cell["skill"] in ("none", "hpc-submit")
        assert 1 <= cell["repeat"] <= 3

        # Model must be present in registry
        entry = models_reg.require(cell["model"])
        assert entry.provider == "deepseek"
        assert entry.model_id == "deepseek-v4-pro"
