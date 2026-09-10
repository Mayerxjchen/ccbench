"""Gate 0：workspace 隔离回归测试（dftworld2 benchmark 架构）。

测试 `eval.apply_dockerfile_copies` / `eval.load_task` 对两种布局的支持：

- v2 root 布局：  ``NNN-slug/Dockerfile`` + ``COPY public/ /app/``
- v1 legacy 布局：``NNN-slug/environment/Dockerfile`` + ``COPY <f> /app/<f>``

核心不变量：Agent workspace（/app）只能含任务 Dockerfile 显式 COPY 的目标，
`reference/ solution/ tests/` 绝不进入 —— 无论用哪种布局、无论任务 Dockerfile
写得有多糟，都不允许"找不到环境就整体 seed task 目录"的 fallback 泄漏。

G0 验收映射：
- G0.1 root-level Dockerfile supported          → test_root_*
- G0.2 COPY public/ /app/ recursively works     → test_copy_*（含递归/多 source）
- G0.3 legacy environment layout unchanged      → test_legacy_*
- G0.4 hidden benchmark assets inaccessible     → 所有 test 的 hidden 断言
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import eval as E  # noqa: E402
from bench.contracts.case import CaseSpec  # noqa: E402
from bench.core.packager import package_candidate  # noqa: E402

LEGACY_IMAGE = "dftworld-base-cp2k"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def make_task(tmp_path: Path) -> Path:
    """构造一个最小 task 目录，只含 'hidden' benchmark 资产。

    public/ 或 environment/ 由各测试自行创建（避免重复 mkdir）。
    """
    task = tmp_path / "task"
    # hidden assets —— 任何情况下都不得进入 workspace
    for d, f in [
        ("reference", "reference.json"),
        ("solution", "solve.sh"),
        ("tests", "test_outputs.py"),
    ]:
        (task / d).mkdir(parents=True)
        (task / d / f).write_text("HIDDEN")
    return task


def write_dockerfile(task: Path, layout: str, body: str) -> None:
    df_dir = task if layout == "root" else task / "environment"
    (df_dir / "Dockerfile").write_text(body)


def write_min_task_toml(task: Path) -> None:
    (task / "task.toml").write_text(
        '[task]\ndescription = "test"\n[agent]\ntimeout_sec = 600.0\n'
    )


def hidden_leaked(workspace: Path) -> list[str]:
    """检查 hidden benchmark 资产是否泄漏进 workspace。"""
    leaked = []
    for name in ("reference", "solution", "tests"):
        for p in workspace.rglob("*"):
            if name in p.parts:
                leaked.append(str(p.relative_to(workspace)))
    return leaked


def assert_workspace(workspace: Path, expected: list[str]) -> None:
    got = sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file())
    assert got == sorted(expected), f"workspace 内容不符:\n  got={got}\n  want={expected}"
    assert hidden_leaked(workspace) == [], (
        f"hidden 资产泄漏: {hidden_leaked(workspace)}"
    )


# --------------------------------------------------------------------------- #
# G0.1 + G0.2 + G0.4：root Dockerfile + COPY public/ /app/ 递归
# --------------------------------------------------------------------------- #

def test_root_copy_public_recursive(tmp_path):
    task = make_task(tmp_path)
    (task / "public").mkdir(parents=True, exist_ok=True)
    (task / "public" / "input.xyz").write_text("Si input")
    (task / "public" / "sub").mkdir()
    (task / "public" / "sub" / "data.txt").write_text("data")
    write_dockerfile(task, "root", f"FROM {LEGACY_IMAGE}\nCOPY public/ /app/\n")
    write_min_task_toml(task)

    ws = tmp_path / "ws"
    E.apply_dockerfile_copies(ws, task)
    assert_workspace(ws, ["input.xyz", "sub/data.txt"])
    assert (ws / "input.xyz").read_text() == "Si input"


def test_root_copy_public_no_trailing_slash(tmp_path):
    task = make_task(tmp_path)
    (task / "public").mkdir(parents=True, exist_ok=True)
    (task / "public" / "a.txt").write_text("a")
    write_dockerfile(task, "root", f"FROM {LEGACY_IMAGE}\nCOPY public /app/\n")

    ws = tmp_path / "ws"
    E.apply_dockerfile_copies(ws, task)
    assert_workspace(ws, ["a.txt"])


def test_root_copy_relative_dot_public(tmp_path):
    task = make_task(tmp_path)
    (task / "public").mkdir(parents=True, exist_ok=True)
    (task / "public" / "a.txt").write_text("a")
    write_dockerfile(task, "root", f"FROM {LEGACY_IMAGE}\nCOPY ./public/ /app/\n")

    ws = tmp_path / "ws"
    E.apply_dockerfile_copies(ws, task)
    assert_workspace(ws, ["a.txt"])


def test_root_copy_single_file(tmp_path):
    task = make_task(tmp_path)
    (task / "public").mkdir(exist_ok=True, parents=True)
    (task / "public" / "settings.incar").write_text("ENCUT = 400")
    write_dockerfile(
        task, "root", f"FROM {LEGACY_IMAGE}\nCOPY public/settings.incar /app/\n"
    )

    ws = tmp_path / "ws"
    E.apply_dockerfile_copies(ws, task)
    assert_workspace(ws, ["settings.incar"])
    assert (ws / "settings.incar").read_text() == "ENCUT = 400"


def test_root_copy_multiple_sources(tmp_path):
    task = make_task(tmp_path)
    (task / "public").mkdir(exist_ok=True, parents=True)
    (task / "public" / "f1.txt").write_text("1")
    (task / "public" / "f2.txt").write_text("2")
    write_dockerfile(
        task, "root", f"FROM {LEGACY_IMAGE}\nCOPY public/f1.txt public/f2.txt /app/\n"
    )

    ws = tmp_path / "ws"
    E.apply_dockerfile_copies(ws, task)
    assert_workspace(ws, ["f1.txt", "f2.txt"])


def test_copy_dir_to_nested_dst(tmp_path):
    """COPY training_data /app/data/training_data → workspace/data/training_data。"""
    task = make_task(tmp_path)
    (task / "public").mkdir(exist_ok=True, parents=True)
    (task / "public" / "training_data").mkdir(parents=True, exist_ok=True)
    (task / "public" / "training_data" / "type.raw").write_text("2")
    write_dockerfile(
        task, "root",
        f"FROM {LEGACY_IMAGE}\nCOPY public/training_data /app/data/training_data\n",
    )

    ws = tmp_path / "ws"
    E.apply_dockerfile_copies(ws, task)
    assert_workspace(ws, ["data/training_data/type.raw"])


def test_root_load_task_uses_root_dockerfile(tmp_path):
    """G0.1：load_task 必须从 root Dockerfile 解析 image。"""
    task = make_task(tmp_path)
    write_dockerfile(task, "root", f"FROM {LEGACY_IMAGE}\nCOPY public/ /app/\n")
    write_min_task_toml(task)
    (task / "instruction.md").write_text("test instruction")

    spec = E.load_task(task)
    assert spec.image == LEGACY_IMAGE


# --------------------------------------------------------------------------- #
# G0.3：legacy environment/ 布局完全不变
# --------------------------------------------------------------------------- #

def test_legacy_single_file(tmp_path):
    task = make_task(tmp_path)
    (task / "environment").mkdir(parents=True, exist_ok=True)
    (task / "environment" / "H2O.inp").write_text("CUTOFF 200")
    write_dockerfile(task, "legacy", f"FROM {LEGACY_IMAGE}\nCOPY H2O.inp /app/H2O.inp\n")

    ws = tmp_path / "ws"
    E.apply_dockerfile_copies(ws, task)
    assert_workspace(ws, ["H2O.inp"])
    assert (ws / "H2O.inp").read_text() == "CUTOFF 200"


def test_legacy_dir_to_nested(tmp_path):
    task = make_task(tmp_path)
    (task / "environment" / "training_data").mkdir(exist_ok=True, parents=True)
    (task / "environment" / "training_data" / "set.raw").write_text("data")
    write_dockerfile(
        task, "legacy",
        f"FROM {LEGACY_IMAGE}\nCOPY training_data /app/data/training_data\n",
    )

    ws = tmp_path / "ws"
    E.apply_dockerfile_copies(ws, task)
    assert_workspace(ws, ["data/training_data/set.raw"])


def test_legacy_multiple_copy_lines(tmp_path):
    """一个 legacy Dockerfile 含多条 COPY（如 017）。"""
    task = make_task(tmp_path)
    (task / "environment").mkdir(parents=True, exist_ok=True)
    (task / "environment" / "H2O.inp").write_text("x")
    (task / "environment" / "guide.txt").write_text("y")
    write_dockerfile(
        task, "legacy",
        f"FROM {LEGACY_IMAGE}\nCOPY H2O.inp /app/H2O.inp\nCOPY guide.txt /app/guide.txt\n",
    )

    ws = tmp_path / "ws"
    E.apply_dockerfile_copies(ws, task)
    assert_workspace(ws, ["H2O.inp", "guide.txt"])


# --------------------------------------------------------------------------- #
# fail loudly：不支持语法 / 越界 / 无 fallback 泄漏
# --------------------------------------------------------------------------- #

def test_copy_dotdot_source_rejected(tmp_path):
    task = make_task(tmp_path)
    (task / "public").mkdir(exist_ok=True, parents=True)
    write_dockerfile(
        task, "root", f"FROM {LEGACY_IMAGE}\nCOPY ../secret /app/secret\n"
    )
    ws = tmp_path / "ws"
    with pytest.raises(ValueError, match="build context"):
        E.apply_dockerfile_copies(ws, task)


def test_copy_non_app_dst_rejected(tmp_path):
    task = make_task(tmp_path)
    (task / "public").mkdir(exist_ok=True, parents=True)
    (task / "public" / "a.txt").write_text("a")
    write_dockerfile(task, "root", f"FROM {LEGACY_IMAGE}\nCOPY public/a.txt /dest/a.txt\n")
    ws = tmp_path / "ws"
    with pytest.raises(ValueError, match="/app"):
        E.apply_dockerfile_copies(ws, task)


def test_copy_dst_dotdot_rejected(tmp_path):
    task = make_task(tmp_path)
    (task / "public").mkdir(exist_ok=True, parents=True)
    (task / "public" / "a.txt").write_text("a")
    write_dockerfile(
        task, "root", f"FROM {LEGACY_IMAGE}\nCOPY public/a.txt /app/../leak\n"
    )
    ws = tmp_path / "ws"
    with pytest.raises(ValueError, match="/app"):
        E.apply_dockerfile_copies(ws, task)


def test_copy_flag_rejected(tmp_path):
    task = make_task(tmp_path)
    (task / "public").mkdir(exist_ok=True, parents=True)
    write_dockerfile(
        task, "root", f"FROM {LEGACY_IMAGE}\nCOPY --chown=x public/ /app/\n"
    )
    ws = tmp_path / "ws"
    with pytest.raises(ValueError, match="不支持"):
        E.apply_dockerfile_copies(ws, task)


def test_missing_dockerfile_no_leak(tmp_path):
    """没有任何 Dockerfile → 必须报错，绝不能 fallback 复制整个 task 目录。"""
    task = make_task(tmp_path)  # 有 public/ + hidden，但无 Dockerfile
    write_min_task_toml(task)
    ws = tmp_path / "ws"
    with pytest.raises(FileNotFoundError, match="Dockerfile"):
        E.apply_dockerfile_copies(ws, task)
    assert hidden_leaked(ws) == [], "无 Dockerfile 时必须报错，不能 seed 任何东西"


# --------------------------------------------------------------------------- #
# G0.5：allowlist packager 是新 release gate（旧 Docker COPY 解析器仅作兼容）
# --------------------------------------------------------------------------- #

def write_packagable_task_toml(task: Path) -> None:
    (task / "task.toml").write_text(
        'schema_version = "1.2"\n'
        '[execution]\n'
        'class = "local_sandbox"\n'
        '[candidate]\n'
        'instruction = "instruction.md"\n'
        'submission_root = "."\n'
        'legacy_submission_layout = true\n'
        '[[candidate.files]]\n'
        'source = "public/**"\n'
        'destination = "."\n'
        'strip_prefix = "public"\n'
        '[task]\n'
        'description = "packager gate"\n'
        '[agent]\n'
        'timeout_sec = 600.0\n'
        '[verifier]\n'
        'timeout_sec = 600.0\n'
        '[verifier.env]\n'
        '[environment]\n'
        'cpus = 2\n'
        'memory_mb = 4096\n'
        'storage_mb = 10240\n'
        'gpus = 0\n'
        'allow_internet = false\n'
        '[environment.env]\n'
        '[solution.env]\n',
        encoding="utf-8",
    )


def test_packager_gate_no_wholesale_copy(tmp_path):
    """packager 是新 release gate：只拷贝 allowlist，绝不整体 seed 任何目录。

    即便 case 目录同时含 public/ 和 hidden 资产，manifest 也仅含 allowlist
    结果；任何整体拷贝路径（如 Dockerfile fallback）都会把 hidden 带进去。
    """
    task = make_task(tmp_path)  # reference/ solution/ tests/ 已存在
    (task / "public").mkdir(exist_ok=True)
    (task / "public" / "input.xyz").write_text("Si input")
    (task / "instruction.md").write_text("solve it")
    write_packagable_task_toml(task)

    bundle = tmp_path / "bundle"
    manifest = package_candidate(CaseSpec.load(task), bundle)

    # manifest 只含 allowlist 结果，hidden 资产绝不出现
    paths = sorted(f.path for f in manifest.files)
    assert paths == ["input.xyz", "instruction.md"], f"allowlist 内容不符: {paths}"
    assert hidden_leaked(bundle) == [], f"packager 泄漏 hidden 资产: {hidden_leaked(bundle)}"
    # 任何 whole-case 拷贝路径（含 instruction + public + hidden）都应失败
    whole = [f.path for f in manifest.files if "reference" in f.path or "solution" in f.path or "tests" in f.path]
    assert whole == []


def test_packager_gate_detects_drift(tmp_path):
    """同一 case 内容 → 同一 digest；内容一变 → digest 必变（seed 用版本锚定）。"""
    task = make_task(tmp_path)
    (task / "public").mkdir(exist_ok=True)
    (task / "public" / "input.xyz").write_text("Si input")
    (task / "instruction.md").write_text("solve it")
    write_packagable_task_toml(task)

    spec = CaseSpec.load(task)
    first = package_candidate(spec, tmp_path / "first")
    second = package_candidate(spec, tmp_path / "second")
    assert first.public_digest == second.public_digest

    (task / "public" / "input.xyz").write_text("Si input changed")
    drift = package_candidate(spec, tmp_path / "drift")
    assert first.public_digest != drift.public_digest


# --------------------------------------------------------------------------- #
# Task 8 G0 扩展：模型运输在可信侧 —— Candidate 环境与 workspace 绝不携带
# endpoint/credential（api profile 只引用 env 名，值只存在于可信进程）。
# --------------------------------------------------------------------------- #

def test_candidate_bundle_contains_no_api_secrets(tmp_path):
    """候选工作区不允许出现 endpoint/credential 的 env 名或它们的值。

    RetryingModelClient 跑在 Harness 进程（可信侧）；Candidate 只拿任务数据。
    API profile 通过 endpoint_env/credential_env 命名环境变量，值永不下发。
    """
    task = make_task(tmp_path)
    (task / "public").mkdir(exist_ok=True)
    (task / "public" / "input.xyz").write_text("Si input")
    (task / "instruction.md").write_text("solve it")
    write_packagable_task_toml(task)

    bundle = tmp_path / "bundle"
    package_candidate(CaseSpec.load(task), bundle)

    secret_names = {"DFTWORLD_API_ENDPOINT", "DFTWORLD_API_KEY"}
    for path in bundle.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for name in secret_names:
            assert name not in content, f"endpoint/credential env 名泄漏进 {path}"
            value = os.environ.get(name)
            if value:
                assert value not in content, f"endpoint/credential 值泄漏进 {path}"


def test_transport_attempt_metadata_never_contains_credentials(tmp_path):
    """attempt 元数据（EventStore + metadata_digest）不含 endpoint/credential。

    Api profile 只引用 env 名；如果 profile 里被塞进了字面 endpoint/token，
    transport 构造必须拒绝，而不是把它写进公共记录。
    """
    from bench.core.event_store import EventStore
    from bench.core.model_transport import (
        HttpFailure,
        RetryingModelClient,
        TerminalApiError,
    )

    class Boom:
        async def request(self, operation_id, request, timeout_sec):
            return HttpFailure(503, body="<html>credential dump</html>")

    store = EventStore(tmp_path / "events.jsonl")
    client = RetryingModelClient(Boom(), base_delay_sec=0.0, max_delay_sec=0.0,
                                 max_attempts=1, events=store)
    with pytest.raises(TerminalApiError) as exc:
        import asyncio
        asyncio.run(client.request("MODEL-1", {"messages": []}))
    joined = " ".join(e.to_dict()["payload"].get("state", "") for e in store.load_events())
    for name in ("DFTWORLD_API_ENDPOINT", "DFTWORLD_API_KEY"):
        assert name not in joined and name not in exc.value.reason
    assert "<html>" not in exc.value.reason


def test_candidate_env_rejects_api_secret_names():
    """ClaudeCodeAdapter 构造时拒绝把 api-profile credential env 名转发进容器。

    模型 endpoint/credential 只存在于可信 Harness 进程；Candidate 容器环境
    即使有人显式传 `DFTWORLD_API_KEY` 也必须 fail-fast。run-scoped 的
    controller env（BENCH_HPC_*）不是 api credential 名，不受影响。
    """
    from bench.agents import ClaudeCodeAdapter
    from bench.core.model_transport import credential_env_names

    forbidden = credential_env_names(
        {"endpoint_env": "DFTWORLD_API_ENDPOINT", "credential_env": "DFTWORLD_API_KEY"}
    )
    with pytest.raises(ValueError, match="trusted API secrets"):
        ClaudeCodeAdapter(
            model="claude-3-7-sonnet-20250219",
            max_turns=8,
            threads_root=Path("/tmp/t"),
            task_name="001-hello",
            image="bench-candidate-claude-code-sandbox:v1",
            case_dir=Path("/tmp/c"),
            container_env={
                "DFTWORLD_API_KEY": "sk-fake",
                "DFTWORLD_API_ENDPOINT": "https://fake",
            },
            forbidden_env_names=forbidden,
        )

    # run-scoped controller env (gateway URL/token) is NOT an api credential
    adapter = ClaudeCodeAdapter(
        model="claude-3-7-sonnet-20250219",
        max_turns=8,
        threads_root=Path("/tmp/t"),
        task_name="001-hello",
        image="bench-candidate-claude-code-sandbox:v1",
        case_dir=Path("/tmp/c"),
        container_env={
            "BENCH_HPC_GATEWAY_URL": "http://host.docker.internal:9000",
            "BENCH_HPC_RUN_TOKEN": "abc",
        },
        forbidden_env_names=forbidden,
    )
    assert adapter.container_env["BENCH_HPC_RUN_TOKEN"] == "abc"
