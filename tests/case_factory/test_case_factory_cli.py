"""Task 6 — dftworld Case Factory CLI.

render / validate / diff must emit JSON to stdout, exit nonzero on failure,
never partially write on an invalid design, render into a private temp dir then
atomically replace only generated files, and accept exactly one explicit Case
directory.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CLI = [sys.executable, "-m", "dftworld_bench.case_factory"]

DESIGN = {
    "schema_version": 1,
    "category": "mlp",
    "case_kind": "final_model_retraining",
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


@pytest.fixture()
def case_dir(tmp_path):
    cd = tmp_path / "042-example"
    (cd / "public").mkdir(parents=True)
    (cd / "instruction.md").write_text("# Example\n", encoding="utf-8")
    (cd / "case-design.yaml").write_text(
        yaml.safe_dump(DESIGN, sort_keys=False), encoding="utf-8"
    )
    return cd


def _run(case_dir: Path, command: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [*CLI, command, str(case_dir), "--target", "dftworld"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"raw_stdout": proc.stdout, "stderr": proc.stderr}
    return proc.returncode, payload


def test_render_creates_generated_files(case_dir):
    rc, payload = _run(case_dir, "render")
    assert rc == 0, payload
    assert payload["valid"] is True
    assert payload["benchmark_valid"] is False
    for name in ("task.toml", "Dockerfile", ".dockerignore",
                 "source/dftworld-target.lock.json"):
        assert (case_dir / name).is_file(), f"{name} not created"


def test_render_emits_json_and_gates(case_dir):
    rc, payload = _run(case_dir, "render")
    assert rc == 0
    gates = payload["factory_gates"]
    assert gates["design_valid"] is True
    assert gates["target_adapter_valid"] is True
    assert gates["runtime_valid"] is False
    assert gates["candidate_smoke_valid"] is False


def test_render_then_validate_is_clean(case_dir):
    rc, _ = _run(case_dir, "render")
    assert rc == 0
    rc2, payload2 = _run(case_dir, "validate")
    assert rc2 == 0, payload2
    assert payload2["valid"] is True
    assert payload2["errors"] == []


def test_validate_fails_on_invalid_design(case_dir):
    (case_dir / "case-design.yaml").write_text(
        "execution:\n  class: quantum\n", encoding="utf-8"
    )
    rc, payload = _run(case_dir, "validate")
    assert rc != 0
    assert payload["valid"] is False


def test_render_fails_on_invalid_design_no_partial_write(case_dir):
    (case_dir / "case-design.yaml").write_text(
        "execution:\n  class: quantum\n", encoding="utf-8"
    )
    rc, payload = _run(case_dir, "render")
    assert rc != 0
    assert payload["valid"] is False
    assert not (case_dir / "task.toml").exists(), "partial write leaked"
    assert not (case_dir / "Dockerfile").exists()


def test_diff_reports_drift_after_tamper(case_dir):
    _run(case_dir, "render")
    (case_dir / "task.toml").write_text("drifted\n", encoding="utf-8")
    rc, payload = _run(case_dir, "diff")
    assert rc != 0
    assert "task.toml" in payload["drifted"]


def test_render_refuses_drift_by_default(case_dir):
    """Default render must fail on drifted generated files, never overwrite."""
    _run(case_dir, "render")
    (case_dir / "task.toml").write_text("DRIFTED\n", encoding="utf-8")
    rc, payload = _run(case_dir, "render")
    assert rc != 0
    assert payload["valid"] is False
    assert "drifted" in payload
    assert "task.toml" in payload["drifted"]
    # The drifted bytes must be untouched — default render refuses.
    assert (case_dir / "task.toml").read_text(encoding="utf-8") == "DRIFTED\n"


def test_render_force_generated_overwrites_drift(case_dir):
    """Explicit --force-generated shows the diff and overwrites the set."""
    _run(case_dir, "render")
    (case_dir / "task.toml").write_text("DRIFTED\n", encoding="utf-8")
    proc = subprocess.run(
        [*CLI, "render", str(case_dir), "--target", "dftworld", "--force-generated"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["valid"] is True
    assert "task.toml" in payload["drifted"]
    # The set is back to the adapter's expected bytes.
    rc2, out2 = _run(case_dir, "diff")
    assert rc2 == 0, out2
    assert out2["valid"] is True
    assert out2["drifted"] == []


def test_render_rolls_back_on_commit_failure(case_dir, monkeypatch):
    """A failure mid-commit restores every generated file whole."""
    import dftworld_bench.case_factory.cli as cli

    _run(case_dir, "render")
    original = {
        name: (case_dir / name).read_bytes()
        for name in ("task.toml", "Dockerfile", ".dockerignore")
    }

    calls = {"n": 0}
    real_replace = cli._replace_file

    def _flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("injected commit failure")
        return real_replace(src, dst)

    monkeypatch.setattr(cli, "_replace_file", _flaky)
    # The commit failure is rolled back then re-raised (never swallowed).
    with pytest.raises(OSError):
        cli.cmd_render(case_dir, "dftworld")
    # Every generated file was rolled back to its pre-render bytes.
    for name, content in original.items():
        assert (case_dir / name).read_bytes() == content, f"{name} not rolled back"


def test_render_rolls_back_created_files_on_fresh_commit_failure(case_dir, monkeypatch):
    """A failure during a FRESH render removes files this commit created.

    Only existing files were backed up before; newly created files must be
    deleted on rollback so no partial generated set survives.
    """
    import dftworld_bench.case_factory.cli as cli

    calls = {"n": 0}
    real_replace = cli._replace_file

    def _flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("injected fresh commit failure")
        return real_replace(src, dst)

    monkeypatch.setattr(cli, "_replace_file", _flaky)
    with pytest.raises(OSError):
        cli.cmd_render(case_dir, "dftworld")
    # No generated file may survive: the set was created entirely by this
    # failed commit.
    for name in ("task.toml", "Dockerfile", ".dockerignore"):
        assert not (case_dir / name).exists(), f"{name} left after fresh rollback"
    assert not (case_dir / "source" / "dftworld-target.lock.json").exists()


def test_render_lock_committed_last(tmp_path, monkeypatch):
    """The lock is the last file replaced — its digest covers the siblings."""
    import dftworld_bench.case_factory.cli as cli

    case_dir = tmp_path / "042-example"
    (case_dir / "public").mkdir(parents=True)
    (case_dir / "instruction.md").write_text("# Example\n", encoding="utf-8")
    (case_dir / "case-design.yaml").write_text(
        yaml.safe_dump(DESIGN, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(SystemExit):
        cli.cmd_render(case_dir, "dftworld")

    order: list[str] = []
    real_replace = cli._replace_file

    def _record(src, dst):
        order.append(dst.name)
        return real_replace(src, dst)

    monkeypatch.setattr(cli, "_replace_file", _record)
    with pytest.raises(SystemExit):
        cli.cmd_render(case_dir, "dftworld")
    assert order == ["task.toml", "Dockerfile", ".dockerignore"]


def test_render_preserves_prior_smoke_gates(case_dir):
    _run(case_dir, "render")
    # Simulate a later independent check that set candidate_smoke_valid=true.
    # The lock is written exactly as the adapter renders it (sorted, trailing
    # newline) so the drift check accepts the gate change as a legit state.
    lock = case_dir / "source" / "dftworld-target.lock.json"
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["factory_gates"]["candidate_smoke_valid"] = True
    lock.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Re-render must not clobber the smoke gate.
    rc, out = _run(case_dir, "render")
    assert rc == 0, out
    assert out["factory_gates"]["candidate_smoke_valid"] is True


def test_render_never_touches_authored_files(case_dir):
    (case_dir / "public" / "data.bin").write_bytes(b"\x00\x01")
    (case_dir / "reference").mkdir()
    (case_dir / "reference" / "thresholds.json").write_text("{}", encoding="utf-8")
    _run(case_dir, "render")
    assert (case_dir / "public" / "data.bin").read_bytes() == b"\x00\x01"
    assert (case_dir / "reference" / "thresholds.json").read_text() == "{}"
    assert (case_dir / "instruction.md").exists()


def test_missing_design_fails(case_dir):
    (case_dir / "case-design.yaml").unlink()
    rc, payload = _run(case_dir, "validate")
    assert rc != 0
    assert payload["valid"] is False


def test_unknown_target_fails(case_dir):
    proc = subprocess.run(
        [*CLI, "validate", str(case_dir), "--target", "nonexistent"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode != 0
