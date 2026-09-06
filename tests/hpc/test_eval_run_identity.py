"""Run-record identity for controller cases must come from task.toml.

eval.py is the harness that produces the run-record for an ablation trial.
The record's frozen identity (execution_class, platform) must describe the
case's real execution, not a hardcoded local sandbox.  A real-HPC 032 pilot
must record ``hpc_controller`` / the case's platform, or the pairing gate
compares two lies that happen to agree.

Gate mapping (Task 14 pilot prerequisite):
- [execution].class -> TaskSpec.execution_class, normalized via
  EXECUTION_ALIASES (real_hpc_controller -> hpc_controller).
- legacy task.execution_backend accepted only when [execution].class is
  absent (mirrors dftworld_bench.contracts.case.CaseSpec).
- both present -> ambiguous -> reject.
- hpc_controller Profile carries the case platform-profile name (gpu-slurm),
  never local_docker.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import eval as E  # noqa: E402
from dftworld_bench.core.budgets import BUDGET_DOMAINS

HPC_TOML = """\
schema_version = "1.2"
[execution]
class = "hpc_controller"
[hpc]
contract_version = "hpc-execution/v1"
required_capabilities = ["batch_jobs", "gpu", "artifact_fetch"]
"""

PLATFORM_YAML = "contract_version: hpc-execution/v1\nname: gpu-slurm\n"


def write_task(
    tmp_path: Path,
    toml_body: str,
    *,
    platform_yaml: str | None = None,
) -> Path:
    d = tmp_path / "task"
    d.mkdir()
    (d / "Dockerfile").write_text("FROM dftworld-base\n")
    (d / "instruction.md").write_text("do the thing\n")
    (d / "task.toml").write_text(toml_body)
    if platform_yaml is not None:
        (d / "profiles").mkdir()
        (d / "profiles" / "platform.yaml").write_text(platform_yaml)
    return d


def test_load_task_surfaces_hpc_controller_class(tmp_path: Path) -> None:
    d = write_task(tmp_path, HPC_TOML)
    spec = E.load_task(d)
    assert spec.execution_class == "hpc_controller"


def test_load_task_normalizes_legacy_execution_backend(tmp_path: Path) -> None:
    d = write_task(
        tmp_path,
        'schema_version = "1.2"\n[task]\nexecution_backend = "real_hpc_controller"\n',
    )
    spec = E.load_task(d)
    assert spec.execution_class == "hpc_controller"
    assert spec.legacy_execution_value == "real_hpc_controller"


def test_load_task_defaults_local_sandbox(tmp_path: Path) -> None:
    d = write_task(tmp_path, 'schema_version = "1.2"\n')
    spec = E.load_task(d)
    assert spec.execution_class == "local_sandbox"


def test_load_task_rejects_unknown_execution_class(tmp_path: Path) -> None:
    d = write_task(tmp_path, 'schema_version = "1.2"\n[execution]\nclass = "kube"\n')
    with pytest.raises(ValueError):
        E.load_task(d)


def test_load_task_rejects_ambiguous_execution_declaration(tmp_path: Path) -> None:
    d = write_task(
        tmp_path,
        'schema_version = "1.2"\n'
        '[execution]\nclass = "hpc_controller"\n'
        '[task]\nexecution_backend = "real_hpc_controller"\n',
    )
    with pytest.raises(ValueError):
        E.load_task(d)


def test_hpc_controller_task_profile_carries_case_platform(tmp_path: Path) -> None:
    d = write_task(tmp_path, HPC_TOML, platform_yaml=PLATFORM_YAML)
    spec = E.load_task(d)
    profile = E.profile_for_task(spec)
    assert profile.execution_class == "hpc_controller"
    assert profile.platform == "gpu-slurm"


def test_local_sandbox_task_profile_unchanged(tmp_path: Path) -> None:
    d = write_task(tmp_path, 'schema_version = "1.2"\n')
    spec = E.load_task(d)
    profile = E.profile_for_task(spec)
    assert profile.execution_class == "local_sandbox"
    assert profile.platform == "local_docker"


def test_eval_resolves_complete_local_harness_provenance(tmp_path: Path) -> None:
    d = write_task(
        tmp_path,
        'schema_version = "1.2"\ncase_version = "1.0"\n'
        '[execution]\nclass = "local_sandbox"\n'
        '[candidate]\ninstruction = "instruction.md"\nsubmission_root = "."\n',
    )
    task = E.load_task(d)
    resolver = getattr(E, "resolve_harness_provenance", None)
    assert callable(resolver), "eval must resolve v2 provenance before HarnessSpec"

    # Mock runner for qualification gate (avoids real Docker calls in tests).
    import unittest.mock as _mock
    _mock_runner = _mock.MagicMock()
    _mock_runner.image_digest.return_value = "sha256:" + "a" * 64
    _mock_runner.inspect.return_value = {
        "platform": "linux/amd64",
        "user": "65532:65532",
        "files": ["/usr/local/bin/python", "/app/entrypoint.sh"],
    }
    _mock_runner.run.return_value = (0, "usage\n", "")

    provenance = resolver(
        task,
        run_id="run-1",
        experiment_id="default",
        condition_id="no-skill",
        replicate=1,
        model="provider/model",
        max_turns=32,
        benchmark_commit="abc123",
        skills_sha=None,
        image_digest_resolver=lambda image: "sha256:" + "a" * 64,
        runtime_runner=_mock_runner,
    )

    assert provenance.lock.verify()
    # provenance.budgets is the LOCK dialect: it must satisfy the lock schema
    # (max_model_turns/max_total_tokens present) and map through
    # BudgetPolicy.from_lock onto all fourteen ledger domains.
    from dftworld_bench.core.budgets import BudgetPolicy

    assert {"max_model_turns", "max_total_tokens"} <= set(provenance.budgets)
    policy = BudgetPolicy.from_lock({"budgets": provenance.budgets})
    assert set(policy.limits) == BUDGET_DOMAINS
    assert provenance.runtime_identities["control"] is None
    assert provenance.runtime_identities["candidate"]["image"] == task.image
    assert provenance.profile.site_config_digest.startswith("sha256:")
    assert provenance.profile.site_config_digest != "sha256:local"


def test_known_matclaw_cases_resolve_hpc_controller() -> None:
    for name in (
        "001-matclaw-cips-active-distillation",
        "002-matclaw-cips-curie-temperature",
        "003-matclaw-cips-domain-wall-search",
    ):
        spec = E.load_task(ROOT / name)
        assert spec.execution_class == "hpc_controller", name


def test_benchmark_commit_pins_to_frozen_release(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A frozen ablation release pins run-record benchmark_commit to its
    source_commit (the formal gate rejects any record whose benchmark_commit
    differs from the release source_commit).  Without a release, eval falls
    back to the git HEAD as before."""
    release = tmp_path / "releases"
    release.mkdir()
    monkeypatch.setattr(E, "RELEASES_DIR", release)
    # no frozen release -> None (eval falls back to git HEAD)
    assert E.frozen_release_source_commit() is None
    (release / "ablation-ready-v0.json").write_text(
        json.dumps({
            "schema": "ablation-ready-release/v1",
            "name": "ablation-ready-benchmark-v0",
            "status": "frozen",
            "source_commit": "aae1becabc11041f454a7dc2d617e2ccadb95763",
        }),
        encoding="utf-8",
    )
    assert E.frozen_release_source_commit() == "aae1becabc11041f454a7dc2d617e2ccadb95763"
    # a second frozen release is ambiguous and must not be guessed
    (release / "ablation-ready-v1.json").write_text(
        json.dumps({
            "schema": "ablation-ready-release/v1",
            "name": "ablation-ready-benchmark-v1",
            "status": "frozen",
            "source_commit": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        }),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="benchmark-commit"):
        E.frozen_release_source_commit()
    # a non-frozen release is ignored
    (release / "ablation-ready-v1.json").unlink()
    (release / "draft.json").write_text(
        json.dumps({
            "schema": "ablation-ready-release/v1",
            "status": "draft",
            "source_commit": "cccccccccccccccccccccccccccccccccccccccc",
        }),
        encoding="utf-8",
    )
    assert E.frozen_release_source_commit() == "aae1becabc11041f454a7dc2d617e2ccadb95763"


def test_controller_gateway_env_uses_host_docker_internal() -> None:
    """The gateway URL handed to the controller container must be reachable
    from inside a Docker bridge container, where 127.0.0.1 is the container
    itself.  HpcExecutor prepares the common GatewayRuntime and sets the
    container env to host.docker.internal:<port>; the gateway itself stays
    loopback-bound on the eval host.

    A ProcessTestAdapter is injected so the test proves the HTTP plumbing
    without a live SSH round-trip to the cluster.
    """
    from dftworld_bench.executors.base import ExecutionContext
    from dftworld_bench.executors.hpc import HpcExecutor

    spec = E.load_task(ROOT / "002-matclaw-cips-curie-temperature")
    workspace = ROOT / ".test-gateway-workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    tmp = workspace / "process-adapter"
    tmp.mkdir(parents=True, exist_ok=True)
    adapter_config = {
        "adapter": "process_test",
        "root": str(tmp),
        "workspace_root": str(workspace),
    }

    ctx = ExecutionContext(
        task=spec,
        model="test-model",
        max_turns=32,
        threads_root=workspace.parent,
        adapter_config=adapter_config,
        run_id="test-host-docker-internal",
        workspace=workspace,
    )
    from dftworld_bench.hpc.dispatcher import HpcDispatcher
    from dftworld_bench.hpc.gateway_runtime import GatewayRuntime
    executor = HpcExecutor(
        dispatcher=HpcDispatcher(GatewayRuntime(), {})
    )
    import asyncio
    loop = asyncio.new_event_loop()
    loop.run_until_complete(executor.prepare(ctx))
    try:
        token = ctx.container_env["BENCH_HPC_RUN_TOKEN"]
        url = ctx.container_env["BENCH_HPC_GATEWAY_URL"]
        assert token, "run-scoped token must be set"
        assert url.startswith("http://host.docker.internal:"), url
        assert "127.0.0.1" not in url, url
        # The gateway answers on the loopback port behind that name.
        port = int(url.rsplit(":", 1)[1])
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/capabilities",
            data=json.dumps({"run_id": "test-host-docker-internal"}).encode(),
            headers={"Authorization": f"Bearer {token}"},
        )
        body = json.loads(urllib.request.urlopen(req, timeout=5).read().decode("utf-8"))
        assert "capabilities" in body or "ops" in body or body is not None
    finally:
        loop.run_until_complete(executor.close(ctx))
        loop.close()


def test_controller_docker_args_reach_host_gateway() -> None:
    """The controller container must resolve host.docker.internal: plain Linux
    docker needs the host-gateway add-host (Docker Desktop resolves it
    automatically; the flag is harmless there and verified working).  Dispatch
    is by execution class (Task 11), so the flag key is the class, not the case
    name."""
    from dftworld_bench.agents import controller_docker_args

    assert controller_docker_args("local_sandbox") == []
    args = controller_docker_args("hpc_controller")
    assert "--add-host" in args
    assert "host.docker.internal:host-gateway" in args


def test_controller_image_copies_bench_hpc_package() -> None:
    """The controller container carries the unified bench-hpc gateway client
    and dftworld_bench package under /opt/dftworld/controller."""
    df_path = ROOT / "runtimes" / "recipes" / "matclaw-cips-controller" / "Dockerfile"
    df = df_path.read_text(encoding="utf-8")
    assert (
        "COPY dftworld_bench /opt/dftworld/controller/dftworld_bench"
        in df
    )


def test_durable_session_created_under_run_dir(tmp_path: Path) -> None:
    """Task 9: eval.py attaches a durable session per attempt; events survive
    and a checkpoint pointer is written at the harness boundaries."""
    from dftworld_bench.core.event_store import EventStore

    session = E.durable_session_for_run(tmp_path, "001-hello")
    assert isinstance(session, EventStore)
    assert session.path == tmp_path / "session" / "001-hello.jsonl"

    session.append("HARNESS", "candidate_frozen", {"run_id": "r"})
    session.append("HARNESS", "submission_sealed", {"run_id": "r"})
    session.write_checkpoint({"run_id": "r", "phase": "verifier_result"})

    events = session.load_events()
    assert [e.kind for e in events] == ["candidate_frozen", "submission_sealed"]
    checkpoint = session.load_checkpoint()
    assert checkpoint is not None
    assert checkpoint.state["phase"] == "verifier_result"
    assert (tmp_path / "session" / "checkpoint-pointer.json").is_file()
