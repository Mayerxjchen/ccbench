from __future__ import annotations

import pytest

import eval as eval_mod


def test_old_ws_command_resolves_hpc_long_budget() -> None:
    args = eval_mod.parse_args(["034-ai2kit-water64-end-to-end-potential", "--skills"])
    settings = eval_mod.resolve_task_run_settings(args, "hpc_controller")
    assert settings.model == "deepseek/deepseek-v4-pro"
    assert settings.max_turns == 1024
    assert settings.condition_id == "with-skill"
    assert settings.counted is True


def test_old_ns_command_resolves_local_budget() -> None:
    args = eval_mod.parse_args(["001-hello"])
    settings = eval_mod.resolve_task_run_settings(args, "local_sandbox")
    assert settings.max_turns == 64
    assert settings.condition_id == "no-skill"


def test_counted_run_rejects_turn_override() -> None:
    args = eval_mod.parse_args(["034-ai2kit-water64-end-to-end-potential", "--max-turns", "8"])
    with pytest.raises(SystemExit, match="uncounted-smoke"):
        eval_mod.resolve_task_run_settings(args, "hpc_controller")


def test_uncounted_smoke_override_is_visible() -> None:
    args = eval_mod.parse_args([
        "034-ai2kit-water64-end-to-end-potential",
        "--max-turns", "8", "--model", "deepseek/debug", "--uncounted-smoke",
    ])
    settings = eval_mod.resolve_task_run_settings(args, "hpc_controller")
    assert settings.max_turns == 8
    assert settings.model == "deepseek/debug"
    assert settings.counted is False
    assert settings.frozen is False
    assert settings.override_present is True


def test_condition_must_match_skills_flag() -> None:
    args = eval_mod.parse_args(["001-hello", "--skills", "--condition", "no-skill"])
    with pytest.raises(SystemExit, match="conflicts"):
        eval_mod.resolve_task_run_settings(args, "local_sandbox")


def test_experiment_spec_and_run_lock_v2_generation(tmp_path: Path) -> None:
    from pathlib import Path
    from bench.contracts.experiment_v2 import (
        ExperimentBudget,
        ModelEntry,
        build_run_lock_v2,
        validate_run_lock,
    )

    budget = ExperimentBudget(
        max_model_turns=1024,
        max_total_tokens=100000000,
        agent_active_walltime_sec=86400.0,
        scheduler_wait_walltime_sec=604800.0,
    )
    model_entry = ModelEntry(
        name="deepseek-v4-pro",
        provider="deepseek",
        model_id="deepseek-v4-pro",
        identity_strength="alias-only",
    )
    lock = build_run_lock_v2(
        run_id="run-test",
        experiment_id="main-v1",
        case="001-matclaw-cips-active-distillation",
        model="deepseek-v4-pro",
        skill="with-skill",
        repeat=1,
        budget=budget,
        model_entry=model_entry,
        candidate_digest="sha256:" + "b" * 64,
        verifier_digest="sha256:" + "1" * 64,
        bench_commit="a" * 40,
    )
    validate_run_lock(lock)
    assert lock["schema_version"] == 2
    assert lock["experiment_id"] == "main-v1"
