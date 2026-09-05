"""Agent adapters: the provider-specific half of the Trusted Harness.

The harness (``dftworld_bench.core.harness``) owns orchestration and infra;
it speaks to an ``AgentAdapter`` and never touches pagent, docker, or a
scheduler.

Candidate Agent Architecture:
- Formal Candidate Engine: ``ClaudeCodeAdapter`` driving Claude Code inside
  the isolated sandbox image (mlffbench-agent-claude-code:v1).
- Legacy Engine: ``PagentAdapter`` retained for backward compatibility with
  historical baseline runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from dftworld_bench.core.model_proxy import ModelGatewayProxy
from dftworld_bench.core.tool_watchdog import ToolWatchdog

class AgentExecutionError(RuntimeError):
    """Raised when the candidate agent process exits with non-zero return code."""

class AgentTimeoutError(RuntimeError):
    """Raised when the candidate agent exceeds walltime budget."""

class AgentProtocolError(RuntimeError):
    """Raised when candidate agent output violates the expected protocol."""

class AgentBudgetExceededError(RuntimeError):
    """Raised when candidate agent exceeds turn or token budget limits."""

AGENT_HOME = "/app"
# 测评只用工作区工具；关掉宿主桥接，避免泄题 / 干扰
BENCH_SANDBOX_TOOLS = (
    "run_command",
    "read_file",
    "write_file",
    "str_replace",
    "list_dir",
)

SYSTEM = f"""\
你是在 Linux 容器里完成计算化学 / 环境任务的 agent。
任务文件根目录是 {AGENT_HOME}。把输出写到指令要求的 {AGENT_HOME}/... 路径。
任务数据已在工作目录中。写完要求的输出文件后即可结束，不要闲聊。
"""

PAGENT_HOME = Path(__file__).resolve().parents[1] / ".pagent"


# -- Event Stream Definitions -----------------------------------------------

@dataclass
class AgentEvent:
    """Base event emitted during an agent execution turn."""
    type: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult(AgentEvent):
    """Signals completion of an agent turn, with optional usage statistics."""
    type: str = "turn_result"
    turn_index: int = 0
    content: str = ""
    usage: dict[str, int | None] = field(default_factory=dict)


@dataclass
class ToolCallBegin(AgentEvent):
    """Signals that a tool call has been initiated by the agent."""
    type: str = "tool_call_begin"
    name: str = ""
    arguments: str = ""
    call_id: str = ""


@dataclass
class ToolResult(AgentEvent):
    """Signals the outcome of a completed tool call."""
    type: str = "tool_result"
    name: str = ""
    content: str = ""
    ok: bool = True
    call_id: str = ""


@dataclass
class TextDelta(AgentEvent):
    """Incremental text produced by the agent."""
    type: str = "text_delta"
    text: str = ""


def parse_claude_code_event(line: str) -> AgentEvent | None:
    """Parse a single JSON line from Claude Code output into an AgentEvent."""
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    evt_type = data.get("type", "")

    if evt_type == "content_block_delta":
        delta = data.get("delta", {})
        if delta.get("type") == "text_delta":
            return TextDelta(text=delta.get("text", ""), raw=data)
    elif evt_type == "text":
        return TextDelta(text=data.get("text", ""), raw=data)

    if evt_type in ("tool_use", "tool_call"):
        return ToolCallBegin(
            name=data.get("name", ""),
            arguments=json.dumps(data.get("input", {})),
            call_id=data.get("id", ""),
            raw=data,
        )
    if evt_type in ("tool_result",):
        return ToolResult(
            name=data.get("name", ""),
            content=str(data.get("content", "")),
            ok=not data.get("is_error", False),
            call_id=data.get("tool_use_id", ""),
            raw=data,
        )

    if "usage" in data or evt_type in ("message_stop", "turn_complete", "result"):
        raw_usage = data.get("usage")
        usage: dict[str, int | None] = {}
        if isinstance(raw_usage, dict):
            in_tok = raw_usage.get("input_tokens")
            out_tok = raw_usage.get("output_tokens")
            total_tok = raw_usage.get("total_tokens")
            if total_tok is None and in_tok is not None and out_tok is not None:
                total_tok = in_tok + out_tok
            usage = {
                "prompt_tokens": in_tok,
                "completion_tokens": out_tok,
                "total_tokens": total_tok,
            }
        elif raw_usage is None and "total_tokens" in data:
            usage = {
                "prompt_tokens": data.get("prompt_tokens"),
                "completion_tokens": data.get("completion_tokens"),
                "total_tokens": data.get("total_tokens"),
            }
        else:
            usage = {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            }
        return TurnResult(
            turn_index=data.get("turn", 0),
            usage=usage,
            raw=data,
        )

    return None


# PAgent has been retired. Claude Code is the sole formal Candidate Agent.
_HAS_PAGENT = False



@runtime_checkable
class AgentAdapter(Protocol):
    """The agent-specific half of the Trusted Harness.

    ``prepare`` packages the case into the candidate workspace; ``start`` opens
    the candidate and drives the agent to completion (the harness enforces the
    per-case timeout); ``stop`` freezes the candidate (writes metainfo); ``close``
    destroys the candidate. ``collect_logs`` returns usage/tool accounting for
    the result record; ``version`` identifies the agent engine.
    """

    async def prepare(self) -> None: ...
    async def start(self, instruction: str) -> None: ...
    async def stop(self, metainfo: dict) -> None: ...
    async def close(self) -> None: ...
    def collect_logs(self) -> dict: ...
    @property
    def version(self) -> str: ...


# -- Sandbox and Workspace Utilities ----------------------------------------

def ensure_image(image: str) -> None:
    """检查镜像是否存在。用 ``docker images`` 列表匹配,兼容 Docker Desktop 29。"""
    tag = image if ":" in image else f"{image}:latest"
    listed = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
    )
    if listed.returncode == 0 and tag in listed.stdout.splitlines():
        return
    probe = subprocess.run(
        ["docker", "image", "inspect", tag],
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        return
    raise SystemExit(
        f"本地没有镜像 {image!r}。先构建：\n"
        f"  cd {Path(__file__).resolve().parents[1] / 'base-env-build'} && bash build.sh"
    )


def _parse_copy_directives(text: str) -> list[tuple[str, list[str]]]:
    """从 Dockerfile 提取 ``(dst, [src, ...])`` 列表（COPY/ADD 指令）。"""
    logical: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        buf = (buf + " " + line) if buf else line
        if not buf.endswith("\\"):
            logical.append(buf)
            buf = ""
    if buf:
        logical.append(buf)

    out: list[tuple[str, list[str]]] = []
    for line in logical:
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(COPY|ADD)\b(.*)$", line)
        if not m:
            continue
        rest = m.group(2).strip()
        if rest.startswith("--"):
            raise ValueError(f"COPY/ADD 不支持选项语法: {line!r}")
        if rest.startswith("["):
            raise ValueError(f"COPY/ADD 不支持 JSON 数组形式: {line!r}")
        tokens = rest.split()
        if len(tokens) < 2:
            raise ValueError(f"COPY/ADD 缺少 src 或 dst: {line!r}")
        if any("'" in t or '"' in t for t in tokens):
            raise ValueError(f"COPY/ADD 不支持带引号/空格路径: {line!r}")
        srcs, dst = tokens[:-1], tokens[-1]
        out.append((dst, srcs))
    return out


def _app_dest(workspace: Path, dst: str) -> Path:
    """把 Dockerfile 的 /app 绝对目标映射到 workspace 内路径；逃逸即报错。"""
    if not dst.startswith("/app"):
        raise ValueError(f"COPY/ADD dst 必须位于 /app 下: {dst!r}")
    rel = dst[len("/app"):].strip().lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError(f"COPY/ADD dst 不能逃逸 /app: {dst!r}")
    dest = workspace
    for p in parts:
        dest /= p
    return dest


def _resolve_copy_src(build_context: Path, src: str) -> Path:
    """解析 COPY src（build context 相对路径）；越界/缺失即报错。"""
    src_path = (build_context / src).resolve()
    ctx = build_context.resolve()
    try:
        src_path.relative_to(ctx)
    except ValueError:
        raise ValueError(f"COPY src 越出 build context: {src!r}")
    if not src_path.exists():
        raise ValueError(f"COPY src 不存在: {src!r}")
    return src_path


def apply_dockerfile_copies(workspace: Path, task_dir: Path) -> None:
    """把任务 Dockerfile 里 COPY 的目标注入 workspace。"""
    dockerfile = _task_dockerfile(task_dir)
    build_context = dockerfile.parent
    for dst, srcs in _parse_copy_directives(dockerfile.read_text(encoding="utf-8")):
        dest = _app_dest(workspace, dst)
        dst_is_dir = dst.endswith("/") or dst == "/app"
        for src in srcs:
            src_path = _resolve_copy_src(build_context, src)
            if src_path.is_dir():
                shutil.copytree(src_path, dest, dirs_exist_ok=True)
            elif dst_is_dir:
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest / src_path.name)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dest)


def _task_dockerfile(task_dir: Path) -> Path:
    """任务 Dockerfile：root 布局（v2 ``public/``）优先，environment/（001-024）回退。"""
    root_df = task_dir / "Dockerfile"
    if root_df.is_file():
        return root_df
    legacy_df = task_dir / "environment" / "Dockerfile"
    if legacy_df.is_file():
        return legacy_df
    raise FileNotFoundError(
        f"{task_dir}: 没有任务 Dockerfile（root 或 environment/ 均无）"
    )


def seed_workspace(workspace: Path, image: str, task_dir: Path) -> None:
    """镜像 ``/app`` 预置文件 + 任务 Dockerfile COPY 数据 → workspace。"""
    workspace.mkdir(parents=True, exist_ok=True)
    for child in workspace.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    run_image = image if ":" in image else f"{image}:latest"
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{workspace}:/out",
            run_image,
            "bash",
            "-lc",
            "shopt -s nullglob dotglob; "
            "for f in /app/*; do cp -a \"$f\" /out/; done",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    apply_dockerfile_copies(workspace, task_dir)


def container_id(sandbox) -> str:
    backend = getattr(sandbox, "backend", sandbox)
    inner = getattr(backend, "inner", backend)
    cid = getattr(inner, "container_id", getattr(sandbox, "container_id", None))
    if not cid:
        raise RuntimeError("sandbox has no docker container id")
    return cid


def docker_exec(
    cid: str, script: str, *, timeout: float | None = None,
    operation_id: str = "", events: Any = None,
) -> subprocess.CompletedProcess:
    """Execute *script* inside a Docker container with process-group isolation.

    Uses ToolWatchdog-style TERM→grace→KILL on walltime expiry instead of
    relying on subprocess.timeout (which only kills the parent process, not
    the docker-init child tree). When *events* (an EventStore) is provided,
    a ``tool_timeout`` event is recorded on expiry for durable audit.
    """
    cmd = ["docker", "exec", cid, "bash", "-lc", script]
    watchdog = ToolWatchdog(walltime_sec=timeout or 300.0)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # new process group (setsid)
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(
            args=cmd, returncode=proc.returncode,
            stdout=stdout, stderr=stderr,
        )
    except subprocess.TimeoutExpired:
        # TERM → grace → KILL the process group (via watchdog pattern)
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=watchdog.grace_sec)
        except subprocess.TimeoutExpired:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()
        # Record the timeout event for durable audit
        if events is not None and operation_id:
            events.append(
                operation_id, "tool_timeout",
                {"timeout_sec": timeout, "killed": True},
            )
        return subprocess.CompletedProcess(
            args=cmd, returncode=-9,
            stdout="", stderr=f"docker_exec timed out after {timeout}s (SIGTERM→SIGKILL)",
        )


def bind_workspace_as_app(sandbox) -> None:
    """容器内 ``/app`` → 宿主 workspace。agent / tests / 配置文件共用这一路径。"""
    cid = container_id(sandbox)
    workdir = getattr(sandbox, "workdir", "/app")
    setup = docker_exec(
        cid,
        f"rm -rf /app && ln -sfn {shlex.quote(workdir)} /app",
        timeout=60,
    )
    if setup.returncode != 0:
        raise RuntimeError(f"bind /app failed: {setup.stderr or setup.stdout}")


def controller_docker_args(execution_class: str) -> list[str]:
    """``docker run`` argv for ``hpc_controller`` execution, else ``[]``."""
    if execution_class != "hpc_controller":
        return []
    return [
        "--add-host", "host.docker.internal:host-gateway",
    ]


def docker_gpu_args(gpus: int) -> list[str]:
    """``docker run`` 显式 GPU 分配参数；0 张 → ``[]``，多卡(>1)不支持。"""
    _patch_container_backend_gpus(gpus)
    if gpus <= 0:
        return []
    if gpus != 1:
        raise ValueError(f"unsupported gpus={gpus}; only 0 or 1 is supported")
    return ["--gpus", "device=0"]


def _patch_container_backend_gpus(gpus: int = 0) -> None:
    """Legacy compatibility helper: docker_gpu_args is the canonical backend."""
    pass


class DeepSeek:
    def __init__(self, model_id: str, base_url: str | None = None, apikey: str | None = None) -> None:
        self.model_id = model_id
        self.base_url = base_url
        self.apikey = apikey


def make_provider(model: str, *, base_url: str | None = None, api_key: str | None = None) -> Any:
    """Legacy provider factory helper for test wiring compatibility."""
    provider, _, model_id = model.partition("/")
    if provider == "deepseek":
        return DeepSeek(model_id, base_url=base_url, apikey=api_key)
    raise ValueError(f"unsupported legacy provider: {provider}")


_CURRENT_RUN_ARGS: dict[str, list[str]] = {"extra": []}
_CURRENT_TASK: dict[str, str] = {
    "name": "",
    "execution_class": "local_sandbox",
}


# PAgent runner execution paths removed. All benchmark runs use ClaudeCodeAdapter.



def count_tool_calls(thread_dir: str) -> int:
    """统计一次任务全程的工具调用次数。口径 = ``total_tool_calls``。"""
    path = Path(thread_dir) / "messages.jsonl"
    if not path.is_file():
        return 0
    n = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            content = row.get("content")
            if isinstance(content, dict) and content.get("type") == "function":
                n += 1
            elif isinstance(content, list):
                n += sum(
                    1 for p in content if isinstance(p, dict) and p.get("type") == "function"
                )
    except Exception:
        return 0
    return n


def collect_skill_invocations(thread_dir: str) -> list[str]:
    """从 messages.jsonl 提取实际调用过的 skill 名(去重、按出现顺序)。"""
    path = Path(thread_dir) / "messages.jsonl"
    if not path.is_file():
        return []
    invoked: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            content = row.get("content")
            entries = content if isinstance(content, list) else [content]
            for p in entries:
                if not isinstance(p, dict) or p.get("type") != "function":
                    continue
                if p.get("name") != "use_skill":
                    continue
                try:
                    args = json.loads(p.get("arguments") or "{}")
                    name = str(args.get("name") or "").strip()
                except (json.JSONDecodeError, AttributeError):
                    name = ""
                if name and name not in invoked:
                    invoked.append(name)
    except Exception:
        return []
    return invoked


# Re-export for read-only legacy deserialization compatibility
from dftworld_bench.legacy.pagent_compat import PagentAdapter  # noqa: F401


# -- Claude Code Candidate Agent Adapter ------------------------------------

class ClaudeCodeAdapter:
    """Candidate Agent Adapter that drives Claude Code inside an isolated sandbox."""

    def __init__(
        self,
        *,
        model: str,
        max_turns: int = 128,
        max_total_tokens: int = 50_000_000,
        threads_root: Path,
        task_name: str,
        image: str = "mlffbench-agent-claude-code:v1",
        case_dir: Path,
        gpus: int = 0,
        agent_timeout_sec: float = 600.0,
        skill_roots: tuple[Path, ...] = (),
        verbose: bool = False,
        container_env: dict[str, str] | None = None,
        forbidden_env_names: frozenset[str] = frozenset(),
        execution_class: str = "local_sandbox",
        api_endpoint: str | None = None,
        api_key: str | None = None,
        engine_version: str = "claude-code-v1",
    ) -> None:
        self.model = model
        self.max_turns = max_turns
        self.max_total_tokens = max_total_tokens
        self.threads_root = Path(threads_root)
        self.task_name = task_name
        self.image = image
        self.case_dir = Path(case_dir)
        self.gpus = gpus
        self.agent_timeout_sec = agent_timeout_sec
        self.skill_roots = tuple(skill_roots)
        self.verbose = verbose
        self.execution_class = execution_class
        self.api_endpoint = api_endpoint
        self._api_key = api_key
        self._engine_version = engine_version

        incoming = dict(container_env) if container_env else {}
        leaked = sorted(set(incoming) & set(forbidden_env_names))
        if leaked:
            raise ValueError(
                "candidate env would receive trusted API secrets: " + ", ".join(leaked)
            )
        self.container_env = incoming

        self.thread_dir = str(self.threads_root / task_name)
        self.workspace = str(self.threads_root / task_name / "workspace")
        self.container_id: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._budget_ledger: Any = None
        self._turn_index: int = 0
        self._tool_calls: int = 0
        self._skills_invoked: list[str] = []
        self._usage: dict[str, int | None] = {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
        self._model_proxy: ModelGatewayProxy | None = None

    def attach_budget_ledger(self, ledger: Any) -> None:
        self._budget_ledger = ledger

    @property
    def version(self) -> str:
        return f"{self._engine_version}:{self.model}"

    async def prepare(self) -> None:
        ensure_image(self.image)
        seed_workspace(Path(self.workspace), self.image, self.case_dir)

    async def start(self, instruction: str) -> None:
        ws_path = Path(self.workspace)
        ws_path.mkdir(parents=True, exist_ok=True)
        thread_path = Path(self.thread_dir)
        thread_path.mkdir(parents=True, exist_ok=True)

        # 0. Quarantine check: workspace must NOT contain pre-existing .claude settings/config
        ws_claude = ws_path / ".claude"
        if ws_claude.exists():
            for p in ws_claude.rglob("settings*.json"):
                raise ValueError(f"Security Violation: workspace contains unauthorized injected .claude settings: {p}")
            for p in ws_claude.rglob("config*.json"):
                raise ValueError(f"Security Violation: workspace contains unauthorized injected .claude config: {p}")

        # 1. Prepare host-side trusted settings and skills (to be mounted read-only :ro)
        trusted_claude_dir = thread_path / "trusted_claude"
        trusted_claude_dir.mkdir(parents=True, exist_ok=True)
        settings_file = trusted_claude_dir / "settings.json"
        settings_file.write_text(
            json.dumps(self._get_tool_policy_settings(), indent=2), encoding="utf-8"
        )

        trusted_skills_dir = thread_path / "trusted_skills"
        trusted_skills_dir.mkdir(parents=True, exist_ok=True)
        for root in self.skill_roots:
            if root.exists() and (root / "SKILL.md").exists():
                dest = trusted_skills_dir / root.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(root, dest)

        # 2. Start host-side ModelGatewayProxy for trusted credential isolation
        def _on_budget_exceeded():
            if self._proc is not None:
                self._terminate_proc_group(self._proc, 2.0)

        proxy_url: str | None = None
        ephemeral_token: str | None = None
        if self._api_key or self.api_endpoint:
            self._model_proxy = ModelGatewayProxy(
                real_api_endpoint=self.api_endpoint or "https://api.anthropic.com",
                real_api_key=self._api_key or "",
                budget_ledger=self._budget_ledger,
                max_model_turns=self.max_turns,
                max_total_tokens=self.max_total_tokens,
                task_name=self.task_name,
                on_budget_exceeded=_on_budget_exceeded,
            )
            await self._model_proxy.start()
            proxy_url = self._model_proxy.proxy_url("host.docker.internal")
            ephemeral_token = self._model_proxy.ephemeral_token

        # 3. Launch hardened container with read-only security overlays
        cid = await self._start_container(
            proxy_url, ephemeral_token, settings_file, trusted_skills_dir
        )
        self.container_id = cid

        # 4. Execute Claude Code CLI with fail-closed monitoring
        try:
            await self._run_claude_code(instruction)
        finally:
            self._finalize_stats()

    async def _start_container(
        self,
        proxy_url: str | None,
        ephemeral_token: str | None,
        trusted_settings: Path,
        trusted_skills_dir: Path,
    ) -> str:
        cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            # Strict container security hardening (P1 boundary)
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit", "256",
            "--memory", "4g",
            "--cpus", "4",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=512m",
            "-v", f"{self.workspace}:/app",
            "-w", "/app",
            "--add-host", "host.docker.internal:host-gateway",
            # Network egress isolation: prevent direct internet access; only allow host-gateway
            "--dns", "127.0.0.1",
            "--env", "http_proxy=http://127.0.0.1:9",
            "--env", "https_proxy=http://127.0.0.1:9",
            "--env", "all_proxy=http://127.0.0.1:9",
            "--env", "no_proxy=host.docker.internal",
        ]
        # Read-only mounts for trusted security policy and skills
        cmd.extend(["-v", f"{trusted_settings.resolve()}:/app/.claude/settings.json:ro"])
        cmd.extend(["-v", f"{trusted_settings.resolve()}:/home/agent/.claude/settings.json:ro"])
        if any(trusted_skills_dir.iterdir()):
            cmd.extend(["-v", f"{trusted_skills_dir.resolve()}:/app/.claude/skills:ro"])

        _patch_container_backend_gpus(self.gpus)
        cmd.extend(docker_gpu_args(self.gpus))
        cmd.extend(controller_docker_args(self.execution_class))

        for k, v in self.container_env.items():
            cmd.extend(["--env", f"{k}={v}"])

        # Inject ONLY the host model proxy and run-scoped ephemeral token (P0 credential isolation)
        if proxy_url:
            cmd.extend(["--env", f"ANTHROPIC_BASE_URL={proxy_url}"])
        if ephemeral_token:
            cmd.extend(["--env", f"ANTHROPIC_API_KEY={ephemeral_token}"])

        cmd.extend([self.image, "sleep", str(int(self.agent_timeout_sec) + 120)])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"failed to start candidate container: {stderr.decode('utf-8', errors='replace').strip()}"
            )
        cid = stdout.decode("utf-8", errors="replace").strip()
        return cid

    def _get_tool_policy_settings(self) -> dict[str, Any]:
        """Tool policy permissions loaded into container via read-only mount."""
        return {
            "permissions": {
                "allow": [
                    "Bash",
                    "FileRead",
                    "FileEdit",
                    "FileWrite",
                    "GlobTool",
                    "GrepTool",
                    "DirectoryList",
                ],
                "deny": [
                    "WebSearch",
                    "Browser",
                ],
            }
        }

    async def _install_skills_to_claude_dir(self) -> None:
        if not self.skill_roots:
            return
        claude_skills_dir = Path(self.workspace) / ".claude" / "skills"
        claude_skills_dir.mkdir(parents=True, exist_ok=True)

        for root in self.skill_roots:
            if not root.exists():
                continue
            if (root / "SKILL.md").exists():
                dest = claude_skills_dir / root.name
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(root, dest)
            else:
                for sub in root.iterdir():
                    if sub.is_dir() and (sub / "SKILL.md").exists():
                        dest = claude_skills_dir / sub.name
                        if dest.exists():
                            shutil.rmtree(dest)
                        shutil.copytree(sub, dest)

    async def _run_claude_code(self, instruction: str) -> None:
        assert self.container_id is not None
        messages_path = Path(self.thread_dir) / "messages.jsonl"
        log_file = open(messages_path, "w", encoding="utf-8")

        cli_argv = [
            "docker",
            "exec",
            "-i",
            "-w",
            "/app",
            self.container_id,
            "claude",
            "-p",
            instruction,
            "--output-format",
            "json",
        ]

        watchdog = ToolWatchdog(walltime_sec=self.agent_timeout_sec)
        proc = await asyncio.create_subprocess_exec(
            *cli_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._proc = proc

        try:
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.agent_timeout_sec,
                )
            except asyncio.TimeoutError:
                self._terminate_proc_group(proc, watchdog.grace_sec)
                raise AgentTimeoutError(f"Candidate agent timed out after {self.agent_timeout_sec}s")

            raw_out = stdout_bytes.decode("utf-8", errors="replace")
            raw_err = stderr_bytes.decode("utf-8", errors="replace")
            self._process_claude_output(raw_out, raw_err, log_file)

            # Strict Fail-Closed checks (P0)
            if self._model_proxy and self._model_proxy.budget_exceeded:
                raise AgentBudgetExceededError(f"Candidate agent exceeded budget limits in task {self.task_name}")

            if proc.returncode != 0:
                raise AgentExecutionError(
                    f"Candidate agent CLI exited with code {proc.returncode}: {raw_err.strip() or raw_out.strip()}"
                )

        finally:
            log_file.close()

    def _terminate_proc_group(self, proc: asyncio.subprocess.Process, grace_sec: float) -> None:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        import time
        start = time.time()
        while time.time() - start < grace_sec:
            if proc.returncode is not None:
                return
            time.sleep(0.1)
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def _process_claude_output(self, stdout: str, stderr: str, log_file: Any) -> None:
        parsed_any = False
        for line in stdout.splitlines():
            line_str = line.strip()
            if not line_str:
                continue
            event = parse_claude_code_event(line_str)
            if event is not None:
                parsed_any = True
                self._handle_event(event)
                log_file.write(json.dumps(event.raw or {"type": event.type}) + "\n")

        if not parsed_any and stdout.strip():
            try:
                data = json.loads(stdout.strip())
                if isinstance(data, dict):
                    log_file.write(json.dumps(data) + "\n")
                    self._parse_full_json_result(data)
            except json.JSONDecodeError:
                log_file.write(json.dumps({"type": "raw_output", "content": stdout}) + "\n")

        if stderr.strip():
            log_file.write(json.dumps({"type": "stderr", "content": stderr}) + "\n")

    def _handle_event(self, event: AgentEvent) -> None:
        if isinstance(event, ToolCallBegin):
            self._tool_calls += 1
            if event.name == "use_skill" or "skill" in event.name:
                try:
                    args = json.loads(event.arguments)
                    skill_name = args.get("name") or args.get("skill")
                    if skill_name and skill_name not in self._skills_invoked:
                        self._skills_invoked.append(skill_name)
                except Exception:
                    pass
            if self.verbose:
                print(f"  tool → {event.name}({event.arguments[:100]}…)")

        elif isinstance(event, TurnResult):
            self._turn_index += 1
            # ModelGatewayProxy is the sole owner of budget ledger charging;
            # Adapter records observation telemetry only to eliminate double-charging.
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                val = event.usage.get(k)
                if val is not None:
                    curr = self._usage[k]
                    self._usage[k] = (curr or 0) + val

        elif isinstance(event, TextDelta) and self.verbose:
            sys.stdout.write(event.text)
            sys.stdout.flush()

    def _parse_full_json_result(self, data: dict[str, Any]) -> None:
        tools = data.get("tool_calls") or data.get("tools")
        if isinstance(tools, list):
            self._tool_calls += len(tools)
            for t in tools:
                if isinstance(t, dict):
                    name = t.get("name", "")
                    if name in ("use_skill", "hpc-submit") and name not in self._skills_invoked:
                        self._skills_invoked.append(name)

        usage = data.get("usage")
        if isinstance(usage, dict):
            self._usage = {
                "prompt_tokens": usage.get("input_tokens"),
                "completion_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens") or (
                    (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
                    if usage.get("input_tokens") is not None
                    else None
                ),
            }
        else:
            self._usage = {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
            }

    def _finalize_stats(self) -> None:
        msg_file = Path(self.thread_dir) / "messages.jsonl"
        if not msg_file.is_file():
            return
        if self._tool_calls == 0 or not self._skills_invoked:
            try:
                for line in msg_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    typ = row.get("type", "")
                    if typ in ("tool_use", "tool_call"):
                        if self._tool_calls == 0:
                            self._tool_calls += 1
                        name = row.get("name", "")
                        if "skill" in name and name not in self._skills_invoked:
                            self._skills_invoked.append(name)
            except Exception:
                pass

    async def stop(self, metainfo: dict[str, Any]) -> None:
        thread_path = Path(self.thread_dir)
        thread_path.mkdir(parents=True, exist_ok=True)
        meta_file = thread_path / "metainfo.json"
        meta_file.write_text(json.dumps(metainfo, indent=2, ensure_ascii=False), encoding="utf-8")

    async def close(self) -> None:
        if self._model_proxy:
            await self._model_proxy.close()
            self._model_proxy = None
        if self.container_id:
            cid = self.container_id
            self.container_id = None
            proc = await asyncio.create_subprocess_exec(
                "docker", "stop", cid,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()

    def collect_logs(self) -> dict[str, Any]:
        return {
            "thread_dir": self.thread_dir,
            "workspace": self.workspace,
            "tool_calls": self._tool_calls,
            "tokens": self._usage["total_tokens"],
            "skills_invoked": list(self._skills_invoked),
            "usage": dict(self._usage),
            "engine": "claude-code",
            "engine_version": self.version,
        }
