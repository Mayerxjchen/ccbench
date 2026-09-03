"""P1: ComputeProfile + ComputeRouter contract tests.

The agent selects only a compute class; the operator's ComputeProfile maps
it to a SiteProfile, and ComputeRouter fails closed on unknown class, missing
route, resource contradiction, unregistered site, or config tampering. The
public view never leaks site vocabulary.
"""

from __future__ import annotations

import copy

import pytest

from dftworld_bench.hpc.compute_profile import (
    ComputeProfile,
    ComputeProfileError,
    ComputeRouter,
)
from dftworld_bench.hpc.job import JobResources
from dftworld_bench.hpc.request import (
    ExecutionRequestV2,
    RequestError,
    request_from_legacy_v1_spec,
)
from dftworld_bench.hpc.site_profile import HpcSiteProfile


def _site(partition: str, *, cpu_partition: str | None) -> HpcSiteProfile:
    slurm: dict = {
        "partition": partition,
        "account": "acct-x",
        "qos": "normal",
        "gres": "gpu:1",
        "cpus_per_task": "8",
        "mem": "64G",
        "time_paper": "08:00:00",
    }
    if cpu_partition:
        slurm["cpu_partition"] = cpu_partition
    return HpcSiteProfile.from_cluster_config({
        "ssh": {"host": f"login-{partition}", "user": "u", "port": 22},
        "paths": {"remote_root": f"/runs/{partition}", "apptainer": "/bin/apptainer"},
        "slurm": slurm,
        "runtime": {"expected_node_arch": "x86_64"},
    })


CPU_SITE = _site("cpu", cpu_partition="cpu")
GPU_SITE = _site("gpu", cpu_partition=None)


def _profile(routes: dict) -> ComputeProfile:
    return ComputeProfile.from_dict({
        "schema_version": 1,
        "profile_id": "maintainer-hybrid-v1",
        "routes": routes,
    })


def _request(compute_class: str, gpus: int) -> ExecutionRequestV2:
    return ExecutionRequestV2(
        operation_id="op-1",
        compute_class=compute_class,
        runtime="cp2k",
        command=("run",),
        resources=JobResources(cpus=1, memory_gb=1, gpus=gpus, walltime_minutes=5),
    )


# -- normal routing -----------------------------------------------------------


def test_cpu_route_resolves_to_named_site():
    profile = _profile({"cpu": {"site_profile": "cpu-v1"}})
    router = ComputeRouter(profile, site_profiles={"cpu-v1": CPU_SITE})
    route = router.route("cpu")
    assert route.compute_class == "cpu"
    assert route.site_profile_name == "cpu-v1"
    assert route.resource.partition == "cpu"
    assert route.resource.max_gpus == 0


def test_gpu_and_cpu_routes_coexist():
    profile = _profile({
        "cpu": {"site_profile": "cpu-v1"},
        "gpu": {"site_profile": "gpu-v1"},
    })
    router = ComputeRouter(
        profile, site_profiles={"cpu-v1": CPU_SITE, "gpu-v1": GPU_SITE}
    )
    assert router.route("cpu").site_profile_name == "cpu-v1"
    assert router.route("gpu").site_profile_name == "gpu-v1"


def test_route_lock_block_binds_all_three_identities():
    profile = _profile({"gpu": {"site_profile": "gpu-v1"}})
    router = ComputeRouter(profile, site_profiles={"gpu-v1": GPU_SITE})
    block = router.route("gpu").lock_hpc_block()
    assert block["compute_route"] == {
        "compute_class": "gpu", "site_profile": "gpu-v1",
    }
    assert block["compute_profile_digest"] == profile.digest
    assert block["site_profile_digest"] == GPU_SITE.digest
    assert block["compute_profile_id"] == "maintainer-hybrid-v1"


# -- fail-closed paths --------------------------------------------------------


def test_missing_route_never_falls_back_to_other_site():
    profile = _profile({"cpu": {"site_profile": "cpu-v1"}})
    router = ComputeRouter(profile, site_profiles={"cpu-v1": CPU_SITE})
    with pytest.raises(ComputeProfileError, match="no 'gpu' route"):
        router.route("gpu")


def test_unknown_compute_class_is_rejected():
    router = ComputeRouter(
        _profile({"cpu": {"site_profile": "cpu-v1"}}),
        site_profiles={"cpu-v1": CPU_SITE},
    )
    with pytest.raises(ComputeProfileError, match="unknown compute class"):
        router.route("tpu")


def test_route_rejects_resource_contradiction():
    router = ComputeRouter(
        _profile({"cpu": {"site_profile": "cpu-v1"},
                  "gpu": {"site_profile": "gpu-v1"}}),
        site_profiles={"cpu-v1": CPU_SITE, "gpu-v1": GPU_SITE},
    )
    # A hand-built (non-schema) cpu request carrying GPUs cannot pass routing.
    with pytest.raises(ComputeProfileError, match="gpus=0"):
        router.route_request(_request("cpu", 2))
    with pytest.raises(ComputeProfileError, match="gpus>=1"):
        router.route_request(_request("gpu", 0))


def test_route_refers_unregistered_site_profile_fails_at_construction():
    profile = _profile({"gpu": {"site_profile": "ghost-site"}})
    with pytest.raises(ComputeProfileError, match="unregistered site profile"):
        ComputeRouter(profile, site_profiles={"cpu-v1": CPU_SITE})


def test_route_site_without_matching_queue_is_fail_closed():
    # cpu route pointed at a GPU-only site: resolve_workload("cpu") must fail.
    profile = _profile({"cpu": {"site_profile": "gpu-v1"}})
    router = ComputeRouter(profile, site_profiles={"gpu-v1": GPU_SITE})
    with pytest.raises(ComputeProfileError, match="cannot satisfy"):
        router.route("cpu")


# -- digest + schema tamper ---------------------------------------------------


def test_digest_is_stable_and_route_sensitive():
    a = _profile({"cpu": {"site_profile": "cpu-v1"}})
    b = _profile({"cpu": {"site_profile": "cpu-v1"}})
    assert a.digest == b.digest
    c = _profile({"cpu": {"site_profile": "cpu-v2"}})
    assert a.digest != c.digest


def test_schema_rejects_duplicate_or_unknown_route_keys():
    for bad in (
        {"profile_id": "p", "schema_version": 1,
         "routes": {"cpu": {"site_profile": "c"}, "gpu": {"site_profile": "g"},
                    "tpu": {"site_profile": "t"}}},
        {"profile_id": "p", "schema_version": 1,
         "routes": {"cpu": {"site_profile": "c", "extra": 1}}},
    ):
        with pytest.raises(ComputeProfileError):
            ComputeProfile.from_dict(bad)


# -- agent-facing v2 contract -------------------------------------------------


def test_v2_payload_requires_explicit_compute_class():
    payload = {
        "schema_version": 2, "operation_id": "op", "attempt": 1,
        "runtime": "cp2k", "command": ["run"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [], "outputs": [],
    }
    with pytest.raises(RequestError, match="compute_class"):
        ExecutionRequestV2.from_dict(payload)


def test_v2_compute_class_must_match_gpus():
    base = {
        "schema_version": 2, "operation_id": "op", "attempt": 1,
        "runtime": "cp2k", "command": ["run"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 2, "walltime_minutes": 5},
        "inputs": [], "outputs": [],
    }
    bad = copy.deepcopy(base)
    bad["compute_class"] = "cpu"  # gpus 2 contradicts cpu
    with pytest.raises(RequestError):
        ExecutionRequestV2.from_dict(bad)
    good = copy.deepcopy(base)
    good["compute_class"] = "gpu"
    assert ExecutionRequestV2.from_dict(good).compute_class == "gpu"


def test_legacy_v1_bridge_infers_and_marks():
    req = request_from_legacy_v1_spec({
        "schema_version": 1, "idempotency_key": "k", "operation_id": "op",
        "runtime": "cp2k", "command": ["run"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 1, "walltime_minutes": 5},
        "inputs": [], "outputs": [],
    })
    assert req.compute_class == "gpu"
    assert req.compute_class_source == "legacy-inferred"


# -- no provider vocabulary to the agent --------------------------------------


def test_public_view_exposes_no_site_vocabulary():
    profile = _profile({
        "cpu": {"site_profile": "ikkem-cpu"},
        "gpu": {"site_profile": "compshare-gpu"},
    })
    router = ComputeRouter(
        profile, site_profiles={"ikkem-cpu": CPU_SITE, "compshare-gpu": GPU_SITE}
    )
    view = router.public_view()
    blob = str(view).lower()
    for token in ("ikkem", "compshare", "partition", "region", "image"):
        assert token not in blob
    assert sorted(view["compute_classes"]) == ["cpu", "gpu"]
    assert view["digest"] == profile.digest


def test_example_profiles_validate_and_route():
    import json
    from pathlib import Path
    import jsonschema

    repo_root = Path(__file__).resolve().parent.parent.parent
    site_schema = json.loads(
        (repo_root / "schemas" / "hpc-site-profile.schema.json").read_text()
    )
    comp_schema = json.loads(
        (repo_root / "schemas" / "compute-profile.schema.json").read_text()
    )

    generic_site_doc = json.loads(
        (repo_root / "examples" / "hpc" / "generic-slurm-site-profile.json").read_text()
    )
    jsonschema.validate(instance=generic_site_doc, schema=site_schema)

    generic_comp_doc = json.loads(
        (repo_root / "examples" / "hpc" / "generic-slurm-compute-profile.json").read_text()
    )
    jsonschema.validate(instance=generic_comp_doc, schema=comp_schema)

    maintainer_doc = json.loads(
        (repo_root / "examples" / "hpc" / "maintainer-hybrid-compute-profile.json").read_text()
    )
    jsonschema.validate(instance=maintainer_doc, schema=comp_schema)

    profile = ComputeProfile.from_dict(maintainer_doc)
    router = ComputeRouter(
        profile, site_profiles={"ikkem-cpu": CPU_SITE, "compshare-gpu": GPU_SITE}
    )
    r_cpu = router.route("cpu")
    assert r_cpu.site_profile_name == "ikkem-cpu"
    r_gpu = router.route("gpu")
    assert r_gpu.site_profile_name == "compshare-gpu"
