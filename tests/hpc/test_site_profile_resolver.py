"""SiteProfile resource resolution and ACL pre-check.

Covers: ACL removal, account replacement, CPU-unavailable mapping,
GPU-available mapping, and NS/WS identity equality.
"""

from __future__ import annotations

import pytest

from ccbench.hpc.site_profile import (
    HpcSiteProfile,
    ResolvedResource,
    SiteProfileBlockedError,
    SiteProfileError,
)


# -- fixtures -----------------------------------------------------------------

def _base_payload(**overrides) -> dict:
    """Minimal valid site profile payload."""
    base = {
        "schema_version": 1,
        "site_id": "test-site",
        "scheduler": "slurm",
        "connection": {
            "credential_profile_id": "test-key",
            "target_binding": "ssh://login:22",
            "remote_user": "testuser",
            "remote_root_policy": "/data/{run_id}",
        },
        "account": "test-account",
        "queues": {
            "gpu": {
                "partition": "gpu",
                "qos": "normal",
                "max_cpus": 64,
                "max_memory_gb": 256,
                "max_gpus": 1,
                "max_walltime_minutes": 1440,
            },
        },
        "runtime_policy": {
            "requires_apptainer": True,
            "runtime_store": "/data/runtimes",
        },
    }
    base.update(overrides)
    return base


def _profile(**overrides) -> HpcSiteProfile:
    return HpcSiteProfile.from_dict(_base_payload(**overrides))


def _ssh_ok(allowed: str, *, user_account: str = ""):
    """Mock ssh_fn that returns scontrol output with the given AllowAccounts.

    ``user_account`` is the account returned by the sacctmgr association
    query.  Defaults to the first account in the allowlist.
    """
    assoc_account = user_account or allowed.split(",")[0].strip()

    def ssh_fn(command: str) -> str:
        if "scontrol" in command:
            return (
                f"PartitionName=gpu\n"
                f"   AllowGroups=ALL AllowAccounts={allowed} "
                f"AllowQos=normal,long\n"
            )
        # sacctmgr association query
        return f"ce_cluster|{assoc_account}||normal,long|\n"
    return ssh_fn


def _ssh_real_site(account: str):
    """Mock ssh_fn returning real the site output format."""
    def ssh_fn(command: str) -> str:
        if "scontrol" in command:
            return (
                "PartitionName=gpu\n"
                "   AllowGroups=ALL AllowAccounts=acct-alpha,acct-all,"
                "acct-gamma,dp-site,acct-delta AllowQos=normal,long\n"
            )
        return f"ce_cluster|{account}||normal,long|\n"
    return ssh_fn


def _ssh_no_acl():
    """Mock ssh_fn returning partition config without AllowAccounts field."""
    def ssh_fn(command: str) -> str:
        if "scontrol" in command:
            return "PartitionName=gpu\n   State=UP\n"
        return ""
    return ssh_fn


def _ssh_fail():
    """Mock ssh_fn that raises on any command."""
    def ssh_fn(command: str) -> str:
        raise ConnectionError("SSH timeout")
    return ssh_fn


# -- resolve_workload ---------------------------------------------------------

class TestResolveWorkload:
    def test_identity_mapping_when_no_resource_mapping(self):
        """Without resource_mapping, workload type IS the queue name."""
        profile = _profile(queues={
            "gpu": {
                "partition": "gpu", "qos": "normal",
                "max_cpus": 64, "max_memory_gb": 256,
                "max_gpus": 1, "max_walltime_minutes": 1440,
            },
        })
        resolved = profile.resolve_workload("gpu")
        assert resolved.partition == "gpu"
        assert resolved.queue_name == "gpu"
        assert resolved.max_gpus == 1
        assert resolved.gres == "gpu:1"

    def test_cpu_maps_to_gpu_queue(self):
        """CPU workload maps to gpu queue when resource_mapping says so."""
        profile = _profile(
            queues={
                "gpu": {
                    "partition": "gpu", "qos": "normal",
                    "max_cpus": 64, "max_memory_gb": 256,
                    "max_gpus": 1, "max_walltime_minutes": 1440,
                },
            },
            resource_mapping={
                "cpu": {"queue": "gpu", "note": "no cpu partition access"},
                "gpu": {"queue": "gpu"},
            },
        )
        resolved = profile.resolve_workload("cpu")
        assert resolved.partition == "gpu"
        assert resolved.queue_name == "gpu"
        assert resolved.workload_type == "cpu"
        assert resolved.mapping_note == "no cpu partition access"
        assert resolved.max_gpus == 1
        assert resolved.gres == "gpu:1"

    def test_unknown_workload_type_raises(self):
        profile = _profile()
        with pytest.raises(SiteProfileError, match="not in profile queues"):
            profile.resolve_workload("fpga")

    def test_mapped_queue_not_in_queues_raises(self):
        """Mapping to a valid queue name that isn't in the queues dict."""
        profile = _profile(
            queues={
                "gpu": {
                    "partition": "gpu", "qos": "normal",
                    "max_cpus": 64, "max_memory_gb": 256,
                    "max_gpus": 1, "max_walltime_minutes": 1440,
                },
            },
            resource_mapping={"cpu": {"queue": "cpu"}},
        )
        with pytest.raises(SiteProfileError, match="not in profile queues"):
            profile.resolve_workload("cpu")

    def test_zero_gpu_queue_has_no_gres(self):
        profile = _profile(queues={
            "cpu": {
                "partition": "cpu", "qos": "normal",
                "max_cpus": 128, "max_memory_gb": 512,
                "max_walltime_minutes": 2880,
            },
        })
        resolved = profile.resolve_workload("cpu")
        assert resolved.max_gpus == 0
        assert resolved.gres is None


# -- check_acl ----------------------------------------------------------------

class TestCheckACL:
    def test_account_in_allowlist_passes(self):
        resolved = ResolvedResource(
            account="my-account", partition="gpu", qos="normal",
            max_cpus=64, max_memory_gb=256, max_walltime_minutes=1440,
            max_gpus=1, workload_type="gpu", queue_name="gpu",
        )
        # Should not raise.
        HpcSiteProfile.check_acl(
            resolved,
            ssh_fn=_ssh_ok("other,my-account,another",
                           user_account="my-account"),
        )

    def test_account_not_in_allowlist_raises_blocked(self):
        resolved = ResolvedResource(
            account="blocked-account", partition="gpu", qos="normal",
            max_cpus=64, max_memory_gb=256, max_walltime_minutes=1440,
            max_gpus=1, workload_type="gpu", queue_name="gpu",
        )
        with pytest.raises(SiteProfileBlockedError) as exc_info:
            HpcSiteProfile.check_acl(
                resolved,
                ssh_fn=_ssh_ok("other,another",
                               user_account="blocked-account"),
            )
        assert exc_info.value.partition == "gpu"
        assert exc_info.value.account == "blocked-account"
        assert "BLOCKED_SITE_ACL" in str(exc_info.value)

    def test_real_site_output_format(self):
        """Real the site output: AllowGroups=ALL AllowAccounts=... on same line."""
        resolved = ResolvedResource(
            account="acct-alpha", partition="gpu", qos="normal",
            max_cpus=64, max_memory_gb=256, max_walltime_minutes=1440,
            max_gpus=1, workload_type="gpu", queue_name="gpu",
        )
        HpcSiteProfile.check_acl(
            resolved, ssh_fn=_ssh_real_site("acct-alpha")
        )

    def test_real_site_blocked_account(self):
        """acct-blocked is NOT in the real AllowAccounts list."""
        resolved = ResolvedResource(
            account="acct-blocked", partition="gpu", qos="normal",
            max_cpus=64, max_memory_gb=256, max_walltime_minutes=1440,
            max_gpus=1, workload_type="gpu", queue_name="gpu",
        )
        with pytest.raises(SiteProfileBlockedError, match="acct-blocked"):
            HpcSiteProfile.check_acl(
                resolved, ssh_fn=_ssh_real_site("acct-blocked")
            )

    def test_allow_accounts_all_means_unrestricted(self):
        """AllowAccounts=ALL grants access to any account."""
        def ssh_fn(command: str) -> str:
            if "scontrol" in command:
                return "PartitionName=gpu\n   AllowGroups=ALL AllowAccounts=ALL\n"
            return "ce_cluster|any-account||normal|\n"
        resolved = ResolvedResource(
            account="any-account", partition="gpu", qos="normal",
            max_cpus=64, max_memory_gb=256, max_walltime_minutes=1440,
            max_gpus=1, workload_type="gpu", queue_name="gpu",
        )
        HpcSiteProfile.check_acl(resolved, ssh_fn=ssh_fn)

    def test_missing_allow_accounts_fails_closed(self):
        """No AllowAccounts field → fail-closed, not unrestricted."""
        resolved = ResolvedResource(
            account="any-account", partition="gpu", qos="normal",
            max_cpus=64, max_memory_gb=256, max_walltime_minutes=1440,
            max_gpus=1, workload_type="gpu", queue_name="gpu",
        )
        with pytest.raises(SiteProfileBlockedError, match="missing"):
            HpcSiteProfile.check_acl(resolved, ssh_fn=_ssh_no_acl())

    def test_ssh_failure_raises_site_profile_error(self):
        resolved = ResolvedResource(
            account="x", partition="gpu", qos="normal",
            max_cpus=64, max_memory_gb=256, max_walltime_minutes=1440,
            max_gpus=1, workload_type="gpu", queue_name="gpu",
        )
        with pytest.raises(SiteProfileError, match="ACL check failed"):
            HpcSiteProfile.check_acl(resolved, ssh_fn=_ssh_fail())


# -- resource mapping + digest ------------------------------------------------

class TestResourceMappingDigest:
    def test_mapping_change_changes_digest(self):
        """Changing resource_mapping changes the profile digest."""
        p1 = _profile()
        p2 = _profile(resource_mapping={
            "cpu": {"queue": "gpu", "note": "constrained"},
            "gpu": {"queue": "gpu"},
        })
        assert p1.digest != p2.digest

    def test_mapping_note_changes_digest(self):
        """Even the note text is part of the digest."""
        p1 = _profile(resource_mapping={"cpu": {"queue": "gpu", "note": "v1"}})
        p2 = _profile(resource_mapping={"cpu": {"queue": "gpu", "note": "v2"}})
        assert p1.digest != p2.digest

    def test_ns_ws_same_identity(self):
        """NS and WS use the same profile; workload resolution produces the
        same account/partition/QOS for both, preserving ablation identity."""
        profile = _profile(
            resource_mapping={
                "cpu": {"queue": "gpu"},
                "gpu": {"queue": "gpu"},
            },
        )
        ns_resolved = profile.resolve_workload("gpu")  # NS arm
        ws_resolved = profile.resolve_workload("gpu")  # WS arm
        assert ns_resolved.account == ws_resolved.account
        assert ns_resolved.partition == ws_resolved.partition
        assert ns_resolved.qos == ws_resolved.qos
        assert ns_resolved.gres == ws_resolved.gres
        # Digest is profile-level, not per-resolve.
        assert profile.digest == profile.digest


# -- to_adapter_config --------------------------------------------------------

class TestToAdapterConfig:
    def test_adapter_config_carries_resolved_params(self):
        profile = _profile()
        resolved = profile.resolve_workload("gpu")
        config = profile.to_adapter_config(
            resolved, workspace_root="/tmp/work"
        )
        assert config["account"] == "test-account"
        assert config["platform_profile"]["default_queue"] == "gpu"
        queue = config["platform_profile"]["queues"][0]
        assert queue["name"] == "gpu"
        assert queue["max_gpus"] == 1

    def test_adapter_config_no_credentials(self):
        profile = _profile()
        resolved = profile.resolve_workload("gpu")
        config = profile.to_adapter_config(
            resolved, workspace_root="/tmp/work"
        )
        # The config must not carry credential bytes or SSH key material.
        text = str(config)
        for forbidden in ("ssh_private_key", "password", "secret",
                          "PRIVATE", "BEGIN OPENSSH"):
            assert forbidden not in text
        # run_token is an opaque session identifier, not a credential.
