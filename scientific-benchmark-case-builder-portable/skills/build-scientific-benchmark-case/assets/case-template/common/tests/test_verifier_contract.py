#!/usr/bin/env python3
"""Construction-time self-test of the runtime verifier (builder side).

This file proves, in a faithful /tests + sealed-submission + result-dir mount
layout, that tests/verifier.py + tests/test.sh always produce a schema-valid
common result.json and classify empty/forged/missing-model/broken-lineage
submissions as AGENT_FAILURE. It is a case-construction quality test: it runs
under pytest at build time and is never invoked from tests/test.sh.

Run from anywhere: all paths are computed relative to this file, never from
the current working directory.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
CASE_DIR = TESTS_DIR.parent
FIXTURES = TESTS_DIR / "fixtures"
RESULT_FIELDS = {
    "run_id", "result_class", "failure_code", "reason",
    "retryable", "is_counted_scientifically",
}


def run_test_sh(tmp_path: Path, submission: Path | None) -> tuple[dict, int]:
    """Mount tests/ at an isolated path and run the entry with a sealed root."""
    mount = tmp_path / "tests"
    shutil.copytree(TESTS_DIR, mount)
    result_dir = tmp_path / "logs" / "verifier"
    env = {"RESULT_DIR": str(result_dir), "BENCH_RUN_ID": "contract-smoke", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    if submission is not None:
        env["SUBMISSION_ROOT"] = str(submission)
    proc = subprocess.run(
        ["bash", str(mount / "test.sh")],
        text=True, capture_output=True, env=env, cwd=tmp_path,
    )
    result_path = result_dir / "result.json"
    assert result_path.is_file(), f"test.sh never wrote result.json: {proc.stdout}\n{proc.stderr}"
    return json.loads(result_path.read_text(encoding="utf-8")), proc.returncode


def sealed_fixture(tmp_path: Path, name: str) -> Path:
    dest = tmp_path / "submission"
    shutil.copytree(FIXTURES / name, dest)
    return dest


def _assert_common_schema(result: dict) -> None:
    assert RESULT_FIELDS >= set(result), f"unexpected result.json fields: {set(result)}"
    assert {"run_id", "result_class", "failure_code", "reason", "retryable"} <= set(result)
    assert result["result_class"] in {"VALID_RESULT", "AGENT_FAILURE", "INFRA_INVALID"}


def test_valid_structural_submission_passes_as_diagnostic(tmp_path: Path) -> None:
    result, code = run_test_sh(tmp_path, sealed_fixture(tmp_path, "positive/structural-minimal"))
    _assert_common_schema(result)
    assert code == 0
    assert result["result_class"] == "VALID_RESULT"
    assert result["failure_code"] == "PASS"
    # The technical-chain PASS must be honest about what it did NOT prove.
    assert "deferred" in result["reason"]
    assert "Discovery diagnostic" in result["reason"]


def test_empty_submission_is_agent_failure_not_infra(tmp_path: Path) -> None:
    result, code = run_test_sh(tmp_path, sealed_fixture(tmp_path, "negative/empty"))
    _assert_common_schema(result)
    assert code == 1
    assert result["result_class"] == "AGENT_FAILURE"
    assert result["failure_code"] == "NO_SUBMISSION"
    assert result["retryable"] is False


def test_missing_manifest_is_no_submission(tmp_path: Path) -> None:
    submission = tmp_path / "submission"
    submission.mkdir()
    (submission / "notes.txt").write_text("no manifest here\n", encoding="utf-8")
    result, code = run_test_sh(tmp_path, submission)
    _assert_common_schema(result)
    assert code == 1
    assert result["result_class"] == "AGENT_FAILURE"
    assert result["failure_code"] == "NO_SUBMISSION"


def test_malformed_manifest_is_invalid_submission(tmp_path: Path) -> None:
    submission = sealed_fixture(tmp_path, "positive/structural-minimal")
    (submission / "manifest.json").write_text("{ not json", encoding="utf-8")
    result, code = run_test_sh(tmp_path, submission)
    _assert_common_schema(result)
    assert code == 1
    assert result["result_class"] == "AGENT_FAILURE"
    assert result["failure_code"] == "INVALID_SUBMISSION"


def test_forged_manifest_hash_fails_chain(tmp_path: Path) -> None:
    result, code = run_test_sh(tmp_path, sealed_fixture(tmp_path, "negative/forged-manifest"))
    _assert_common_schema(result)
    assert code == 1
    assert result["result_class"] == "AGENT_FAILURE"
    assert "V2" in result["reason"]


def test_missing_model_fails_chain(tmp_path: Path) -> None:
    result, code = run_test_sh(tmp_path, sealed_fixture(tmp_path, "negative/missing-model"))
    _assert_common_schema(result)
    assert code == 1
    assert result["result_class"] == "AGENT_FAILURE"
    assert "student_model" in result["reason"]


def test_broken_lineage_fails_chain(tmp_path: Path) -> None:
    result, code = run_test_sh(tmp_path, sealed_fixture(tmp_path, "negative/broken-lineage"))
    _assert_common_schema(result)
    assert code == 1
    assert result["result_class"] == "AGENT_FAILURE"
    assert "V3" in result["reason"]


def test_unmounted_submission_is_infra_invalid(tmp_path: Path) -> None:
    result, code = run_test_sh(tmp_path, None)
    _assert_common_schema(result)
    assert code == 2
    assert result["result_class"] == "INFRA_INVALID"
    assert result["retryable"] is True
    assert result["is_counted_scientifically"] is False


def test_entry_never_runs_builder_selftests(tmp_path: Path) -> None:
    # The WP4 separation: test.sh delegates only to verifier.py. A submission
    # root polluted with unittest-style files must not change classification.
    submission = sealed_fixture(tmp_path, "positive/structural-minimal")
    (submission / "test_contract_selfcheck.py").write_text(
        "import unittest\n\nclass T(unittest.TestCase):\n"
        "    def test_x(self):\n        assert False\n",
        encoding="utf-8",
    )
    result, code = run_test_sh(tmp_path, submission)
    assert code == 0
    assert result["result_class"] == "VALID_RESULT"
