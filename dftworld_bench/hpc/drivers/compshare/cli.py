"""Thin subprocess wrapper around the official `compshare` CLI.

Official CLI Protocol Reference:
- GitHub: compshare-cn/compshare-cli/skills/compshare-cli/SKILL.md
- All automation commands use the global `--json` flag before the command group:
  `compshare --json <command> <subcommand> ...`
- Output format is always the standard envelope:
  Success: {"ok": true, "schema_version": "1", "data": {...}}
  Failure: {"ok": false, "schema_version": "1", "error": {"code": "...", "message": "..."}}
- Key commands:
  - `compshare --version`
  - `compshare --json doctor`
  - `compshare --json instance search --region ... --zone ... --gpu ... [--image ...] --available`
  - `compshare --json instance create ... --yes --timeout ...`
  - `compshare --json instance show <instance_id>`
  - `compshare --json instance job submit <instance_id> [--workdir ...] -- <command...>`
  - `compshare --json instance job show <instance_id> <job_id>`
  - `compshare --json instance job logs <instance_id> <job_id>`
  - `compshare --json instance job cancel <instance_id> <job_id>`
  - `compshare --json instance cp <src> <dest>`
  - `compshare --json instance stop <instance_id> --yes`
  - `compshare --json instance delete <instance_id> --yes`
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

PINNED_COMPSHARE_CLI_VERSION = "0.4.1"


class CompShareCliError(Exception):
    """General error executing compshare CLI."""

    def __init__(
        self,
        message: str,
        code: str = "",
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.raw_response = raw_response or {}


class CompShareCliNotFoundError(CompShareCliError):
    """The `compshare` executable was not found in PATH."""


class CompShareCliJsonError(CompShareCliError):
    """The CLI returned invalid or unparseable JSON envelope."""


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
    """Thin wrapper for official `compshare --json ...` commands."""

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

    def _exec(self, subargs: Sequence[str]) -> Any:
        """Execute command with global `--json` flag and unwrap response envelope."""
        argv = [self.cli_bin, "--json"] + list(subargs)
        res = self._runner(argv, self._env)
        if res.returncode != 0 and not res.stdout.strip().startswith("{"):
            raise CompShareCliError(
                f"compshare CLI error (exit {res.returncode}): {res.stderr.strip() or res.stdout.strip()}"
            )
        try:
            envelope = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise CompShareCliJsonError(
                f"Invalid JSON envelope from compshare CLI: {res.stdout[:200]!r}"
            ) from exc

        if not isinstance(envelope, dict):
            raise CompShareCliJsonError(
                f"Expected JSON envelope object, got {type(envelope).__name__}"
            )

        if not envelope.get("ok", False):
            err = envelope.get("error") or {}
            code = err.get("code", "UNKNOWN_ERROR")
            msg = err.get("message", f"compshare error: {err}")
            if any(k in code.upper() for k in ("CAPACITY", "STOCK", "INSUFFICIENT")):
                raise CompShareCliCapacityError(msg, code=code, raw_response=envelope)
            raise CompShareCliError(f"[{code}] {msg}", code=code, raw_response=envelope)

        return envelope.get("data", {})

    def version(self) -> str:
        """Check and record compshare-cli version."""
        res = self._runner([self.cli_bin, "--version"], self._env)
        if res.returncode != 0:
            raise CompShareCliError(f"Failed to check compshare version: {res.stderr}")
        return res.stdout.strip()

    def doctor(self) -> dict[str, Any]:
        """Verify CLI configuration and credential connectivity."""
        return self._exec(["doctor"])

    def instance_search(
        self,
        *,
        region: str = "cn-sh2",
        zone: str = "cn-sh2-02",
        gpu: str = "4090",
        image: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search available specifications and real inventory."""
        args = ["instance", "search", "--region", region, "--zone", zone, "--gpu", gpu, "--available"]
        if image:
            args.extend(["--image", image])
        data = self._exec(args)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "items" in data:
                return data["items"]
            if "instances" in data:
                return data["instances"]
            if "data" in data and isinstance(data["data"], dict):
                return data["data"].get("items") or data["data"].get("instances") or []
            return []
        return []

    def instance_list(
        self,
        *,
        status: str | None = None,
        all: bool = True,
        page_token: str | None = None,
    ) -> list[dict[str, Any]]:
        """List account instances, consuming every provider page.

        A zero-orphan decision must never treat a malformed envelope or an
        unconsumed continuation token as an empty account.  The parser accepts
        the official ``items``/``instances`` shapes and the common nested
        ``data`` form, but rejects every other response.
        """
        args = ["instance", "list"]
        if all:
            args.append("--all")
        if status:
            args.extend(["--status", status])
        if page_token:
            args.extend(["--page-token", page_token])

        result: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()
        while True:
            res = self._exec(args)
            items: Any
            next_token: Any = None
            if isinstance(res, list):
                items = res
            elif isinstance(res, dict):
                if "items" in res:
                    items = res["items"]
                    next_token = (
                        res.get("next_page_token")
                        or res.get("next_token")
                        or res.get("page_token")
                    )
                elif "instances" in res:
                    items = res["instances"]
                    next_token = (
                        res.get("next_page_token")
                        or res.get("next_token")
                        or res.get("page_token")
                    )
                elif isinstance(res.get("data"), dict):
                    nested = res["data"]
                    if "items" in nested:
                        items = nested["items"]
                    elif "instances" in nested:
                        items = nested["instances"]
                    else:
                        raise CompShareCliJsonError(
                            "Malformed instance list data: expected items/instances"
                        )
                    next_token = (
                        nested.get("next_page_token")
                        or nested.get("next_token")
                        or nested.get("page_token")
                        or res.get("next_page_token")
                        or res.get("next_token")
                    )
                else:
                    raise CompShareCliJsonError(
                        "Malformed instance list data: expected items/instances"
                    )
            else:
                raise CompShareCliJsonError(
                    f"Malformed instance list data: {type(res).__name__}"
                )

            if not isinstance(items, list) or any(
                not isinstance(item, dict) for item in items
            ):
                raise CompShareCliJsonError(
                    "Malformed instance list data: items must be a list of objects"
                )
            result.extend(items)
            if next_token in (None, ""):
                return result
            if not isinstance(next_token, str) or next_token in seen_tokens:
                raise CompShareCliJsonError(
                    "Malformed instance list pagination: repeated/invalid page token"
                )
            seen_tokens.add(next_token)
            args = ["instance", "list"]
            if all:
                args.append("--all")
            if status:
                args.extend(["--status", status])
            args.extend(["--page-token", next_token])

    def instance_create(
        self,
        *,
        image: str,
        name: str | None = None,
        remark: str | None = None,
        region: str = "cn-sh2",
        zone: str = "cn-sh2-02",
        gpu: str = "4090",
        count: int = 1,
        cpu: int = 16,
        memory: str = "64GiB",
        disk: str = "100GiB",
        charge: str = "Postpay",
        max_price: float = 20.0,
        timeout: int = 900,
        dry_run: bool = False,
        image_source: str = "platform",
    ) -> dict[str, Any]:
        """Create a single GPU instance with explicit limits and ownership marker."""
        cmd = [
            "instance",
            "create",
            "--region",
            region,
            "--zone",
            zone,
            "--gpu",
            gpu,
            "--count",
            str(count),
            "--cpu",
            str(cpu),
            "--memory",
            memory,
            "--image",
            image,
            "--image-source",
            image_source,
            "--disk",
            disk,
            "--charge",
            charge,
            "--max-count",
            "1",
            "--max-price",
            str(max_price),
            "--yes",
            "--timeout",
            str(timeout),
        ]
        if name:
            cmd.extend(["--name", name])
        if remark:
            cmd.extend(["--remark", remark])
        if dry_run:
            cmd.append("--dry-run")
        return self._exec(cmd)

    def instance_show(self, instance_id: str) -> dict[str, Any]:
        """Query instance status and metadata."""
        data = self._exec(["instance", "show", instance_id])
        if "instance" in data:
            return data["instance"]
        return data

    def wait_instance_ready(
        self,
        instance_id: str,
        *,
        timeout_sec: float = 300.0,
        poll_interval_sec: float = 1.0,
    ) -> dict[str, Any]:
        """Poll until instance status is Running or fail closed."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            info = self.instance_show(instance_id)
            status = str(info.get("status", "")).upper()
            if status == "RUNNING":
                return info
            if status in ("ERROR", "FAILED", "TERMINATED", "DELETED", "STOPPED"):
                raise CompShareCliError(
                    f"Instance {instance_id} entered non-running state: {status}"
                )
            time.sleep(poll_interval_sec)
        raise CompShareCliError(f"Instance {instance_id} timed out waiting for RUNNING")

    def instance_job_submit(
        self,
        instance_id: str,
        command: Sequence[str],
        *,
        cwd: str = "",
        workdir: str = "",
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Submit a remote job on the instance using official --cwd."""
        cmd = ["instance", "job", "submit", instance_id]
        target_dir = cwd or workdir
        if target_dir:
            cmd.extend(["--cwd", target_dir])
        if environment:
            for k, v in sorted(environment.items()):
                cmd.extend(["--env", f"{k}={v}"])
        cmd.append("--")
        cmd.extend(command)
        return self._exec(cmd)

    def instance_job_show(self, instance_id: str, job_id: str) -> dict[str, Any]:
        """Query remote job status."""
        data = self._exec(["instance", "job", "show", instance_id, job_id])
        if "job" in data:
            return data["job"]
        return data

    def instance_job_logs(self, instance_id: str, job_id: str) -> str:
        """Retrieve logs for a remote job."""
        data = self._exec(["instance", "job", "logs", instance_id, job_id])
        if isinstance(data, dict):
            return str(data.get("logs", ""))
        return str(data)

    def instance_job_cancel(
        self, instance_id: str, job_id: str, *, yes: bool = True
    ) -> dict[str, Any]:
        """Cancel a remote job on the instance with explicit confirmation bypass."""
        cmd = ["instance", "job", "cancel", instance_id, job_id]
        if yes:
            cmd.append("--yes")
        return self._exec(cmd)

    def instance_cp(self, instance_id: str, src: str, dest: str) -> dict[str, Any]:
        """Copy files to or from the remote instance.

        Usage:
          Upload: instance_cp(instance_id, local_path, f":{remote_path}")
          Download: instance_cp(instance_id, f":{remote_path}", local_path)
        """
        return self._exec(["instance", "cp", instance_id, src, dest])

    def instance_stop(self, instance_id: str, *, timeout: int = 60) -> dict[str, Any]:
        """Stop an instance with explicit timeout."""
        return self._exec(["instance", "stop", instance_id, "--yes", "--timeout", str(timeout)])

    def instance_delete(self, instance_id: str, *, timeout: int = 60) -> dict[str, Any]:
        """Permanently delete and release an instance with explicit timeout."""
        return self._exec(["instance", "delete", instance_id, "--yes", "--timeout", str(timeout)])


class FakeCompShareCliRunner:
    """Mock runner simulating official `compshare --json ...` CLI subprocess execution."""

    def __init__(self, *, initial_stock: int = 4) -> None:
        self.stock = initial_stock
        self.instances: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.files: dict[str, dict[str, bytes]] = {}  # instance_id -> {remote_path: bytes}
        self._seq = 0

    def __call__(
        self, argv: Sequence[str], env: Mapping[str, str] | None = None
    ) -> CliResult:
        if len(argv) < 2:
            return CliResult(0, f"compshare-cli {PINNED_COMPSHARE_CLI_VERSION}\n", "")

        if "--version" in argv:
            return CliResult(0, f"compshare-cli {PINNED_COMPSHARE_CLI_VERSION}\n", "")

        # Official CLI requires global --json
        if argv[1] != "--json":
            return CliResult(
                1,
                "",
                json.dumps({
                    "ok": False,
                    "schema_version": "1",
                    "error": {"code": "INVALID_ARGUMENT", "message": "Expected global --json option"},
                }),
            )

        cmd = argv[2]
        subcmd = argv[3] if len(argv) > 3 else ""

        def ok_response(data: Any) -> CliResult:
            payload = {"ok": True, "schema_version": "1", "data": data}
            return CliResult(0, json.dumps(payload), "")

        def error_response(code: str, message: str, status: int = 1) -> CliResult:
            payload = {"ok": False, "schema_version": "1", "error": {"code": code, "message": message}}
            return CliResult(status, json.dumps(payload), message)

        if cmd == "doctor":
            return ok_response({
                "profile": "default",
                "auth_ok": True,
                "api_endpoint": "https://api.compshare.cn/v1",
                "account_id": "acc-mock-001",
            })

        if cmd == "instance":
            if subcmd == "search":
                if self.stock <= 0:
                    return ok_response({"instances": []})
                return ok_response({
                    "instances": [{
                        "gpu": "4090",
                        "available_count": self.stock,
                        "price_per_hour": 1.88,
                        "region": "cn-sh2",
                        "zone": "cn-sh2-02",
                    }]
                })

            if subcmd == "list":
                status_filter = None
                if "--status" in argv:
                    idx = argv.index("--status")
                    if idx + 1 < len(argv):
                        status_filter = argv[idx + 1].lower()
                items = []
                for inst in self.instances.values():
                    if status_filter and inst.get("status", "").lower() != status_filter:
                        continue
                    items.append(inst)
                return ok_response({"items": items})

            if subcmd == "create":
                if self.stock <= 0:
                    return error_response("OUT_OF_CAPACITY", "No available RTX 4090 GPU in target zone")
                if "--dry-run" in argv:
                    return ok_response({"dry_run": True, "capacity_available": True})
                self._seq += 1
                inst_id = f"inst-{self._seq:04d}"
                self.stock -= 1
                inst_name = ""
                if "--name" in argv:
                    idx = argv.index("--name")
                    if idx + 1 < len(argv):
                        inst_name = argv[idx + 1]
                inst_remark = ""
                if "--remark" in argv:
                    idx = argv.index("--remark")
                    if idx + 1 < len(argv):
                        inst_remark = argv[idx + 1]

                record = {
                    "id": inst_id,
                    "instance_id": inst_id,
                    "name": inst_name,
                    "remark": inst_remark,
                    "status": "Running",
                    "gpu": "4090",
                    "count": 1,
                    "created_at": time.time(),
                }
                self.instances[inst_id] = record
                self.files[inst_id] = {}
                return ok_response({
                    "instance_id": inst_id,
                    "name": inst_name,
                    "remark": inst_remark,
                    "status": "Running",
                    "request_id": f"req-{self._seq:04d}",
                })

            if subcmd == "show":
                inst_id = argv[4] if len(argv) > 4 else ""
                inst = self.instances.get(inst_id)
                if not inst:
                    return error_response("NOT_FOUND", f"Instance {inst_id} not found")
                return ok_response({"instance": dict(inst)})

            if subcmd == "stop":
                inst_id = argv[4] if len(argv) > 4 else ""
                inst = self.instances.get(inst_id)
                if not inst:
                    return error_response("NOT_FOUND", f"Instance {inst_id} not found")
                inst["status"] = "Stopped"
                return ok_response({"instance_id": inst_id, "status": "Stopped"})

            if subcmd == "delete":
                inst_id = argv[4] if len(argv) > 4 else ""
                if inst_id in self.instances:
                    self.instances[inst_id]["status"] = "Terminated"
                    del self.instances[inst_id]
                    self.stock += 1
                    return ok_response({"instance_id": inst_id, "deleted": True})
                return error_response("NOT_FOUND", f"Instance {inst_id} not found")

            if subcmd == "cp":
                if len(argv) >= 7:
                    inst_id = argv[4]
                    src = argv[5]
                    dest = argv[6]
                else:
                    src = argv[4] if len(argv) > 4 else ""
                    dest = argv[5] if len(argv) > 5 else ""
                    inst_id = ""

                # upload: local -> :remote (or inst_id:remote)
                if dest.startswith(":") or (":" in dest and not dest.startswith("/")):
                    remote_p = dest.lstrip(":")
                    if ":" in remote_p:
                        inst_id, remote_p = remote_p.split(":", 1)
                    inst_files = self.files.setdefault(inst_id, {})
                    if os.path.isfile(src):
                        with open(src, "rb") as fh:
                            inst_files[remote_p] = fh.read()
                    return ok_response({"copied": True, "bytes": len(inst_files.get(remote_p, b""))})

                # download: :remote -> local (or inst_id:remote -> local)
                if src.startswith(":") or (":" in src and not src.startswith("/")):
                    remote_p = src.lstrip(":")
                    if ":" in remote_p:
                        inst_id, remote_p = remote_p.split(":", 1)
                    inst_files = self.files.get(inst_id, {})
                    content = inst_files.get(remote_p, b"mock output content\n")
                    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
                    with open(dest, "wb") as fh:
                        fh.write(content)
                    return ok_response({"copied": True, "bytes": len(content)})
                return ok_response({"copied": True})

            if subcmd == "job":
                job_action = argv[4] if len(argv) > 4 else ""
                inst_id = argv[5] if len(argv) > 5 else ""
                if job_action == "submit":
                    self._seq += 1
                    job_id = f"job-{self._seq:04d}"
                    cmd_idx = argv.index("--") if "--" in argv else -1
                    command = list(argv[cmd_idx + 1:]) if cmd_idx != -1 else []
                    record = {
                        "job_id": job_id,
                        "instance_id": inst_id,
                        "command": command,
                        "status": "COMPLETED",
                        "exit_code": 0,
                        "logs": f"Executed: {' '.join(command)}\nCompleted successfully.\n",
                    }
                    self.jobs[job_id] = record
                    return ok_response({"job_id": job_id, "status": "QUEUED"})

                if job_action == "show":
                    job_id = argv[6] if len(argv) > 6 else ""
                    job = self.jobs.get(job_id)
                    if not job:
                        return error_response("NOT_FOUND", f"Job {job_id} not found")
                    return ok_response({"job": dict(job)})

                if job_action == "logs":
                    job_id = argv[6] if len(argv) > 6 else ""
                    job = self.jobs.get(job_id)
                    if not job:
                        return error_response("NOT_FOUND", f"Job {job_id} not found")
                    return ok_response({"logs": job.get("logs", "")})

                if job_action == "cancel":
                    job_id = argv[6] if len(argv) > 6 else ""
                    job = self.jobs.get(job_id)
                    if not job:
                        return error_response("NOT_FOUND", f"Job {job_id} not found")
                    job["status"] = "CANCELLED"
                    return ok_response({"job_id": job_id, "status": "CANCELLED"})

        return error_response("UNKNOWN_COMMAND", f"Unrecognized CLI command: {argv}")
