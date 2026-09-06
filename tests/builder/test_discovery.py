"""Tests for discovery evidence schema validation and failure taxonomy classification."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from ccbench.builder.discovery import (
    DiscoveryDecision,
    DiscoveryEvidenceError,
    FailureClass,
    classify_discovery_evidence,
    record_discovery_result,
    validate_discovery_evidence_doc,
)


@pytest.fixture
def valid_discovery_doc() -> dict:
    return {
        "run_id": "disc-run-001",
        "candidate_bundle_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
        "case_ir_digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
        "outcome": {
            "terminal_state": "COMPLETED",
            "candidate_exit_code": 0,
            "verifier_exit_code": 0,
        },
        "metrics": {"energy_rmse": 0.02, "force_rmse": 0.05},
        "failure_class": "SUCCESS",
    }


def test_empty_or_arbitrary_json_rejected(tmp_path: Path):
    """Arbitrary JSON like {} must be rejected from classification."""
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "empty.json").write_text("{}", encoding="utf-8")

    with pytest.raises(DiscoveryEvidenceError, match="No valid discovery evidence document"):
        classify_discovery_evidence(metrics_dir)


def test_missing_outcome_rejected(tmp_path: Path):
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "doc.json").write_text(json.dumps({"passed": True, "notes": "fake"}), encoding="utf-8")

    with pytest.raises(DiscoveryEvidenceError, match="No valid discovery evidence document"):
        classify_discovery_evidence(metrics_dir)


def test_success_derives_promoted(tmp_path: Path, valid_discovery_doc: dict):
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "run.json").write_text(json.dumps(valid_discovery_doc), encoding="utf-8")

    decision, evidence = classify_discovery_evidence(metrics_dir)
    assert decision == DiscoveryDecision.PROMOTED
    assert evidence["derived_failure_class"] == "SUCCESS"


def test_agent_limitation_derives_promoted(tmp_path: Path, valid_discovery_doc: dict):
    """A run where agent failed (scientific challenge) promotes the case to benchmark."""
    doc = dict(valid_discovery_doc)
    doc["outcome"] = {"terminal_state": "COMPLETED", "candidate_exit_code": 0, "verifier_exit_code": 1}
    doc["failure_class"] = "AGENT_LIMITATION"

    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "run.json").write_text(json.dumps(doc), encoding="utf-8")

    decision, evidence = classify_discovery_evidence(metrics_dir)
    assert decision == DiscoveryDecision.PROMOTED
    assert evidence["derived_failure_class"] == "AGENT_LIMITATION"


def test_design_blocked_derives_refine(tmp_path: Path, valid_discovery_doc: dict):
    doc = dict(valid_discovery_doc)
    doc["failure_class"] = "CASE_DESIGN_BLOCKED"

    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "run.json").write_text(json.dumps(doc), encoding="utf-8")

    decision, evidence = classify_discovery_evidence(metrics_dir)
    assert decision == DiscoveryDecision.REFINE


def test_infra_invalid_derives_reject(tmp_path: Path, valid_discovery_doc: dict):
    doc = dict(valid_discovery_doc)
    doc["failure_class"] = "INFRA_INVALID"

    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    (metrics_dir / "run.json").write_text(json.dumps(doc), encoding="utf-8")

    decision, evidence = classify_discovery_evidence(metrics_dir)
    assert decision == DiscoveryDecision.REJECT
