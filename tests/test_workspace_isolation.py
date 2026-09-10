"""Active workspace isolation tests for the allowlist packager.

Historical Dockerfile/eval composition tests remain in the retired archive.  The
active release gate is package_candidate and its hidden-asset boundary.
"""

from pathlib import Path

import pytest

from bench.contracts.case import CaseSpec
from bench.core.packager import package_candidate


def make_task(tmp_path: Path) -> Path:
    task = tmp_path / "task"
    for directory, name in (
        ("reference", "reference.json"),
        ("solution", "solve.sh"),
        ("tests", "test_outputs.py"),
    ):
        (task / directory).mkdir(parents=True)
        (task / directory / name).write_text("HIDDEN")
    return task


def hidden_leaked(workspace: Path) -> list[str]:
    return [
        str(path.relative_to(workspace))
        for path in workspace.rglob("*")
        if any(part in path.parts for part in ("reference", "solution", "tests"))
    ]


def write_packagable_task_toml(task: Path) -> None:
    (task / "task.toml").write_text(
        'schema_version = "1.2"\n'
        '[execution]\nclass = "local_sandbox"\n'
        '[candidate]\ninstruction = "instruction.md"\n'
        'submission_root = "."\nlegacy_submission_layout = true\n'
        '[[candidate.files]]\nsource = "public/**"\ndestination = "."\n'
        'strip_prefix = "public"\n'
        '[task]\ndescription = "packager gate"\n'
        '[agent]\ntimeout_sec = 600.0\n'
        '[verifier]\ntimeout_sec = 600.0\n'
        '[verifier.env]\n[environment]\ncpus = 2\n'
        'memory_mb = 4096\nstorage_mb = 10240\ngpus = 0\n'
        'allow_internet = false\n[environment.env]\n[solution.env]\n',
        encoding="utf-8",
    )


def test_packager_gate_no_wholesale_copy(tmp_path: Path) -> None:
    task = make_task(tmp_path)
    (task / "public").mkdir()
    (task / "public" / "input.xyz").write_text("Si input")
    (task / "instruction.md").write_text("solve it")
    write_packagable_task_toml(task)

    manifest = package_candidate(CaseSpec.load(task), tmp_path / "bundle")
    assert sorted(file.path for file in manifest.files) == ["input.xyz", "instruction.md"]
    assert hidden_leaked(tmp_path / "bundle") == []


def test_packager_gate_detects_drift(tmp_path: Path) -> None:
    task = make_task(tmp_path)
    (task / "public").mkdir()
    (task / "public" / "input.xyz").write_text("Si input")
    (task / "instruction.md").write_text("solve it")
    write_packagable_task_toml(task)

    first = package_candidate(CaseSpec.load(task), tmp_path / "first")
    second = package_candidate(CaseSpec.load(task), tmp_path / "second")
    assert first.public_digest == second.public_digest
    (task / "public" / "input.xyz").write_text("changed")
    assert first.public_digest != package_candidate(CaseSpec.load(task), tmp_path / "drift").public_digest


def test_candidate_bundle_contains_no_api_secrets(tmp_path: Path) -> None:
    task = make_task(tmp_path)
    (task / "public").mkdir()
    (task / "public" / "input.xyz").write_text("Si input")
    (task / "instruction.md").write_text("solve it")
    write_packagable_task_toml(task)
    bundle = tmp_path / "bundle"
    package_candidate(CaseSpec.load(task), bundle)
    for path in bundle.rglob("*"):
        if path.is_file():
            assert "DFTWORLD_API_KEY" not in path.read_text(errors="replace")


def test_candidate_env_rejects_api_secret_names() -> None:
    from bench.agents import ClaudeCodeAdapter

    with pytest.raises(ValueError, match="trusted API secrets"):
        ClaudeCodeAdapter(
            model="claude-3-7-sonnet-20250219",
            max_turns=8,
            threads_root=Path("/tmp/t"),
            task_name="fixture",
            image="candidate:fixture",
            case_dir=Path("/tmp/c"),
            container_env={"DFTWORLD_API_KEY": "sk-fake"},
            forbidden_env_names={"DFTWORLD_API_KEY"},
        )
