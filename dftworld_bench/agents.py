"""Agent adapters: the provider-specific half of the Trusted Harness.

The harness (``dftworld_bench.core.harness``) owns orchestration and infra;
it speaks to an ``AgentAdapter`` and never touches pagent, docker, or a
scheduler. ``PagentAdapter`` is the Local reference provider: it packages the
case into a Docker workspace, opens a pagentv4 ``Runner``, drives the agent,
and tears the candidate down — all behind the protocol.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

from pagentv4 import (
    Agent,
    DeepSeek,
    Kimi,
    LongCat,
    MiMo,
    Ollama,
    Provider,
    Runner,
    Sglang,
    TextDelta,
    Thread,
    ToolCallBegin,
    ToolResult,
    TurnResult,
    Vllm,
)
from pagentv4.runtime.base_runner import assemble_run_resources
from pagentv4.runtime.run_state import RunState

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
    backend = sandbox.backend
    inner = getattr(backend, "inner", backend)
    cid = getattr(inner, "container_id", None)
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
    the docker-init child tree).  When *events* (an EventStore) is provided,
    a ``tool_timeout`` event is recorded on expiry for durable audit.
    """
    import signal as _signal
    from dftworld_bench.core.tool_watchdog import ToolWatchdog

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
            os.killpg(pgid, _signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=watchdog.grace_sec)
        except subprocess.TimeoutExpired:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, _signal.SIGKILL)
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
    workdir = sandbox.workdir
    setup = docker_exec(
        cid,
        f"rm -rf /app && ln -sfn {shlex.quote(workdir)} /app",
        timeout=60,
    )
    if setup.returncode != 0:
        raise RuntimeError(f"bind /app failed: {setup.stderr or setup.stdout}")


# ``hpc_controller`` execution: the agent inside the local Docker sandbox is
# only a control layer that reaches the HPC scheduler over the host-side
# trusted gateway (the common GatewayRuntime + HttpGatewayServer).  Never copy
# a private key into the image or /app.  Which cases get this behavior is
# decided by the case's ``[execution] class`` in its manifest (resolved by
# dftworld_bench.executors), never by case name here.  The gateway URL and
# run-scoped token reach the controller via container env, so no per-case host
# policy, runtime-lock, or qualification-receipt file is mounted.


def controller_docker_args(execution_class: str) -> list[str]:
    """``docker run`` argv for ``hpc_controller`` execution, else ``[]``.

    The controller is the *control layer* only: all SSH/scheduler access lives
    behind the trusted gateway.  No ssh-agent socket, no ``~/.ssh`` mounts, no
    per-case policy file — the site operator owns those on the gateway host.
    We only add the host-gateway alias so the controller container can reach
    the loopback-bound gateway the executor exposes.  The decision is made by
    the declared execution class, never by case name.
    """
    if execution_class != "hpc_controller":
        return []
    return [
        # The controller reaches the host-side trusted gateway at
        # host.docker.internal (executor passes BENCH_HPC_GATEWAY_URL).  Docker
        # Desktop resolves it automatically; plain Linux needs the host-gateway
        # add-host.  Verified working under both on this deployment.
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


def _patch_container_backend_gpus(gpus: int) -> None:
    """让容器 backend 创建 ``docker run`` 时显式带 ``--gpus`` 及控制器转发参数。"""
    _CURRENT_RUN_ARGS["extra"] = (
        docker_gpu_args(gpus)
        + controller_docker_args(_CURRENT_TASK["execution_class"])
    )
    if gpus <= 0 and _CURRENT_TASK["execution_class"] != "hpc_controller":
        return
    from pagentv4.sandbox.backends.container import ContainerBackend

    if getattr(ContainerBackend, "_dftworld_controller_patched", False):
        return
    _original_start = ContainerBackend.start

    async def _start_with_gpus(self, spec, workdir):
        if not spec.image:
            raise ValueError(
                f"{self.cli} backend requires spec.image; "
                f"pass Sandbox.create(image=..., backend={self.cli!r})"
            )
        import shutil as _shutil

        if _shutil.which(self.cli) is None:
            from pagentv4.sandbox.base import SandboxError

            raise SandboxError(f"{self.cli} CLI not found in PATH")

        os.makedirs(workdir, exist_ok=True)
        self.spec = spec
        self.workdir = workdir

        argv: list[str] = [
            self.cli,
            "run",
            "-d",
            "--rm",
            "-v",
            f"{workdir}:{workdir}",
            "-w",
            workdir,
        ]
        argv.extend(_CURRENT_RUN_ARGS["extra"])
        for key, value in spec.env.items():
            argv.extend(["--env", f"{key}={value}"])
        if spec.command:
            argv.append(spec.image)
            argv.extend(spec.command)
        else:
            ttl = spec.container_ttl_seconds
            sleep_arg = str(ttl) if ttl is not None else "infinity"
            argv.extend([spec.image, "sleep", sleep_arg])

        import asyncio as _asyncio

        process = await _asyncio.create_subprocess_exec(
            *argv,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            from pagentv4.sandbox.base import SandboxError

            raise SandboxError(
                f"{self.cli} run failed: "
                f"{stderr.decode('utf-8', errors='replace').strip()}"
            )
        self.container_id = stdout.decode("utf-8", errors="replace").strip()

    ContainerBackend.start = _start_with_gpus
    ContainerBackend._dftworld_controller_patched = True


def make_provider(
    model: str, *, base_url: str | None = None, api_key: str | None = None
):
    if "/" in model:
        vendor, name = model.split("/", 1)
    else:
        vendor, name = "deepseek", model

    vendors = {
        "deepseek": DeepSeek,
        "kimi": Kimi,
        "longcat": LongCat,
        "mimo": MiMo,
        "ollama": Ollama,
        "vllm": Vllm,
        "sglang": Sglang,
        "openai": Provider,
    }
    cls = vendors.get(vendor.lower())
    if cls is None:
        raise SystemExit(f"unknown provider {vendor!r} in model {model!r}")
    return cls(name, base_url=base_url, apikey=api_key)


async def open_runner(
    thread_id: str,
    *,
    threads_root: Path,
    image: str,
    model: str,
    max_turns: int,
    container_ttl_seconds: int,
    skill_roots: tuple[Path, ...] = (),
    gpus: int = 0,
    task_name: str = "",
    container_env: dict[str, str] | None = None,
    execution_class: str = "local_sandbox",
    api_endpoint: str | None = None,
    api_key: str | None = None,
):
    """打开落在 ``threads_root/<thread_id>/`` 的 Thread，并组装 Runner。"""
    _CURRENT_TASK["name"] = task_name
    _CURRENT_TASK["execution_class"] = execution_class
    _patch_container_backend_gpus(gpus)
    thread = Thread.open(
        thread_id,
        root=threads_root,
        overrides={
            "backend": "docker",
            "image": image,
            "command_policy": "open",
            "container_ttl_seconds": container_ttl_seconds,
            "conversation_backend": "jsonl",
            "conversation_root": ".",
            "model": model,
            "sandbox_tools": BENCH_SANDBOX_TOOLS,
            "project_path": str(PAGENT_HOME / "_no_host"),
        },
    )

    _open_sandbox = thread.open_sandbox

    async def open_sandbox_with_agent_home():
        spec = thread.spec
        if spec.backend in ("container", "docker", "podman"):
            # pagentv4's open_sandbox_for_spec does not forward env to
            # Sandbox.create; the gateway URL/token must reach the controller
            # container as --env flags, so open the docker sandbox ourselves.
            from pagentv4.sandbox.sandbox import Sandbox, profile_host_root

            sandbox = await Sandbox.create(
                backend=spec.backend,
                workdir=str(thread.workspace_path),
                host_root=profile_host_root(spec),
                image=spec.image,
                container_ttl_seconds=spec.container_ttl_seconds,
                command_policy=spec.command_policy,
                tools=tuple(spec.sandbox_tools or ()),
                env=dict(container_env or {}),
            )
        else:
            sandbox = await _open_sandbox()
        sandbox.home = AGENT_HOME
        return sandbox

    thread.open_sandbox = open_sandbox_with_agent_home  # type: ignore[method-assign]

    run_state = RunState(phase="waking_sandbox")
    resources = await assemble_run_resources(
        thread,
        extra_system=SYSTEM,
        run_state=run_state,
        skill_roots=skill_roots,
    )
    system_prompt = resources.system_prompt.replace("/home/agent", AGENT_HOME)

    store = thread.open_store()
    runner = Runner(
        thread=thread,
        sandbox=resources.sandbox,
        store=store,
        messages=thread.load_messages(),
        agent=Agent(
            make_provider(model, base_url=api_endpoint, api_key=api_key),
            system=system_prompt,
            tools=list(resources.tools),
            max_turns=max_turns,
        ),
        skills=resources.skills,
        conversation_id=thread.messages_conversation_id,
    )
    runner.run_state = run_state
    return runner


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


class PagentAdapter:
    """Local Docker + pagentv4 provider behind the ``AgentAdapter`` protocol."""

    def __init__(
        self,
        *,
        model: str,
        max_turns: int,
        threads_root: Path,
        task_name: str,
        image: str,
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
    ):
        self.model = model
        self.max_turns = max_turns
        self.threads_root = Path(threads_root)
        self.task_name = task_name
        self.image = image
        self.case_dir = Path(case_dir)
        self.gpus = gpus
        # execution dispatch is contract-only: the execution class decides the
        # container extras (hpc_controller host-gateway add-host).  The adapter
        # receives only executor-resolved inputs; never a case id dispatch key.
        self.execution_class = execution_class
        self.api_endpoint = api_endpoint
        self._api_key = api_key
        # Task 8 guard: the Candidate container must never receive the trusted
        # model endpoint/credential.  ``container_env`` carries only run-scoped
        # controller env (e.g. BENCH_HPC_*); api-profile credential env names
        # are resolved by the transport in the trusted process and are rejected
        # here if a caller tries to forward them into the candidate.
        incoming = dict(container_env) if container_env else {}
        leaked = sorted(set(incoming) & set(forbidden_env_names))
        if leaked:
            raise ValueError(
                "candidate env would receive trusted API secrets: " + ", ".join(leaked)
            )
        self.container_env = incoming
        self.agent_timeout_sec = agent_timeout_sec
        self.skill_roots = tuple(skill_roots)
        self.verbose = verbose
        self.runner = None
        self.thread_dir = str(self.threads_root / task_name)
        self.workspace = str(self.threads_root / task_name / "workspace")
        self._usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        self._budget_ledger = None
        self._turn_index = 0
        self._tool_calls = 0
        self._skills_invoked: list[str] = []

    def attach_budget_ledger(self, ledger) -> None:
        """Receive the run's harness-owned BudgetLedger (Task 8/I3 wiring)."""
        self._budget_ledger = ledger

    @property
    def version(self) -> str:
        return self.model

    async def prepare(self) -> None:
        """package: 把镜像 /app 数据 + 任务 COPY 数据注入 candidate workspace。"""
        ensure_image(self.image)
        seed_workspace(Path(self.workspace), self.image, self.case_dir)

    async def start(self, instruction: str) -> None:
        """candidate_start + agent_start: 开 sandbox/runner 并驱动 agent 到结束。"""
        self.runner = await open_runner(
            self.task_name,
            threads_root=self.threads_root,
            image=self.image,
            model=self.model,
            max_turns=self.max_turns,
            container_ttl_seconds=int(self.agent_timeout_sec) + 120,
            skill_roots=self.skill_roots,
            gpus=self.gpus,
            task_name=self.task_name,
            container_env=self.container_env,
            execution_class=self.execution_class,
            api_endpoint=self.api_endpoint,
            api_key=self._api_key,
        )
        bind_workspace_as_app(self.runner.sandbox)
        # install_skills 在 assemble 时已跑一次,但 bind 会把 /app/.skills 删掉,
        # 必须在 bind 之后重装,落到 workspace/.skills。
        if self.runner.skills.names():
            await self.runner.sandbox.install_skills(self.runner.skills)
        try:
            await self._consume(instruction)
        finally:
            self._tool_calls = count_tool_calls(self.thread_dir)
            self._skills_invoked = collect_skill_invocations(self.thread_dir)

    async def stop(self, metainfo: dict) -> None:
        """candidate_freeze: 写 thread metainfo（runner 仍存活时）。"""
        if self.runner is not None:
            self.runner.thread.save_metainfo(metainfo)

    async def close(self) -> None:
        """candidate_destroy: 关 runner / 销毁候选容器。幂等。"""
        if self.runner is not None:
            runner = self.runner
            self.runner = None
            await runner.close()

    def collect_logs(self) -> dict:
        return {
            "thread_dir": self.thread_dir,
            "workspace": self.workspace,
            "tool_calls": self._tool_calls,
            "tokens": self._usage["total_tokens"],
            "skills_invoked": list(self._skills_invoked),
            "usage": dict(self._usage),
        }

    async def _consume(self, instruction: str) -> None:
        runner = self.runner
        usage = self._usage
        verbose = self.verbose

        async def consume() -> None:
            async for event in runner.run(instruction):
                # token 统计必须无条件执行,不依赖 verbose
                if isinstance(event, TurnResult) and event.usage:
                    for key in usage:
                        usage[key] += int(event.usage.get(key, 0) or 0)
                    if self._budget_ledger is not None:
                        self._turn_index += 1
                        turn_op = f"{self.task_name}/model-turn/{self._turn_index}"
                        self._budget_ledger.charge("model_turns", 1, turn_op)
                        total = int(event.usage.get("total_tokens", 0) or 0)
                        if total > 0:
                            self._budget_ledger.charge("tokens", total, turn_op)
                if not verbose:
                    continue
                if isinstance(event, ToolCallBegin):
                    args = event.arguments
                    if len(args) > 200:
                        args = args[:200] + "…"
                    print(f"  tool → {event.name}({args})")
                elif isinstance(event, ToolResult):
                    body = (event.content or "").replace("\n", " ")
                    if len(body) > 160:
                        body = body[:160] + "…"
                    print(f"    {'ok' if event.ok else 'fail'}: {body}")
                elif isinstance(event, TextDelta):
                    sys.stdout.write(event.text)
                    sys.stdout.flush()

        await consume()
