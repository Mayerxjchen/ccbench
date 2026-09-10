"""Claude Code Candidate adapter and tool policy.

The pilot owns run state and this module owns the Candidate container,
sidecar and model-gateway lifecycle.  A sealed result is evaluated by the
separate verifier worker; no legacy experiment harness is involved.

Candidate Agent Architecture:
- Sole Formal Candidate Engine: ``ClaudeCodeAdapter`` driving Claude Code inside
  the isolated sandbox image (bench-agent-claude-code:2.1.266).
- Host Model Gateway Proxy provides run-scoped credential isolation and real-time streaming budget accounting.
- Docker internal network ensures OS-level physical network egress isolation.
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

from bench.core.model_proxy import ModelGatewayProxy
from bench.core.sidecar_topology import (
    CANDIDATE_ROLE_LABEL,
    SidecarTopologyManager,
    validate_resource_run_uid,
)
from bench.core.tool_watchdog import ToolWatchdog

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
CONTAINER_TOOL_ALLOWLIST = frozenset({"Bash", "Read", "Write", "Edit", "Glob", "Grep"})
# Claude's JSONL stream is diagnostic data, not an unbounded log sink.  Keep a
# generous per-stream cap while ensuring a runaway agent cannot exhaust the
# pilot host before the wall-clock watchdog fires.
MAX_CLAUDE_STREAM_BYTES = 4 * 1024 * 1024
MAX_CLAUDE_TOTAL_OUTPUT_BYTES = 8 * 1024 * 1024

SYSTEM = f"""\
你是在 Linux 容器里完成计算化学 / 环境任务的 agent。
任务文件根目录是 {AGENT_HOME}。把输出写到指令要求的 {AGENT_HOME}/... 路径。
任务数据已在工作目录中。写完要求的输出文件后即可结束，不要闲聊。
"""


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


@dataclass(frozen=True)
class ClaudeModelRoute:
    """The three identities involved in custom Claude-compatible routing.

    Claude Code validates the value passed to ``--model`` locally.  A custom
    gateway ID therefore cannot be passed there verbatim.  Claude Code sees a
    supported model while the trusted proxy validates that client identity and
    rewrites it to the opaque ``upstream_model``.
    """

    requested_model: str
    cli_model: str
    upstream_model: str
    context_selector: str | None = None


_KNOWN_CLAUDE_MODEL_RE = re.compile(
    r"^(?:claude[-_].+|(?:sonnet|opus|haiku)(?:\[[^\]]+\])?)$",
    re.IGNORECASE,
)
DEFAULT_CLAUDE_CLI_MODEL = "claude-sonnet-4-6"
_MODEL_CONTEXT_RE = re.compile(r"^(?P<base>.+)\[(?P<context>[^\]]+)\]$")


def resolve_claude_model_route(model: str) -> ClaudeModelRoute:
    """Resolve a requested ID without registry fallback or lossy rewriting.

    Official Claude IDs/aliases can be passed directly.  Any other ID is
    treated as an opaque upstream gateway model and routed through a pinned
    Claude model ID.  Bracketed suffixes (for example ``[1M]``) are
    recorded as a context selector but retained byte-for-byte upstream.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    requested = model
    match = _MODEL_CONTEXT_RE.match(requested)
    context = match.group("context") if match else None
    cli_model = requested if _KNOWN_CLAUDE_MODEL_RE.match(requested) else DEFAULT_CLAUDE_CLI_MODEL
    return ClaudeModelRoute(
        requested_model=requested,
        cli_model=cli_model,
        upstream_model=requested,
        context_selector=context,
    )


def build_claude_code_cli_argv(
    model: str,
    *,
    instruction: str,
    tools: tuple[str, ...],
    effort: str,
    max_budget_usd: float = 0.0,
    session_id: str | None = None,
    resume_session: bool = False,
) -> list[str]:
    """Build the constrained in-container Claude command.

    ``model`` is the CLI-facing model.  Use :func:`resolve_claude_model_route`
    before calling this helper when the requested ID is custom.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    if not tools:
        raise ValueError("at least one Claude tool must be allowlisted")
    argv = [
        "claude", "-p", instruction, "--output-format", "json",
        "--bare", "--restricted", "--strict-mcp-config",
        "--permission-prompts", "none", "--disable-slash-commands",
        # ``--tools`` controls what the model may request; ``--allowedTools``
        # controls what Claude may actually execute.  Keep both explicit so a
        # permissive user/project settings file cannot widen this surface.
        "--tools", ",".join(tools), "--allowedTools", ",".join(tools),
        "--disallowedTools", "WebSearch,Browser,WebFetch", "--model", model,
        "--effort", effort,
    ]
    if max_budget_usd > 0:
        argv.extend(["--max-budget-usd", str(max_budget_usd)])
    if session_id:
        argv.extend(["--resume", session_id] if resume_session else ["--session-id", session_id])
    return argv



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

def ensure_image(image: str, expected_digest: str | None = None) -> str:
    """Inspect an image and return an immutable runnable reference.

    ``.Id`` is a local content ID (``sha256:<id>``); registry digests are
    ``repo@sha256:<digest>`` and are not interchangeable.  A qualified
    registry reference is therefore matched against ``RepoDigests`` while a
    local ID is matched against ``.Id``.
    """
    tag = image if ":" in image else f"{image}:latest"
    inspect_res = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}\n{{range .RepoDigests}}{{.}}\n{{end}}", tag],
        capture_output=True,
        text=True,
    )
    if inspect_res.returncode != 0:
        from bench.paths import RUNTIME_RECIPES_DIR
        raise RuntimeError(
            f"本地没有镜像 {image!r}。Docker image inspect failed: {inspect_res.stderr.strip()}。\n"
            f"先构建：cd {RUNTIME_RECIPES_DIR} && bash build.sh"
        )
    values = [line.strip() for line in inspect_res.stdout.splitlines() if line.strip()]
    actual_id = values[0] if values else ""
    repo_digests = set(values[1:])
    if not actual_id.startswith("sha256:"):
        raise RuntimeError(f"Docker inspect returned no local Image ID for {image!r}")
    if expected_digest:
        if expected_digest.startswith("sha256:"):
            matches = expected_digest == actual_id or any(
                ref.rsplit("@", 1)[-1] == expected_digest for ref in repo_digests
            )
        else:
            matches = expected_digest in repo_digests
        if not matches:
            raise RuntimeError(
                f"Docker live image digest mismatch for {image!r}: "
                f"live ID={actual_id}, RepoDigests={sorted(repo_digests)}, expected={expected_digest}. "
                "The image tag may have been retagged or rebuilt after qualification."
            )
        if expected_digest == actual_id:
            return actual_id
        for ref in sorted(repo_digests):
            if ref.rsplit("@", 1)[-1] == expected_digest or ref == expected_digest:
                return ref
        # This branch is unreachable after the match check, but retaining a
        # fail-closed guard makes future inspect-format changes explicit.
        raise RuntimeError("qualified image digest has no immutable runnable reference")
    return actual_id


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
    if gpus <= 0:
        return []
    if gpus != 1:
        raise ValueError(f"unsupported gpus={gpus}; only 0 or 1 is supported")
    return ["--gpus", "device=0"]


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
        image: str = "bench-agent-claude-code:2.1.266",
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
        api_auth_mode: str = "x-api-key",
        engine_version: str = "claude-code-v1",
        expected_image_digest: str | None = None,
        workspace: Path | None = None,
        session_id: str | None = None,
        resume_session: bool = False,
        tools: tuple[str, ...] | None = None,
        effort: str = "medium",
        max_budget_usd: float = 0.0,
        preserve_workspace: bool = False,
        sidecar_image: str | None = None,
        sidecar_expected_image_digest: str | None = None,
        cli_model: str | None = None,
        preflight_timeout_sec: float = 20.0,
        upstream_response_timeout_sec: float = 300.0,
        max_output_bytes: int = MAX_CLAUDE_STREAM_BYTES,
        resource_run_uid: str | None = None,
    ) -> None:
        self.model = model  # requested/upstream model identity
        self.model_route = resolve_claude_model_route(model)
        if cli_model is not None:
            if not isinstance(cli_model, str) or not re.fullmatch(r"claude[-_].+", cli_model, re.I):
                raise ValueError("cli_model must be a pinned full Claude model ID")
            self.model_route = ClaudeModelRoute(
                requested_model=self.model_route.requested_model,
                cli_model=cli_model,
                upstream_model=self.model_route.upstream_model,
                context_selector=self.model_route.context_selector,
            )
        self.cli_model = self.model_route.cli_model
        self.upstream_model = self.model_route.upstream_model
        self.max_turns = max_turns
        self.max_total_tokens = max_total_tokens
        self.threads_root = Path(threads_root)
        self.task_name = task_name
        self.image = image
        self.expected_image_digest = expected_image_digest
        self.case_dir = Path(case_dir)
        self.gpus = gpus
        self.agent_timeout_sec = agent_timeout_sec
        self.skill_roots = tuple(skill_roots)
        self.verbose = verbose
        self.execution_class = execution_class
        self.api_endpoint = api_endpoint
        self._api_key = api_key
        if api_auth_mode not in {"x-api-key", "bearer"}:
            raise ValueError("api_auth_mode must be x-api-key or bearer")
        self.api_auth_mode = api_auth_mode
        self._engine_version = engine_version
        self.session_id = session_id
        self.resume_session = resume_session
        self.tools = tuple(tools or ("Bash", "Read", "Write", "Edit", "Glob", "Grep"))
        if not self.tools or any(tool not in CONTAINER_TOOL_ALLOWLIST for tool in self.tools):
            raise ValueError(
                "candidate tools must be a subset of the frozen container allowlist: "
                + ", ".join(sorted(CONTAINER_TOOL_ALLOWLIST))
            )
        self.effort = effort
        self.max_budget_usd = max_budget_usd
        self.preserve_workspace = preserve_workspace
        self.sidecar_image = sidecar_image
        self.sidecar_expected_image_digest = sidecar_expected_image_digest
        self.actual_sidecar_image_digest: str | None = None
        if preflight_timeout_sec <= 0:
            raise ValueError("preflight_timeout_sec must be positive")
        self.preflight_timeout_sec = float(preflight_timeout_sec)
        if upstream_response_timeout_sec <= 0:
            raise ValueError("upstream_response_timeout_sec must be positive")
        self.upstream_response_timeout_sec = float(upstream_response_timeout_sec)
        if max_output_bytes < 1024:
            raise ValueError("max_output_bytes must be at least 1024")
        self.max_output_bytes = int(max_output_bytes)
        self.resource_run_uid = (
            validate_resource_run_uid(resource_run_uid)
            if resource_run_uid is not None else None
        )

        incoming = dict(container_env) if container_env else {}
        leaked = sorted(set(incoming) & set(forbidden_env_names))
        if leaked:
            raise ValueError(
                "candidate env would receive trusted API secrets: " + ", ".join(leaked)
            )
        self.container_env = incoming

        self.thread_dir = str(self.threads_root / task_name)
        self.workspace = str(workspace or (self.threads_root / task_name / "workspace"))
        self.container_id: str | None = None
        self.internal_net: str | None = None
        self.sidecar_cid: str | None = None
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
        self._preflight_result: dict[str, str | int] | None = None
        self._topology: SidecarTopologyManager | None = None
        # ``close()`` tears down the proxy and clears Docker handles.  Keep a
        # safe telemetry snapshot so callers (notably eval's Ctrl-C/finally
        # path) can still publish diagnostics after teardown.
        self._closed_logs: dict[str, Any] | None = None

    def attach_budget_ledger(self, ledger: Any) -> None:
        self._budget_ledger = ledger

    @property
    def version(self) -> str:
        return f"{self._engine_version}:{self.model}"

    async def prepare(self) -> None:
        self.actual_image_digest = ensure_image(self.image, self.expected_image_digest)
        # From this point onward docker run/exec use the inspected immutable
        # reference rather than the mutable user tag.
        self.image = self.actual_image_digest
        workspace = Path(self.workspace)
        # The pilot has already exported and digest-locked the public case.
        # Never let image seeding erase that bundle. Generic harness callers
        # retain the old seed behavior by leaving preserve_workspace=False.
        if not self.preserve_workspace:
            seed_workspace(workspace, self.image, self.case_dir)
        else:
            workspace.mkdir(parents=True, exist_ok=True)
            if not (workspace / "instruction.md").is_file():
                raise RuntimeError("preserved Candidate workspace lacks instruction.md")

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

        ephemeral_token: str | None = None
        host_port: int | None = None
        if self._api_key or self.api_endpoint:
            self._model_proxy = ModelGatewayProxy(
                real_api_endpoint=self.api_endpoint or "https://api.anthropic.com",
                real_api_key=self._api_key or "",
                budget_ledger=self._budget_ledger,
                max_model_turns=self.max_turns,
                max_total_tokens=self.max_total_tokens,
                task_name=self.task_name,
                expected_client_model=self.cli_model,
                upstream_model=self.upstream_model,
                upstream_auth_mode=self.api_auth_mode,
                on_budget_exceeded=_on_budget_exceeded,
                upstream_response_timeout_sec=self.upstream_response_timeout_sec,
                on_upstream_timeout=_on_budget_exceeded,
            )
            host_port = await self._model_proxy.start()
            ephemeral_token = self._model_proxy.ephemeral_token
            self._preflight_result = await self._model_proxy.preflight(
                timeout_sec=self.preflight_timeout_sec
            )
            if self._preflight_result.get("classification") != "ok":
                raise RuntimeError(
                    "model gateway preflight failed: "
                    + json.dumps(self._preflight_result, sort_keys=True)
                )

        # 3. Create OS-level internal docker network & optional dual-homed sidecar gateway
        if self.sidecar_image:
            self.actual_sidecar_image_digest = ensure_image(
                self.sidecar_image, self.sidecar_expected_image_digest
            )
            self.sidecar_image = self.actual_sidecar_image_digest
        self._topology = SidecarTopologyManager(
            image=self.image,
            sidecar_image=self.sidecar_image,
            run_uid=self.resource_run_uid,
        )
        candidate_base_url: str | None = None
        try:
            self.internal_net = await self._topology.create_network()
            if host_port is not None:
                self.sidecar_cid = await self._topology.start_sidecar(host_port=host_port)
                candidate_base_url = "http://model-gateway:8080"

            # 4. Launch hardened Candidate container attached ONLY to the internal network
            cid = await self._start_container(
                candidate_base_url, ephemeral_token, settings_file, trusted_skills_dir,
                network_name=self.internal_net,
            )
            self.container_id = cid
            self._topology.register_candidate(cid)
            await self._topology.verify_isolation()
        except Exception:
            # Fail-closed atomic rollback on ANY startup failure
            try:
                if self._topology:
                    try:
                        await self._topology.rollback()
                    except Exception:
                        # Rollback failed: DO NOT discard _topology!
                        # Retain self._topology and container/network handles
                        # so that agent.close() or external reconciler can retry cleanup.
                        self.container_id = self._topology.candidate_cid
                        self.sidecar_cid = self._topology.sidecar_cid
                        self.internal_net = self._topology.network_name
                        raise
                    else:
                        self._topology = None
                        self.container_id = None
                        self.sidecar_cid = None
                        self.internal_net = None
            finally:
                if self._model_proxy:
                    try:
                        await self._model_proxy.close()
                    finally:
                        self._model_proxy = None
            raise

        # 5. Execute Claude Code CLI with fail-closed monitoring
        try:
            await self._run_claude_code(instruction)
        finally:
            self._finalize_stats()

    async def _start_container(
        self,
        candidate_base_url: str | None,
        ephemeral_token: str | None,
        trusted_settings: Path,
        trusted_skills_dir: Path,
        network_name: str | None = None,
    ) -> str:
        cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            # Strict container security hardening (P1 boundary)
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user", "10001:10001",
            "--pids-limit", "256",
            "--memory", "4g",
            "--cpus", "4",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=512m",
            "--tmpfs", "/home/agent:rw,nosuid,size=256m",
            "--read-only",
            # Ignore any image-provided entrypoint.  The candidate lifecycle
            # must start from the known sleep contract before docker exec
            # invokes Claude, even when a local tag was retagged.
            "--entrypoint", "sleep",
            "-v", f"{self.workspace}:/app",
            "-w", "/app",
            "--label", CANDIDATE_ROLE_LABEL,
        ]
        if self._topology:
            cmd.extend(["--label", f"bench.run_id={self._topology.run_uid}"])
        if network_name:
            cmd.extend(["--network", network_name])

        # Read-only mounts for trusted security policy and skills.  Never mount
        # into /app: Docker creates a host-side `.claude` path when the
        # destination is absent, which would become persistent workspace state
        # and make a subsequent resume look like prompt injection.  Claude is
        # explicitly pointed at the private tmpfs HOME instead.
        cmd.extend(["--env", "HOME=/home/agent"])
        cmd.extend(["--env", "CLAUDE_CONFIG_DIR=/home/agent/.claude"])
        cmd.extend(["-v", f"{trusted_settings.resolve()}:/home/agent/.claude/settings.json:ro"])
        if any(trusted_skills_dir.iterdir()):
            cmd.extend(["-v", f"{trusted_skills_dir.resolve()}:/home/agent/.claude/skills:ro"])

        cmd.extend(docker_gpu_args(self.gpus))
        cmd.extend(controller_docker_args(self.execution_class))

        for k, v in self.container_env.items():
            cmd.extend(["--env", f"{k}={v}"])

        # Inject ONLY the internal sidecar model gateway and run-scoped ephemeral token (P0 credential isolation)
        if candidate_base_url:
            cmd.extend(["--env", f"ANTHROPIC_BASE_URL={candidate_base_url}"])
        if ephemeral_token:
            cmd.extend(["--env", f"ANTHROPIC_API_KEY={ephemeral_token}"])

        cmd.extend([self.image, str(int(self.agent_timeout_sec) + 120)])

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
                "allow": list(self.tools),
                "deny": [
                    "WebSearch",
                    "Browser",
                    "WebFetch",
                    "mcp__*",
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

        # End Docker option parsing before the container identifier.  This
        # prevents an identifier beginning with a dash from being interpreted
        # as an exec option and keeps the command contract unambiguous.
        cli_argv = ["docker", "exec", "-i", "-w", "/app", "--", self.container_id]
        cli_argv.extend(build_claude_code_cli_argv(
            self.cli_model,
            instruction=instruction,
            tools=self.tools,
            effort=self.effort,
            max_budget_usd=self.max_budget_usd,
            session_id=self.session_id,
            resume_session=self.resume_session,
        ))

        watchdog = ToolWatchdog(walltime_sec=self.agent_timeout_sec)
        proc = await asyncio.create_subprocess_exec(
            *cli_argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        self._proc = proc

        try:
            async def capture_stream(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
                data = bytearray()
                exceeded = False
                while True:
                    chunk = await stream.read(min(64 * 1024, self.max_output_bytes - len(data) + 1))
                    if not chunk:
                        break
                    remaining = self.max_output_bytes - len(data)
                    data.extend(chunk[: max(0, remaining)])
                    if len(chunk) > remaining:
                        exceeded = True
                        break
                return bytes(data), exceeded

            async def capture_output() -> tuple[bytes, bytes, bool]:
                tasks = [
                    asyncio.create_task(capture_stream(proc.stdout)),  # type: ignore[arg-type]
                    asyncio.create_task(capture_stream(proc.stderr)),  # type: ignore[arg-type]
                ]
                try:
                    done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    exceeded = any(task.result()[1] for task in done)
                    if exceeded:
                        # Stop the complete process group as soon as either
                        # pipe crosses its cap; otherwise the unread pipe can
                        # deadlock the child while we wait for it to exit.
                        self._terminate_proc_group(proc, watchdog.grace_sec)
                    results = await asyncio.gather(*tasks, return_exceptions=False)
                    # EOF on both pipes does not itself guarantee that the
                    # docker exec process has reaped; make returncode
                    # deterministic before protocol/error classification.
                    await proc.wait()
                    output = results[0][0], results[1][0]
                    exceeded = exceeded or sum(len(part) for part in output) > MAX_CLAUDE_TOTAL_OUTPUT_BYTES
                    return output[0], output[1], exceeded
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()

            try:
                stdout_bytes, stderr_bytes, output_exceeded = await asyncio.wait_for(
                    capture_output(),
                    timeout=self.agent_timeout_sec,
                )
            except asyncio.TimeoutError:
                self._terminate_proc_group(proc, watchdog.grace_sec)
                raise AgentTimeoutError(f"Candidate agent timed out after {self.agent_timeout_sec}s")

            raw_out = stdout_bytes.decode("utf-8", errors="replace")
            raw_err = stderr_bytes.decode("utf-8", errors="replace")
            self._process_claude_output(raw_out, raw_err, log_file)

            # capture_output is bounded and terminates the complete process
            # group on overflow.  Surface this as an agent failure even if the
            # truncated prefix happened to contain a valid JSONL completion.
            if output_exceeded:
                raise AgentExecutionError(
                    f"Candidate agent output exceeded {self.max_output_bytes} bytes per stream"
                )

            # Strict Fail-Closed checks (P0)
            if self._model_proxy and self._model_proxy.budget_exceeded:
                raise AgentBudgetExceededError(f"Candidate agent exceeded budget limits in task {self.task_name}")

            if proc.returncode != 0:
                gateway_detail = ""
                if self._model_proxy is not None:
                    gateway_detail = (
                        f" [gateway path={self._model_proxy.last_request_path!r}"
                        f" status={self._model_proxy.last_upstream_status!r}"
                        f" client_model={self._model_proxy.last_client_model!r}"
                        f" upstream_model={self._model_proxy.last_upstream_model!r}]"
                    )
                raise AgentExecutionError(
                    f"Candidate agent CLI exited with code {proc.returncode}: "
                    f"{raw_err.strip() or raw_out.strip()}{gateway_detail}"
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
                    if name in ("use_skill", "bench-compute-request") and name not in self._skills_invoked:
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
        errors: list[str] = []

        # Stop docker-exec first. Otherwise an in-flight Claude process can
        # keep the Candidate container and proxy connection alive while the
        # rest of teardown waits on them.
        if self._proc is not None and self._proc.returncode is None:
            try:
                pgid = os.getpgid(self._proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    os.killpg(pgid, signal.SIGKILL)
                    await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except (ProcessLookupError, PermissionError):
                pass
            except Exception as e:
                errors.append(f"candidate process close: {e}")

        if self._closed_logs is None:
            try:
                self._closed_logs = self.collect_logs()
            except Exception:
                self._closed_logs = None

        if self._topology:
            try:
                await self._topology.close()
            except Exception as e:
                errors.append(f"topology close: {e}")
            finally:
                if self._topology.is_closed:
                    self._topology = None
                    self.container_id = None
                    self.sidecar_cid = None
                    self.internal_net = None
                else:
                    self.container_id = self._topology.candidate_cid
                    self.sidecar_cid = self._topology.sidecar_cid
                    self.internal_net = self._topology.network_name
        else:
            # Fallback direct cleanup if topology manager was bypassed
            if self.container_id:
                cid = self.container_id
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "docker", "stop", cid,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, err = await proc.communicate()
                    if proc.returncode == 0 or b"No such container" in err:
                        self.container_id = None
                    else:
                        errors.append(f"docker stop candidate failed: {err.decode('utf-8', errors='replace').strip()}")
                except Exception as e:
                    errors.append(f"docker stop candidate exception: {e}")

            if self.sidecar_cid:
                scid = self.sidecar_cid
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "docker", "stop", scid,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, err = await proc.communicate()
                    if proc.returncode == 0 or b"No such container" in err:
                        self.sidecar_cid = None
                    else:
                        errors.append(f"docker stop sidecar failed: {err.decode('utf-8', errors='replace').strip()}")
                except Exception as e:
                    errors.append(f"docker stop sidecar exception: {e}")

            if self.internal_net:
                net = self.internal_net
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "docker", "network", "rm", net,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, err = await proc.communicate()
                    if proc.returncode == 0 or b"No such network" in err:
                        self.internal_net = None
                    else:
                        errors.append(f"docker network rm failed: {err.decode('utf-8', errors='replace').strip()}")
                except Exception as e:
                    errors.append(f"docker network rm exception: {e}")

        if self._model_proxy:
            try:
                await self._model_proxy.close()
            except Exception as e:
                errors.append(f"model_proxy close: {e}")
            self._model_proxy = None

        if errors:
            raise RuntimeError(f"Errors occurred during ClaudeCodeAdapter teardown: {'; '.join(errors)}")

    def collect_logs(self) -> dict[str, Any]:
        if self._closed_logs is not None and self._model_proxy is None:
            return dict(self._closed_logs)
        return {
            "thread_dir": self.thread_dir,
            "workspace": self.workspace,
            "tool_calls": self._tool_calls,
            "tokens": self._usage["total_tokens"],
            "skills_invoked": list(self._skills_invoked),
            "usage": dict(self._usage),
            "engine": "claude-code",
            "engine_version": self.version,
            "requested_model": self.model_route.requested_model,
            "cli_model": self.model_route.cli_model,
            "upstream_model": self.model_route.upstream_model,
            "context_selector": self.model_route.context_selector,
            "image": self.image,
            "image_digest": getattr(self, "actual_image_digest", None),
            "resource_run_uid": self.resource_run_uid,
            "sidecar_image": self.sidecar_image,
            "sidecar_image_digest": self.actual_sidecar_image_digest,
            "candidate_exit_code": self._proc.returncode if self._proc is not None else None,
            "container_id": self.container_id,
            "internal_network": self.internal_net,
            "model_gateway": {
                "request_path": self._model_proxy.last_request_path if self._model_proxy else None,
                "upstream_status": self._model_proxy.last_upstream_status if self._model_proxy else None,
                "upstream_timeout_count": self._model_proxy.upstream_timeout_count if self._model_proxy else None,
                "turn_count": self._model_proxy.turn_count if self._model_proxy else None,
                "tokens_used": self._model_proxy.tokens_used if self._model_proxy else None,
                "budget_exceeded_reason": self._model_proxy.budget_exceeded_reason if self._model_proxy else None,
                "preflight": self._preflight_result,
                "usage_events": list(self._model_proxy.usage_events[-256:]) if self._model_proxy else [],
                "event_summaries": list(self._model_proxy.event_summaries[-512:]) if self._model_proxy else [],
                "request_summaries": list(self._model_proxy.request_summaries[-128:]) if self._model_proxy else [],
                "request_outcomes": list(self._model_proxy.request_outcomes[-128:]) if self._model_proxy else [],
                "response_summaries": list(self._model_proxy.response_summaries[-128:]) if self._model_proxy else [],
            },
        }
