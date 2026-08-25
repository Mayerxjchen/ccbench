"""Allowlist Case Packager: deterministic bundles, zero hidden-file leaks."""

from __future__ import annotations

from pathlib import Path

import pytest

from dftworld_bench.contracts.case import CaseSpec
from dftworld_bench.core.packager import PackageError, package_candidate


def _write_manifest(case: Path) -> None:
    (case / "task.toml").write_text(
        'schema_version = "1.2"\n'
        'artifacts = []\n'
        '[execution]\n'
        'class = "local_sandbox"\n'
        '\n'
        '[candidate]\n'
        'instruction = "instruction.md"\n'
        'submission_root = "."\n'
        'legacy_submission_layout = true\n'
        '\n'
        '[[candidate.files]]\n'
        'source = "public/**"\n'
        'destination = "."\n'
        'strip_prefix = "public"\n'
        '\n'
        '[task]\n'
        'name = "benchmark/fixture"\n'
        'description = "packager fixture"\n'
        '\n'
        '[agent]\n'
        'timeout_sec = 600.0\n'
        '\n'
        '[verifier]\n'
        'timeout_sec = 600.0\n'
        '[verifier.env]\n'
        '\n'
        '[environment]\n'
        "cpus = 2\n"
        "memory_mb = 4096\n"
        "storage_mb = 10240\n"
        "gpus = 0\n"
        "allow_internet = false\n"
        "[environment.env]\n"
        "[solution.env]\n",
        encoding="utf-8",
    )


def _build_case(root: Path, name: str = "case") -> Path:
    case = root / name
    (case / "public").mkdir(parents=True)
    (case / "public" / "input.xyz").write_text("Si input\n", encoding="utf-8")
    (case / "public" / "sub").mkdir()
    (case / "public" / "sub" / "data.txt").write_text("data\n", encoding="utf-8")
    for hidden in ("reference", "solution", "tests"):
        (case / hidden).mkdir(parents=True)
        (case / hidden / f"{hidden}.json").write_text("HIDDEN", encoding="utf-8")
    (case / "instruction.md").write_text("solve it\n", encoding="utf-8")
    _write_manifest(case)
    return case


@pytest.fixture
def case_fixture(tmp_path: Path) -> CaseSpec:
    return CaseSpec.load(_build_case(tmp_path))


def test_packager_is_allowlist_only_and_deterministic(case_fixture, tmp_path):
    first = package_candidate(case_fixture, tmp_path / "first")
    second = package_candidate(case_fixture, tmp_path / "second")
    assert first.public_digest == second.public_digest
    assert not (tmp_path / "first" / "reference").exists()
    assert not (tmp_path / "first" / "solution").exists()
    assert not (tmp_path / "first" / "tests").exists()


def test_packager_rejects_symlink_in_public(case_fixture, tmp_path):
    (case_fixture.path / "public" / "escape").symlink_to("../reference")
    with pytest.raises(PackageError, match="symlink"):
        package_candidate(case_fixture, tmp_path / "bundle")


def test_manifest_lists_exactly_the_allowlist(case_fixture, tmp_path):
    manifest = package_candidate(case_fixture, tmp_path / "bundle")
    paths = [f.path for f in manifest.files]
    assert sorted(paths) == ["input.xyz", "instruction.md", "sub/data.txt"]
    for record in manifest.files:
        assert record.sha256 == _sha256_of(tmp_path / "bundle" / record.path)


def test_instruction_always_lands_at_root(case_fixture, tmp_path):
    manifest = package_candidate(case_fixture, tmp_path / "bundle")
    assert any(f.path == "instruction.md" for f in manifest.files)
    assert (tmp_path / "bundle" / "instruction.md").read_text() == "solve it\n"


def test_public_digest_changes_with_content(case_fixture, tmp_path):
    first = package_candidate(case_fixture, tmp_path / "first")
    (case_fixture.path / "public" / "input.xyz").write_text("Si input CHANGED\n")
    second = package_candidate(case_fixture, tmp_path / "second")
    assert first.public_digest != second.public_digest


def test_destination_collision_is_rejected(tmp_path):
    case = _build_case(tmp_path)
    # Two distinct sources mapped onto the same destination.
    (case / "task.toml").write_text(
        (case / "task.toml").read_text().replace(
            'source = "public/**"\ndestination = "."\nstrip_prefix = "public"',
            'source = "public/input.xyz"\ndestination = "same"\n\n[[candidate.files]]\nsource = "public/sub/data.txt"\ndestination = "same"',
        ),
        encoding="utf-8",
    )
    spec = CaseSpec.load(case)
    with pytest.raises(PackageError, match="collision"):
        package_candidate(spec, tmp_path / "bundle")


def test_forbidden_name_in_destination_is_rejected(tmp_path):
    case = _build_case(tmp_path)
    (case / "public" / "tests").write_text("nope", encoding="utf-8")
    spec = CaseSpec.load(case)
    with pytest.raises(PackageError, match="leak-scan forbidden name"):
        package_candidate(spec, tmp_path / "bundle")


def _sha256_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
