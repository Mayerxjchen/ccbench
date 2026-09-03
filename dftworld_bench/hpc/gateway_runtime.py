"""Per-run gateway lifecycle: one lease per run.

``GatewayRuntime.start(run_id, adapter_config)`` builds the adapter and the
trusted gateway for one benchmark run, issues a run-scoped token, and returns a
:class:`GatewayLease`. Closing the lease revokes the token and tears down the
per-run networks idempotently.

The per-run Docker networks (Candidate on an ``--internal`` network, gateway on
that plus an egress-capable one) are owned by the runtime. The default factory
returns the two network names without touching Docker, so the runtime is fully
testable in CI; a real site supplies a factory that creates the networks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from dftworld_bench.hpc.adapters.process_test import ProcessTestAdapter
from dftworld_bench.hpc.audit import GatewayAudit
from dftworld_bench.hpc.gateway import ALL_OPS, Gateway
from dftworld_bench.hpc.runtime_resolution import RuntimeResolver

NetworksFactory = Callable[[str], tuple[str, str]]


class GatewayRuntimeError(Exception):
    """A lease could not be created or torn down."""


def default_networks(run_id: str) -> tuple[str, str]:
    """Deterministic network names; actual Docker wiring is site-owned."""
    return (f"bench-hpc-{run_id}-candidate", f"bench-hpc-{run_id}-gateway")


def build_adapter(adapter_config: dict[str, Any]):
    """Instantiate an adapter from a config dict (registry is minimal by design).

    Real-site adapters (slurm) cannot be invented from a dict — the trusted
    composition supplies the fully constructed instance, which this factory
    passes through unchanged.
    """
    kind = adapter_config["adapter"]
    if kind == "process_test":
        return ProcessTestAdapter(
            Path(adapter_config["root"]),
            timeout_sec=float(adapter_config.get("timeout_sec", 30.0)),
        )
    if kind == "slurm":
        instance = adapter_config.get("adapter_instance")
        if instance is None:
            raise GatewayRuntimeError(
                "slurm adapter requires an 'adapter_instance' (site config "
                "and transport are trusted-harness responsibilities)"
            )
        return instance
    raise GatewayRuntimeError(f"unknown adapter {kind!r}")


def build_driver(adapter_config: dict[str, Any]):
    """Instantiate the driver boundary for a config dict.

    The driver wraps (never replaces) the adapter; slurm sites supply their
    adapter through ``adapter_instance`` because SlurmAdapter needs a live
    site config and transport that no registry should invent.
    """
    from dftworld_bench.hpc.drivers.process import ProcessDriver
    from dftworld_bench.hpc.drivers.slurm import SlurmDriver

    kind = adapter_config["adapter"]
    if kind == "process_test":
        return ProcessDriver(build_adapter(adapter_config))
    if kind == "slurm":
        instance = adapter_config.get("adapter_instance")
        if instance is None:
            raise GatewayRuntimeError(
                "slurm driver requires an 'adapter_instance' (site config + "
                "transport are trusted-harness responsibilities)"
            )
        return SlurmDriver(instance)
    raise GatewayRuntimeError(f"unknown adapter {kind!r}")


@dataclass
class GatewayLease:
    """One run's capability: token + gateway + network names."""

    run_id: str
    token: str
    gateway: Gateway
    networks: tuple[str, str] = ()
    closed: bool = False

    def close(self) -> None:
        """Revoke the token and drop the networks; idempotent."""
        if self.closed:
            return
        self.gateway.revoke(self.token)
        self.networks = ()
        self.closed = True


class GatewayRuntime:
    """Issues and tracks one lease per run."""

    def __init__(
        self,
        *,
        networks_factory: NetworksFactory | None = None,
        quota: dict[str, Any] | None = None,
        audit: GatewayAudit | None = None,
    ) -> None:
        self._networks = networks_factory or default_networks
        self._quota = quota
        self._audit = audit
        self._leases: dict[str, GatewayLease] = {}

    def start(self, run_id: str, adapter_config: dict[str, Any]) -> GatewayLease:
        adapter = build_adapter(adapter_config)
        workspace_root = adapter_config.get("workspace_root")
        # Trusted composition: the site supplies the runtime lock directory;
        # the resolver turns Agent capability tokens into locked runtimes and
        # gives adapters the digest -> SIF path map (Architecture Freeze §3).
        resolver: RuntimeResolver | None = None
        lock_dir = adapter_config.get("runtime_lock_dir")
        if lock_dir:
            resolver = RuntimeResolver.from_lock_dir(Path(lock_dir))
            attach = getattr(adapter, "set_runtime_store", None)
            if attach is not None:
                attach(resolver.runtime_store())
        gateway = Gateway(
            adapter,
            quota=self._quota,
            audit=self._audit,
            workspace_root=Path(workspace_root) if workspace_root else None,
            runtime_resolver=resolver,
        )
        token = gateway.issue(run_id, ALL_OPS, ttl_sec=float(adapter_config.get("token_ttl_sec", 300.0)))
        lease = GatewayLease(
            run_id=run_id,
            token=token,
            gateway=gateway,
            networks=self._networks(run_id),
        )
        previous = self._leases.pop(run_id, None)
        if previous is not None and not previous.closed:
            previous.close()  # replace, never leak an old lease's token
        self._leases[run_id] = lease
        return lease
