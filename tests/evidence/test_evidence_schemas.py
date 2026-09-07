"""Evidence-retention schemas (plan Task 1).

Four retention classes, case/run split, Manifest v2 requiring a restorable
primary + replica, and a policy that forbids finalizing a construction case.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"


def validate(instance: dict, schema_name: str) -> None:
    path = SCHEMAS / schema_name
    assert path.is_file(), f"missing schema {path}"
    jsonschema.validate(instance, json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture
def valid_manifest() -> dict:
    return {
        "schema_version": "2.0",
        "case": "002",
        "case_version": "2.2.11",
        "run_id": "run-1",
        "seed": 2026081206,
        "profile": "paper",
        "evidence_class": "formal",
        "git_commit": "71078a4",
        "git_clean": True,
        "started_at": "2026-08-18T11:36:27Z",
        "finished_at": "2026-08-18T12:12:34Z",
        "exit_status": 0,
        "gpu_image": "dftworld-base-matclaw-cips:2.2.11-gpu-amd64",
        "gpu_image_digest": "sha256:34db42302a16a3b4b83276597bec1e0a0776cc906863cb3f0674d6e0d03919b0",
        "cpu_verifier_image": "dftworld-base-matclaw-cips:2.2.11-cpu-amd64",
        "cpu_verifier_image_digest": "sha256:f36968b0e3422f13fde3664dd5e5f1ae0c10143c50a65476feae40ec354a05e9",
        "hardware": {"job": "3567687", "node": "<site-node-gpu3>"},
        "software": {"apptainer_sif_sha256": "99" * 32},
        "command": "formal round 1 seed 2026081206",
        "workspace_identity": "002-2026081206",
        "evaluator_bundle_sha256": "ab" * 32,
        "artifact_policy_sha256": "cd" * 32,
        "artifacts": [
            {
                "path": "submission/result.json",
                "role": "scoring_required",
                "size_bytes": 1234,
                "sha256": "ef" * 32,
            }
        ],
        "bundle": {
            "format": "tar.zst",
            "sha256": "11" * 32,
            "size_bytes": 123456,
            "primary_uri": "cas+file:///evidence-store/sha256/11/1111",
            "primary_version": "immutable",
            "replica_uri": "cas+file:///evidence-backup/sha256/11/1111",
            "replica_version": "immutable",
            "verified_at": "2026-08-18T00:00:00Z",
        },
        "verifier_report": {"valid": True, "errors": [], "metrics": {"Tc_K": 259.44}},
    }


@pytest.fixture
def valid_policy() -> dict:
    return {
        "schema_version": "1",
        "case_id": "002",
        "state": "benchmark_valid",
        "finalization_allowed": True,
        "scoring_required": [
            {"path": "result.json", "role": "scoring_required"},
            {"path": "trajectories", "role": "scoring_required", "glob": "md/production_*.traj"},
        ],
        "reproduction_required": [
            {"path": "manifest.json", "role": "reproduction_required"}
        ],
    }


@pytest.fixture
def valid_evaluator() -> dict:
    # build_evaluator_manifest.py emits both keys; the schema requires both.
    return {
        "schema_version": "1",
        "case_id": "002",
        "bundle_sha256": "ab" * 32,
        "evaluator_bundle_sha256": "ab" * 32,
        "files": [
            {"path": "tests/verifier.py", "sha256": "ab" * 32},
            {"path": "solution/solve.sh", "sha256": "ab" * 32},
        ],
    }


def test_manifest_v2_requires_restorable_primary_and_replica(valid_manifest):
    validate(valid_manifest, "evidence-manifest-v2.schema.json")
    del valid_manifest["bundle"]["replica_uri"]
    with pytest.raises(jsonschema.ValidationError):
        validate(valid_manifest, "evidence-manifest-v2.schema.json")


def test_manifest_v2_requires_bundle_digest_and_format(valid_manifest):
    validate(valid_manifest, "evidence-manifest-v2.schema.json")
    valid_manifest["bundle"]["format"] = "zip"
    with pytest.raises(jsonschema.ValidationError):
        validate(valid_manifest, "evidence-manifest-v2.schema.json")
    valid_manifest["bundle"]["sha256"] = "not-hex"
    with pytest.raises(jsonschema.ValidationError):
        validate(valid_manifest, "evidence-manifest-v2.schema.json")


def test_manifest_v2_artifact_requires_role_size_digest(valid_manifest):
    validate(valid_manifest, "evidence-manifest-v2.schema.json")
    art = dict(valid_manifest["artifacts"][0])
    del art["role"]
    valid_manifest["artifacts"] = [art]
    with pytest.raises(jsonschema.ValidationError):
        validate(valid_manifest, "evidence-manifest-v2.schema.json")


def test_construction_case_cannot_finalize(valid_policy):
    validate(valid_policy, "evidence-policy.schema.json")
    valid_policy["state"] = "construction"
    valid_policy["finalization_allowed"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate(valid_policy, "evidence-policy.schema.json")


def test_finalizable_policy_allows_constructed_state(valid_policy):
    valid_policy["state"] = "constructed"
    valid_policy["finalization_allowed"] = False
    validate(valid_policy, "evidence-policy.schema.json")


def test_retention_roles_are_limited_to_four_classes(valid_manifest):
    validate(valid_manifest, "evidence-manifest-v2.schema.json")
    art = dict(valid_manifest["artifacts"][0])
    art["role"] = "sometimes"
    valid_manifest["artifacts"] = [art]
    with pytest.raises(jsonschema.ValidationError):
        validate(valid_manifest, "evidence-manifest-v2.schema.json")


def test_evaluator_manifest_requires_case_and_digest(valid_evaluator):
    validate(valid_evaluator, "evaluator-bundle.schema.json")
    del valid_evaluator["bundle_sha256"]
    with pytest.raises(jsonschema.ValidationError):
        validate(valid_evaluator, "evaluator-bundle.schema.json")
    valid_evaluator["bundle_sha256"] = "ab" * 32
    del valid_evaluator["evaluator_bundle_sha256"]
    with pytest.raises(jsonschema.ValidationError):
        validate(valid_evaluator, "evaluator-bundle.schema.json")
