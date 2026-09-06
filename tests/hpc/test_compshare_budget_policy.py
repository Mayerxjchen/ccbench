"""Focused contract tests for the normalized CompShare budget policy."""

from __future__ import annotations

import pytest

from ccbench.hpc.site_profile import (
    CompShareBudgetPolicy,
    HpcSiteProfile,
    SiteProfileError,
)


def _profile(*, scheduler: str = "compshare", budget: dict | None = None) -> dict:
    payload = {
        "schema_version": 1,
        "site_id": "budget-site",
        "scheduler": scheduler,
        "connection": {
            "credential_profile_id": "offline-key",
            "target_binding": "offline://site",
            "remote_user": "offline",
            "remote_root_policy": "/workspace/{run_id}",
        },
        "account": "offline-account",
        "queues": {
            "gpu": {
                "partition": "gpu",
                "qos": "normal",
                "max_cpus": 16,
                "max_memory_gb": 64,
                "max_gpus": 1,
                "max_walltime_minutes": 120,
            }
        },
        "runtime_policy": {"requires_apptainer": False},
    }
    if scheduler == "compshare":
        payload["runtime_policy"]["budget_policy"] = budget or {
            "max_budget_cny": 50.0,
            "max_instance_hours": 4.0,
            "max_instances": 1,
        }
    return payload


def test_compshare_budget_is_normalized_and_read_only():
    profile = HpcSiteProfile.from_dict(_profile())
    policy = profile.compshare_budget_policy

    assert isinstance(policy, CompShareBudgetPolicy)
    assert policy.max_budget_cny == 50.0
    assert policy.max_instance_hours == 4.0
    assert policy.max_instances == 1
    assert policy.managed_account_scope_id is None
    assert policy.to_dict() == {
        "max_budget_cny": 50.0,
        "max_instance_hours": 4.0,
        "max_instances": 1,
    }
    with pytest.raises(Exception):
        policy.max_instances = 2


@pytest.mark.parametrize(
    "budget",
    [
        {"max_budget_cny": 50.0, "max_instance_hours": 4.0},
        {
            "max_budget_cny": 50.0,
            "max_instance_hours": 4.0,
            "max_instances": 1,
            "unknown": True,
        },
        {
            "max_budget_cny": 50.0,
            "max_instance_hours": 4.0,
            "max_instances": 2,
        },
        {
            "max_budget_cny": 50.0,
            "max_instance_hours": 4.0,
            "max_instances": 1,
            "failover_standby": {"region": "other-region"},
        },
    ],
)
def test_formal_compshare_budget_is_strict(budget: dict):
    with pytest.raises(SiteProfileError):
        HpcSiteProfile.from_dict(_profile(budget=budget))


def test_missing_compshare_budget_is_rejected():
    payload = _profile()
    payload["runtime_policy"].pop("budget_policy")
    with pytest.raises(SiteProfileError):
        HpcSiteProfile.from_dict(payload)


def test_slurm_profile_remains_compatible_without_provider_budget():
    profile = HpcSiteProfile.from_dict(_profile(scheduler="slurm"))
    assert profile.budget_policy is None
    with pytest.raises(SiteProfileError):
        _ = profile.compshare_budget_policy


def test_budget_policy_changes_site_digest():
    first = HpcSiteProfile.from_dict(_profile())
    changed = _profile()
    changed["runtime_policy"]["budget_policy"]["managed_account_scope_id"] = "scope-a"
    second = HpcSiteProfile.from_dict(changed)
    assert first.digest != second.digest
    assert second.compshare_budget_policy.managed_account_scope_id == "scope-a"
