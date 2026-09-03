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


def test_cluster_config_supports_distinct_cpu_queue_ceilings():
    config = {
        "ssh": {"host": "site", "user": "operator", "port": 22},
        "paths": {"remote_root": "/runs", "apptainer": "/bin/apptainer"},
        "slurm": {
            "account": "acct",
            "partition": "gpu",
            "cpu_partition": "cpu",
            "qos": "normal",
            "cpus_per_task": "8",
            "mem": "64G",
            "time_paper": "08:00:00",
            "cpu_cpus_per_task": "2",
            "cpu_mem": "8G",
            "cpu_time_paper": "01:00:00",
        },
    }
    site = HpcSiteProfile.from_cluster_config(config)

    gpu = site.resolve_workload("gpu")
    cpu = site.resolve_workload("cpu")
    assert (gpu.max_cpus, gpu.max_memory_gb, gpu.max_walltime_minutes) == (
        8, 64, 480,
    )
    assert (cpu.max_cpus, cpu.max_memory_gb, cpu.max_walltime_minutes) == (
        2, 8, 60,
    )


def test_cluster_config_carries_explicit_runtime_facts():
    """P3 (freeze §5): apptainer binary path and runtime lock directory are
    explicit SiteProfile policy, not caller folklore."""
    config = {
        "ssh": {"host": "site", "user": "operator", "port": 22},
        "paths": {"remote_root": "/runs", "apptainer": "/opt/apptainer/bin/apptainer"},
        "runtime": {"lock_dir": "reference/runtime"},
        "slurm": {
            "account": "acct", "partition": "gpu", "qos": "normal",
            "cpus_per_task": "8", "mem": "64G", "time_paper": "08:00:00",
        },
    }
    site = HpcSiteProfile.from_cluster_config(config)
    assert site.runtime_policy["apptainer_bin"] == "/opt/apptainer/bin/apptainer"
    assert site.runtime_policy["runtime_lock_dir"] == "reference/runtime"
    assert site.runtime_policy["runtime_store"] == "/runs"


def test_cluster_config_rejects_mixed_gpu_mig_partitions():
    """Full-GPU and MIG are separate profiles and separate qualifications;
    a comma list would mix compute classes inside one site identity."""
    base = {
        "ssh": {"host": "site", "user": "operator", "port": 22},
        "paths": {"remote_root": "/runs", "apptainer": "/bin/apptainer"},
        "slurm": {
            "account": "acct", "partition": "gpu,gpu-mig-1g", "qos": "normal",
            "cpus_per_task": "8", "mem": "64G", "time_paper": "08:00:00",
        },
    }
    with pytest.raises(SiteProfileError, match="separate cluster"):
        HpcSiteProfile.from_cluster_config(base)
    mixed_cpu = dict(base)
    mixed_cpu["slurm"] = {**base["slurm"], "partition": "gpu", "cpu_partition": "cpu,mig"}
    with pytest.raises(SiteProfileError, match="separate cluster"):
        HpcSiteProfile.from_cluster_config(mixed_cpu)


# -- resource_class alias layer -----------------------------------------------


def profile_with_classes() -> dict:
    payload = profile()
    payload["resource_classes"] = {
        "gpu-small": {"queue": "gpu", "max_cpus": 8, "max_memory_gb": 32,
                      "max_walltime_minutes": 240},
        "gpu-lite": {"queue": "gpu", "max_gpus": 1,
                     "max_walltime_minutes": 60},
        "cpu-narrow": {"queue": "cpu", "max_cpus": 4},
    }
    return payload


def test_resource_class_resolves_within_backing_ceilings():
    site = HpcSiteProfile.from_dict(profile_with_classes())
    small = site.resolve_resource_class("gpu-small")
    assert (small.queue_name, small.partition, small.qos) == (
        "gpu", "gpu-q", "long",
    )
    assert (small.max_cpus, small.max_memory_gb, small.max_walltime_minutes) == (
        8, 32, 240,
    )
    # The alias constrains only what it names; everything else inherits.
    assert small.max_gpus == 1
    # The fully-constrained alias still carries the class note.
    assert small.mapping_note == "resource_class=gpu-small"
    lite = site.resolve_resource_class("gpu-lite")
    assert lite.max_gpus == 1
    assert lite.max_cpus == 32  # inherited from the gpu queue ceiling
    assert lite.mapping_note == "resource_class=gpu-lite"
    narrow = site.resolve_resource_class("cpu-narrow")
    assert (narrow.queue_name, narrow.max_cpus) == ("cpu", 4)
    assert narrow.max_gpus == 0


def test_resource_class_unknown_name_fails_closed():
    site = HpcSiteProfile.from_dict(profile_with_classes())
    with pytest.raises(SiteProfileError, match="no resource_class 'gpu-big'"):
        site.resolve_resource_class("gpu-big")


def test_resource_class_unknown_backing_queue_fails_closed_at_load():
    # Schema allows cpu/gpu; a class naming a queue the site does not define
    # must fail at profile load (from_dict), not at resolution time.
    # The test profile has both cpu and gpu queues; drop the cpu queue so the
    # alias naming it points at a queue the site does not define.
    broken = profile()
    del broken["queues"]["cpu"]
    broken["resource_classes"] = {"gpu-small": {"queue": "cpu"}}
    with pytest.raises(SiteProfileError, match="names unknown queue"):
        HpcSiteProfile.from_dict(broken)


def test_resource_class_ceiling_exceeding_backing_fails_closed_at_load():
    payload = profile_with_classes()
    payload["resource_classes"]["gpu-small"]["max_walltime_minutes"] = 9000
    with pytest.raises(SiteProfileError, match="9000 exceeds the backing queue"):
        HpcSiteProfile.from_dict(payload)
    # Zero-GPU backing queue cannot grant GPUs either.
    payload = profile_with_classes()
    payload["resource_classes"]["cpu-narrow"]["max_gpus"] = 2
    with pytest.raises(SiteProfileError, match="max_gpus=2 exceeds the backing"):
        HpcSiteProfile.from_dict(payload)


def test_resource_class_public_capabilities_abstract_and_partition_free():
    site = HpcSiteProfile.from_dict(profile_with_classes())
    capabilities = site.public_capabilities()
    encoded = json.dumps(capabilities)
    assert "gpu-q" not in encoded
    assert "partition" not in encoded
    assert "long" not in encoded  # the gpu queue's QOS name
    classes = capabilities["resource_classes"]
    assert set(classes) == {"cpu", "gpu", "gpu-small", "gpu-lite", "cpu-narrow"}
    assert classes["gpu-small"] == {
        "max_cpus": 8,
        "max_memory_gb": 32,
        "max_walltime_minutes": 240,
        "max_gpus": 1,
    }
    # Aliases always expose max_gpus (the value they resolve to), even when the
    # class itself names no gpu ceiling.
    assert classes["gpu-lite"]["max_gpus"] == 1


def test_resource_class_digest_binds_aliases():
    base = HpcSiteProfile.from_dict(profile_with_classes())
    moved = profile_with_classes()
    moved["resource_classes"]["gpu-small"]["max_cpus"] = 4
    assert HpcSiteProfile.from_dict(moved).digest != base.digest
    aliased = profile_with_classes()
    aliased["resource_classes"]["gpu-extra"] = {"queue": "gpu"}
    assert HpcSiteProfile.from_dict(aliased).digest != base.digest


def test_resource_class_rides_to_public_dict():
    site = HpcSiteProfile.from_dict(profile_with_classes())
    public = site.to_public_dict()
    assert public["resource_classes"] == {
        "cpu-narrow": {"queue": "cpu", "max_cpus": 4},
        "gpu-lite": {"queue": "gpu", "max_gpus": 1,
                     "max_walltime_minutes": 60},
        "gpu-small": {"queue": "gpu", "max_cpus": 8, "max_memory_gb": 32,
                      "max_walltime_minutes": 240},
    }
    assert json.dumps(public)  # JSON-serializable without partition leakage


def test_cluster_config_resource_classes_passthrough():
    config = {
        "ssh": {"host": "site", "user": "operator", "port": 22},
        "paths": {"remote_root": "/runs", "apptainer": "/bin/apptainer"},
        "slurm": {
            "account": "acct",
            "partition": "gpu",
            "cpu_partition": "cpu",
            "qos": "normal",
            "cpus_per_task": "8",
            "mem": "64G",
            "time_paper": "08:00:00",
            "resource_classes": {
                "smoke": {"queue": "gpu", "max_walltime_minutes": 30},
            },
        },
    }
    site = HpcSiteProfile.from_cluster_config(config)
    smoke = site.resolve_resource_class("smoke")
    assert smoke.max_walltime_minutes == 30
    assert smoke.max_cpus == 8  # inherited from the gpu queue
