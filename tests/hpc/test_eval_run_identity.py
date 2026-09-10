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
  absent (mirrors bench.contracts.case.CaseSpec).
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
from bench.core.budgets import BUDGET_DOMAINS

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
    from bench.core.budgets import BudgetPolicy

    assert {"max_model_turns", "max_total_tokens"} <= set(provenance.budgets)
    policy = BudgetPolicy.from_lock({"budgets": provenance.budgets})
    assert set(policy.limits) == BUDGET_DOMAINS
    assert provenance.runtime_identities["control"] is None
    assert provenance.runtime_identities["candidate"]["image"] == task.image
    assert provenance.profile.site_config_digest.startswith("sha256:")
    assert provenance.profile.site_config_digest != "sha256:local"


def test_known_matclaw_cases_resolve_portable_candidate() -> None:
    from bench.contracts.case import CaseSpec
    for name in (
        "001-matclaw-cips-active-distillation",
        "002-matclaw-cips-curie-temperature",
        "003-matclaw-cips-domain-wall-search",
    ):
        spec = E.load_task(ROOT / name)
        assert spec.execution_class == "local_sandbox", name
        assert CaseSpec.load(ROOT / "cases" / name).candidate_runner == "container_claude_code", name


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


def test_candidate_gateway_is_internal_and_candidate_has_no_scheduler_credentials() -> None:
    """The unified Candidate path has no case-owned scheduler gateway.

    Compute-profile operators own routing credentials; Candidate receives only
    the model proxy endpoint.  Keep this assertion static so the contract can
    be tested on CI hosts without permission to bind a local socket.
    """
    from bench.agents import ClaudeCodeAdapter

    adapter = ClaudeCodeAdapter(
        model="deepseek-v4-pro[1M]",
        threads_root=ROOT / ".test-gateway-workspace",
        task_name="candidate-route",
        case_dir=ROOT / "cases" / "002-matclaw-cips-curie-temperature",
        session_id="run-1",
        workspace=ROOT,
    )
    assert adapter.model == "deepseek-v4-pro[1M]"
    assert "BENCH_HPC_RUN_TOKEN" not in adapter.container_env
    assert "BENCH_HPC_GATEWAY_URL" not in adapter.container_env
    assert adapter.cli_model == "claude-sonnet-4-6"
    assert adapter.upstream_model == "deepseek-v4-pro[1M]"


def test_controller_docker_args_reach_host_gateway() -> None:
    """The controller container must resolve host.docker.internal: plain Linux
    docker needs the host-gateway add-host (Docker Desktop resolves it
    automatically; the flag is harmless there and verified working).  Dispatch
    is by execution class (Task 11), so the flag key is the class, not the case
    name."""
    from bench.agents import controller_docker_args

    assert controller_docker_args("local_sandbox") == []
    args = controller_docker_args("hpc_controller")
    assert "--add-host" in args
    assert "host.docker.internal:host-gateway" in args


def test_candidate_recipe_is_pinned_nonroot_and_up_to_date() -> None:
    """The active Candidate recipe is independent of retired HPC controllers."""
    df_path = ROOT / "runtimes" / "recipes" / "claude-code-candidate-base" / "Dockerfile"
    df = df_path.read_text(encoding="utf-8")
    assert "node:22-bookworm-slim" in df
    assert "USER 10001:10001" in df
    assert 'CMD ["sleep", "3600"]' in df


def test_durable_session_created_under_run_dir(tmp_path: Path) -> None:
    """Task 9: eval.py attaches a durable session per attempt; events survive
    and a checkpoint pointer is written at the harness boundaries."""
    from bench.core.event_store import EventStore

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
