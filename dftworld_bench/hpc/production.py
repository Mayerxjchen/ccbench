"""Trusted production composition of the real Slurm execution stack.

This module is the sanctioned birthplace of adapter instances for real-site
runs (mirroring scripts/infra/qualify_hpc_dispatcher._open_session): eval's
composition root may construct HpcDispatcher/GatewayRuntime but never names
an adapter class directly (pinned by
tests/hpc/test_dispatcher_production_path.py).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from scripts.ablation.transport.slurm_transport import (
    SshConfig,
    SshSlurmTransport,
)

from dftworld_bench.hpc.adapters.slurm import SlurmAdapter
from dftworld_bench.hpc.audit import GatewayAudit
from dftworld_bench.hpc.runtime_catalog import TrustedRuntimeCatalog
from dftworld_bench.hpc.site_profile import HpcSiteProfile


def build_slurm_stack(
    *,
    cluster_profile_path: Path,
    case_id: str,
    audit_path: Path,
    token_ttl_sec: float = 7200.0,
    workload: str = "gpu",
    qualification_root: Path | str | None = None,
    trust_store: Any | None = None,
) -> dict[str, Any]:
    """Compose {audit, adapter, run_adapter_config, site_profile}.

    ``run_adapter_config`` rides open_run's per-run override so each session
    gets a long-lived token without touching site-config validation.
    """
    raw = tomllib.loads(
        Path(cluster_profile_path).read_text(encoding="utf-8")
    )
    site = HpcSiteProfile.from_cluster_config(raw)
    ssh = raw["ssh"]
    resolved = site.resolve_workload(workload)
    raw_lock_dir = site.runtime_policy.get("runtime_lock_dir", "runtimes/locks")
    lock_dir = Path(raw_lock_dir)
    if not lock_dir.is_dir():
        if Path("runtimes/locks").is_dir():
            lock_dir = Path("runtimes/locks")
    if not lock_dir.is_dir():
        raise RuntimeError(
            f"configured runtime lock directory does not exist: {lock_dir}"
        )
    lock_files = tuple(lock_dir.glob("*-runtime.lock.json"))
    if lock_files and qualification_root is None:
        raise RuntimeError(
            "Slurm runtime locks require an explicit qualification_root; "
            "refusing to infer it from the lock directory"
        )
    if lock_files and trust_store is None:
        raise RuntimeError(
            "Slurm runtime locks require an explicit qualification trust_store; "
            "qualification cannot proceed without operator trust anchors"
        )

    transport = SshSlurmTransport(
        ssh=SshConfig(
            host=ssh["host"],
            user=ssh.get("user") or None,
            port=int(ssh.get("port") or 22) or None,
            options={k: str(v) for k, v in (ssh.get("options") or {}).items()},
        ),
        workspace=str(Path.cwd()),
        remote_workspace=raw["paths"]["remote_root"],
        sync="sync_back",
    )
    res_suffix = f"{resolved.max_gpus}gpu" if resolved.max_gpus else "cpu"
    resource_profile = {
        "name": f"{resolved.partition}-{res_suffix}",
        "qos": resolved.qos,
        "max_cpus": resolved.max_cpus,
        "max_memory_gb": resolved.max_memory_gb,
        "max_gpus": resolved.max_gpus,
        "max_walltime_minutes": resolved.max_walltime_minutes,
    }
    adapter = SlurmAdapter(
        site.to_adapter_config(
            resolved,
            workspace_root=raw["paths"]["remote_root"],
            gateway_url="local://eval",
            run_token="0" * 32,
        ),
        transport,
        case_id=case_id,
        resource_profile=resource_profile,
        script_dir=Path("jobs/hpc-scripts"),
    )
    # The explicit profile registry and Catalog are the only authority passed
    # to GatewayRuntime.  A bare lock parser is deliberately not sufficient
    # for a production runtime.
    runtime_catalog = TrustedRuntimeCatalog(
        lock_dir=lock_dir,
        qualification_root=qualification_root,
        trust_store=trust_store,
        trusted_site_profiles={site.site_id: site},
    )
    return {
        "audit": GatewayAudit(audit_path),
        "adapter": adapter,
        "site_profile": site,
        "run_adapter_config": {
            "adapter": "slurm",
            "adapter_instance": adapter,
            "token_ttl_sec": token_ttl_sec,
            "runtime_catalog": runtime_catalog,
            "runtime_lock_dir": str(lock_dir),
            "qualification_root": (
                str(qualification_root) if qualification_root is not None else None
            ),
        },
    }


def build_hybrid_stack(
    *,
    compute_profile_path: Path,
    cluster_profile_path: Path | None = None,
    cpu_site_profile_path: Path | None = None,
    gpu_site_profile_path: Path | None = None,
    compshare_cli: Any | None = None,
    case_id: str,
    audit_path: Path,
    token_ttl_sec: float = 7200.0,
    runtime_lock_dir: Path | str = "runtimes/locks",
    qualification_root: Path | str | None = None,
    trust_store: Any | None = None,
    repo_root: Path | str | None = None,
    compshare_workspace_root: Path | str | None = None,
    compshare_state_root: Path | str | None = None,
) -> dict[str, Any]:
    """Compose heterogeneous hybrid stack routing CPU to Slurm and GPU to CompShare.

    ``compshare_state_root`` is the explicit durable account-lock/ledger root
    for formal CompShare runs.  ``compshare_workspace_root`` remains an
    explicit offline qualification/replay workspace; when a fake CLI is
    injected it may serve as the state root for those isolated tests.  A
    caller cannot use either path to bypass the Catalog: the returned
    RoutedDriver is still given only ``runtime_catalog.to_resolver()``.
    """
    import json
    from dftworld_bench.hpc.compute_profile import ComputeProfile, ComputeRouter
    from dftworld_bench.hpc.drivers.compshare import CompShareCli, CompShareDriver
    from dftworld_bench.hpc.drivers.routed import RoutedDriver
    from dftworld_bench.hpc.drivers.slurm import SlurmDriver

    compute_profile = ComputeProfile.from_file(Path(compute_profile_path))
    if not Path(runtime_lock_dir).is_dir():
        if Path("runtimes/locks").is_dir():
            runtime_lock_dir = "runtimes/locks"
    site_profiles: dict[str, HpcSiteProfile] = {}
    drivers: dict[str, Any] = {}

    audit = GatewayAudit(audit_path)

    # 1. CPU route (Slurm / IKKEM CPU workload)
    cpu_target = compute_profile.routes.get("cpu", "")
    if cluster_profile_path and Path(cluster_profile_path).is_file():
        slurm_stack = build_slurm_stack(
            cluster_profile_path=cluster_profile_path,
            case_id=case_id,
            audit_path=audit_path,
            token_ttl_sec=token_ttl_sec,
            workload="cpu",
            qualification_root=qualification_root,
            trust_store=trust_store,
        )
        slurm_site = slurm_stack["site_profile"]
        site_profiles[slurm_site.site_id] = slurm_site
        if cpu_target:
            site_profiles[cpu_target] = slurm_site
        drivers["slurm"] = SlurmDriver(slurm_stack["adapter"])
    elif cpu_site_profile_path and Path(cpu_site_profile_path).is_file():
        raw_cpu = json.loads(Path(cpu_site_profile_path).read_text(encoding="utf-8"))
        cpu_site = HpcSiteProfile.from_dict(raw_cpu)
        site_profiles[cpu_site.site_id] = cpu_site
        if cpu_target:
            site_profiles[cpu_target] = cpu_site

    # 2. GPU route (CompShare GPU workload)
    cli = compshare_cli or CompShareCli()
    gpu_site: HpcSiteProfile | None = None

    gpu_target = compute_profile.routes.get("gpu", "compshare-gpu")
    if gpu_site_profile_path and Path(gpu_site_profile_path).is_file():
        raw_gpu = json.loads(Path(gpu_site_profile_path).read_text(encoding="utf-8"))
        gpu_site = HpcSiteProfile.from_dict(raw_gpu)
        site_profiles[gpu_site.site_id] = gpu_site
        if gpu_target:
            site_profiles[gpu_target] = gpu_site

    profile_state_root = (
        gpu_site.compshare_state_root if gpu_site is not None else None
    )
    resolved_state_root = (
        compshare_state_root
        if compshare_state_root is not None
        else profile_state_root
    )
    if resolved_state_root is None and compshare_cli is not None:
        resolved_state_root = compshare_workspace_root

    compshare_driver = CompShareDriver(
        cli,
        site_profile=gpu_site,
        audit=audit,
        workspace_root=(
            str(compshare_workspace_root)
            if compshare_workspace_root is not None
            else "/tmp/mlffbench/compshare_jobs"
        ),
        state_root=resolved_state_root,
        production=True,
    )
    report = compshare_driver.manager.reconcile_and_recover()
    if not report.clean:
        raise RuntimeError(
            f"Production recovery failed: dangling or orphan instances remain "
            f"(failed: {report.failed_instances}, dangling: {report.dangling_cloud_instances}); "
            "refusing to start production stack in unverified billing state"
        )
    drivers["compshare"] = compshare_driver

    router = ComputeRouter(compute_profile, site_profiles=site_profiles)
    lock_dir = Path(runtime_lock_dir)
    runtime_catalog = TrustedRuntimeCatalog(
        lock_dir=lock_dir,
        qualification_root=(
            Path(qualification_root)
            if qualification_root is not None
            else lock_dir.parent.parent
        ),
        trust_store=trust_store,
        trusted_site_profiles={
            profile.site_id: profile for profile in site_profiles.values()
        },
        repo_root=repo_root,
    )

    routed = RoutedDriver(
        router, drivers, runtime_resolver=runtime_catalog.to_resolver()
    )

    return {
        "audit": audit,
        "adapter": routed,
        "driver": routed,
        "compute_profile": compute_profile,
        "router": router,
        "run_adapter_config": {
            "adapter": "routed",
            "adapter_instance": routed,
            "token_ttl_sec": token_ttl_sec,
            "runtime_catalog": runtime_catalog,
            "runtime_lock_dir": str(runtime_lock_dir),
            "qualification_root": str(
                qualification_root
                if qualification_root is not None
                else lock_dir.parent.parent
            ),
        },
    }
