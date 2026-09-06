"""Structural submission contract tests."""

from __future__ import annotations

import json

import pytest

from ccbench.core.submission_contract import (
    StructuralError,
    validate_submission,
)


def test_missing_required_artifact_is_agent_failure(tmp_path):
    """A missing required file must be flagged with MISSING_REQUIRED_PATH."""
    errors = validate_submission(
        tmp_path,
        {"required": [{"path": "model.pb", "type": "file"}]},
    )
    assert errors[0].code == "MISSING_REQUIRED_PATH"
    assert errors[0].path == "model.pb"


def test_present_required_file_passes(tmp_path):
    """A present required file must not produce errors."""
    (tmp_path / "model.pb").write_bytes(b"data")
    errors = validate_submission(
        tmp_path,
        {"required": [{"path": "model.pb", "type": "file"}]},
    )
    assert errors == []


def test_required_directory(tmp_path):
    """A required directory must be checked by type."""
    (tmp_path / "out").mkdir()
    errors = validate_submission(
        tmp_path,
        {"required": [{"path": "out", "type": "directory"}]},
    )
    assert errors == []


def test_type_mismatch_file_vs_directory(tmp_path):
    """A file where a directory is required must be flagged."""
    (tmp_path / "out").write_bytes(b"not a dir")
    errors = validate_submission(
        tmp_path,
        {"required": [{"path": "out", "type": "directory"}]},
    )
    assert errors[0].code == "TYPE_MISMATCH"


def test_optional_missing_path_is_ok(tmp_path):
    """An absent optional path must not produce errors."""
    errors = validate_submission(
        tmp_path,
        {"optional": [{"path": "README.md", "type": "file"}]},
    )
    assert errors == []


def test_parent_traversal_contract_path_rejected(tmp_path):
    """A contract path escaping the root must be rejected."""
    errors = validate_submission(
        tmp_path,
        {"required": [{"path": "../solution/x", "type": "file"}]},
    )
    assert errors[0].code == "UNSAFE_CONTRACT_PATH"


def test_absolute_contract_path_rejected(tmp_path):
    """An absolute contract path must be rejected."""
    errors = validate_submission(
        tmp_path,
        {"required": [{"path": "/etc/passwd", "type": "file"}]},
    )
    assert errors[0].code == "UNSAFE_CONTRACT_PATH"


def test_json_schema_check(tmp_path):
    """A declared JSON file must conform to its schema."""
    (tmp_path / "data.json").write_text(
        json.dumps({"a": 1}),
        encoding="utf-8",
    )
    errors = validate_submission(
        tmp_path,
        {
            "json_schema": [{
                "path": "data.json",
                "schema": {
                    "type": "object",
                    "required": ["b"],
                    "properties": {"b": {"type": "string"}},
                },
            }],
        },
    )
    assert errors[0].code == "JSON_SCHEMA_VIOLATION"


def test_json_parse_error(tmp_path):
    """An unparseable declared JSON file must be flagged."""
    (tmp_path / "data.json").write_text("{not json", encoding="utf-8")
    errors = validate_submission(
        tmp_path,
        {
            "json_schema": [{
                "path": "data.json",
                "schema": {"type": "object"},
            }],
        },
    )
    assert errors[0].code == "JSON_PARSE_ERROR"


def test_file_size_limit(tmp_path):
    """A file exceeding max_single_bytes must be flagged."""
    (tmp_path / "big.bin").write_bytes(b"x" * 100)
    errors = validate_submission(
        tmp_path,
        {"files": {"max_single_bytes": 10}},
    )
    assert errors[0].code == "FILE_SIZE_EXCEEDED"


def test_total_size_limit(tmp_path):
    """Aggregate bytes exceeding max_total_bytes must be flagged."""
    (tmp_path / "a.bin").write_bytes(b"x" * 60)
    (tmp_path / "b.bin").write_bytes(b"x" * 60)
    errors = validate_submission(
        tmp_path,
        {"files": {"max_total_bytes": 100}},
    )
    assert errors[0].code == "TOTAL_SIZE_EXCEEDED"


def test_structural_error_to_dict():
    """StructuralError must serialize to a stable dict."""
    err = StructuralError("MISSING_REQUIRED_PATH", "model.pb", "missing")
    assert err.to_dict() == {
        "code": "MISSING_REQUIRED_PATH",
        "path": "model.pb",
        "message": "missing",
    }


def test_empty_contract_passes(tmp_path):
    """An empty contract must not produce errors."""
    errors = validate_submission(tmp_path, {})
    assert errors == []
