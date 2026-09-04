"""Tests for scripts/qualification/capture_compshare_bootstrap.py."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dftworld_bench.hpc.drivers.compshare.bootstrap_evidence import (
    EXPECTED_CLI_VERSION,
    INCOMPLETE_NOT_REPLAYABLE,
    BootstrapEvidenceError,
    classify_bootstrap_manifest,
    validate_bootstrap_manifest,
)
from scripts.qualification.capture_compshare_bootstrap import (
    execute_bootstrap_capture,
    main,
    plan_bootstrap_capture,
)


def _fake_runner_success(argv: list[str]) -> tuple[int, str, str]:
    cmd_str = " ".join(argv)
    if "--version" in argv:
        return 0, f"compshare {EXPECTED_CLI_VERSION}\n", ""
    if "doctor" in argv:
        return 0, json.dumps({
            "ok": True,
            "data": {
                "profile": "compshare-cli",
                "auth_ok": True,
                "api_endpoint": "https://api.compshare.cn",
                "account_id": "account-12345",
            }
        }), ""
    if "list" in argv:
        return 0, json.dumps({"ok": True, "data": {"items": []}}), ""
    if "zones" in argv:
        return 0, json.dumps({"ok": True, "data": {"items": [{"Region": "cn-sh2", "Zone": "cn-sh2-02"}]}}), ""
    if "families" in argv:
        return 0, json.dumps({"ok": True, "data": {"items": [{"Name": "4090"}]}}), ""
    if "price" in argv:
        return 0, json.dumps({"ok": True, "data": {"items": [{"ChargeType": "Postpay", "Instance": 2.05}]}}), ""
    if "search" in argv:
        return 0, json.dumps({"ok": True, "data": {"AvailableInstanceTypes": [{"Name": "4090"}]}}), ""
    raise RuntimeError(f"Unexpected fake command: {cmd_str}")


def test_plan_only_mode_makes_zero_subprocess_calls():
    with patch("subprocess.run") as mock_subproc:
        exit_code = main(["--cli-bin", "compshare"])
        assert exit_code == 0
        assert mock_subproc.call_count == 0

    plans = plan_bootstrap_capture(cli_bin="compshare")
    assert len(plans) == 9
    probe_names = [p["name"] for p in plans]
    assert "version" in probe_names
    assert "doctor" in probe_names
    assert "instance_list" in probe_names
    assert "price_4090" in probe_names
    assert "search_sh2" in probe_names


def test_execute_capture_success_with_fake_runner(tmp_path: Path):
    fake_cli = tmp_path / "bin" / "compshare"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_text("#!/bin/sh\nexit 0\n")
    fake_cli.chmod(0o755)

    out_dir = tmp_path / "evidence_run"
    called_commands: list[list[str]] = []

    def tracking_runner(argv):
        called_commands.append(list(argv))
        return _fake_runner_success(argv)

    manifest = execute_bootstrap_capture(
        output_dir=out_dir,
        cli_bin=str(fake_cli),
        credential_profile_id="compshare-cli",
        runner=tracking_runner,
        source_commit="1" * 40,
    )

    assert len(called_commands) == 9
    assert manifest["cli"]["version"] == EXPECTED_CLI_VERSION
    assert len(manifest["probes"]) == 9
    assert len(manifest["evidence_files"]) == 9

    # Artifacts exist and are non-empty
    manifest_file = out_dir / "manifest.json"
    assert manifest_file.is_file()
    assert (out_dir / "version.txt").is_file()
    assert (out_dir / "doctor.json").is_file()
    assert (out_dir / "instance_list.json").is_file()

    # Re-verify through bootstrap_evidence validator
    verified = validate_bootstrap_manifest(manifest, evidence_root=out_dir)
    assert verified["manifest_digest"] == manifest["manifest_digest"]


def test_canary_dropped_from_summary(tmp_path: Path):
    # Use directory name without forbidden words
    cli_dir = tmp_path / "cli_tools"
    cli_dir.mkdir(parents=True)
    fake_cli = cli_dir / "compshare"
    fake_cli.write_text("#!/bin/sh\n")
    fake_cli.chmod(0o755)

    out_dir = tmp_path / "evidence_canary"

    def canary_runner(argv):
        if "--version" in argv:
            return 0, f"compshare {EXPECTED_CLI_VERSION}\n", ""
        if "doctor" in argv:
            return 0, json.dumps({
                "ok": True,
                "data": {
                    "profile": "compshare-cli",
                    "auth_ok": True,
                    "api_endpoint": "https://api.compshare.cn",
                    "account_id": "account-123",
                    "token": "CANARY_TOKEN_VAL_123",
                    "stdout": "raw CANARY output",
                }
            }), ""
        return _fake_runner_success(argv)

    manifest = execute_bootstrap_capture(
        output_dir=out_dir,
        cli_bin=str(fake_cli),
        runner=canary_runner,
        source_commit="2" * 40,
    )

    manifest_str = json.dumps(manifest)
    # The summary inside manifest must NOT retain the forbidden 'stdout' or 'token' keys
    doctor_summary = [p["summary"] for p in manifest["probes"] if p["name"] == "doctor"][0]
    assert "token" not in doctor_summary
    assert "stdout" not in doctor_summary
    assert "CANARY_TOKEN_VAL_123" not in doctor_summary


def test_non_pinned_cli_version_fails(tmp_path: Path):
    fake_cli = tmp_path / "bin" / "compshare"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_text("#!/bin/sh\n")
    fake_cli.chmod(0o755)

    out_dir = tmp_path / "evidence_bad_ver"

    def bad_ver_runner(argv):
        if "--version" in argv:
            return 0, "compshare 0.3.9\n", ""
        return _fake_runner_success(argv)

    with pytest.raises(BootstrapEvidenceError, match="unsupported CompShare CLI version"):
        execute_bootstrap_capture(
            output_dir=out_dir,
            cli_bin=str(fake_cli),
            runner=bad_ver_runner,
            source_commit="3" * 40,
        )


def test_refuses_to_overwrite_existing_directory(tmp_path: Path):
    fake_cli = tmp_path / "bin" / "compshare"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_text("#!/bin/sh\n")
    fake_cli.chmod(0o755)

    out_dir = tmp_path / "evidence_exists"
    out_dir.mkdir(parents=True)
    (out_dir / "old.txt").write_text("existing")

    with pytest.raises(BootstrapEvidenceError, match="already exists and is not empty"):
        execute_bootstrap_capture(
            output_dir=out_dir,
            cli_bin=str(fake_cli),
            runner=_fake_runner_success,
            source_commit="4" * 40,
        )


def test_artifact_tamper_is_rejected(tmp_path: Path):
    fake_cli = tmp_path / "bin" / "compshare"
    fake_cli.parent.mkdir(parents=True)
    fake_cli.write_text("#!/bin/sh\n")
    fake_cli.chmod(0o755)

    out_dir = tmp_path / "evidence_drift"
    manifest = execute_bootstrap_capture(
        output_dir=out_dir,
        cli_bin=str(fake_cli),
        runner=_fake_runner_success,
        source_commit="5" * 40,
    )

    # Tamper with an artifact
    doc_path = out_dir / "doctor.json"
    original_len = len(doc_path.read_text())
    # Tamper keeping the same size
    tampered = " " * (original_len - 1) + "}"
    doc_path.write_text(tampered)

    with pytest.raises(BootstrapEvidenceError, match="(digest mismatch|size mismatch)"):
        validate_bootstrap_manifest(manifest, evidence_root=out_dir)


def test_old_legacy_manifest_is_incomplete_not_replayable(tmp_path: Path):
    old_manifest = {
        "schema_version": 1,
        "gate": "Gate A2",
        "artifacts": {
            "doctor.json": {"sha256": "0" * 64}
        }
    }
    manifest_file = tmp_path / "old_manifest.json"
    manifest_file.write_text(json.dumps(old_manifest))

    classification = classify_bootstrap_manifest(old_manifest)
    assert classification["status"] == INCOMPLETE_NOT_REPLAYABLE
