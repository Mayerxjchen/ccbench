"""Tests for the Resolved Run Lock v2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.contracts.resolved_lock import (
    FrozenExperimentOverrideError,
    ResolvedRunLock,
)


@pytest.fixture
def complete_payload():
    """A complete valid lock payload."""
    return {
        "case": {
            "case_id": "test/case",
            "case_version": "1.0",
            "schema_version": "1.2",
        },
        "experiment": {
            "template_name": "test-template",
            "condition_id": "run-1-r1",
            "replicate": 1,
        },
        "agent": {
            "provider": "openai",
            "model_id": "gpt-4o",
            "identity_strength": "alias",
            "engine": "pagent",
            "prompt_digest": "sha256:abc",
            "sampling_digest": "sha256:def",
            "context_digest": "sha256:ghi",
            "skill_bundle_digest": "sha256:none",
            "tool_surface_digest": "sha256:jkl",
        },
        "api": {
            "api_profile_digest": "sha256:mno",
            "endpoint_env": "DFTWORLD_API_ENDPOINT",
            "credential_env": "DFTWORLD_API_KEY",
        },
        "candidate_runtime": {
            "image": "dftworld-base:latest",
            "runtime_digest": "sha256:pqr",
            "qualification_status": "passed",
        },
        "verifier": {
            "runtime_digest": "sha256:stu",
            "isolation_config_digest": "sha256:vwx",
        },
        "infra": {
            "version": "2.0.0",
            "commit": "abc123",
            "lock_created_at": "2026-01-01T00:00:00Z",
        },
        "budgets": {
            "max_model_turns": 512,
            "max_total_tokens": 100000000,
            "agent_active_walltime_sec": 86400,
            "scheduler_wait_walltime_sec": 604800,
        },
    }


def test_lock_is_write_once_and_secret_free(tmp_path, complete_payload):
    """Lock must be write-once and contain no secrets."""
    lock = ResolvedRunLock.create(complete_payload)
    lock.write_once(tmp_path / "resolved-run-lock.json")

    # Read and verify no secrets
    content = (tmp_path / "resolved-run-lock.json").read_text()
    assert "secret" not in content.lower()

    # Verify lock_digest is included
    data = json.loads(content)
    assert "lock_digest" in data
    assert data["lock_digest"] == lock.digest

    # Verify write-once
    with pytest.raises(FileExistsError):
        lock.write_once(tmp_path / "resolved-run-lock.json")


def test_lock_deterministic_digest(complete_payload):
    """Lock digest must be deterministic."""
    lock1 = ResolvedRunLock.create(complete_payload)
    lock2 = ResolvedRunLock.create(complete_payload)
    assert lock1.digest == lock2.digest


def test_lock_rejects_invalid_payload():
    """Lock must reject payloads missing required fields."""
    incomplete = {"case": {"case_id": "test"}}
    with pytest.raises(Exception):  # jsonschema.ValidationError
        ResolvedRunLock.create(incomplete)


def test_lock_verify(complete_payload):
    """Lock.verify() must return True for valid lock."""
    lock = ResolvedRunLock.create(complete_payload)
    assert lock.verify() is True


def test_lock_to_dict(complete_payload):
    """Lock.to_dict() must return the payload without lock_digest."""
    lock = ResolvedRunLock.create(complete_payload)
    data = lock.to_dict()
    assert "lock_digest" not in data
    assert data["case"]["case_id"] == "test/case"
