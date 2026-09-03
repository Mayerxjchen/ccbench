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
from dftworld_bench.hpc.site_profile import HpcSiteProfile


def build_slurm_stack(
    *,
    cluster_profile_path: Path,
    case_id: str,
    audit_path: Path,
    token_ttl_sec: float = 7200.0,
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
    resolved = site.resolve_workload("gpu")

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
    resource_profile = {
        "name": f"{resolved.partition}-{resolved.max_gpus}gpu",
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
    return {
        "audit": GatewayAudit(audit_path),
        "adapter": adapter,
        "site_profile": site,
        "run_adapter_config": {
            "adapter": "slurm",
            "adapter_instance": adapter,
            "token_ttl_sec": token_ttl_sec,
            # P1 boundary: GatewayRuntime composes the RuntimeResolver from
            # the locked runtime directory, wires the gateway (capability ->
            # sealed digest) and attaches the digest -> SIF store to the
            # adapter.  Agents submit capability tokens; digests stay here.
            "runtime_lock_dir": site.runtime_policy.get(
                "runtime_lock_dir", "reference/runtime"
            ),
        },
    }
