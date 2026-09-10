from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from bench.contracts.result import BenchmarkResult
from bench.core.verifier_worker import (
    VerifierWorkerError,
    VerifierWorkerSpec,
    build_verifier_worker_command,
    make_verifier_receipt,
    directory_digest,
    verify_formal_admission,
    verify_verifier_receipt,
)


def _spec(tmp_path: Path) -> VerifierWorkerSpec:
    submission = tmp_path / "sealed"
    submission.mkdir()
    (submission / "manifest.json").write_text("{}\n", encoding="utf-8")
    verifier = tmp_path / "private-verifier"
    verifier.mkdir()
    (verifier / "test.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    spec = VerifierWorkerSpec(
        case_id="case-001",
        verifier_profile="verifier-v1",
        verifier_profile_digest="sha256:" + "1" * 64,
        image="private-verifier:v1",
        image_digest="sha256:" + "2" * 64,
        submission=submission,
        verifier_bundle=verifier,
        logs=tmp_path / "logs",
        timeout_sec=60,
        run_id="run-001",
        attempt=1,
        case_version="1.0.0",
        public_digest="sha256:" + "3" * 64,
        verifier_bundle_digest="sha256:" + "4" * 64,
        candidate_image="candidate:v1",
    )
    return replace(spec, verifier_bundle_digest=directory_digest(verifier))


def test_worker_command_is_readonly_networkless_and_candidate_free(tmp_path: Path) -> None:
    cmd = build_verifier_worker_command(_spec(tmp_path))
    joined = " ".join(cmd)
    assert "--network none" in joined
    assert "--read-only" in joined
    assert "--cap-drop ALL" in joined
    assert "private-verifier:v1@sha256:" + "2" * 64 in joined
    assert ":/submission:ro" in joined
    assert ":/tests:ro" in joined
    assert "/var/run/docker.sock" not in joined
    assert "ANTHROPIC_API_KEY" not in joined


def test_worker_rejects_same_image_and_symlink_bundle(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    with pytest.raises(VerifierWorkerError, match="independent"):
        build_verifier_worker_command(
            VerifierWorkerSpec(**{**spec.__dict__, "candidate_image": spec.image})
        )
    with pytest.raises(VerifierWorkerError, match="image digest must be independent"):
        build_verifier_worker_command(
            replace(
                spec,
                candidate_image="candidate:retagged",
                candidate_image_digest=spec.image_digest,
            )
        )
    link = tmp_path / "bundle-link"
    link.symlink_to(spec.verifier_bundle, target_is_directory=True)
    with pytest.raises(VerifierWorkerError, match="real directory"):
        build_verifier_worker_command(
            VerifierWorkerSpec(**{**spec.__dict__, "verifier_bundle": link})
        )

    (spec.verifier_bundle / "escape").symlink_to(spec.submission, target_is_directory=True)
    with pytest.raises(VerifierWorkerError, match="unsafe symlink"):
        build_verifier_worker_command(spec)


def test_worker_requires_and_rechecks_private_material_digests(tmp_path: Path) -> None:
    from bench.core.verifier_worker import run_verifier_worker

    spec = _spec(tmp_path)
    (spec.submission / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "legacy_layout": False,
                "exclusions": [],
                "files": [],
                "total_bytes": 0,
                "sealed_at": "2026-09-10T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "gold.json").write_text('{"value": 1}\n', encoding="utf-8")

    with pytest.raises(VerifierWorkerError, match="reference must be content-addressed"):
        build_verifier_worker_command(replace(spec, reference=reference))

    pinned = replace(
        spec,
        reference=reference,
        reference_digest=directory_digest(reference),
    )
    build_verifier_worker_command(pinned)
    (reference / "gold.json").write_text('{"value": 2}\n', encoding="utf-8")
    key = Ed25519PrivateKey.generate()
    with pytest.raises(VerifierWorkerError, match="reference changed before execution"):
        run_verifier_worker(
            pinned,
            runner=lambda *_args, **_kwargs: None,
            signing_key_hex=key.private_bytes_raw().hex(),
        )


def test_receipt_binds_case_profile_image_submission_and_result(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    result = BenchmarkResult.valid("case-001", passed=True, reason="ok")
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes_raw().hex()
    public = key.public_key().public_bytes_raw().hex()
    receipt = make_verifier_receipt(spec, result, signing_key_hex=private)
    assert receipt["tmpfs"] == spec.tmpfs
    assert receipt["cpus"] == spec.cpus
    assert receipt["memory"] == spec.memory
    assert receipt["pids_limit"] == spec.pids_limit
    verify_verifier_receipt(receipt, spec=spec, trusted_public_key_hex=public, expected_result=result.to_dict())
    receipt["result"]["reason"] = "tampered"
    with pytest.raises(VerifierWorkerError, match="digest mismatch"):
        verify_verifier_receipt(receipt, spec=spec, trusted_public_key_hex=public)


def test_receipt_rejects_submission_manifest_drift(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes_raw().hex()
    public = key.public_key().public_bytes_raw().hex()
    receipt = make_verifier_receipt(spec, BenchmarkResult.valid("case-001", True), signing_key_hex=private)
    (spec.submission / "manifest.json").write_text("changed\n", encoding="utf-8")
    with pytest.raises(VerifierWorkerError, match="binding mismatch"):
        verify_verifier_receipt(receipt, spec=spec, trusted_public_key_hex=public)


def test_receipt_signature_is_pinned_to_the_trusted_key(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    key = Ed25519PrivateKey.generate()
    receipt = make_verifier_receipt(
        spec, BenchmarkResult.valid("case-001", True),
        signing_key_hex=key.private_bytes_raw().hex(),
    )
    other = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
    with pytest.raises(VerifierWorkerError, match="not trusted"):
        verify_verifier_receipt(receipt, spec=spec, trusted_public_key_hex=other)


def test_receipt_rejects_cross_candidate_replay(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    key = Ed25519PrivateKey.generate()
    receipt = make_verifier_receipt(
        spec, BenchmarkResult.valid("case-001", True),
        signing_key_hex=key.private_bytes_raw().hex(),
    )
    replay_spec = replace(spec, candidate_image="candidate:another")
    with pytest.raises(VerifierWorkerError, match="candidate_image"):
        verify_verifier_receipt(
            receipt, spec=replay_spec,
            trusted_public_key_hex=key.public_key().public_bytes_raw().hex(),
        )


def test_formal_admission_binds_run_and_expires(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone
    from bench.core.verifier_worker import _signature_payload

    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    body = {
        "schema_version": "1", "run_id": "run-001", "attempt": 1,
        "case_id": "case-001", "case_version": "1.0.0",
        "public_digest": "sha256:" + "3" * 64,
        "agent_profile": "claude-mvp", "agent_profile_digest": "sha256:" + "5" * 64,
        "model_id": "claude-sonnet-4-6", "candidate_image": "candidate:v1",
        "sidecar_image": "gateway:v1", "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(), "nonce": "one-use-run-001",
    }
    body["signature"] = {
        "algorithm": "ed25519", "key_id": "coordinator-v1",
        "signature_hex": key.sign(_signature_payload(body)).hex(),
    }
    path = tmp_path / "admission.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    digest = verify_formal_admission(
        path, trusted_public_key_hex=key.public_key().public_bytes_raw().hex(),
        expected={key: body[key] for key in ("run_id", "case_id", "case_version", "public_digest")},
    )
    assert digest.startswith("sha256:")
    body["run_id"] = "other-run"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(VerifierWorkerError, match="binding mismatch"):
        verify_formal_admission(
            path, trusted_public_key_hex=key.public_key().public_bytes_raw().hex(),
            expected={"run_id": "run-001"},
        )
