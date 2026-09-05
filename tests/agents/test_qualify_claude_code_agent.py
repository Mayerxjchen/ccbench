"""Unit tests verifying CandidateAgentVerifier, Ed25519 signatures, and receipt integrity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest

from dftworld_bench.verifiers.candidate_agent_verifier import (
    CandidateAgentVerifier,
    VerificationVerdict,
    generate_ed25519_key_pair,
)


@pytest.fixture
def valid_evidence() -> dict[str, Any]:
    lock_path = Path(__file__).resolve().parents[2] / "base-env-build" / "agent-claude-code" / "claude-code.lock.json"
    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    image_digest = lock_data["built_image_digest"]

    return {
        "canary_1_image_isolation": {
            "status": "PASS",
            "image_name": "mlffbench-candidate-claude-code-sandbox:v1",
            "image_digest": image_digest,
            "probe_returncode": 0,
            "probe_stdout": (
                "User: agent (UID=1000)\n"
                "PASS: Zero scientific computing engines in candidate agent image.\n"
                "PASS: No docker socket or host ssh credentials detected.\n"
                "ALL AGENT SANDBOX PROBES PASSED"
            ),
        },
        "canary_2_adversarial_containment": {
            "status": "PASS",
            "intercepted_commands": {
                cmd: {
                    "returncode": 126,
                    "stderr": f"Access Denied: command {cmd} is strictly prohibited by MLFFBench security policy.",
                    "blocked": True,
                }
                for cmd in ["ssh", "sbatch", "compshare", "docker", "nc", "nmap"]
            },
            "secret_isolation_verified": True,
            "network_egress_blocked": True,
            "raw_socket_blocked": True,
        },
        "canary_3_skills_topology": {
            "status": "PASS",
            "target_path": "/app/.claude/skills/hpc-submit/SKILL.md",
            "skill_mounted": True,
        },
        "canary_4_model_gateway_and_budget": {
            "status": "PASS",
            "sidecar_gateway_verified": True,
            "proxy_auth_verified": True,
            "budget_cutoff_verified": True,
            "budget_cutoff_http_code": 429,
            "callback_count": 1,
            "over_limit_chunk_forwarded": False,
            "ledger_before": 0,
            "ledger_after_request_1": 25,
            "ledger_after_overflow": 35,
        },
        "canary_5_run_lock_compliance": {
            "status": "PASS",
            "lock_digest": "sha256:" + "a" * 64,
            "lock_verified": True,
        },
        "container_cleanup": {
            "running_containers_found": 0,
            "clean": True,
        },
    }


def test_verifier_accepts_preflight_pass(valid_evidence: dict[str, Any], tmp_path: Path):
    verifier = CandidateAgentVerifier()
    verdict = verifier.verify(valid_evidence)
    assert verdict.passed is True
    assert verdict.status == "CONTAINER_PREFLIGHT_PASS"

    priv_hex, pub_hex = generate_ed25519_key_pair()
    receipt_file = verifier.seal_receipt(
        run_id="test-preflight-001",
        evidence=valid_evidence,
        evidence_dir=tmp_path / "evidence",
        signing_key_hex=priv_hex,
    )
    assert receipt_file.is_file()
    receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt_data["status"] == "CONTAINER_PREFLIGHT_PASS"
    assert receipt_data["agent_execution_status"] == "NOT_RUN"
    assert receipt_data["formal_benchmark_readiness"] == "BLOCKED"
    assert "source_commit" in receipt_data
    assert "code_identity" in receipt_data
    assert "offline_anchors" in receipt_data
    assert receipt_data["signature"]["algorithm"] == "ed25519"

    # Verify cryptographic signature
    assert verifier.verify_receipt_file(receipt_file, expected_public_key_hex=pub_hex) is True


def test_verifier_promotes_when_real_execution_passes(valid_evidence: dict[str, Any], tmp_path: Path):
    valid_evidence["real_agent_execution"] = {
        "executed": True,
        "status": "PASS",
        "tokens_used": 150,
        "turns": 2,
    }
    verifier = CandidateAgentVerifier()
    verdict = verifier.verify(valid_evidence)
    assert verdict.passed is True
    assert verdict.status == "PROMOTED"

    priv_hex, pub_hex = generate_ed25519_key_pair()
    receipt_file = verifier.seal_receipt(
        run_id="test-promoted-001",
        evidence=valid_evidence,
        evidence_dir=tmp_path / "evidence",
        signing_key_hex=priv_hex,
    )
    assert verifier.verify_receipt_file(receipt_file, expected_public_key_hex=pub_hex) is True
    receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt_data["status"] == "PROMOTED"
    assert receipt_data["agent_execution_status"] == "PASS"
    assert receipt_data["formal_benchmark_readiness"] == "READY"


def test_verifier_tampered_receipt_fails(valid_evidence: dict[str, Any], tmp_path: Path):
    verifier = CandidateAgentVerifier()
    priv_hex, pub_hex = generate_ed25519_key_pair()
    receipt_file = verifier.seal_receipt(
        run_id="test-tamper-001",
        evidence=valid_evidence,
        evidence_dir=tmp_path / "evidence",
        signing_key_hex=priv_hex,
    )
    # Tamper with the receipt on disk
    data = json.loads(receipt_file.read_text(encoding="utf-8"))
    data["status"] = "PROMOTED"  # Maliciously attempt to promote
    receipt_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    assert verifier.verify_receipt_file(receipt_file, expected_public_key_hex=pub_hex) is False


def test_verifier_rejects_missing_canary(valid_evidence: dict[str, Any]):
    del valid_evidence["canary_3_skills_topology"]
    verifier = CandidateAgentVerifier()
    verdict = verifier.verify(valid_evidence)
    assert verdict.passed is False
    assert verdict.status == "REJECTED"
    assert any("Missing required canary" in r for r in verdict.reasons)


def test_verifier_rejects_non_126_intercept(valid_evidence: dict[str, Any]):
    valid_evidence["canary_2_adversarial_containment"]["intercepted_commands"]["ssh"]["returncode"] = 0
    verifier = CandidateAgentVerifier()
    verdict = verifier.verify(valid_evidence)
    assert verdict.passed is False
    assert any("did not exit with code 126" in r for r in verdict.reasons)


def test_verifier_rejects_mismatched_image_digest(valid_evidence: dict[str, Any]):
    valid_evidence["canary_1_image_isolation"]["image_digest"] = "sha256:" + "0" * 64
    verifier = CandidateAgentVerifier()
    verdict = verifier.verify(valid_evidence)
    assert verdict.passed is False
    assert any("does not match lock" in r for r in verdict.reasons)


def test_verifier_rejects_orphan_containers(valid_evidence: dict[str, Any]):
    valid_evidence["container_cleanup"]["running_containers_found"] = 2
    verifier = CandidateAgentVerifier()
    verdict = verifier.verify(valid_evidence)
    assert verdict.passed is False
    assert any("Orphan agent containers found" in r for r in verdict.reasons)


def test_verifier_refuses_to_seal_without_explicit_signing_key(valid_evidence: dict[str, Any], tmp_path: Path):
    verifier = CandidateAgentVerifier()
    with pytest.raises(ValueError, match="Cannot seal qualification receipt without an explicitly provided maintainer signing key"):
        verifier.seal_receipt(
            run_id="test-no-key-001",
            evidence=valid_evidence,
            evidence_dir=tmp_path / "evidence",
            signing_key_hex=None,
        )


def test_verifier_rejects_self_signed_forgery_without_external_trust_root(valid_evidence: dict[str, Any], tmp_path: Path):
    """Verify that an attacker generating their own key and embedding it in receipt is REJECTED."""
    verifier = CandidateAgentVerifier()
    priv_hex, pub_hex = generate_ed25519_key_pair()
    receipt_file = verifier.seal_receipt(
        run_id="test-forged-001",
        evidence=valid_evidence,
        evidence_dir=tmp_path / "evidence",
        signing_key_hex=priv_hex,
    )
    # 1. Default verification without configured trust store MUST FAIL closed
    # (even though receipt contains the forged public_key)
    assert verifier.verify_receipt_file(receipt_file) is False

    # 2. When the operator trust store actively registers this key, verification passes
    from dftworld_bench.hpc.trust_store import QualificationTrustStore
    store = QualificationTrustStore()
    store.register_key("candidate-agent-v2", pub_hex, purpose="candidate-agent-qualification")
    assert verifier.verify_receipt_file(receipt_file, trust_store=store) is True


def test_verifier_rejects_canary_4_missing_callback(valid_evidence: dict[str, Any]):
    valid_evidence["canary_4_model_gateway_and_budget"]["callback_count"] = 0
    verifier = CandidateAgentVerifier()
    verdict = verifier.verify(valid_evidence)
    assert verdict.passed is False
    assert any("callback was not invoked" in r for r in verdict.reasons)


def test_verifier_rejects_canary_4_over_limit_leak(valid_evidence: dict[str, Any]):
    valid_evidence["canary_4_model_gateway_and_budget"]["over_limit_chunk_forwarded"] = True
    verifier = CandidateAgentVerifier()
    verdict = verifier.verify(valid_evidence)
    assert verdict.passed is False
    assert any("leaked over-limit chunk" in r for r in verdict.reasons)


def test_verifier_rejects_canary_4_no_overflow_accounting(valid_evidence: dict[str, Any]):
    valid_evidence["canary_4_model_gateway_and_budget"]["ledger_after_overflow"] = 25
    valid_evidence["canary_4_model_gateway_and_budget"]["ledger_after_request_1"] = 25
    verifier = CandidateAgentVerifier()
    verdict = verifier.verify(valid_evidence)
    assert verdict.passed is False
    assert any("did not record overflow accounting" in r for r in verdict.reasons)


def test_verifier_rejects_canary_4_missing_sidecar(valid_evidence: dict[str, Any]):
    valid_evidence["canary_4_model_gateway_and_budget"]["sidecar_gateway_verified"] = False
    verifier = CandidateAgentVerifier()
    verdict = verifier.verify(valid_evidence)
    assert verdict.passed is False
    assert any("failed to verify production dual-homed sidecar" in r for r in verdict.reasons)

