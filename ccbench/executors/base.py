"""Case-agnostic execution contract.

A case declares ``[execution] class`` in its manifest; the registry resolves
that class to an :class:`Executor` that owns the runtime-side decisions
(gateway issuance, local GPU, controller mounts).  No executor ever dispatches
on a case name or id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class ExecutionContext:
    """One attempt's execution inputs; executors fill the runtime fields.

    ``task`` is the eval-side ``TaskSpec`` (name, image, gpus, execution_class).
    ``container_env`` and ``local_gpus`` are the runtime decisions the executor
    makes so the harness never needs to know the case's name.  The generic
    ``run_id``/``workspace``/``adapter_config``/``gateway_*`` fields are filled
    by the common GatewayRuntime path and are never derived from ``task.name``.
    """

    task: Any
    threads_root: Path
    model: str
    max_turns: int
    verbose: bool = False
    container_env: dict[str, str] = field(default_factory=dict)
    local_gpus: int = 0
    resources: list[Any] = field(default_factory=list)
    # Generic execution fields for the common GatewayRuntime path (Task 16).
    # None of these is inferred from the case name or id.
    run_id: str = ""
    workspace: Path | None = None
    runtime_set: Any = None
    adapter_config: dict[str, Any] | None = None
    gateway_lease: Any = None
    gateway_server: Any = None


@runtime_checkable
class Executor(Protocol):
    """One execution class's runtime lifecycle.

    - ``prepare``: bring up runtime prerequisites (e.g. the host-side trusted
      gateway for ``hpc_controller``) and fill ``context.container_env`` /
      ``context.local_gpus``.
    - ``execute``: the execution step itself, when a driver owns it.
    - ``settle``: finalize after a successful drive.
    - ``close``: idempotent teardown (gateway shutdown etc.).
    """

    async def prepare(self, context: ExecutionContext) -> None: ...
    async def execute(self, context: ExecutionContext) -> None: ...
    async def settle(self, context: ExecutionContext) -> None: ...
    async def close(self, context: ExecutionContext) -> None: ...
