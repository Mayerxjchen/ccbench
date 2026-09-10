"""``hpc_controller`` execution: the local agent is only a control layer.

The controller image ships no SSH client, rsync, key, or config — scheduler
access lives behind the trusted :class:`HpcDispatcher`.  The executor receives
its dispatcher as an injected dependency (composition root: ``eval.py``, which
resolves the SiteProfile/credential and builds the runtime stack); it never
constructs a Gateway, adapter, or transport itself.

``prepare`` opens one dispatcher session for the run, serves that session's
gateway over the common ``HttpGatewayServer``, and hands the controller
container its run-scoped URL + bearer token via ``BENCH_HPC_GATEWAY_URL`` /
``BENCH_HPC_RUN_TOKEN``, pinned to zero local GPUs — the GPU lives on the
remote job.

The executor is case-agnostic: it reads the run id and workspace from
``ExecutionContext`` and never derives anything from ``task.name``.
"""

from __future__ import annotations

from bench.executors.base import ExecutionContext
from bench.hpc.dispatcher import DispatcherSession, HpcDispatcher

# The container env names the controller reads to reach its run's gateway.
_GATEWAY_URL_ENV = "BENCH_HPC_GATEWAY_URL"
_RUN_TOKEN_ENV = "BENCH_HPC_RUN_TOKEN"


class HpcExecutor:
    """Execution for ``hpc_controller`` cases through the injected dispatcher.

    ``prepare`` opens a run session, starts the loopback HTTP server around
    its gateway, and fills ``container_env``/``local_gpus``.  ``close`` tears
    the server down and closes the session (revoking the token), idempotently.
    """

    def __init__(self, *, dispatcher: HpcDispatcher) -> None:
        if dispatcher is None:
            raise ValueError(
                "HpcExecutor requires an injected HpcDispatcher; production "
                "modules never construct Gateway/adapter stacks themselves"
            )
        self._dispatcher = dispatcher

    async def prepare(self, context: ExecutionContext) -> None:
        if not context.run_id:
            raise ValueError(
                "HpcExecutor.prepare requires context.run_id; it is never "
                "derived from the case name"
            )
        workspace = context.workspace
        if workspace is None:
            workspace = context.threads_root / context.task.name / "workspace"
        adapter_config = getattr(context, "adapter_config", None)
        if adapter_config is None:
            raise ValueError(
                "HpcExecutor.prepare requires context.adapter_config "
                "(e.g. {'adapter': 'process_test', 'root': ...}); the "
                "executor never guesses an adapter"
            )
        session = self._dispatcher.open_run(
            context.run_id, workspace=workspace, adapter_config=dict(adapter_config)
        )
        server = session.serve()  # started; loopback URL on server.url
        host_url = server.url
        # The controller runs inside a Docker bridge container where 127.0.0.1
        # is the container itself; host.docker.internal reaches the host loopback
        # (Docker Desktop: automatic; Linux: --add-host host-gateway).
        container_url = host_url.replace("127.0.0.1", "host.docker.internal")
        context.gateway_lease = getattr(session, "_lease", None)
        context.gateway_session = session
        context.gateway_server = server
        context.workspace = workspace
        context.container_env = {
            _GATEWAY_URL_ENV: container_url,
            _RUN_TOKEN_ENV: session.token,
        }
        # The GPU lives on the remote job, never on the controller container.
        context.local_gpus = 0

    async def execute(self, context: ExecutionContext) -> None:
        pass

    async def settle(self, context: ExecutionContext) -> None:
        session: DispatcherSession | None = getattr(context, "gateway_session", None)
        if session is not None:
            session.settle()

    async def close(self, context: ExecutionContext) -> None:
        server = getattr(context, "gateway_server", None)
        if server is not None:
            server.close()
            context.gateway_server = None
        session = getattr(context, "gateway_session", None)
        if session is not None:
            session.close()  # revokes the run token
            context.gateway_session = None
        context.gateway_lease = None
