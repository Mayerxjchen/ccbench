"""Tests for RoutedDriver: heterogeneous CPU/GPU routing and lifecycle settlement."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dftworld_bench.hpc.compute_profile import (
    ComputeProfile,
    ComputeRouter,
    ResolvedRoute,
)
from dftworld_bench.hpc.drivers.base import HpcDriver
from dftworld_bench.hpc.drivers.compshare import (
    CompShareCli,
    CompShareDriver,
    FakeCompShareCliRunner,
)
from dftworld_bench.hpc.drivers.process import ProcessDriver
from dftworld_bench.hpc.drivers.routed import RoutedDriver
from dftworld_bench.hpc.runtime_resolution import (
    ResolvedRuntime,
    RuntimeResolver,
    RuntimeStatus,
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


def _make_routed_environment(tmp_path: Path):
    cpu_site = _site("cpu", cpu_partition="cpu")
    gpu_site = _site("gpu", cpu_partition=None)

    profile = ComputeProfile.from_dict({
        "schema_version": 1,
        "profile_id": "maintainer-hybrid-v1",
        "routes": {
            "cpu": {"site_profile": "cpu-site"},
            "gpu": {"site_profile": "gpu-site"},
        },
    })
    router = ComputeRouter(
        profile,
        site_profiles={
            "cpu-site": cpu_site,
            "gpu-site": gpu_site,
        },
    )

    # Process driver for CPU
    from dftworld_bench.hpc.adapters.process_test import ProcessTestAdapter
    cpu_adapter = ProcessTestAdapter(tmp_path / "process_root")
    cpu_driver = ProcessDriver(cpu_adapter)

    # CompShare driver for GPU
    runner = FakeCompShareCliRunner(initial_stock=2)
    cli = CompShareCli(runner=runner)
    gpu_driver = CompShareDriver(cli, workspace_root=str(tmp_path / "cs_workspace"))

    # Runtime resolver with locks for cp2k and deepmd
    locks_dir = tmp_path / "runtime_locks"
    locks_dir.mkdir(parents=True)
    cp2k_lock = {
        "schema": "dispatcher-cp2k-runtime-lock/v1",
        "image_name": "dftworld-cp2k",
        "runtime": {
            "sif_path_remote": "/opt/cp2k.sif",
            "sif_sha256": "05f708b1b03d949af095a770c00ca7930fea293b161a2383d71ea99e5cfef5dd",
        },
    }
    (locks_dir / "cp2k-runtime.lock.json").write_text(json.dumps(cp2k_lock))

    deepmd_lock = {
        "schema": "dispatcher-compshare-runtime-lock/v2",
        "runtime_profile_id": "compshare-deepmd-gpu-v1",
        "capability": "deepmd",
        "image_name": "mlff-deepmd-gpu-v1",
        "artifact": {
            "artifact_kind": "compshare_image",
            "image_id": "img-deepmd-gpu-v1",
        },
        "qualification": {
            "status": "PASS",
            "receipt_path": "receipts/deepmd-qual.json",
            "receipt_digest": "sha256:" + "b" * 64,
        },
        "provenance": {
            "provider": "compshare",
        },
    }
    (locks_dir / "deepmd-runtime.lock.json").write_text(json.dumps(deepmd_lock))

    parsed_resolver = RuntimeResolver.from_lock_dir(locks_dir)
    # This unit test isolates routing with the result of a trusted catalog
    # promotion.  The bare lock parser above remains untrusted and is tested
    # separately for fail-closed behavior.
    entries = []
    seen_sources: set[str] = set()
    import dataclasses
    for entry in parsed_resolver._by_name.values():
        if entry.source in seen_sources:
            continue
        seen_sources.add(entry.source)
        entries.append(dataclasses.replace(
            entry,
            status=RuntimeStatus.QUALIFIED,
            qualification_verified=True,
            qualification_receipt_digest="sha256:" + "1" * 64,
        ))
    resolver = RuntimeResolver(entries, trusted=True)

    # Both sites mapped in drivers by scheduler ("slurm" in site_profile maps to cpu_driver for test,
    # or we can register "slurm" -> cpu_driver, "compshare" -> gpu_driver)
    # Notice that cpu_site.scheduler is "slurm" and gpu_site.scheduler is "slurm" (from cluster config),
    # but we can map "slurm": cpu_driver or customize gpu_site with scheduler="compshare"
    object.__setattr__(gpu_site, "scheduler", "compshare")

    routed = RoutedDriver(
        router,
        {"slurm": cpu_driver, "compshare": gpu_driver},
        runtime_resolver=resolver,
    )
    return routed, runner, resolver


def test_routed_driver_satisfies_protocol(tmp_path: Path):
    routed, _, _ = _make_routed_environment(tmp_path)
    assert isinstance(routed, HpcDriver)
    caps = routed.capabilities()
    assert caps["scheduler"] == "routed"
    assert "cpu" in caps["compute_classes"]
    assert "gpu" in caps["compute_classes"]


def test_routed_driver_per_operation_dispatch_and_settle(tmp_path: Path):
    routed, runner, resolver = _make_routed_environment(tmp_path)

    # 1. Submit CPU operation (e.g. CP2K labeling)
    cpu_spec = {
        "compute_class": "cpu",
        "runtime": "cp2k",
        "command": ["echo", "cpu_ok"],
        "resources": {"cpus": 2, "memory_gb": 4, "gpus": 0, "walltime_minutes": 10},
        "inputs": [],
        "outputs": [],
    }
    cpu_res = routed.submit(cpu_spec, run_id="run-hybrid", operation_id="aimd-01", attempt=1)
    assert cpu_res["duplicate"] is False
    assert len(runner.instances) == 0  # CPU did NOT spin up a GPU instance!

    # 2. Submit GPU operation (e.g. DeePMD training)
    gpu_spec = {
        "compute_class": "gpu",
        "runtime": "deepmd",
        "command": ["dp", "train", "in.json"],
        "resources": {"cpus": 4, "memory_gb": 16, "gpus": 1, "walltime_minutes": 30},
        "inputs": [],
        "outputs": [{"path": "model.pb"}],
    }
    gpu_res = routed.submit(gpu_spec, run_id="run-hybrid", operation_id="train-01", attempt=1)
    assert gpu_res["duplicate"] is False
    assert len(runner.instances) == 1  # CompShare GPU instance spun up!
    inst_id = next(iter(runner.instances.keys()))

    # 3. Check statuses via routed driver
    cpu_stat = routed.status("run-hybrid", cpu_res["job_id"])
    assert cpu_stat["state"] in ("QUEUED", "RUNNING", "SUCCEEDED")

    gpu_stat = routed.status("run-hybrid", gpu_res["job_id"])
    assert gpu_stat["state"] == "SUCCEEDED"

    # 4. Settle run: stops and deletes CompShare instance
    assert routed.settle("run-hybrid") is True
    assert inst_id not in runner.instances
    usage = routed.usage()
    assert usage["compshare"]["active_instances"] == 0


def test_routed_driver_capabilities_classes_and_runtime_filtering(tmp_path: Path):
    routed, _, resolver = _make_routed_environment(tmp_path)
    caps = routed.capabilities()
    assert "classes" in caps
    assert "cpu" in caps["classes"]
    assert "gpu" in caps["classes"]
    assert "runtimes" in caps
    assert "cp2k" in caps["runtimes"]
    assert "deepmd" in caps["runtimes"]


def test_routed_driver_rejects_candidate_scheduler_override(tmp_path: Path):
    routed, _, _ = _make_routed_environment(tmp_path)
    spec = {
        "compute_class": "cpu",
        "scheduler": "compshare",  # Candidate attempts to force compshare on cpu route
        "runtime": "cp2k",
        "command": ["echo", "cpu_ok"],
        "resources": {"cpus": 2, "memory_gb": 4, "gpus": 0, "walltime_minutes": 10},
        "inputs": [],
        "outputs": [],
    }
    from dftworld_bench.hpc.drivers.base import HpcDriverError
    with pytest.raises(HpcDriverError, match="Candidate cannot override scheduler"):
        routed.submit(spec, run_id="run-tamper", operation_id="tamper-01")


def test_routed_driver_hard_rejects_unqualified_runtime_placeholder(tmp_path: Path):
    routed, _, _ = _make_routed_environment(tmp_path)
    spec = {
        "compute_class": "cpu",
        "runtime": "unqualified-runtime",
        "command": ["echo", "fail"],
        "resources": {"cpus": 2, "memory_gb": 4, "gpus": 0, "walltime_minutes": 10},
        "inputs": [],
        "outputs": [],
    }
    from dftworld_bench.hpc.drivers.base import HpcDriverError
    with pytest.raises(HpcDriverError, match="Runtime resolution failed"):
        routed.submit(spec, run_id="run-unqual", operation_id="unqual-01")
