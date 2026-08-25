"""HpcSiteProfile: trusted site policy identity, credential-free.

The public profile binds scheduler policy (target identity, account, queues,
QOS, remote-root policy, runtime store) into a digest without ever containing
credential bytes. What the Candidate sees — ``public_capabilities()`` — is
abstract resource classes and ceilings only; no account, partition, QOS,
host, user, remote root, or credential profile ID survives serialization.
"""

from __future__ import annotations

import json

import pytest

from dftworld_bench.hpc.site_profile import HpcSiteProfile, SiteProfileError


def profile() -> dict:
    return {
        "schema_version": 1,
        "site_id": "site-v1",
        "scheduler": "slurm",
        "connection": {
            "credential_profile_id": "<site-alias>-key-1",
            "target_binding": "ssh://gateway.<site-alias>.example:2222",
            "remote_user": "svc-bench",
            "remote_root_policy": "/data/bench/{run_id}",
        },
        "account": "acct-blocked",
        "queues": {
            "cpu": {"partition": "cpu-q", "qos": "normal", "max_cpus": 64,
                    "max_memory_gb": 256, "max_walltime_minutes": 2880},
            "gpu": {"partition": "gpu-q", "qos": "long", "max_cpus": 32,
                    "max_memory_gb": 128, "max_gpus": 1,
                    "max_walltime_minutes": 1440},
        },
        "runtime_policy": {
            "requires_apptainer": True,
            "runtime_store": "/data/bench/runtimes",
            "gres_template": "--gres=gpu:1",
        },
    }


def test_public_profile_rejects_credential_value():
    bad = profile()
    bad["ssh_private_key"] = "PRIVATE"
    with pytest.raises(SiteProfileError):
        HpcSiteProfile.from_dict(bad)
    nested = profile()
    nested["connection"]["private_key"] = "PRIVATE"
    with pytest.raises(SiteProfileError):
        HpcSiteProfile.from_dict(nested)


def test_profile_digest_excludes_secret_but_binds_policy():
    site = HpcSiteProfile.from_dict(profile())
    assert site.digest.startswith("sha256:")
    assert "hostname" not in site.public_identity()
    # Rotating an equivalent ephemeral key does not change policy identity:
    # the digest never contained credential bytes in the first place.
    again = HpcSiteProfile.from_dict(profile())
    assert again.digest == site.digest
    # But changing any policy field does.
    moved = profile()
    moved["account"] = "other-account"
    assert HpcSiteProfile.from_dict(moved).digest != site.digest
    retargeted = profile()
    retargeted["connection"]["target_binding"] = "ssh://elsewhere:22"
    assert HpcSiteProfile.from_dict(retargeted).digest != site.digest
    requed = profile()
    requed["queues"]["gpu"]["qos"] = "normal"
    assert HpcSiteProfile.from_dict(requed).digest != site.digest


def test_candidate_capabilities_are_abstract():
    capabilities = HpcSiteProfile.from_dict(profile()).public_capabilities()
    encoded = json.dumps(capabilities)
    assert "acct-blocked" not in encoded
    assert "partition" not in encoded
    assert "qos" not in encoded.lower()
    assert set(capabilities["resource_classes"]) == {"cpu", "gpu"}
    # Ceilings are visible; the names that produce them are not.
    gpu = capabilities["resource_classes"]["gpu"]
    assert gpu["max_gpus"] == 1


def test_immutable_profile_rejects_mutation():
    site = HpcSiteProfile.from_dict(profile())
    with pytest.raises(Exception):
        site.account = "hacked"


def test_unknown_fields_rejected():
    bad = profile()
    bad["secret_extra"] = "x"
    with pytest.raises(SiteProfileError):
        HpcSiteProfile.from_dict(bad)


def test_jiatgeng_policy_encoded_without_credentials():
    """the site policy is recorded as pure policy: account, queues, QOS, gres cap,
    Apptainer requirement, runtime store — nothing that authenticates."""
    site = HpcSiteProfile.from_dict(profile())
    encoded = json.dumps(site.to_public_dict())
    assert site.account == "acct-blocked"
    assert site.queues["gpu"]["qos"] == "long"
    assert site.queues["gpu"]["max_gpus"] == 1
    assert site.runtime_policy["requires_apptainer"] is True
    assert site.runtime_policy["gres_template"] == "--gres=gpu:1"
    assert "BEGIN ... PRIVATE KEY" not in encoded
