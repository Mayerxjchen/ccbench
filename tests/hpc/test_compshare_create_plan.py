"""Offline command-plan and managed create contracts for CompShare."""

from __future__ import annotations

from pathlib import Path

import pytest

from dftworld_bench.hpc.drivers.compshare import (
    BudgetConfig,
    CompShareCli,
    CompShareCliError,
    CompShareManagerError,
    FakeCompShareCliRunner,
    InstanceCreateSpec,
    RunScopedInstanceManager,
    build_instance_create_argv,
    build_instance_create_plan,
    make_ownership_marker,
)


def _spec(**overrides) -> InstanceCreateSpec:
    name, remark = make_ownership_marker("offline-plan-run")
    values = {
        "image": "img-real-canonical-001",
        "name": name,
        "remark": remark,
    }
    values.update(overrides)
    return InstanceCreateSpec(**values)


def test_offline_plan_never_invokes_runner_or_reads_credentials(tmp_path: Path):
    calls: list[tuple] = []

    def fail_runner(argv, env):
        calls.append((tuple(argv), env))
        raise AssertionError("offline plan must not invoke the provider runner")

    cli = CompShareCli(
        runner=fail_runner,
        env={"COMPSHARE_PRIVATE_KEY": "must-not-be-read"},
    )
    plan = cli.plan_instance_create(_spec())

    assert calls == []
    assert plan.argv == build_instance_create_argv(_spec())
    assert plan.network_required is True
    assert plan.mutates_provider is True
    assert "must-not-be-read" not in plan.argv
    assert all("PRIVATE_KEY" not in item for item in plan.argv)


def test_spec_aliases_normalize_to_same_argv():
    canonical = _spec()
    alias = InstanceCreateSpec(
        image_id=canonical.image,
        gpu_type="4090",
        name=canonical.name,
        remark=canonical.remark,
    )
    assert alias.image == canonical.image
    assert alias.gpu == canonical.gpu
    assert build_instance_create_argv(alias) == build_instance_create_argv(canonical)


def test_provider_dry_run_is_online_and_legacy_alias_is_compatible():
    runner = FakeCompShareCliRunner(initial_stock=1)
    cli = CompShareCli(runner=runner)

    plan = build_instance_create_plan(_spec(provider_dry_run=True))
    assert plan.provider_dry_run is True
    assert "--dry-run" in plan.argv

    result = cli.instance_create(
        image="img-real-canonical-002",
        name=make_ownership_marker("provider-dry-run")[0],
        remark=make_ownership_marker("provider-dry-run")[1],
        provider_dry_run=True,
    )
    assert result["dry_run"] is True

    # Existing callers may use the old spelling; it maps to the same online
    # provider operation and does not turn into the offline plan API.
    result = cli.instance_create(
        image="img-real-canonical-003",
        name=make_ownership_marker("legacy-dry-run")[0],
        remark=make_ownership_marker("legacy-dry-run")[1],
        dry_run=True,
    )
    assert result["dry_run"] is True


def test_online_create_rejects_missing_canonical_marker_without_runner():
    calls: list[tuple] = []

    def fail_runner(argv, env):
        calls.append((argv, env))
        raise AssertionError("unsafe create must be rejected before runner")

    cli = CompShareCli(runner=fail_runner)
    with pytest.raises(CompShareCliError, match="canonical"):
        cli.instance_create(image="img-real-no-marker")
    assert calls == []

@pytest.mark.parametrize(
    "overrides",
    [
        {"count": 0},
        {"count": 2},
        {"image": "img-deepmd-placeholder"},
        {"name": None, "remark": None},
        {"name": "mlffbench-deadbeefdeadbeef", "remark": None},
    ],
)
def test_create_plan_rejects_unsafe_inputs(overrides):
    with pytest.raises(CompShareCliError):
        _spec(**overrides)


def test_manager_preflight_rejects_existing_active_managed_instance(tmp_path: Path):
    runner = FakeCompShareCliRunner(initial_stock=2)
    cli = CompShareCli(runner=runner)
    existing_name, existing_remark = make_ownership_marker("existing-run")
    existing = cli.instance_create(
        image="img-existing",
        name=existing_name,
        remark=existing_remark,
    )

    manager = RunScopedInstanceManager(
        cli,
        budget=BudgetConfig(max_instances=1),
        ledger_path=tmp_path / "ledger.jsonl",
    )
    with pytest.raises(CompShareManagerError) as _unused:
        # Local plan validation happens before the account list call; this
        # image is valid and reaches the max_instances guard below.
        manager.get_or_create_instance(
            "new-run", "img-new", operation_id="op-1"
        )
    assert "max_instances" in str(_unused.value)
    assert existing["instance_id"] in runner.instances


def test_manager_rejects_non_singleton_budget_before_provider_call(tmp_path: Path):
    with pytest.raises(Exception, match="max_instances=1"):
        RunScopedInstanceManager(
            CompShareCli(runner=FakeCompShareCliRunner()),
            budget=BudgetConfig(max_instances=2),
            ledger_path=tmp_path / "ledger.jsonl",
        )
