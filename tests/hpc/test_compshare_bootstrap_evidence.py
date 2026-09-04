"""Focused pure-contract tests for the Gate A2 bootstrap evidence envelope."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from dftworld_bench.hpc.drivers.compshare.bootstrap_evidence import (
    BootstrapEvidenceError,
    EXPECTED_CLI_VERSION,
    INCOMPLETE_NOT_REPLAYABLE,
    REPLAYABLE,
    build_allowlisted_probes,
    build_bootstrap_manifest,
    build_evidence_file,
    build_probe_record,
    canonical_manifest_digest,
    classify_bootstrap_manifest,
    require_pinned_cli_version,
    validate_allowlisted_argv,
    validate_bootstrap_manifest,
    validate_relative_path,
)


NOW = "2026-09-05T00:00:00Z"
SOURCE_COMMIT = "a" * 40
CLI_SHA256 = "b" * 64
CAPTURE_TOOL_SHA256 = "c" * 64
ENVIRONMENT = {
    "os": "Darwin 25.0.0",
    "arch": "arm64",
    "python": "3.13.7",
    "locale": "en_US.UTF-8",
    "timezone": "Asia/Shanghai",
}


def _sealed_manifest(tmp_path: Path, *, credential_profile_id: str = "compshare-cli"):
    raw_root = tmp_path / "raw"
    raw_root.mkdir(parents=True)
    specs = build_allowlisted_probes()
    probes = []
    evidence_files = []
    for spec in specs:
        artifact = raw_root / f"{spec.name}.json"
        artifact.write_text(json.dumps({"probe": spec.name}), encoding="utf-8")
        relative_path = f"raw/{artifact.name}"
        evidence_files.append(
            build_evidence_file(artifact, root=tmp_path, relative_path=relative_path)
        )
        if spec.name == "version":
            summary = {"version": EXPECTED_CLI_VERSION}
        elif spec.name == "doctor":
            summary = {
                "profile": "compshare-cli",
                "auth_ok": True,
                "api_endpoint": "https://api.example.invalid",
                "account_id": "account-1",
                "stdout": "SECRET_CANARY",
                "token": "SECRET_CANARY",
            }
        else:
            summary = {"available": True, "probe": spec.name}
        probes.append(
            build_probe_record(
                name=spec.name,
                argv=spec.argv,
                captured_at=NOW,
                started_at=NOW,
                finished_at=NOW,
                exit_code=0,
                summary=summary,
                evidence_path=relative_path,
            )
        )
    manifest = build_bootstrap_manifest(
        credential_profile_id=credential_profile_id,
        captured_at=NOW,
        source_commit=SOURCE_COMMIT,
        cli_version=EXPECTED_CLI_VERSION,
        executable_sha256=CLI_SHA256,
        probes=probes,
        evidence_files=evidence_files,
        environment=ENVIRONMENT,
        capture_tool_sha256=CAPTURE_TOOL_SHA256,
    )
    return manifest


def test_schema_is_valid_and_accepts_a_sealed_manifest(tmp_path: Path):
    schema_path = (
        Path(__file__).parents[2]
        / "schemas"
        / "compshare-bootstrap-evidence.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)

    manifest = _sealed_manifest(tmp_path)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert validate_bootstrap_manifest(manifest, evidence_root=tmp_path) == manifest
    assert manifest["manifest_digest"] == canonical_manifest_digest(manifest)


def test_allowlist_is_exact_and_never_contains_credential_index():
    specs = build_allowlisted_probes()
    assert [spec.name for spec in specs] == [
        "version",
        "doctor",
        "instance_list",
        "zones",
        "families",
        "price_4090",
        "price_5090",
        "search_sh2",
        "search_wlcb",
    ]
    assert all("compshare-cli" not in spec.argv for spec in specs)
    assert all("SECRET_CANARY" not in spec.argv for spec in specs)

    doctor = next(spec for spec in specs if spec.name == "doctor")
    assert validate_allowlisted_argv(doctor.argv, probe_name="doctor") == doctor.argv
    with pytest.raises(BootstrapEvidenceError, match="allowlist|secret"):
        validate_allowlisted_argv(
            [*doctor.argv, "SECRET_CANARY"], probe_name="doctor"
        )
    with pytest.raises(BootstrapEvidenceError, match="allowlist|secret"):
        validate_allowlisted_argv(
            ["compshare", "--json", "doctor", "--private-key", "x"],
            probe_name="doctor",
        )
    with pytest.raises(BootstrapEvidenceError, match="unsafe argv|option|secret"):
        build_allowlisted_probes(region="cn-sh2;cat SECRET_CANARY")


def test_only_pinned_cli_version_is_accepted_and_arbitrary_output_is_rejected():
    assert require_pinned_cli_version("0.4.1") == EXPECTED_CLI_VERSION
    assert require_pinned_cli_version("compshare-cli v0.4.1") == EXPECTED_CLI_VERSION
    with pytest.raises(BootstrapEvidenceError, match="unsupported|required"):
        require_pinned_cli_version("0.4.0")
    with pytest.raises(BootstrapEvidenceError, match="version|secret"):
        require_pinned_cli_version("0.4.1\nSECRET_CANARY")


def test_manifest_digest_and_artifact_binding_fail_closed(tmp_path: Path):
    manifest = _sealed_manifest(tmp_path)
    mutated = copy.deepcopy(manifest)
    mutated["source_commit"] = "d" * 40
    with pytest.raises(BootstrapEvidenceError, match="manifest_digest"):
        validate_bootstrap_manifest(mutated)

    artifact = tmp_path / "raw" / "version.json"
    artifact.write_text("tampered", encoding="utf-8")
    with pytest.raises(BootstrapEvidenceError, match="digest|size"):
        validate_bootstrap_manifest(manifest, evidence_root=tmp_path)


def test_evidence_paths_hashes_and_symlinks_are_checked(tmp_path: Path):
    root = tmp_path / "evidence"
    root.mkdir()
    artifact = root / "raw.json"
    artifact.write_bytes(b"raw provider response")
    entry = build_evidence_file(artifact, root=root, relative_path="raw.json")
    assert entry["size_bytes"] == len(b"raw provider response")
    assert len(entry["sha256"]) == 64

    with pytest.raises(BootstrapEvidenceError):
        validate_relative_path("../raw.json")
    with pytest.raises(BootstrapEvidenceError):
        validate_relative_path("/tmp/raw.json")
    with pytest.raises(BootstrapEvidenceError):
        validate_relative_path("raw\\file.json")

    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(BootstrapEvidenceError, match="escapes|outside"):
        build_evidence_file(outside, root=root)

    linked = root / "linked.json"
    linked.symlink_to(artifact)
    with pytest.raises(BootstrapEvidenceError, match="symlink"):
        build_evidence_file(linked, root=root, relative_path="linked.json")


def test_doctor_summary_drops_forbidden_channels_and_canary():
    record = build_probe_record(
        name="doctor",
        argv=("compshare", "--json", "doctor"),
        captured_at=NOW,
        exit_code=0,
        summary={
            "profile": "compshare-cli",
            "auth_ok": True,
            "api_endpoint": "https://api.example.invalid",
            "account_id": "account-1",
            "stdout": "SECRET_CANARY",
            "stderr": "SECRET_CANARY",
            "private_key": "SECRET_CANARY",
        },
        evidence_path="raw/doctor.json",
    )
    encoded = json.dumps(record, sort_keys=True)
    assert "SECRET_CANARY" not in encoded
    assert set(record["summary"]) <= {
        "profile",
        "auth_ok",
        "api_endpoint",
        "account_id",
    }


def test_old_manifest_is_diagnostic_only_and_never_rewritten(tmp_path: Path):
    path = tmp_path / "legacy-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "gate": "Gate A2",
                "artifacts": {"doctor.json": {"path": "/outside"}},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    before = path.read_bytes()
    classified = classify_bootstrap_manifest(path)
    assert classified["status"] == INCOMPLETE_NOT_REPLAYABLE
    assert classified["errors"]
    assert path.read_bytes() == before


def test_credential_profile_id_is_index_only(tmp_path: Path):
    manifest = _sealed_manifest(tmp_path, credential_profile_id="operator-profile-01")
    argv_text = json.dumps([probe["argv"] for probe in manifest["probes"]])
    assert "operator-profile-01" not in argv_text
    assert "SECRET_CANARY" not in argv_text

    with pytest.raises(BootstrapEvidenceError, match="secret-looking"):
        _sealed_manifest(tmp_path / "second", credential_profile_id="SECRET_CANARY")
