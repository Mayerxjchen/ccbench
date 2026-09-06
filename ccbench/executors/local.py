"""Local Docker sandbox execution (the default class)."""

from __future__ import annotations

from ccbench.executors.base import ExecutionContext


class LocalExecutor:
    """Default execution: the agent container uses the task's own GPU
    allowance; nothing extra to prepare or tear down."""

    async def prepare(self, context: ExecutionContext) -> None:
        context.local_gpus = int(getattr(context.task, "gpus", 0) or 0)

    async def execute(self, context: ExecutionContext) -> None:
        pass

    async def settle(self, context: ExecutionContext) -> None:
        pass

    async def close(self, context: ExecutionContext) -> None:
        pass
