"""Case-level evaluator bundle identity (plan Task 2).

One immutable digest per case over the hidden evaluator assets
(tests/solution/hidden reference/profiles). Deterministic — independent of
mtime, ownership, and walk order. Public projections expose the digest but
never the file list.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.evidence.build_evaluator_manifest import build_evaluator_manifest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def case_032(tmp_path: Path) -> Path:
    case = tmp_path / "032-matclaw-cips-curie-temperature"
    (case / "tests").mkdir(parents=True)
    (case / "solution").mkdir()
    (case / "reference").mkdir()
    (case / "profiles").mkdir()
    (case / "public").mkdir()
    (case / "tests" / "verifier.py").write_text("def verify(): return True\n")
    (case / "tests" / "test_outputs.py").write_text("assert True\n")
    (case / "solution" / "solve.sh").write_text("#!/bin/bash\necho solve\n")
    (case / "reference" / "source.lock.json").write_text('{"model_sha256":"x"}\n')
    (case / "profiles" / "formal.yaml").write_text("profile: paper\n")
    (case / "public" / "task.toml").write_text("public agent-visible\n")
    (case / "scratch.bin").write_bytes(b"\x00" * 4)
    return case


def test_evaluator_digest_is_independent_of_mtime(case_032: Path) -> None:
    first = build_evaluator_manifest(case_032)
    os.utime(case_032 / "tests" / "verifier.py", (1, 1))
    second = build_evaluator_manifest(case_032)
    assert first.bundle_sha256 == second.bundle_sha256


def test_public_projection_contains_digest_but_not_hidden_paths(case_032: Path) -> None:
    manifest = build_evaluator_manifest(case_032)
    public = manifest.public_projection()
    assert public["evaluator_bundle_sha256"] == manifest.bundle_sha256
    assert "files" not in public
    assert public["bundle_sha256"] == manifest.bundle_sha256


def test_public_and_scratch_are_excluded(case_032: Path) -> None:
    manifest = build_evaluator_manifest(case_032)
    paths = [f["path"] for f in manifest.files]
    assert "public/task.toml" not in paths
    assert "scratch.bin" not in paths
    assert "tests/verifier.py" in paths
    assert "solution/solve.sh" in paths


def test_digest_changes_when_evaluator_content_changes(case_032: Path) -> None:
    first = build_evaluator_manifest(case_032)
    (case_032 / "tests" / "verifier.py").write_text("def verify(): return False\n")
    second = build_evaluator_manifest(case_032)
    assert first.bundle_sha256 != second.bundle_sha256


def test_construction_state_flagged_for_034(case_032: Path) -> None:
    manifest = build_evaluator_manifest(case_032, state="construction")
    assert manifest.state == "construction"
    assert manifest.to_dict()["state"] == "construction"


def test_escaping_symlink_is_rejected(tmp_path: Path) -> None:
    case = tmp_path / "case"
    (case / "tests").mkdir(parents=True)
    (case / "tests" / "verifier.py").write_text("x\n")
    os.symlink(tmp_path / "outside.txt", case / "tests" / "leak.py")
    with pytest.raises(ValueError, match="escapes"):
        build_evaluator_manifest(case)


def test_internal_symlink_hashes_target_bytes(tmp_path: Path) -> None:
    case = tmp_path / "case"
    (case / "tests").mkdir(parents=True)
    (case / "tests" / "model.data").write_bytes(b"checkpoint-bytes")
    os.symlink(case / "tests" / "model.data", case / "tests" / "model.data-alias")
    manifest = build_evaluator_manifest(case)
    by_name = {f["path"]: f["sha256"] for f in manifest.files}
    assert by_name["tests/model.data"] == by_name["tests/model.data-alias"]


def test_relative_manifest_matches_json_roundtrip(case_032: Path) -> None:
    manifest = build_evaluator_manifest(case_032)
    data = manifest.to_dict()
    assert data["bundle_sha256"] == manifest.bundle_sha256
    assert len(data["files"]) == 5  # tests x2 + solution + reference + profiles
