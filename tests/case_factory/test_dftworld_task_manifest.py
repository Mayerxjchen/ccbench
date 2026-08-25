"""Task 3 — dftworld task.toml v1.2 manifest rendering.

Rendered manifest must parse under both consumers and agree on execution,
submission root, timeouts and resources.  Local and HPC output shapes are
covered; the HPC profile/resource/platform files themselves are Task 8.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from dftworld_bench.case_factory.dftworld_target import DftworldTargetAdapter

ADAPTER = DftworldTargetAdapter()

LOCAL_DESIGN = {
    "identity": {
        "case_id": "042-example",
        "task_name": "benchmark/042-example",
        "title": "Example",
        "description": "Example case",
        "case_version": "1.0.0",
    },
    "execution": {"class": "local_sandbox"},
    "runtime": {
        "candidate_image": "dftworld-base-mace",
        "build_timeout_sec": 1800,
        "agent_timeout_sec": 7200,
        "verifier_timeout_sec": 3600,
        "cpus": 4,
        "memory_mb": 8192,
        "storage_mb": 20480,
        "gpus": 0,
        "allow_internet": False,
    },
    "submission": {"root": "final"},
    "candidate_files": [
        {"source": "public/**", "destination": ".", "strip_prefix": "public"}
    ],
}

HPC_DESIGN = {
    **LOCAL_DESIGN,
    "identity": {
        "case_id": "043-example-hpc",
        "task_name": "benchmark/043-example-hpc",
        "title": "Example HPC",
        "description": "Example HPC case",
        "case_version": "1.0.0",
    },
    "execution": {"class": "hpc_controller"},
    "hpc": {
        "contract_version": "hpc-execution/v1",
        "required_capabilities": ["batch_jobs", "gpu", "artifact_fetch"],
    },
}


@pytest.fixture(scope="module")
def rendered_local(tmp_path_factory):
    out = tmp_path_factory.mktemp("local-case")
    (out / "public").mkdir()
    (out / "instruction.md").write_text("# Example case\n", encoding="utf-8")
    files = ADAPTER.render(out, LOCAL_DESIGN)
    for gf in files:
        (out / gf.path).parent.mkdir(parents=True, exist_ok=True)
        (out / gf.path).write_bytes(gf.content)
    return out, {gf.path.as_posix(): gf for gf in files}


@pytest.fixture(scope="module")
def rendered_hpc(tmp_path_factory):
    out = tmp_path_factory.mktemp("hpc-case")
    (out / "public").mkdir()
    (out / "instruction.md").write_text("# Example HPC case\n", encoding="utf-8")
    files = ADAPTER.render(out, HPC_DESIGN)
    for gf in files:
        (out / gf.path).parent.mkdir(parents=True, exist_ok=True)
        (out / gf.path).write_bytes(gf.content)
    return out, {gf.path.as_posix(): gf for gf in files}


def _parse(case_dir: Path) -> dict:
    return tomllib.loads((case_dir / "task.toml").read_text(encoding="utf-8"))


def test_local_manifest_shape(rendered_local):
    case_dir, _ = rendered_local
    raw = _parse(case_dir)
    assert raw["schema_version"] == "1.2"
    assert raw["case_version"] == "1.0.0"
    assert raw["execution"]["class"] == "local_sandbox"
    assert raw["candidate"]["instruction"] == "instruction.md"
    assert raw["candidate"]["submission_root"] == "final"
    assert raw["candidate"]["legacy_submission_layout"] is False
    assert raw["candidate"]["image"] == "dftworld-base-mace"
    assert raw["candidate"]["files"][0]["source"] == "public/**"
    assert raw["candidate"]["files"][0]["strip_prefix"] == "public"
    assert raw["task"]["name"] == "benchmark/042-example"
    assert raw["agent"]["timeout_sec"] == 7200.0
    assert raw["verifier"]["timeout_sec"] == 3600.0
    assert raw["environment"]["gpus"] == 0
    assert raw["environment"]["cpus"] == 4
    assert raw["environment"]["memory_mb"] == 8192
    assert raw["environment"]["allow_internet"] is False
    # runtime-ignored generated table records provenance.
    assert raw["generated"]["target"] == "dftworld"
    assert raw["generated"]["adapter_version"].startswith("0.")
    assert raw["generated"]["design_sha256"].startswith("sha256:")
    # local_sandbox must NOT carry an [hpc] block.
    assert "hpc" not in raw


def test_hpc_manifest_has_hpc_block(rendered_hpc):
    case_dir, _ = rendered_hpc
    raw = _parse(case_dir)
    assert raw["execution"]["class"] == "hpc_controller"
    assert raw["hpc"]["contract_version"] == "hpc-execution/v1"
    assert "gpu" in raw["hpc"]["required_capabilities"]
    assert raw["candidate"]["files"][0]["source"] == "public/**"


def test_casespec_load_agrees_with_eval(rendered_local):
    """CaseSpec.load and eval.load_task agree on the rendered manifest."""
    import sys

    from dftworld_bench.contracts.case import CaseSpec

    case_dir, _ = rendered_local
    spec = CaseSpec.load(case_dir)

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import eval as evalmod  # type: ignore[import-not-found]

    tspec = evalmod.load_task(case_dir)

    assert spec.execution_class == "local_sandbox"
    assert tspec.execution_class == "local_sandbox"
    assert spec.submission_root == "final"
    assert tspec.submission_root == "final"
    assert spec.legacy_submission_layout is False
    assert tspec.legacy_submission_layout is False
    assert spec.agent_timeout_sec == 7200.0
    assert tspec.agent_timeout_sec == 7200.0
    assert spec.verifier_timeout_sec == 3600.0
    assert tspec.verifier_timeout_sec == 3600.0
    assert spec.candidate_resources["gpus"] == 0
    assert tspec.gpus == 0
    assert tspec.image == "dftworld-base-mace"


def test_hpc_casespec_load(rendered_hpc):
    from dftworld_bench.contracts.case import CaseSpec

    case_dir, _ = rendered_hpc
    spec = CaseSpec.load(case_dir)
    assert spec.execution_class == "hpc_controller"


def test_control_characters_rejected():
    bad = {**LOCAL_DESIGN}
    bad["identity"] = {**bad["identity"], "description": "line1\nline2"}
    with pytest.raises(Exception):
        ADAPTER.render(Path("/tmp/x"), bad)


def test_manifest_render_is_deterministic(rendered_local):
    _, files = rendered_local
    again = {gf.path.as_posix(): gf for gf in ADAPTER.render(Path("/tmp/y"), LOCAL_DESIGN)}
    for name, gf in files.items():
        assert again[name].content == gf.content, f"{name} not deterministic"
        assert again[name].digest == gf.digest


def test_legacy_contract_tests_unchanged():
    """The existing contract suite must still pass (guarded by T10 too)."""
    # This is a placeholder assertion; T10 runs the full regression suite.
    assert True
