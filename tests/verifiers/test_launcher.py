"""Tests for ccbench.verifiers.launcher (P0-F)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from ccbench.verifiers.launcher import (
    DEFAULT_PYTHON_CANDIDATES,
    VerifierResult,
    LaunchConfig,
    launch,
    main,
    resolve_python,
    write_ctrf_log,
    write_result_json,
    write_reward_txt,
)


# ---- VerifierResult ----

class TestVerifierResult:
    def test_pass_result_json(self):
        r = VerifierResult(
            run_id="001-hello",
            result_class="VALID_RESULT",
            failure_code="PASS",
            reason="hello.txt matches",
        )
        data = json.loads(r.to_json())
        assert data["run_id"] == "001-hello"
        assert data["result_class"] == "VALID_RESULT"
        assert data["failure_code"] == "PASS"
        assert data["failing"] == []
        assert data["retryable"] is False
        assert data["is_counted_scientifically"] is True

    def test_fail_result_json(self):
        r = VerifierResult(
            run_id="025-name2smi",
            result_class="AGENT_FAILURE",
            failure_code="SCIENTIFIC_FAIL",
            reason="canonical SMILES mismatch",
            failing=["test_canonical_smiles", "test_homo_lumo"],
        )
        data = json.loads(r.to_json())
        assert data["result_class"] == "AGENT_FAILURE"
        assert len(data["failing"]) == 2
        assert "test_canonical_smiles" in data["failing"]

    def test_infra_invalid_result(self):
        r = VerifierResult(
            run_id="test",
            result_class="INFRA_INVALID",
            failure_code="INFRA_INVALID",
            reason="no cp2k binary",
        )
        data = json.loads(r.to_json())
        assert data["result_class"] == "INFRA_INVALID"


# ---- resolve_python ----

class TestResolvePython:
    def test_returns_first_candidate(self, monkeypatch):
        # Simulate first candidate existing
        monkeypatch.setattr("os.path.isfile", lambda p: p == "/opt/matclaw/bin/python")
        monkeypatch.setattr("os.access", lambda p, m: True)
        assert resolve_python() == "/opt/matclaw/bin/python"

    def test_falls_back_to_sys_executable(self, monkeypatch):
        monkeypatch.setattr("os.path.isfile", lambda p: False)
        assert resolve_python() == sys.executable

    def test_custom_candidates(self, monkeypatch):
        monkeypatch.setattr("os.path.isfile", lambda p: p == "/custom/python3")
        monkeypatch.setattr("os.access", lambda p, m: True)
        assert resolve_python(["/custom/python3"]) == "/custom/python3"


# ---- write helpers ----

class TestWriteHelpers:
    def test_write_result_json_atomic(self, tmp_path):
        path = tmp_path / "result.json"
        r = VerifierResult("x", "VALID_RESULT", "PASS", "ok")
        write_result_json(r, path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["run_id"] == "x"
        # No .tmp leftover
        assert not (tmp_path / "result.json.tmp").exists()

    def test_write_reward_txt(self, tmp_path):
        path = tmp_path / "reward.txt"
        write_reward_txt(1, path)
        assert path.read_text().strip() == "1"
        write_reward_txt(0, path)
        assert path.read_text().strip() == "0"

    def test_write_ctrf_log(self, tmp_path):
        path = tmp_path / "ctrf.log"
        write_ctrf_log(path, 0, "all passed")
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["exit_code"] == 0


# ---- launch ----

class TestLaunch:
    def test_passing_pytest(self, tmp_path):
        """Run a trivially-passing pytest."""
        test_file = tmp_path / "test_ok.py"
        test_file.write_text("def test_ok():\n    assert True\n")
        config = LaunchConfig(
            run_id="test-pass",
            test_path=str(test_file),
            python=sys.executable,
            work_dir=tmp_path,
            log_dir=tmp_path / "logs",
        )
        rc = launch(config)
        assert rc == 0
        result = json.loads((tmp_path / "logs" / "result.json").read_text())
        assert result["result_class"] == "VALID_RESULT"
        assert result["failure_code"] == "PASS"
        assert result["failing"] == []
        assert (tmp_path / "logs" / "reward.txt").read_text().strip() == "1"

    def test_failing_pytest(self, tmp_path):
        """Run a failing pytest — should produce AGENT_FAILURE."""
        test_file = tmp_path / "test_fail.py"
        test_file.write_text("def test_fail():\n    assert False, 'intentional'\n")
        config = LaunchConfig(
            run_id="test-fail",
            test_path=str(test_file),
            python=sys.executable,
            work_dir=tmp_path,
            log_dir=tmp_path / "logs",
        )
        rc = launch(config)
        assert rc == 1
        result = json.loads((tmp_path / "logs" / "result.json").read_text())
        assert result["result_class"] == "AGENT_FAILURE"
        assert result["failure_code"] == "SCIENTIFIC_FAIL"
        assert len(result["failing"]) > 0
        assert (tmp_path / "logs" / "reward.txt").read_text().strip() == "0"

    def test_custom_script_mode(self, tmp_path):
        """Custom script mode (test_args provided) invokes the script directly."""
        script = tmp_path / "my_verifier.py"
        script.write_text("import sys; sys.exit(0)\n")
        config = LaunchConfig(
            run_id="test-custom",
            test_path=str(script),
            test_args=[],
            python=sys.executable,
            work_dir=tmp_path,
            log_dir=tmp_path / "logs",
        )
        rc = launch(config)
        assert rc == 0
        result = json.loads((tmp_path / "logs" / "result.json").read_text())
        assert result["result_class"] == "VALID_RESULT"

    def test_failing_extracted_from_pytest_output(self, tmp_path):
        """Failing test names are extracted from pytest output."""
        test_file = tmp_path / "test_multi.py"
        test_file.write_text(
            "def test_a():\n    assert True\n"
            "def test_b():\n    assert False, 'fail b'\n"
            "def test_c():\n    assert False, 'fail c'\n"
        )
        config = LaunchConfig(
            run_id="test-multi",
            test_path=str(test_file),
            python=sys.executable,
            work_dir=tmp_path,
            log_dir=tmp_path / "logs",
        )
        rc = launch(config)
        assert rc == 1
        result = json.loads((tmp_path / "logs" / "result.json").read_text())
        assert len(result["failing"]) >= 2


# ---- CLI ----

class TestCLI:
    def test_main_passing(self, tmp_path):
        test_file = tmp_path / "test_cli.py"
        test_file.write_text("def test_cli():\n    assert True\n")
        rc = main([
            "--run-id", "cli-test",
            "--test-path", str(test_file),
            "--python", sys.executable,
            "--work-dir", str(tmp_path),
            "--log-dir", str(tmp_path / "logs"),
        ])
        assert rc == 0
        result = json.loads((tmp_path / "logs" / "result.json").read_text())
        assert result["run_id"] == "cli-test"
