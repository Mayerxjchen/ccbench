"""Thin subprocess wrapper around the official `compshare` CLI.

Architecture:
    CompShareDriver
         ↓ subprocess
    compshare ... --json

Invariants:
- All CLI interactions communicate via JSON output (--json flag).
- Non-zero return codes, non-JSON output, and connection timeouts fail closed.
- Subprocess execution is abstracted through an injectable runner for deterministic testing.
- Sensitive credentials or tokens are never logged or exposed.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

logger = logging.getLogger(__name__)


class CompShareCliError(Exception):
    """General error executing compshare CLI."""


class CompShareCliNotFoundError(CompShareCliError):
    """The `compshare` executable was not found in PATH."""


class CompShareCliJsonError(CompShareCliError):
    """The CLI returned invalid or unparseable JSON."""


class CompShareCliCapacityError(CompShareCliError):
    """Requested GPU resources are unavailable in the target region."""


@dataclass
class CliResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str], Mapping[str, str] | None], CliResult]


def default_subprocess_runner(
    argv: Sequence[str], env: Mapping[str, str] | None = None
) -> CliResult:
    """Default runner using standard subprocess.run."""
    try:
        res = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            env=dict(env) if env is not None else None,
            check=False,
            timeout=120,
        )
        return CliResult(res.returncode, res.stdout, res.stderr)
    except subprocess.TimeoutExpired as exc:
        raise CompShareCliError(f"CLI command timed out: {argv}") from exc
    except FileNotFoundError as exc:
        raise CompShareCliNotFoundError(f"CLI binary not found: {argv[0]}") from exc


class CompShareCli:
    """Thin wrapper for `compshare ... --json` commands."""

    def __init__(
        self,
        *,
        cli_bin: str = "compshare",
        runner: CommandRunner | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.cli_bin = cli_bin
        self._runner = runner or default_subprocess_runner
        self._env = dict(env) if env is not None else None

    def _exec(self, subargs: Sequence[str], *, expect_json: bool = True) -> Any:
        argv = [self.cli_bin] + list(subargs)
        res = self._runner(argv, self._env)
        if res.returncode != 0:
            raise CompShareCliError(
                f"compshare CLI error (exit {res.returncode}): {res.stderr.strip() or res.stdout.strip()}"
            )
        if not expect_json:
            return res.stdout
        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise CompShareCliJsonError(
                f"Invalid JSON from compshare CLI: {res.stdout[:200]!r}"
            ) from exc

    def version(self) -> str:
        """Check and record compshare-cli version."""
        res = self._runner([self.cli_bin, "--version"], self._env)
        if res.returncode != 0:
            raise CompShareCliError(f"Failed to check compshare version: {res.stderr}")
        return res.stdout.strip()

    def check_auth(self) -> dict[str, Any]:
        """Verify authentication status."""
        return self._exec(["auth", "status", "--json"])

    def check_stock(self, gpu_type: str = "rtx4090", count: int = 1) -> bool:
        """Query real-time stock and pricing."""
        stocks = self._exec(["stock", "list", "--json"])
        if isinstance(stocks, list):
            for item in stocks:
                if item.get("gpu_type", "").lower() == gpu_type.lower():
                    available = int(item.get("available_count", 0))
                    return available >= count
        elif isinstance(stocks, dict):
            available = int(stocks.get(gpu_type, {}).get("available_count", 0))
            return available >= count
        return False

    def create_instance(
        self,
        *,
        name: str,
        image_id: str,
        gpu_type: str = "rtx4090",
        count: int = 1,
        auto_shutdown_minutes: int = 120,
    ) -> dict[str, Any]:
        """Create a single GPU instance with auto-shutdown guard."""
        cmd = [
            "instance",
            "create",
            "--name",
            name,
            "--image",
            image_id,
            "--gpu",
            gpu_type,
            "--count",
            str(count),
            "--auto-shutdown",
            str(auto_shutdown_minutes),
            "--json",
        ]
        return self._exec(cmd)

    def get_instance(self, instance_id: str) -> dict[str, Any]:
        """Query instance status."""
        return self._exec(["instance", "get", instance_id, "--json"])

    def wait_instance_ready(
        self,
        instance_id: str,
        *,
        timeout_sec: float = 300.0,
        poll_interval_sec: float = 1.0,
    ) -> dict[str, Any]:
        """Poll until instance is RUNNING or fail closed."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            info = self.get_instance(instance_id)
            status = str(info.get("status", "")).upper()
            if status == "RUNNING":
                return info
            if status in ("ERROR", "FAILED", "TERMINATED"):
                raise CompShareCliError(
                    f"Instance {instance_id} entered failed state: {status}"
                )
            time.sleep(poll_interval_sec)
        raise CompShareCliError(f"Instance {instance_id} timed out waiting for RUNNING")

    def run_task(
        self,
        instance_id: str,
        command: Sequence[str],
        *,
        workdir: str = "",
        environment: Mapping[str, str] | None = None,
    ) -> str:
        """Submit a remote task using the CLI remote task runner."""
        cmd = ["task", "run", "--instance", instance_id]
        if workdir:
            cmd.extend(["--workdir", workdir])
        if environment:
            for k, v in sorted(environment.items()):
                cmd.extend(["--env", f"{k}={v}"])
        cmd.append("--json")
        cmd.append("--")
        cmd.extend(command)
        out = self._exec(cmd)
        task_id = out.get("task_id") or out.get("execution_id")
        if not task_id:
            raise CompShareCliError(f"Task submission returned no task_id: {out}")
        return str(task_id)

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        """Query remote task execution status."""
        return self._exec(["task", "status", task_id, "--json"])

    def get_task_logs(self, task_id: str) -> str:
        """Retrieve stdout/stderr logs for a task."""
        res = self._exec(["task", "logs", task_id, "--json"])
        if isinstance(res, dict):
            return res.get("logs", "")
        return str(res)

    def upload_file(self, instance_id: str, local_path: str, remote_path: str) -> None:
        """Upload a file to the remote instance workspace."""
        self._exec(
            ["file", "upload", "--instance", instance_id, local_path, remote_path, "--json"]
        )

    def download_file(self, instance_id: str, remote_path: str, local_path: str) -> None:
        """Download a file from the remote instance workspace."""
        self._exec(
            ["file", "download", "--instance", instance_id, remote_path, local_path, "--json"]
        )

    def stop_instance(self, instance_id: str) -> bool:
        """Stop an instance."""
        res = self._exec(["instance", "stop", instance_id, "--json"])
        return bool(res.get("success", True))

    def terminate_instance(self, instance_id: str) -> bool:
        """Permanently delete and release an instance."""
        res = self._exec(["instance", "delete", instance_id, "--force", "--json"])
        return bool(res.get("success", True))


class FakeCompShareCliRunner:
    """Mock runner simulating `compshare ... --json` CLI subprocess execution."""

    def __init__(self, *, initial_stock: int = 4) -> None:
        self.stock = initial_stock
        self.instances: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.files: dict[str, dict[str, bytes]] = {}  # instance_id -> {remote_path: bytes}
        self._seq = 0

    def __call__(
        self, argv: Sequence[str], env: Mapping[str, str] | None = None
    ) -> CliResult:
        if len(argv) < 2:
            return CliResult(0, "compshare-cli 1.4.2\n", "")

        cmd = argv[1]
        subcmd = argv[2] if len(argv) > 2 else ""

        if "--version" in argv:
            return CliResult(0, "compshare-cli 1.4.2\n", "")

        if cmd == "auth" and subcmd == "status":
            return CliResult(0, json.dumps({"authenticated": True, "account": "maintainer"}), "")

        if cmd == "stock" and subcmd == "list":
            data = [{"gpu_type": "rtx4090", "available_count": self.stock, "hourly_rate_usd": 1.80}]
            return CliResult(0, json.dumps(data), "")

        if cmd == "instance" and subcmd == "create":
            if self.stock < 1:
                return CliResult(1, "", "CapacityExhausted: No RTX 4090 instances available")
            self._seq += 1
            inst_id = f"inst-{self._seq:04d}"
            name = argv[argv.index("--name") + 1] if "--name" in argv else f"inst-{self._seq}"
            image = argv[argv.index("--image") + 1] if "--image" in argv else "img-default"
            gpu = argv[argv.index("--gpu") + 1] if "--gpu" in argv else "rtx4090"
            rec = {
                "instance_id": inst_id,
                "name": name,
                "image_id": image,
                "gpu_type": gpu,
                "gpu_count": 1,
                "status": "RUNNING",
            }
            self.instances[inst_id] = rec
            self.files[inst_id] = {}
            self.stock -= 1
            return CliResult(0, json.dumps(rec), "")

        if cmd == "instance" and subcmd == "get":
            inst_id = argv[3]
            inst = self.instances.get(inst_id)
            if not inst:
                return CliResult(1, "", f"NotFound: Instance {inst_id} does not exist")
            return CliResult(0, json.dumps(inst), "")

        if cmd == "instance" and subcmd == "stop":
            inst_id = argv[3]
            if inst_id in self.instances:
                self.instances[inst_id]["status"] = "STOPPED"
            return CliResult(0, json.dumps({"success": True, "status": "STOPPED"}), "")

        if cmd == "instance" and subcmd == "delete":
            inst_id = argv[3]
            if inst_id in self.instances:
                self.instances[inst_id]["status"] = "TERMINATED"
                self.stock += 1
            return CliResult(0, json.dumps({"success": True, "status": "TERMINATED"}), "")

        if cmd == "task" and subcmd == "run":
            inst_id = argv[argv.index("--instance") + 1]
            idx_dash = argv.index("--") if "--" in argv else len(argv)
            command = argv[idx_dash + 1 :]
            self._seq += 1
            task_id = f"task-{self._seq:04d}"
            self.tasks[task_id] = {
                "task_id": task_id,
                "instance_id": inst_id,
                "command": list(command),
                "status": "COMPLETED",
                "exit_code": 0,
                "logs": f"Executed: {command}\nSuccess.\n",
            }
            return CliResult(0, json.dumps({"task_id": task_id, "status": "QUEUED"}), "")

        if cmd == "task" and subcmd == "status":
            task_id = argv[3]
            task = self.tasks.get(task_id, {"status": "COMPLETED", "exit_code": 0})
            return CliResult(0, json.dumps(task), "")

        if cmd == "task" and subcmd == "logs":
            task_id = argv[3]
            task = self.tasks.get(task_id, {})
            logs = task.get("logs", "Completed.\n")
            return CliResult(0, json.dumps({"logs": logs}), "")

        if cmd == "file" and subcmd == "upload":
            idx = argv.index("--instance")
            inst_id = argv[idx + 1]
            local_p = argv[idx + 2]
            remote_p = argv[idx + 3]
            if os.path.exists(local_p):
                with open(local_p, "rb") as f:
                    self.files.setdefault(inst_id, {})[remote_p] = f.read()
            return CliResult(0, json.dumps({"success": True}), "")

        if cmd == "file" and subcmd == "download":
            idx = argv.index("--instance")
            inst_id = argv[idx + 1]
            remote_p = argv[idx + 2]
            local_p = argv[idx + 3]
            data = self.files.get(inst_id, {}).get(remote_p, b"dummy output\n")
            os.makedirs(os.path.dirname(os.path.abspath(local_p)), exist_ok=True)
            with open(local_p, "wb") as f:
                f.write(data)
            return CliResult(0, json.dumps({"success": True}), "")

        return CliResult(1, "", f"Unknown command: {argv}")
