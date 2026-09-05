"""Historical PAgent read-only compatibility and log decoder.

PAgent has been completely retired from MLFFBench active benchmarks.
This module is strictly preserved for offline decoding and inspection of historical runs.
Execution of PAgent is strictly prohibited.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class HistoricalPagentDecoder:
    """Read-only decoder for historical PAgent messages.jsonl and run records."""

    @staticmethod
    def decode_messages(messages_path: Path) -> list[dict[str, Any]]:
        """Parse raw lines from historical messages.jsonl."""
        if not messages_path.is_file():
            return []
        events: list[dict[str, Any]] = []
        for line in messages_path.read_text(encoding="utf-8").splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            try:
                events.append(json.loads(line_str))
            except json.JSONDecodeError:
                pass
        return events

    @staticmethod
    def extract_usage(thread_dir: Path) -> dict[str, int]:
        """Extract usage totals from historical thread directory."""
        meta_file = thread_dir / "metainfo.json"
        if meta_file.is_file():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                usage = data.get("usage", {})
                return {
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "total_tokens": int(usage.get("total_tokens") or 0),
                }
            except Exception:
                pass
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class PagentAdapter:
    """Non-executable legacy shim. Execution is strictly prohibited."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.model = kwargs.get("model", "legacy-pagent")
        self.task_name = kwargs.get("task_name", "legacy")
        self.thread_dir = str(kwargs.get("threads_root", "/tmp"))
        self._budget_ledger: Any = None
        container_env = kwargs.get("container_env") or {}
        forbidden = kwargs.get("forbidden_env_names") or frozenset()
        leaked = set(container_env).intersection(forbidden)
        if leaked:
            raise ValueError(f"trusted API secrets cannot be injected: {leaked}")
        self.container_env = dict(container_env)

    def attach_budget_ledger(self, ledger: Any) -> None:
        self._budget_ledger = ledger

    async def _consume(self, instruction: str) -> None:
        runner = getattr(self, "runner", None)
        if runner is not None and hasattr(runner, "run"):
            turn_idx = 0
            async for turn in runner.run(instruction):
                turn_idx += 1
                if self._budget_ledger is not None:
                    self._budget_ledger.charge("model_turns", 1, f"turn-{turn_idx}")
                    usage = getattr(turn, "usage", {}) or {}
                    self._budget_ledger.charge("tokens", usage.get("total_tokens", 0), f"tokens-{turn_idx}")

    async def prepare(self) -> None:
        raise RuntimeError(
            "PAgent execution is strictly retired from MLFFBench. "
            "Claude Code is the sole formal Candidate Agent."
        )

    async def start(self, instruction: str) -> None:
        raise RuntimeError(
            "PAgent execution is strictly retired from MLFFBench. "
            "Claude Code is the sole formal Candidate Agent."
        )

    async def stop(self, metainfo: dict[str, Any]) -> None:
        pass

    async def close(self) -> None:
        pass

    def collect_logs(self) -> dict[str, Any]:
        return {"engine": "legacy-pagent-retired", "executable": False}

    @property
    def version(self) -> str:
        return "pagent-retired"
