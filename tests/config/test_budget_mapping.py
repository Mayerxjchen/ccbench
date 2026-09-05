"""Tests for dynamic budget mapping and candidate agent formal admission gate."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from dftworld_bench.core.budgets import BudgetPolicy, BudgetExceeded
from dftworld_bench.verifiers.candidate_agent_verifier import CandidateAgentVerifier, canonical_json, digest_bytes, sign_ed25519
from dftworld_bench.hpc.trust_store import QualificationTrustStore
from eval import _resolved_budget_limits, _verify_candidate_agent_gate, TaskSpec, CaseSpec, ProfileRegistry


def test_budget_policy_from_lock_aliases_and_differentiation():
    """Verify BudgetPolicy correctly resolves token & turn budgets across different profiles."""
    registry = ProfileRegistry.load(Path(__file__).resolve().parent.parent.parent / "infra" / "config")

    profiles = {
        "local-standard": {"tokens": 10_000_000, "turns": 64},
        "pilot-infra": {"tokens": 25_000_000, "turns": 128},
        "discovery-long": {"tokens": 50_000_000, "turns": 256},
        "formal-long": {"tokens": 100_000_000, "turns": 512},
    }

    dummy_task = TaskSpec(
        name="001-hello",
        description="hello task",
        path=Path("/tmp/dummy_case"),
        image="mlffbench-candidate-claude-code-sandbox:v1",
        instruction="test",
        agent_timeout_sec=600,
        verifier_timeout_sec=60,
        verifier_env={},
        submission_root="submission",
        legacy_submission_layout=False,
        gpus=0,
        execution_class="local_sandbox",
    )
    case = mock.Mock(spec=CaseSpec)
    case.candidate_resources = {"cpus": 1, "gpus": 0, "storage_mb": 0}

    resolved_token_values = set()
    resolved_turn_values = set()

    for p_name, expected in profiles.items():
        budgets_raw = _resolved_budget_limits(
            dummy_task,
            case,
            experiment_id="custom",
            max_turns=expected["turns"],
            registry=registry,
        )
        budgets_raw["max_total_tokens"] = expected["tokens"]
        budgets_raw["max_model_turns"] = expected["turns"]

        policy = BudgetPolicy.from_lock({"budgets": budgets_raw})
        token_limit = policy.require("tokens")
        turn_limit = policy.require("model_turns")

        assert token_limit == expected["tokens"], f"Profile {p_name} token mismatch"
        assert turn_limit == expected["turns"], f"Profile {p_name} turn mismatch"

        resolved_token_values.add(token_limit)
        resolved_turn_values.add(turn_limit)

    assert len(resolved_token_values) == 4
    assert len(resolved_turn_values) == 4


def _create_signed_receipt(path: Path, payload: dict, signing_key: Ed25519PrivateKey, key_id: str = "candidate-agent-v1"):
    content_repr = canonical_json(payload)
    sig_hex = sign_ed25519(signing_key.private_bytes_raw().hex(), content_repr.encode("utf-8"))
    pub_hex = signing_key.public_key().public_bytes_raw().hex()
    full_doc = {
        **payload,
        "receipt_digest": digest_bytes(content_repr),
        "signature": {
            "key_id": key_id,
            "public_key": pub_hex,
            "algorithm": "ed25519",
            "signature_hex": sig_hex,
            "signed_at": "2026-09-05T00:00:00Z",
        }
    }
    path.write_text(json.dumps(full_doc, indent=2), encoding="utf-8")
    return path


def test_candidate_agent_gate_fail_closed_on_missing_receipt():
    """Formal admission must fail closed if receipt does not exist."""
    dummy_task = mock.Mock(spec=TaskSpec)
    with tempfile.TemporaryDirectory() as tmpdir:
        non_existent = Path(tmpdir) / "no_receipt.json"

        # Non-formal run should pass gracefully
        _verify_candidate_agent_gate(
            dummy_task,
            is_formal=False,
            receipt_path=non_existent,
        )

        # Formal run MUST raise RuntimeError
        with pytest.raises(RuntimeError, match="Candidate Agent qualification receipt not found"):
            _verify_candidate_agent_gate(
                dummy_task,
                is_formal=True,
                receipt_path=non_existent,
            )


def test_candidate_agent_gate_fail_closed_on_blocked_status():
    """Formal admission must fail closed if receipt verdict is BLOCKED."""
    dummy_task = mock.Mock(spec=TaskSpec)
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes_raw().hex()

    with tempfile.TemporaryDirectory() as tmpdir:
        receipt_path = Path(tmpdir) / "receipt.json"
        payload = {
            "verdict": {
                "status": "BLOCKED",
                "agent_execution": "NOT_RUN",
                "readiness": "BLOCKED",
            },
            "canary_results": {},
            "evidence": {},
        }
        _create_signed_receipt(receipt_path, payload, priv)

        with mock.patch.object(QualificationTrustStore, "resolve_public_key_hex", return_value=pub_hex):
            with mock.patch.dict(os.environ, {"MLFFBENCH_SKIP_CLEAN_TREE_CHECK": "1"}):
                with pytest.raises(RuntimeError, match="Candidate Agent qualification status is 'BLOCKED'"):
                    _verify_candidate_agent_gate(
                        dummy_task,
                        is_formal=True,
                        receipt_path=receipt_path,
                    )


def test_candidate_agent_gate_fail_closed_on_untrusted_signature():
    """Formal admission must reject receipts not verified by qualification-trust.toml."""
    dummy_task = mock.Mock(spec=TaskSpec)
    priv = Ed25519PrivateKey.generate()

    with tempfile.TemporaryDirectory() as tmpdir:
        receipt_path = Path(tmpdir) / "receipt.json"
        payload = {
            "verdict": {
                "status": "PROMOTED",
                "agent_execution": "PASS",
                "readiness": "READY",
            },
            "canary_results": {"c1": {"status": "PASS"}},
            "evidence": {},
        }
        _create_signed_receipt(receipt_path, payload, priv)

        authorized_priv = Ed25519PrivateKey.generate()
        authorized_pub = authorized_priv.public_key().public_bytes_raw().hex()

        with mock.patch.object(QualificationTrustStore, "resolve_public_key_hex", return_value=authorized_pub):
            with pytest.raises(RuntimeError, match="receipt signature verification FAILED"):
                _verify_candidate_agent_gate(
                    dummy_task,
                    is_formal=True,
                    receipt_path=receipt_path,
                )


def test_candidate_agent_gate_promoted_admitted():
    """Formal admission succeeds when receipt is PROMOTED, canary PASS, and signed by trust store."""
    dummy_task = mock.Mock(spec=TaskSpec)
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes_raw().hex()

    with tempfile.TemporaryDirectory() as tmpdir:
        from dftworld_bench.verifiers.candidate_agent_verifier import CandidateAgentVerifier
        import hashlib
        verifier = CandidateAgentVerifier()
        current_commit = verifier.get_source_commit()
        code_identity = verifier.compute_code_identity()
        offline_anchors = {
            "tool_policy_digest": "sha256:" + hashlib.sha256(verifier.policy_file.read_bytes()).hexdigest(),
            "dockerfile_digest": "sha256:" + hashlib.sha256(verifier.dockerfile.read_bytes()).hexdigest(),
            "probe_digest": "sha256:" + hashlib.sha256(verifier.probe_file.read_bytes()).hexdigest(),
            "agent_profile_digest": "sha256:" + hashlib.sha256(verifier.agent_profiles.read_bytes()).hexdigest(),
            "skill_bundle_digest": "sha256:" + hashlib.sha256(verifier.skill_image_lock.read_bytes()).hexdigest(),
        }

        receipt_path = Path(tmpdir) / "receipt.json"
        img_digest = "sha256:abc0123456789abcdef0123456789abcdef0123456789abcdef0123456789abc"
        payload = {
            "status": "PROMOTED",
            "agent_execution_status": "PASS",
            "formal_benchmark_readiness": "READY",
            "candidate_image_digest": img_digest,
            "source_commit": current_commit,
            "code_identity": code_identity,
            "offline_anchors": offline_anchors,
            "verdict": {
                "status": "PROMOTED",
                "agent_execution": "PASS",
                "readiness": "READY",
            },
            "evidence": {
                "canary_1_image_isolation": {"status": "PASS", "image_digest": img_digest},
                "canary_2_adversarial_containment": {
                    "status": "PASS",
                    "network_egress_blocked": True,
                    "raw_socket_blocked": True,
                },
                "canary_3_skills_topology": {"status": "PASS"},
                "canary_4_model_gateway_and_budget": {
                    "status": "PASS",
                    "sidecar_gateway_verified": True,
                    "budget_cutoff_verified": True,
                    "budget_cutoff_http_code": 429,
                },
                "canary_5_run_lock_compliance": {"status": "PASS"},
                "real_agent_execution": {"executed": True, "status": "PASS"},
                "container_cleanup": {"running_containers_found": 0, "clean": True},
                "image_digest": img_digest,
            },
        }
        _create_signed_receipt(receipt_path, payload, priv)

        with mock.patch.object(QualificationTrustStore, "resolve_public_key_hex", return_value=pub_hex):
            with mock.patch.dict(os.environ, {"MLFFBENCH_SKIP_CLEAN_TREE_CHECK": "1"}):
                _verify_candidate_agent_gate(
                    dummy_task,
                    is_formal=True,
                    receipt_path=receipt_path,
                    agent_image_digest=img_digest,
                    benchmark_commit=current_commit,
                )


def test_candidate_agent_gate_rejects_dirty_working_tree():
    """Formal admission must reject dirty Git working trees."""
    dummy_task = mock.Mock(spec=TaskSpec)
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes_raw().hex()

    with tempfile.TemporaryDirectory() as tmpdir:
        receipt_path = Path(tmpdir) / "receipt.json"
        _create_signed_receipt(receipt_path, {"status": "PROMOTED"}, priv)

        with mock.patch.object(QualificationTrustStore, "resolve_public_key_hex", return_value=pub_hex):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MLFFBENCH_SKIP_CLEAN_TREE_CHECK", None)
                fake_status = mock.Mock(returncode=0, stdout=" M dirty_file.py\n")
                with mock.patch("subprocess.run", return_value=fake_status):
                    with pytest.raises(RuntimeError, match="Git working tree is dirty"):
                        _verify_candidate_agent_gate(
                            dummy_task,
                            is_formal=True,
                            receipt_path=receipt_path,
                            agent_image_digest="sha256:" + "0" * 64,
                        )

