"""Verifier runner: isolated container boundary + strict result parsing."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from dftworld_bench.contracts.result import FailureCode, ResultClass
from dftworld_bench.core.verifier import (
    VerifierSpec,
    _parse_verifier_output,
    build_verifier_command,
    run_verifier,
)


def _spec(root: Path) -> VerifierSpec:
    tests = root / "tests"
    tests.mkdir()
    (tests / "test.sh").write_text("#!/bin/bash\nexit 0\n")
    ref = root / "reference"
    ref.mkdir()
    return VerifierSpec(
        image="img:v1",
        timeout_sec=60.0,
        env={"MATCLAW_PROFILE": "paper"},
        tests_dir=tests,
        reference_dir=ref,
    )


def test_command_is_isolated_and_sandboxed(tmp_path):
    submission = tmp_path / "sealed"
    logs = tmp_path / "logs"
    cmd = build_verifier_command(_spec(tmp_path), submission, logs)
    joined = " ".join(cmd)
    assert cmd[:3] == ["docker", "run", "--rm"]
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--user 65532:65532",
    ):
        assert flag in joined, flag
    assert "--tmpfs" in cmd
    assert "/tmp:rw,noexec,nosuid,size=64m" in cmd

    volumes = {cmd[i + 1] for i, token in enumerate(cmd) if token == "--volume"}
    assert f"{submission}:/submission:ro" in volumes
    assert f"{submission}:/app:ro" in volumes
    assert f"{tmp_path / 'tests'}:/tests:ro" in volumes
    assert f"{tmp_path / 'reference'}:/reference:ro" in volumes
    assert f"{logs}:/logs/verifier" in volumes
    assert "--env" in cmd and "MATCLAW_PROFILE=paper" in cmd


def test_command_excludes_candidate_material(tmp_path):
    submission = tmp_path / "sealed"
    logs = tmp_path / "logs"
    candidate_workspace = str(tmp_path / "candidate-workspace")
    cmd = build_verifier_command(_spec(tmp_path), submission, logs)
    joined = " ".join(cmd)
    assert "abc123def456" not in joined  # candidate container id
    assert candidate_workspace not in joined
    assert "/var/run/docker.sock" not in joined
    assert ".ssh" not in joined
    assert "id_rsa" not in joined and "id_ed25519" not in joined


def test_result_json_pass(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "result.json").write_text(
        json.dumps(
            {
                "run_id": "r1",
                "result_class": "VALID_RESULT",
                "failure_code": "PASS",
                "reason": "energy matches",
                "retryable": False,
            }
        ),
        encoding="utf-8",
    )
    result = _parse_verifier_output(logs, "r1")
    assert result.result_class is ResultClass.VALID_RESULT
    assert result.is_counted_scientifically is True
    assert result.failure_code is FailureCode.PASS


def test_result_json_scientific_fail_is_counted(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "result.json").write_text(
        json.dumps(
            {
                "run_id": "r1",
                "result_class": "VALID_RESULT",
                "failure_code": "SCIENTIFIC_FAIL",
                "reason": "wrong lattice",
                "retryable": False,
            }
        ),
        encoding="utf-8",
    )
    result = _parse_verifier_output(logs, "r1")
    assert result.result_class is ResultClass.VALID_RESULT
    assert result.failure_code is FailureCode.SCIENTIFIC_FAIL
    assert result.is_counted_scientifically is True


def test_missing_output_is_verifier_failure_not_scientific(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    result = _parse_verifier_output(logs, "r1")
    assert result.result_class is ResultClass.INFRA_INVALID
    assert result.failure_code is FailureCode.VERIFIER_FAILURE
    assert result.is_counted_scientifically is False


def test_malformed_result_json_is_verifier_failure(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "result.json").write_text("not json {", encoding="utf-8")
    result = _parse_verifier_output(logs, "r1")
    assert result.result_class is ResultClass.INFRA_INVALID
    assert result.failure_code is FailureCode.VERIFIER_FAILURE


def test_schema_invalid_result_json_is_verifier_failure(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "result.json").write_text(
        json.dumps(
            {
                "run_id": "r1",
                "result_class": "BOGUS",
                "failure_code": "PASS",
                "reason": "",
                "retryable": False,
            }
        ),
        encoding="utf-8",
    )
    result = _parse_verifier_output(logs, "r1")
    assert result.result_class is ResultClass.INFRA_INVALID
    assert result.failure_code is FailureCode.VERIFIER_FAILURE


def test_legacy_reward_txt_fallback(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "reward.txt").write_text("0\n", encoding="utf-8")
    result = _parse_verifier_output(logs, "r1")
    assert result.result_class is ResultClass.VALID_RESULT
    assert result.failure_code is FailureCode.SCIENTIFIC_FAIL
    (logs / "reward.txt").write_text("1\n", encoding="utf-8")
    passed = _parse_verifier_output(logs, "r1")
    assert passed.failure_code is FailureCode.PASS


def test_all_volume_sources_are_absolute(tmp_path):
    """docker CLI --volume does NOT resolve relative paths — they become named
    volumes and fail with 'invalid characters for a local volume name'.  Every
    source must be absolute regardless of the caller's cwd."""
    submission = tmp_path / "sealed"
    logs = tmp_path / "logs"
    cmd = build_verifier_command(_spec(tmp_path), submission, logs)
    volumes = [cmd[i + 1] for i, token in enumerate(cmd) if token == "--volume"]
    assert volumes, "expected at least one volume mount"
    for mount in volumes:
        host, _, _ = mount.partition(":")
        assert os.path.isabs(host), mount


def test_verifier_tmpfs_comes_from_resolved_profile(tmp_path):
    """The private /tmp tmpfs comes from the resolved local profile (64m); a
    caller can override it per spec, and the override reaches the argv."""
    submission = tmp_path / "sealed"
    logs = tmp_path / "logs"
    spec = _spec(tmp_path)
    cmd = build_verifier_command(spec, submission, logs)
    assert "/tmp:rw,noexec,nosuid,size=64m" in cmd
    overridden = replace(spec, tmpfs="/tmp:rw,noexec,nosuid,size=256m")
    cmd2 = build_verifier_command(overridden, submission, logs)
    assert "/tmp:rw,noexec,nosuid,size=256m" in cmd2
    assert "size=64m" not in cmd2


def test_run_verifier_returns_parsed_result(tmp_path):
    spec = _spec(tmp_path)
    submission = tmp_path / "sealed"
    submission.mkdir()
    logs = tmp_path / "logs"
    captured: dict = {}

    def fake_runner(cmd, **kwargs):
        captured["cmd"] = cmd
        (logs / "result.json").write_text(
            json.dumps(
                {
                    "run_id": "r1",
                    "result_class": "VALID_RESULT",
                    "failure_code": "PASS",
                    "reason": "",
                    "retryable": False,
                }
            ),
            encoding="utf-8",
        )

    result = run_verifier(spec, submission, logs, run_id="r1", runner=fake_runner)
    assert result.is_counted_scientifically is True
    assert result.failure_code is FailureCode.PASS
    assert captured["cmd"][0] == "docker"
