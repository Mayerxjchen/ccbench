#!/usr/bin/env python3
"""dftworld eval — CLI 参数解析 + 调用 Trusted Harness。

路径约定（单一真相）::

    容器内任务根目录 = /app
      ↔ 宿主 jobs/<ts>/threads/<task>/workspace/
      ↔ pagent sandbox.home

镜像把数据装在 /app；seed 只从镜像 /app 拷到 workspace，再把容器
``/app`` 换成指向 workspace 的 symlink。任务 instruction / tests /
input.json 继续写 ``/app/...``。

一次 eval run 落盘::

    jobs/<timestamp>/            # 原始 Run(永不覆盖)
      summary.json               # run 级元数据 + 每 task 结果(兼容视图)
      threads/<task-name>/
        thread.toml
        metainfo.json
        workspace/          # 沙箱文件（= 容器 /app）
        messages.jsonl
        raw-submission/     # 冻结声明提交的私有宿主副本
        sealed-submission/  # 隔离密封后的干净提交
        verifier-logs/      # 独立 Verifier 的输出(result.json / reward.txt)
    jobs/<ts>__<task>/run-record.json   # canonical 不可变 RunRecord(Task 7)

编排由 ``dftworld_bench.core.harness.TrustedHarness`` 拥有（固定阶段顺序 +
独立 Verifier + 不可变 run record）；agent 侧由 ``PagentAdapter`` 提供
（pagentv4 Runner + 本地 docker）。本文件只做参数解析、任务加载、skill
快照、调用 harness、写兼容 summary。

用法::

    cp .env.example .env   # 填入 API key
    cd base-env-build && bash build.sh base
    uv sync
    uv run python eval.py 001-hello -v
    uv run python eval.py --all
    # 正式消融:两次独立调用,带 experiment + condition;skill 由 --skills 开关决定
    uv run python eval.py --all --experiment skill-ablation-v1 --condition no-skill --no-skills
    uv run python eval.py --all --experiment skill-ablation-v1 --condition with-skill --skills
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import subprocess
import time
import tomllib
from typing import Callable

import yaml
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# PAgent runner execution paths removed. All benchmark runs use ClaudeCodeAdapter.
Runner = None  # type: ignore


from dftworld_bench.agents import (
    AGENT_HOME,  # noqa: F401
    BENCH_SANDBOX_TOOLS,  # noqa: F401
    PAGENT_HOME,
    SYSTEM,  # noqa: F401
    ClaudeCodeAdapter,
    apply_dockerfile_copies,  # noqa: F401  (re-exported for tests)
    ensure_image,  # noqa: F401
)
from dftworld_bench.config.resolver import (
    construct_experiment,
    resolve_formal,
    FrozenExperiment,
)
from dftworld_bench.config.profiles import (
    ProfileRegistry,
    canonical_json,
    digest_bytes,
)
from dftworld_bench.contracts.case import CaseSpec, EXECUTION_ALIASES, EXECUTION_CLASSES
from dftworld_bench.contracts.resolved_lock import ResolvedRunLock
from dftworld_bench.contracts.result import FailureCode
from dftworld_bench.core.event_store import EventStore
from dftworld_bench.core.coordinator import RunCoordinator
from dftworld_bench.core.model_transport import RetryingModelClient
from dftworld_bench.executors import ExecutionContext, resolve
from dftworld_bench.core.harness import (
    HarnessSpec,
    Profile,
    RunMode,
    Treatment,
    TrustedHarness,
)
from dftworld_bench.core.run_store import RunStore
from dftworld_bench.runtime.registry import RuntimeRegistry, RuntimeRegistryError
from dftworld_bench.runtime.qualify import qualify_runtime

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "infra" / "config"
DEFAULT_RUN_CONFIG = ROOT / "infra" / "runs" / "skill-ablation-v2.yaml"
DEFAULT_JOBS = ROOT / "jobs"
# skill bundle 镜像锁定的 manifest;eval 只读它决定 immutable tag
SKILL_MANIFEST = ROOT / "base-env-build" / ".skill-image.json"
# frozen ablation release 目录;存在时 run-record 的 benchmark_commit 钉到其 source_commit
RELEASES_DIR = ROOT / "releases"
FROZEN_RELEASE_SCHEMA = "ablation-ready-release/v1"
# 镜像内 skill 只读源(bundle 镜像独有;compute 镜像没有,杜绝 agent 绕过 install_skills)
SKILLS_IN_IMAGE = "/opt/electromind/skills"

FROM_RE = re.compile(r"^\s*FROM\s+(\S+)", re.MULTILINE)


# -- transport shim for Candidate Agent --------------------------------------


class CandidateModelTransport:
    """Candidate model transport for RetryingModelClient integration."""

    def __init__(self, provider: Any = None) -> None:
        self._provider = provider

    async def request(
        self,
        operation_id: str,
        request: dict[str, Any],
        timeout_sec: float,
    ) -> Any:
        from dftworld_bench.core.model_transport import ModelResponse, HttpFailure

        if self._provider is not None and hasattr(self._provider, "complete"):
            try:
                stream = await self._provider.complete(
                    messages=request.get("messages", []),
                    tools=request.get("tools"),
                )
                collected: list[str] = []
                usage: dict[str, Any] | None = None
                request_id: str | None = None
                async for chunk in stream:
                    if hasattr(chunk, "choices") and chunk.choices:
                        delta = getattr(chunk.choices[0], "delta", None)
                        content = getattr(delta, "content", None) if delta else None
                        if content:
                            collected.append(content)
                    if hasattr(chunk, "usage") and chunk.usage is not None:
                        usage = {"tokens": getattr(chunk.usage, "total_tokens", 0)}
                    if hasattr(chunk, "id") and chunk.id:
                        request_id = chunk.id
                return ModelResponse(
                    text="".join(collected),
                    request_id=request_id,
                    usage=usage,
                )
            except Exception as exc:
                return HttpFailure(status=0, body=str(exc))
        return ModelResponse(text="", request_id=None, usage={"tokens": 0})


class _DockerRunner:
    """Minimal Docker-backed runtime runner for in-process qualification."""

    def image_digest(self, image: str) -> str | None:
        proc = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True, text=True, check=False,
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    def inspect(self, image: str) -> dict[str, Any]:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            return {"platform": "", "user": "", "files": []}
        try:
            data = json.loads(proc.stdout)[0]
        except (IndexError, json.JSONDecodeError):
            return {"platform": "", "user": "", "files": []}
        config = data.get("Config") or {}
        rootfs = data.get("RootFS") or {}
        return {
            "platform": data.get("Os", "") + "/" + data.get("Architecture", ""),
            "user": config.get("User", ""),
            "files": list(rootfs.get("Layers") or []),
        }

    def run(self, image: str, argv: list[str]) -> tuple[int, str, str]:
        proc = subprocess.run(
            ["docker", "run", "--rm", image] + argv,
            capture_output=True, text=True, check=False, timeout=30,
        )
        return proc.returncode, proc.stdout, proc.stderr





def git_head_commit(cwd: Path | None = None) -> str:
    """repo 当前 HEAD 短 hash;不是 git repo 时返回 'unknown'。"""
    cwd = cwd or ROOT
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=10", "HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def frozen_release_source_commit() -> str | None:
    """默认 benchmark 身份: frozen release 的 ``source_commit``。

    run-record 的 ``benchmark_commit`` 必须等于 release 的 ``source_commit``
    才能进入正式消融对比 (ablation gate)。release 存在时把默认值钉到它,免得
    run-record 自动记录 git HEAD 造成与冻结身份漂移。``--benchmark-commit``
    仍是显式逃生舱。零个 frozen release 回退到 git HEAD (旧行为);多于一个
    拒绝猜测,要求显式指定。
    """
    frozen: list[dict] = []
    for manifest in sorted(RELEASES_DIR.glob("*.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("schema") == FROZEN_RELEASE_SCHEMA and data.get("status") == "frozen":
            frozen.append(data)
    if not frozen:
        return None
    if len(frozen) > 1:
        raise SystemExit(
            "存在多个 frozen release,benchmark_commit 有歧义;"
            "请用 --benchmark-commit 显式指定。"
        )
    commit = frozen[0].get("source_commit")
    if not commit:
        raise SystemExit(f"{frozen[0].get('name', '?')}: frozen release 缺少 source_commit")
    return commit


def load_skill_manifest() -> dict:
    """读 ``base-env-build/.skill-image.json``;缺失或未锁定时 fail fast。"""
    if not SKILL_MANIFEST.is_file():
        raise SystemExit(
            f"缺少 {SKILL_MANIFEST}。先构建 skill bundle：\n"
            f"  cd {ROOT / 'base-env-build'} && bash build.sh skills"
        )
    try:
        data = json.loads(SKILL_MANIFEST.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"{SKILL_MANIFEST}: 读取失败: {exc}")
    if not data.get("tag") or not data.get("skills_sha"):
        raise SystemExit(f"{SKILL_MANIFEST}: 缺少 tag/skills_sha，请重新 build skills")
    return data


def extract_skills_image(snapshot_dir: Path, image: str) -> None:
    """把 skill bundle 镜像的 ``/opt/electromind/skills`` 提取到宿主快照目录。"""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{snapshot_dir}:/out",
            image,
            "bash", "-lc",
            f"shopt -s nullglob dotglob; "
            f"if [ -d {SKILLS_IN_IMAGE} ]; then "
            f"  for f in {SKILLS_IN_IMAGE}/*; do cp -a \"$f\" /out/; done; "
            f"else exit 3; fi",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"提取 skill 镜像失败 ({image}): "
            f"{proc.stderr or proc.stdout}"
        )


def verify_skills_sha(snapshot_dir: Path, expected: str) -> str:
    """重算提取快照的内容哈希;与 manifest 不一致则 fail fast,作废本次 run。"""
    from skills_sha import hash_tree
    actual = hash_tree(snapshot_dir)
    if actual != expected:
        raise SystemExit(
            f"skills_sha mismatch: manifest={expected} extracted={actual}。"
            f"镜像/manifest/提取已漂移，本次 run 不作数。"
        )
    return actual


def next_attempt(jobs_dir: Path, experiment_id: str, condition_id: str,
                 task: str, replicate: int) -> int:
    """同一 Trial(exp×cond×task×replicate)的下一次 attempt 序号。"""
    n = 0
    if jobs_dir.is_dir():
        for summary in jobs_dir.glob("*/summary.json"):
            try:
                data = json.loads(summary.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            for r in data.get("results", []):
                if (
                    r.get("experiment_id", data.get("experiment_id")) == experiment_id
                    and r.get("condition_id", data.get("condition_id")) == condition_id
                    and r.get("task") == task
                    and int(r.get("replicate", 1) or 1) == replicate
                ):
                    n = max(n, int(r.get("attempt", 0) or 0))
    return n + 1


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """把 .env 载入 os.environ；已存在的环境变量不覆盖。"""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def require_api_key(model: str) -> None:
    vendor = model.split("/", 1)[0].lower() if "/" in model else "deepseek"
    key_names = {
        "deepseek": "DEEPSEEK_API_KEY",
        "openai": "OPENAI_API_KEY",
        "kimi": "MOONSHOT_API_KEY",
        "longcat": "LONGCAT_API_KEY",
        "mimo": "MIMO_API_KEY",
    }
    name = key_names.get(vendor)
    if name and not os.getenv(name):
        raise SystemExit(
            f"缺少 {name}。请写入 {ROOT / '.env'} 或先 export {name}=..."
        )


@dataclass
class TaskSpec:
    name: str
    path: Path
    image: str
    instruction: str
    agent_timeout_sec: float
    description: str
    gpus: int = 0
    verifier_timeout_sec: float = 600.0
    verifier_env: dict[str, str] = field(default_factory=dict)
    submission_root: str = "."
    legacy_submission_layout: bool = True
    execution_class: str = "local_sandbox"
    legacy_execution_value: str | None = None


@dataclass
class TaskResult:
    task: str
    image: str
    reward: float
    ok: bool
    skill: str = "No Skill"
    tool_calls: int = 0
    tokens: int = 0
    error: str = ""
    elapsed_sec: float = 0.0
    thread_id: str = ""
    thread_dir: str = ""
    workspace: str = ""
    experiment_id: str = "default"
    condition_id: str = "no-skill"
    replicate: int = 1
    attempt: int = 1
    benchmark_commit: str = "unknown"
    # skill 来源与锁定身份(消融控制变量)
    skills_source: str = "none"   # none | image
    skill_image: str = ""         # dftworld-skills:<sha>
    skill_commit: str = ""
    skills_sha: str = ""
    compute_image: str = ""       # 实际执行的计算镜像(两组一致)
    requested_gpus: int = 0       # 任务声明的 GPU 数([environment].gpus)
    # 实际调用过的 skill 名(去重、按出现顺序);空 = 有 skill 但没调
    skills_invoked: list = field(default_factory=list)


def discover_tasks(root: Path = ROOT) -> list[Path]:
    return sorted(
        p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{3}-.+", p.name)
    )


def task_dockerfile(task_dir: Path) -> Path:
    """任务 Dockerfile：root 布局（v2 ``public/``）优先，environment/（001-024）回退。"""
    root_df = task_dir / "Dockerfile"
    if root_df.is_file():
        return root_df
    legacy_df = task_dir / "environment" / "Dockerfile"
    if legacy_df.is_file():
        return legacy_df
    raise FileNotFoundError(f"{task_dir}: 没有任务 Dockerfile（root 或 environment/ 均无）")


def load_task(task_dir: Path) -> TaskSpec:
    dockerfile = task_dockerfile(task_dir).read_text()
    match = FROM_RE.search(dockerfile)
    if not match:
        raise ValueError(f"{task_dir}: Dockerfile missing FROM")
    with (task_dir / "task.toml").open("rb") as f:
        meta = tomllib.load(f)
    instruction = (task_dir / "instruction.md").read_text().strip()
    gpus = int(meta.get("environment", {}).get("gpus", 0))
    if gpus < 0:
        raise ValueError(f"{task_dir.name}: environment.gpus must be >= 0, got {gpus}")
    agent_timeout_sec = float(meta.get("agent", {}).get("timeout_sec", 600.0))
    verifier = meta.get("verifier", {})
    candidate = meta.get("candidate", {})
    explicit_class = (meta.get("execution") or {}).get("class")
    legacy_value = (meta.get("task") or {}).get("execution_backend")
    if explicit_class is not None and legacy_value is not None:
        raise ValueError(
            f"{task_dir.name}: both [execution].class and task.execution_backend "
            "are present; execution is never ambiguous"
        )
    raw_value = explicit_class if explicit_class is not None else legacy_value
    if raw_value is None:
        execution_class = "local_sandbox"
    else:
        normalized = EXECUTION_ALIASES.get(raw_value, raw_value)
        if normalized not in EXECUTION_CLASSES:
            raise ValueError(
                f"{task_dir.name}: unknown execution class {raw_value!r}; "
                f"expected one of {sorted(EXECUTION_CLASSES)}"
            )
        execution_class = normalized
    return TaskSpec(
        name=task_dir.name,
        path=task_dir,
        image=match.group(1),
        instruction=instruction,
        agent_timeout_sec=agent_timeout_sec,
        description=str(meta.get("task", {}).get("description", "")),
        gpus=gpus,
        verifier_timeout_sec=float(verifier.get("timeout_sec", agent_timeout_sec)),
        verifier_env={
            str(k): str(v) for k, v in (verifier.get("env") or {}).items()
        },
        submission_root=str(candidate.get("submission_root", ".")),
        legacy_submission_layout=bool(
            candidate.get("legacy_submission_layout", True)
        ),
        execution_class=execution_class,
        legacy_execution_value=str(raw_value) if raw_value is not None else None,
    )


def durable_session_for_run(run_dir: Path, task_name: str) -> EventStore:
    """Durable append-only session (events + checkpoints) for one attempt.

    Lives under the run directory so a crash anywhere in the attempt leaves a
    resumable session; the RunCoordinator replays committed activities from
    the validated chain and resumes exactly-once.
    """
    return EventStore(run_dir / "session" / f"{task_name}.jsonl")


@dataclass(frozen=True)
class HarnessProvenance:
    """Resolved, persisted inputs required by a v2 Harness attempt."""

    lock: ResolvedRunLock
    budgets: dict[str, int]
    runtime_identities: dict[str, dict | None]
    profile: Profile

    def write_lock(self, session: EventStore) -> Path:
        path = session.path.parent / "resolved-run-lock.json"
        self.lock.write_once(path)
        return path


def docker_image_digest(image: str) -> str:
    """Resolve the actual local OCI image ID; tags alone are not provenance."""
    ensure_image(image)
    proc = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        check=False,
    )
    digest = proc.stdout.strip() if proc.returncode == 0 else ""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"cannot resolve immutable image digest for {image!r}")
    return digest


def _load_site_profile(profile_path: Path | None = None) -> "HpcSiteProfile | None":
    """Load the private site profile from the canonical cluster config or explicit path.

    Returns ``None`` when the config file does not exist (local/test
    environments).  The profile is loaded once per process and cached.
    """
    import json
    import tomllib

    from dftworld_bench.hpc.site_profile import HpcSiteProfile

    config_path = profile_path or Path("scripts/hpc/cluster_profile.toml")
    if not config_path.is_file():
        return None
    raw_text = config_path.read_text(encoding="utf-8")
    if config_path.suffix == ".json":
        return HpcSiteProfile.from_dict(json.loads(raw_text))
    return HpcSiteProfile.from_cluster_config(tomllib.loads(raw_text))


def _executor_deps(
    task,
    *,
    site_profile=None,
    site_profile_path: Path | None = None,
    cluster_profile_path: Path | None = None,
    gpu_site_profile_path: Path | None = None,
    compute_profile_path: Path | None = None,
) -> dict:
    """Composition-root dependencies (trusted eval only).

    The HPC runtime stack — GatewayRuntime, credentials, adapter instance —
    is assembled here and nowhere else: production executors and the
    coordinator receive an ``HpcDispatcher`` and never see the classes behind
    it.

    When ``compute_profile_path`` is specified, builds a heterogeneous hybrid
    stack routing CPU to Slurm and GPU to CompShare.
    """
    if task.execution_class != "hpc_controller":
        return {}
    from dftworld_bench.hpc.dispatcher import HpcDispatcher
    from dftworld_bench.hpc.gateway_runtime import GatewayRuntime
    from dftworld_bench.hpc.production import build_hybrid_stack, build_slurm_stack
    from dftworld_bench.hpc.trust_store import QualificationTrustStore

    effective_cluster = cluster_profile_path or site_profile_path or Path("scripts/hpc/cluster_profile.toml")
    # These are explicit composition-root inputs.  The checked-in trust store
    # is intentionally UNCONFIGURED; loading it here keeps the runtime
    # fail-closed until an operator injects active qualification anchors.
    qualification_root = ROOT
    trust_store = QualificationTrustStore.load_default()

    if compute_profile_path is not None and Path(compute_profile_path).is_file():
        stack = build_hybrid_stack(
            compute_profile_path=Path(compute_profile_path),
            cluster_profile_path=effective_cluster if effective_cluster.is_file() else None,
            gpu_site_profile_path=gpu_site_profile_path,
            case_id=f"eval-{task.name}",
            audit_path=Path("jobs/hpc-audit.jsonl"),
            qualification_root=qualification_root,
            trust_store=trust_store,
            repo_root=ROOT,
        )
        dispatcher = HpcDispatcher(
            GatewayRuntime(audit=stack["audit"]),
            stack["run_adapter_config"],
        )
        return {
            "dispatcher": dispatcher,
            "run_adapter_config": stack["run_adapter_config"],
            "compute_profile": stack["compute_profile"],
        }

    if not effective_cluster.is_file():
        raise RuntimeError(
            "hpc_controller Slurm execution requires a private cluster TOML; "
            "provide --cluster-profile <path.toml>"
        )
    stack = build_slurm_stack(
        cluster_profile_path=effective_cluster,
        case_id=f"eval-{task.name}",
        audit_path=Path("jobs/hpc-audit.jsonl"),
        qualification_root=qualification_root,
        trust_store=trust_store,
    )
    dispatcher = HpcDispatcher(GatewayRuntime(audit=stack["audit"]), stack["run_adapter_config"])
    return {
        "dispatcher": dispatcher,
        "run_adapter_config": stack["run_adapter_config"],
    }


def _resolved_budget_limits(
    task: TaskSpec,
    case: CaseSpec,
    *,
    experiment_id: str,
    max_turns: int,
    registry: ProfileRegistry,
    run_config: Any = None,
) -> dict[str, int]:
    if run_config is not None:
        agent = run_config.budget_for(task.execution_class)
        retries = int(run_config.api.max_attempts) - 1
        retry_delay_ms = int(float(run_config.api.retry_max_delay_sec) * 1000)
        active_sec = float(agent.active_walltime_sec)
        scheduler_sec = float(agent.scheduler_wait_walltime_sec)
        token_limit = int(agent.max_total_tokens)
        cost_rate = max(
            int(run_config.api.input_usd_micros_per_million_tokens),
            int(run_config.api.output_usd_micros_per_million_tokens),
        )
    else:
        profile_name = "formal-long" if experiment_id == "skill-ablation-v1" else "local-standard"
        legacy_agent = registry.require("agents", profile_name)
        api = registry.require("api", "default")
        retries = int(api["max_retries"])
        retry_delay_ms = int(float(api["retry_max_delay_sec"]) * 1000)
        active_sec = float(task.agent_timeout_sec)
        scheduler_sec = float(legacy_agent["scheduler_wait_walltime_sec"])
        token_limit = int(legacy_agent["max_total_tokens"])
        cost_rate = int(api["cost_usd_micros_per_token"]) * 1_000_000
    active_ms = int(active_sec * 1000)
    scheduler_ms = int(scheduler_sec * 1000)
    total_ms = active_ms + scheduler_ms + int(task.verifier_timeout_sec * 1000)
    cpus = int(case.candidate_resources.get("cpus") or 1)
    gpus = int(case.candidate_resources.get("gpus") or 0)
    storage_mb = int(case.candidate_resources.get("storage_mb") or 0)
    active_hours = max(1, (active_ms + 3_599_999) // 3_600_000)
    total_hours = max(1, (total_ms + 3_599_999) // 3_600_000)
    # LOCK dialect: the resolved-run-lock schema requires max_model_turns and
    # max_total_tokens, and the two walltime domains are denominated in
    # SECONDS (BudgetPolicy.from_lock maps them into the ms ledger domains).
    # The remaining ten domains use their direct ledger names and units.
    return {
        "max_model_turns": int(max_turns),
        "max_total_tokens": token_limit,
        "logical_requests": int(max_turns),
        "api_attempts": int(max_turns) * (retries + 1),
        "usd_microcost": (token_limit * cost_rate) // 1_000_000,
        "agent_active_walltime_sec": active_sec,
        "run_total_walltime_ms": total_ms,
        "local_tool_walltime_ms": active_ms,
        "api_retry_walltime_ms": int(max_turns) * retries * retry_delay_ms,
        "scheduler_wait_walltime_sec": scheduler_sec,
        "jobs": int(max_turns) if task.execution_class == "hpc_controller" else 0,
        "cpu_hours": cpus * active_hours,
        "gpu_hours": gpus * active_hours,
        "storage_byte_hours": storage_mb * 1024 * 1024 * total_hours,
    }


def _hpc_compute_runtime(task: TaskSpec) -> dict:
    lock_path = task.path / "reference" / "compute-runtime.lock.json"
    if not lock_path.is_file():
        raise ValueError(f"hpc_controller has no compute runtime lock: {lock_path}")
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    runtime = payload.get("runtime") or {}
    raw_digest = runtime.get("sif_sha256")
    if isinstance(raw_digest, str) and re.fullmatch(r"[0-9a-f]{64}", raw_digest):
        return {
            "role": "compute",
            "profile": str(payload.get("schema", "hpc-compute-runtime")),
            "image": str(runtime.get("sif_path_remote", "remote-sif")),
            "digest": "sha256:" + raw_digest,
        }
    runtime_image = payload.get("runtime_image") or {}
    digest = runtime_image.get("digest") or runtime_image.get("repo_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"compute runtime lock lacks a full digest: {lock_path}")
    return {
        "role": "compute",
        "profile": str(payload.get("schema", "hpc-compute-runtime")),
        "image": str(runtime_image.get("tag", "remote-runtime")),
        "digest": digest,
    }


def resolve_harness_provenance(
    task: TaskSpec,
    *,
    run_id: str,
    experiment_id: str,
    condition_id: str,
    replicate: int,
    model: str,
    max_turns: int,
    benchmark_commit: str,
    skills_sha: str | None,
    image_digest_resolver: Callable[[str], str] | None = None,
    runtime_runner: Any = None,
    run_config: Any = None,
    run_metadata: dict[str, Any] | None = None,
    engine: str = "claude-code",
) -> HarnessProvenance:
    """Resolve a complete lock, budgets, runtimes, and site for one caller.

    Uses the canonical resolve_formal() path for lock construction — no inline
    payload building.  Runtime identities use actual image digests (separate
    from the lock's profile-based digests) for harness record-keeping.
    """
    registry = ProfileRegistry.load(CONFIG_DIR)
    case = CaseSpec.load(task.path)
    _resolver = image_digest_resolver if image_digest_resolver is not None else docker_image_digest
    image_digest = _resolver(task.image)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
        raise ValueError(f"image resolver returned an invalid digest: {image_digest!r}")

    # Budgets: full 14-domain set from profiles (harness ledger needs these).
    budgets = _resolved_budget_limits(
        task,
        case,
        experiment_id=experiment_id,
        max_turns=max_turns,
        registry=registry,
        run_config=run_config,
    )

    # Frozen experiment from profile selections (bind claude-code-formal if available).
    agent_lock_path = Path(__file__).resolve().parent / "base-env-build" / "agent-claude-code" / "claude-code.lock.json"
    agent_image_digest = None
    if agent_lock_path.is_file():
        try:
            agent_lock_data = json.loads(agent_lock_path.read_text(encoding="utf-8"))
            agent_image_digest = agent_lock_data.get("built_image_digest")
        except Exception:
            pass

    agent_profile_name = "claude-code-formal" if "claude-code-formal" in registry.names("agents") else "formal-long"
    selection: dict[str, str] = {"agent": agent_profile_name, "api": "default"}
    site_name = "<site-alias>" if task.execution_class == "hpc_controller" else None
    if site_name:
        selection["site"] = site_name
    experiment = construct_experiment(selection, registry)

    # Canonical lock via resolve_formal() — all digests computed from real data.
    lock = resolve_formal(
        experiment,
        case,
        run_id,
        replicate,
        model=model,
        benchmark_commit=benchmark_commit,
        condition_id=condition_id,
        instruction=task.instruction,
        max_turns=max_turns,
        skills_sha=skills_sha,
        verifier_image_digest=image_digest,
        budgets=budgets,
        run_config=run_config,
        run_metadata=run_metadata,
        engine=engine,
        engine_version="0.2.29",
        agent_image_digest=agent_image_digest,
    )

    # Profile for run record (platform, execution_class, site digest).
    profile = profile_for_task(task)
    site_for_digest = site_name or "local"
    site_digest = registry.digest("sites", site_for_digest)
    profile = Profile(
        name=profile.name,
        execution_class=profile.execution_class,
        platform=profile.platform,
        site_config_digest=site_digest,
        legacy_normalized=profile.legacy_normalized,
    )

    # Runtime identities: consult the registry for structural mapping, overlay
    # actual images/digests from the task and lock.  The registry is the single
    # owner of runtime identity; even empty requirements go through it (it
    # raises RuntimeRegistryError which we catch to fall back to inline).
    runtime_registry = RuntimeRegistry()
    resolved_runtime = None
    try:
        resolved_runtime = runtime_registry.resolve(
            list(case.runtime_requirements), task.execution_class,
        )
    except RuntimeRegistryError:
        resolved_runtime = None

    if resolved_runtime is not None:
        # Registry resolved successfully: use its structural mapping (profiles,
        # roles) and overlay actual images/digests.  For HPC cases the compute
        # identity keeps its real SIF digest from the lock file, not the profile
        # default — the lock is the authoritative compute provenance.
        candidate = {
            "role": "candidate",
            "profile": resolved_runtime.candidate.profile,
            "image": task.image,
            "digest": image_digest,
        }
        verifier_identity = {
            "role": "verifier",
            "profile": resolved_runtime.verifier.profile,
            "image": task.image,
            "digest": image_digest,
        }
        control_identity = {
            "role": "control",
            "profile": resolved_runtime.control.profile,
            "image": task.image,
            "digest": image_digest,
        }
        if task.execution_class == "local_sandbox":
            control = None
            compute = {
                "role": "compute",
                "profile": resolved_runtime.compute.profile,
                "image": task.image,
                "digest": image_digest,
            }
        else:
            control = control_identity
            compute = _hpc_compute_runtime(task)
    else:
        # Fallback: no resolvable compute family (local cases with name-only or
        # no runtime requirements).  Retain the legacy inline identity shape
        # which is constrained by test_eval_run_identity.py: control=None,
        # candidate.image == task.image for local_sandbox.
        candidate = {
            "role": "candidate",
            "profile": f"{task.execution_class}-candidate",
            "image": task.image,
            "digest": image_digest,
        }
        verifier_identity = {
            "role": "verifier",
            "profile": f"{task.execution_class}-verifier",
            "image": task.image,
            "digest": image_digest,
        }
        if task.execution_class == "local_sandbox":
            control = None
            compute = {
                "role": "compute",
                "profile": "local-candidate-compute-combined",
                "image": task.image,
                "digest": image_digest,
            }
        else:
            control = {
                "role": "control",
                "profile": "bench-hpc-control",
                "image": task.image,
                "digest": image_digest,
            }
            compute = _hpc_compute_runtime(task)

    runtime_identities = {
        "candidate": candidate,
        "control": control,
        "compute": compute,
        "verifier": verifier_identity,
    }

    # Qualification gate: every resolved identity must carry a locked digest
    # and pass the role-appropriate qualification battery.  This is a
    # structural invariant — without it the harness cannot verify provenance,
    # so fail closed before any attempt starts.
    _docker_runner = runtime_runner if runtime_runner is not None else _DockerRunner()
    for role_name, identity in runtime_identities.items():
        if identity is None:
            continue
        if not identity.get("digest"):
            raise ValueError(
                f"runtime identity {role_name!r} lacks a locked digest; "
                "run qualify_runtimes.py before attempting a run"
            )
        from dftworld_bench.runtime.registry import RuntimeIdentity as _RI
        runtime_obj = _RI(
            role=identity["role"],
            profile=identity["profile"],
            image=identity["image"],
            digest=identity.get("digest"),
        )
        report = qualify_runtime(runtime_obj, _docker_runner)
        if not report.passed():
            failed = [c.name for c in report.checks if not c.ok]
            raise ValueError(
                f"runtime identity {role_name!r} failed qualification: "
                f"{', '.join(failed)}"
            )

    # Candidate Agent Qualification Gate: enforce strict verified receipt on formal runs
    is_formal = False
    if run_metadata and run_metadata.get("counted"):
        is_formal = True
    elif run_config and getattr(run_config, "mode", None) in ("formal", "pilot"):
        is_formal = True
    elif os.getenv("MLFFBENCH_ENFORCE_AGENT_GATE") == "1":
        is_formal = True

    _verify_candidate_agent_gate(
        task,
        is_formal=is_formal,
        agent_image_digest=agent_image_digest,
    )

    return HarnessProvenance(lock, budgets, runtime_identities, profile)


def _verify_candidate_agent_gate(
    task: TaskSpec,
    *,
    is_formal: bool,
    agent_image_digest: str | None = None,
    receipt_path: Path | None = None,
) -> None:
    """Formal admission gate: verify Candidate Agent qualification receipt.

    Fail-closed policy:
    1. In formal / counted mode, receipt MUST exist and be verified against
       qualification-trust.toml.
    2. Status must be PROMOTED.
    3. Readiness must be READY.
    4. Execution status must be PASS.
    5. All canary checks must PASS.
    6. If locked agent_image_digest is provided, receipt must match it.
    7. If BLOCKED or verification fails, abort formal execution immediately.
    """
    if not is_formal:
        return

    if receipt_path is None:
        env_receipt = os.getenv("MLFFBENCH_CANDIDATE_AGENT_RECEIPT")
        if env_receipt:
            receipt_path = Path(env_receipt)
        else:
            receipt_path = (
                Path.home()
                / ".config"
                / "mlffbench"
                / "evidence"
                / "gate_agent"
                / "claude_code_receipt.json"
            )

    if not receipt_path.is_file():
        raise RuntimeError(
            f"Candidate Agent qualification receipt not found at {receipt_path}. "
            "Formal benchmark requires an official, cryptographically verified qualification receipt. "
            "Run 'python scripts/qualification/qualify_claude_code_agent.py' first."
        )

    from dftworld_bench.verifiers.candidate_agent_verifier import CandidateAgentVerifier
    from dftworld_bench.hpc.trust_store import QualificationTrustStore

    verifier = CandidateAgentVerifier(workspace_root=ROOT)
    trust_store = QualificationTrustStore.load_default()
    if not verifier.verify_receipt_file(receipt_path, trust_store=trust_store):
        raise RuntimeError(
            f"Candidate Agent qualification receipt signature verification FAILED for {receipt_path}. "
            "Receipt signature does not match trusted keys in qualification-trust.toml."
        )

    try:
        receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to parse qualification receipt {receipt_path}: {exc}") from exc

    verdict = receipt_data.get("verdict", {})
    status = verdict.get("status")
    if status != "PROMOTED":
        raise RuntimeError(
            f"Candidate Agent qualification status is {status!r} (BLOCKED). "
            "Formal benchmark execution is rejected."
        )

    readiness = verdict.get("readiness")
    if readiness and readiness != "READY":
        raise RuntimeError(
            f"Candidate Agent readiness is {readiness!r} != 'READY'. Formal benchmark rejected."
        )

    execution_status = verdict.get("agent_execution")
    if execution_status and execution_status != "PASS":
        raise RuntimeError(
            f"Candidate Agent execution status is {execution_status!r} != 'PASS'. Formal benchmark rejected."
        )

    canaries = receipt_data.get("canary_results", {})
    for c_name, c_val in canaries.items():
        if isinstance(c_val, dict) and c_val.get("status") != "PASS":
            raise RuntimeError(
                f"Candidate Agent qualification canary {c_name} status is {c_val.get('status')!r} != 'PASS'."
            )

    if agent_image_digest:
        evidence = receipt_data.get("evidence", {})
        receipt_image_digest = (
            evidence.get("image_digest")
            or evidence.get("canary_1_image_isolation", {}).get("image_digest")
        )
        if receipt_image_digest and receipt_image_digest != agent_image_digest:
            raise RuntimeError(
                f"Candidate Agent receipt image digest mismatch: receipt={receipt_image_digest}, lock={agent_image_digest}"
            )


def profile_for_task(task: TaskSpec) -> Profile:
    """Harness profile whose frozen identity mirrors the case's execution class.

    The agent-side harness always runs in a local Docker sandbox, but the
    run-record's execution_class/platform must describe the case's real
    execution: an ``hpc_controller`` case records the case platform (e.g.
    gpu-slurm) and never ``local_docker``.  Both arms of a paired trial go
    through the same path, so their identity fields still match.
    """
    if task.execution_class == "hpc_controller":
        platform_yaml = task.path / "profiles" / "platform.yaml"
        platform = "gpu-slurm"
        if platform_yaml.is_file():
            with platform_yaml.open("r", encoding="utf-8") as f:
                platform = str(yaml.safe_load(f).get("name", platform))
        return Profile(
            name="local",
            execution_class="hpc_controller",
            platform=platform,
            site_config_digest="sha256:local",
            legacy_normalized=True,
        )
    return Profile(
        name="local",
        execution_class="local_sandbox",
        platform="local_docker",
        site_config_digest="sha256:local",
        legacy_normalized=True,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="mlffbench eval with Claude Code Agent")
    parser.add_argument("tasks", nargs="*", help="任务名，如 001-hello")
    parser.add_argument("--all", action="store_true", help="跑全部任务")
    parser.add_argument(
        "--model",
        default=None,
        help="仅 --uncounted-smoke 可用的 provider/model 覆盖",
    )
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--run-config", type=Path, default=DEFAULT_RUN_CONFIG)
    parser.add_argument(
        "--compute-profile",
        type=Path,
        default=None,
        help="计算路由配置文件路径 (如 examples/hpc/maintainer-hybrid-v1.json)",
    )
    parser.add_argument(
        "--cluster-profile",
        type=Path,
        default=None,
        help="私有 Slurm 集群配置文件路径 (TOML, 包含 SSH 传输凭证)",
    )
    parser.add_argument(
        "--gpu-site-profile",
        type=Path,
        default=None,
        help="私有 CompShare GPU 站点配置文件路径 (JSON)",
    )
    parser.add_argument(
        "--site-profile",
        type=Path,
        default=None,
        help="私有集群/站点配置文件路径 (TOML 或 JSON，将自动映射至对应 route)",
    )
    parser.add_argument("--uncounted-smoke", action="store_true")
    parser.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOBS)
    parser.add_argument(
        "--experiment",
        default="default",
        help="实验 ID:这批结果为何可以彼此比较（默认 %(default)s）",
    )
    parser.add_argument(
        "--condition",
        default=None,
        help="实验条件 ID(如 no-skill / with-skill)。缺省由 --skills 开关推导",
    )
    parser.add_argument(
        "--replicate",
        type=int,
        default=1,
        help="独立重复序号（默认 %(default)s）",
    )
    parser.add_argument(
        "--benchmark-commit",
        default=None,
        help="覆盖自动检测的 benchmark git commit",
    )
    parser.add_argument(
        "--agent-commit",
        default=None,
        help="agent 引擎版本（默认 unknown）",
    )
    skill_group = parser.add_mutually_exclusive_group()
    skill_group.add_argument(
        "--skills",
        dest="skills_enabled",
        action="store_true",
        help="启用 skill:从 dftworld-skills bundle 镜像提取(不读宿主 .pagent/skills/)",
    )
    skill_group.add_argument(
        "--no-skills",
        dest="skills_enabled",
        action="store_false",
        help="禁用 skill:agent 完全没有 use_skill 工具(默认)",
    )
    parser.set_defaults(skills_enabled=False)
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


@dataclass(frozen=True)
class TaskRunSettings:
    run_config: Any
    model: str
    max_turns: int
    condition_id: str
    skills_enabled: bool
    override_present: bool
    frozen: bool
    counted: bool


def resolve_task_run_settings(
    args: argparse.Namespace, execution_class: str
) -> TaskRunSettings:
    """Resolve the one policy object used by runtime, budgets, and lock."""
    from dftworld_bench.config.run_config import load_run_config

    config = load_run_config(args.run_config)
    derived_condition = "with-skill" if args.skills_enabled else "no-skill"
    if args.condition is not None and args.condition != derived_condition:
        raise SystemExit(
            f"--condition {args.condition!r} conflicts with "
            f"{'--skills' if args.skills_enabled else '--no-skills'}"
        )
    override_present = args.model is not None or args.max_turns is not None
    if override_present and not args.uncounted_smoke:
        raise SystemExit(
            "--model/--max-turns policy overrides require --uncounted-smoke"
        )
    budget = config.budget_for(execution_class)
    model = args.model or f"{config.model.provider}/{config.model.model_id}"
    max_turns = args.max_turns or budget.max_model_turns
    counted = config.mode in {"pilot", "formal"} and not args.uncounted_smoke
    return TaskRunSettings(
        run_config=config,
        model=model,
        max_turns=max_turns,
        condition_id=derived_condition,
        skills_enabled=args.skills_enabled,
        override_present=override_present,
        frozen=not args.uncounted_smoke and not override_present,
        counted=counted,
    )


async def amain(argv: list[str] | None = None) -> int:
    # .env contains values only; policy selection comes from Run Config.
    load_dotenv()
    args = parse_args(argv)
    from dftworld_bench.config.run_config import load_run_config

    base_run_config = load_run_config(args.run_config)
    api_endpoint = os.getenv(base_run_config.api.endpoint_env)
    api_key = os.getenv(base_run_config.api.credential_env)
    missing = [
        name for name, value in (
            (base_run_config.api.endpoint_env, api_endpoint),
            (base_run_config.api.credential_env, api_key),
        ) if not value
    ]
    if missing:
        raise SystemExit("missing trusted API environment: " + ", ".join(missing))
    if args.all:
        task_dirs = discover_tasks()
    elif args.tasks:
        task_dirs = []
        for name in args.tasks:
            path = Path(name) if Path(name).is_dir() else ROOT / name
            if not path.is_dir():
                raise SystemExit(f"task not found: {name}")
            task_dirs.append(path)
    else:
        raise SystemExit("请指定任务名，或加 --all")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d__%H-%M-%S")
    run_dir = args.jobs_dir / stamp
    threads_root = run_dir / "threads"
    threads_root.mkdir(parents=True, exist_ok=True)

    # skill bundle:--skills 时从 manifest 锁定镜像,提取快照作为 skill_roots。
    # 提取后哈希必须与 manifest 一致,否则本次 run 作废。
    skills_source = "none"
    skill_image = skill_commit = skills_sha = ""
    skill_roots: tuple[Path, ...] = ()
    if args.skills_enabled:
        manifest = load_skill_manifest()
        snapshot = run_dir / "skills_src"
        extract_skills_image(snapshot, manifest["tag"])
        skills_sha = verify_skills_sha(snapshot, manifest["skills_sha"])
        skill_image = manifest["tag"]
        skill_commit = manifest["commit"]
        skills_source = "image"
        skill_roots = (snapshot,)

    experiment_id = (
        base_run_config.run_config_id if args.experiment == "default" else args.experiment
    )
    condition_id = "with-skill" if args.skills_enabled else "no-skill"
    if args.condition is not None and args.condition != condition_id:
        raise SystemExit(
            f"--condition {args.condition!r} conflicts with Skill treatment {condition_id!r}"
        )
    skill_label = "image-pinned" if args.skills_enabled else "No Skill"
    replicate = args.replicate
    benchmark_commit = args.benchmark_commit or frozen_release_source_commit() or git_head_commit()
    agent_commit = args.agent_commit or "unknown"
    print(
        f"run: exp={experiment_id}  cond={condition_id}  replicate={replicate}  "
        f"benchmark={benchmark_commit}  agent={agent_commit}  "
        f"run_config={base_run_config.run_config_id}@{base_run_config.digest}  "
        f"skills={'ON' if args.skills_enabled else 'OFF'}  "
        f"skills_source={skills_source}  skill_image={skill_image or '-'}"
    )

    store = RunStore(args.jobs_dir)
    results: list[TaskResult] = []
    for path in task_dirs:
        task = load_task(path)
        settings = resolve_task_run_settings(args, task.execution_class)
        agent_active_walltime_sec = settings.run_config.budget_for(
            task.execution_class
        ).active_walltime_sec
        attempt = next_attempt(args.jobs_dir, experiment_id, condition_id, task.name, replicate)
        # Task 9: durable session (events + checkpoints) at the attempt's
        # side-effect boundaries; the RunCoordinator resumes from these.
        session = durable_session_for_run(run_dir, task.name)
        print(f"==> {task.name}  ({task.image})  attempt={attempt}")
        t0 = time.perf_counter()

        # Execution dispatch is contract-only (Task 11): the executor registry
        # maps the case's declared ``execution_class`` to the runtime lifecycle.
        # ``hpc_controller`` starts the host-side trusted gateway and pins the
        # local container to zero GPUs — the GPU lives on the remote Slurm job
        # (``--gres`` in the controller's slurm template).  ``local_sandbox``
        # uses the task's own GPU allowance.  The task.toml ``gpus`` value still
        # feeds the verifier/profile layer, but the container backend must not
        # pass ``--gpus device=0`` for controller cases (Apple M4 has no NVIDIA
        # runtime and would fail at start).
        cluster_prof = args.cluster_profile
        gpu_prof = args.gpu_site_profile
        if args.site_profile:
            if str(args.site_profile).endswith(".json"):
                gpu_prof = gpu_prof or args.site_profile
            else:
                cluster_prof = cluster_prof or args.site_profile

        executor_deps = _executor_deps(
            task,
            site_profile=_load_site_profile(cluster_prof) if cluster_prof else _load_site_profile(),
            site_profile_path=cluster_prof,
            cluster_profile_path=cluster_prof,
            gpu_site_profile_path=gpu_prof,
            compute_profile_path=args.compute_profile,
        )
        run_adapter_config = executor_deps.pop("run_adapter_config", None)
        executor = resolve(task.execution_class, **executor_deps)
        run_id = f"{stamp}__{task.name}"
        context = ExecutionContext(
            task=task,
            threads_root=threads_root,
            model=settings.model,
            max_turns=settings.max_turns,
            verbose=args.verbose,
            run_id=run_id,
            workspace=threads_root / task.name / "workspace",
            adapter_config=run_adapter_config,
        )
        try:
            await executor.prepare(context)
            provenance = resolve_harness_provenance(
                task,
                run_id=run_id,
                experiment_id=experiment_id,
                condition_id=condition_id,
                replicate=replicate,
                model=settings.model,
                max_turns=settings.max_turns,
                benchmark_commit=benchmark_commit,
                skills_sha=skills_sha or None,
                run_config=base_run_config,
                run_metadata={
                    "override_present": settings.override_present,
                    "frozen": settings.frozen,
                    "counted": settings.counted,
                },
                engine="claude-code",
            )
            from dftworld_bench.core.budgets import BudgetPolicy
            budget_policy = BudgetPolicy.from_lock({"budgets": provenance.budgets})
            resolved_tokens = budget_policy.require("tokens")
            resolved_turns = budget_policy.require("model_turns")
            adapter = ClaudeCodeAdapter(
                model=settings.model,
                max_turns=resolved_turns,
                max_total_tokens=resolved_tokens,
                threads_root=threads_root,
                task_name=task.name,
                case_dir=task.path,
                gpus=context.local_gpus,
                agent_timeout_sec=agent_active_walltime_sec,
                skill_roots=skill_roots,
                verbose=args.verbose,
                container_env=context.container_env,
                execution_class=task.execution_class,
                api_endpoint=api_endpoint,
                api_key=api_key,
                forbidden_env_names=frozenset(
                    {base_run_config.api.endpoint_env, base_run_config.api.credential_env}
                ),
            )
            spec = HarnessSpec(
                case_id=task.name,
                case_dir=task.path,
                image=task.image,
                instruction=task.instruction,
                run_id=run_id,
                agent_timeout_sec=agent_active_walltime_sec,
                threads_root=threads_root,
                mode=RunMode.FORMAL if settings.counted else RunMode.SMOKE,
                budgets=provenance.budgets,
                lock_digest=provenance.lock.digest,
                runtime_identities=provenance.runtime_identities,
                gpus=task.gpus,
                verifier_timeout_sec=task.verifier_timeout_sec,
                verifier_env=task.verifier_env,
                submission_root=task.submission_root,
                legacy_submission_layout=task.legacy_submission_layout,
                model=settings.model,
                max_turns=settings.max_turns,
                verbose=args.verbose,
            )
            treatment = Treatment(
                experiment_id=experiment_id,
                condition_id=condition_id,
                skills_source=skills_source,
                skill_image=skill_image,
                skill_commit=skill_commit,
                skills_sha=skills_sha or None,  # no-skill: "" -> None (record requires null)
                replicate=replicate,
                attempt=attempt,
                benchmark_commit=benchmark_commit,
                agent_model=settings.model,
            )
            profile = provenance.profile
            # Production transport: RetryingModelClient wraps the pagent
            # provider via a PagentTransport shim.  The adapter still owns
            # the agent lifecycle; the transport is the single retry/breaker
            # owner for model requests.
            harness = TrustedHarness(adapter, store=store, session=session)
            # The transport is created lazily: the pagent provider only exists
            # after adapter.start() runs inside harness.  We construct the
            # RetryingModelClient here so eval.py is the single composition
            # root; the adapter will inject its provider into the transport.
            transport = CandidateModelTransport(provider=None)
            model_client = RetryingModelClient(
                transport=transport,
                max_attempts=base_run_config.api.max_attempts,
                base_delay_sec=base_run_config.api.retry_base_delay_sec,
                max_delay_sec=base_run_config.api.retry_max_delay_sec,
                request_timeout_sec=base_run_config.api.request_timeout_sec,
                ledger=harness.ledger,
                events=session,
            )
            coordinator = RunCoordinator(
                run_id=run_id,
                lock_digest=provenance.lock.digest,
                executor=harness,
                events=session,
                ledger=harness.ledger,
                case_executor=executor,
                execution_context=context,
            )
            br = await coordinator.start()
        finally:
            await coordinator.close()
            await executor.close(context)

        logs = adapter.collect_logs()
        passed = br.is_counted_scientifically and br.failure_code is FailureCode.PASS
        elapsed_sec = time.perf_counter() - t0
        result = TaskResult(
            task=task.name,
            image=task.image,
            reward=1.0 if passed else 0.0,
            ok=passed,
            skill=skill_label,
            tool_calls=logs.get("tool_calls", 0),
            tokens=logs.get("tokens", 0),
            error="" if passed else br.reason,
            elapsed_sec=elapsed_sec,
            thread_id=task.name,
            thread_dir=logs.get("thread_dir", ""),
            workspace=logs.get("workspace", ""),
            experiment_id=experiment_id,
            condition_id=condition_id,
            replicate=replicate,
            attempt=attempt,
            benchmark_commit=benchmark_commit,
            skills_source=skills_source,
            skill_image=skill_image,
            skill_commit=skill_commit,
            skills_sha=skills_sha,
            compute_image=task.image,
            requested_gpus=task.gpus,
            skills_invoked=logs.get("skills_invoked", []),
        )
        results.append(result)
        status = "PASS" if result.ok else "FAIL"
        extra = f"  error={result.error}" if result.error else ""
        print(
            f"<== {task.name}  {status}  reward={result.reward}  "
            f"calls={result.tool_calls}  tokens={result.tokens}  "
            f"{result.elapsed_sec:.1f}s{extra}"
        )
        if result.thread_dir:
            print(f"    thread: {result.thread_dir}")
            print(f"    record: {args.jobs_dir / spec.run_id / 'run-record.json'}")

    summary = {
        "run_id": stamp,
        "experiment_id": experiment_id,
        "condition_id": condition_id,
        "replicate": replicate,
        "benchmark_commit": benchmark_commit,
        "agent_commit": agent_commit,
        "model": f"{base_run_config.model.provider}/{base_run_config.model.model_id}",
        "max_turns_by_execution_class": {
            name: budget.max_model_turns
            for name, budget in base_run_config.agent_by_execution_class.items()
        },
        "run_config_id": base_run_config.run_config_id,
        "run_config_digest": base_run_config.digest,
        "skill": skill_label,
        "threads_root": str(threads_root),
        "skills_source": skills_source,
        "skill_image": skill_image,
        "skill_commit": skill_commit,
        "skills_sha": skills_sha,
        "results": [asdict(r) for r in results],
        "passed": sum(1 for r in results if r.ok),
        "total": len(results),
    }
    out = run_dir / "summary.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(f"\nsummary: {summary['passed']}/{summary['total']}  -> {out}")
    print(f"threads: {threads_root}")

    try:
        from summarize import flatten_runs, load_runs, render_overview
        execs = flatten_runs(load_runs(args.jobs_dir))
        (args.jobs_dir / "SUMMARY.md").write_text(
            render_overview(execs), encoding="utf-8"
        )
        print(f"summary overview: {args.jobs_dir / 'SUMMARY.md'}")
    except Exception as exc:
        print(f"note: 更新 SUMMARY.md 失败: {exc}")
    return 0 if summary["passed"] == summary["total"] else 1


def main() -> None:
    try:
        raise SystemExit(asyncio.run(amain()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
