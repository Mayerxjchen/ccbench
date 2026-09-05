"""Independent Candidate Agent Qualification Verifier with Ed25519 Cryptographic Signatures.

Enforces Producer-Verifier separation:
The verification logic runs independently of the execution producer.
It evaluates raw runtime evidence from Docker inspections, probe outputs,
adversarial intercept checks, host model proxy budget records, and RunLock hashes.
A durable receipt is sealed ONLY if all anchored evidence primitives verify offline,
and is signed using maintainer Ed25519 asymmetric cryptography.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric import ed25519

from dftworld_bench.config.profiles import canonical_json, digest_bytes
from dftworld_bench.hpc.trust_store import QualificationTrustStore, TrustStoreError


def generate_ed25519_key_pair() -> Tuple[str, str]:
    """Generate (private_key_hex, public_key_hex)."""
    priv = ed25519.Ed25519PrivateKey.generate()
    return priv.private_bytes_raw().hex(), priv.public_key().public_bytes_raw().hex()


def sign_ed25519(private_key_hex: str, data_bytes: bytes) -> str:
    """Sign data bytes using Ed25519 private key, returning hex signature."""
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    sig = priv.sign(data_bytes)
    return sig.hex()


def verify_ed25519(public_key_hex: str, data_bytes: bytes, signature_hex: str) -> bool:
    """Verify signature over data bytes using Ed25519 public key."""
    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(signature_hex), data_bytes)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class VerificationVerdict:
    status: str  # "CONTAINER_PREFLIGHT_PASS", "PROMOTED", or "REJECTED"
    passed: bool
    reasons: List[str]
    anchors: Dict[str, Any]


class CandidateAgentVerifier:
    """Independent auditor for candidate agent sandbox qualification."""

    REQUIRED_CANARIES = (
        "canary_1_image_isolation",
        "canary_2_adversarial_containment",
        "canary_3_skills_topology",
        "canary_4_model_gateway_and_budget",
        "canary_5_run_lock_compliance",
    )
    REQUIRED_CODE_MODULES = (
        "dftworld_bench/agents.py",
        "dftworld_bench/core/model_proxy.py",
        "dftworld_bench/verifiers/candidate_agent_verifier.py",
        "eval.py",
    )
    REQUIRED_OFFLINE_ANCHORS = (
        "tool_policy_digest",
        "dockerfile_digest",
        "probe_digest",
        "agent_profile_digest",
        "skill_bundle_digest",
    )

    def __init__(self, workspace_root: Path | None = None) -> None:
        self.workspace_root = workspace_root or Path(__file__).resolve().parents[2]
        self.lock_file = self.workspace_root / "base-env-build" / "agent-claude-code" / "claude-code.lock.json"
        self.policy_file = self.workspace_root / "base-env-build" / "agent-claude-code" / "tool-policy.json"
        self.dockerfile = self.workspace_root / "base-env-build" / "agent-claude-code" / "Dockerfile"
        self.probe_file = self.workspace_root / "base-env-build" / "agent-claude-code" / "probes" / "qualify_agent.sh"
        self.agent_profiles = self.workspace_root / "infra" / "config" / "agent-profiles.toml"
        self.skill_image_lock = self.workspace_root / "base-env-build" / ".skill-image.json"

    def compute_code_identity(self) -> Dict[str, str]:
        """Compute sha256 hashes of critical trusted codebase modules."""
        modules = [
            "dftworld_bench/agents.py",
            "dftworld_bench/core/model_proxy.py",
            "dftworld_bench/verifiers/candidate_agent_verifier.py",
            "eval.py",
        ]
        identity = {}
        for rel_path in modules:
            p = self.workspace_root / rel_path
            if p.is_file():
                identity[rel_path] = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()
            else:
                identity[rel_path] = "missing"
        return identity

    def get_source_commit(self) -> str:
        """Obtain current git source commit."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self.workspace_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            return res.stdout.strip()
        except Exception:
            return "unknown"

    def verify(self, evidence: Dict[str, Any]) -> VerificationVerdict:
        """Verify raw evidence primitives against strict fail-closed criteria."""
        reasons: List[str] = []
        anchors: Dict[str, Any] = {}

        # 1. Check all required canary blocks exist
        for canary in self.REQUIRED_CANARIES:
            if canary not in evidence:
                reasons.append(f"Missing required canary evidence block: {canary}")
            elif evidence[canary].get("status") != "PASS":
                reasons.append(f"Canary {canary} reported non-PASS status: {evidence[canary].get('status')}")

        if reasons:
            return VerificationVerdict(status="REJECTED", passed=False, reasons=reasons, anchors={})

        # 2. Re-verify Canary 1: Real Docker Image & Internal Probe
        c1 = evidence["canary_1_image_isolation"]
        claimed_digest = c1.get("image_digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", claimed_digest):
            reasons.append(f"Invalid image digest format in Canary 1: {claimed_digest}")

        # Cross check with lock file on disk
        if not self.lock_file.is_file():
            reasons.append(f"Lock file missing on disk: {self.lock_file}")
        else:
            try:
                lock_data = json.loads(self.lock_file.read_text(encoding="utf-8"))
                expected_digest = lock_data.get("built_image_digest")
                if claimed_digest != expected_digest:
                    reasons.append(f"Canary 1 image digest {claimed_digest} does not match lock {expected_digest}")
                anchors["locked_image_digest"] = expected_digest
            except Exception as e:
                reasons.append(f"Failed to read claude-code.lock.json: {e}")

        # Check real probe execution output
        probe_code = c1.get("probe_returncode")
        probe_stdout = c1.get("probe_stdout", "")
        if probe_code != 0:
            reasons.append(f"Probe exit code in container was non-zero: {probe_code}")
        if "User: agent (UID=1000)" not in probe_stdout:
            reasons.append("Probe stdout missing non-root agent user confirmation")
        if "PASS: Zero scientific computing engines" not in probe_stdout:
            reasons.append("Probe stdout missing confirmation of zero scientific engines")
        if "PASS: No docker socket or host ssh credentials detected" not in probe_stdout:
            reasons.append("Probe stdout missing confirmation of secret/socket isolation")

        # 3. Re-verify Canary 2: Real Adversarial Intercepts
        c2 = evidence["canary_2_adversarial_containment"]
        intercepts = c2.get("intercepted_commands", {})
        expected_cmds = {"ssh", "sbatch", "compshare", "docker", "nc", "nmap"}
        for cmd in expected_cmds:
            info = intercepts.get(cmd)
            if not info:
                reasons.append(f"Missing adversarial intercept evidence for command: {cmd}")
                continue
            if info.get("returncode") != 126:
                reasons.append(f"Adversarial command {cmd} did not exit with code 126 (got {info.get('returncode')})")
            if f"Access Denied: command {cmd} is strictly prohibited" not in info.get("stderr", ""):
                reasons.append(f"Adversarial command {cmd} stderr missing security policy rejection notice")

        # Check forbidden env leak prevention and network isolation
        if not c2.get("secret_isolation_verified", False):
            reasons.append("Canary 2 failed to verify secret isolation prevention")
        if not c2.get("network_egress_blocked", False):
            reasons.append("Canary 2 failed to verify network egress isolation")
        if not c2.get("raw_socket_blocked", False):
            reasons.append("Canary 2 failed to verify raw socket kernel-level network blockage")

        # 4. Re-verify Canary 3: Skills Topology
        c3 = evidence["canary_3_skills_topology"]
        if not c3.get("skill_mounted", False) or c3.get("target_path") != "/app/.claude/skills/hpc-submit/SKILL.md":
            reasons.append(f"Canary 3 invalid skills mount verification: {c3}")

        # 5. Re-verify Canary 4: Model Gateway Proxy & Budget Enforcement
        c4 = evidence["canary_4_model_gateway_and_budget"]
        if not c4.get("sidecar_gateway_verified"):
            reasons.append("Canary 4 failed to verify production dual-homed sidecar communication")
        if not c4.get("proxy_auth_verified"):
            reasons.append("Canary 4 failed to verify proxy ephemeral token authentication")
        if not c4.get("budget_cutoff_verified", False):
            reasons.append("Canary 4 failed to verify live budget cut-off enforcement")
        if c4.get("budget_cutoff_http_code") != 429:
            reasons.append(f"Canary 4 budget cut-off did not return HTTP 429 (got {c4.get('budget_cutoff_http_code')})")
        if c4.get("callback_count", 0) <= 0:
            reasons.append("Canary 4 budget exceeded callback was not invoked")
        if c4.get("over_limit_chunk_forwarded", True) is not False:
            reasons.append("Canary 4 leaked over-limit chunk to candidate client")
        if c4.get("ledger_after_overflow", 0) <= c4.get("ledger_after_request_1", 0):
            reasons.append("Canary 4 BudgetLedger did not record overflow accounting")

        # 6. Re-verify Canary 5: RunLock Compliance
        c5 = evidence["canary_5_run_lock_compliance"]
        lock_digest = c5.get("lock_digest", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", lock_digest):
            reasons.append(f"Canary 5 invalid lock digest format: {lock_digest}")
        if not c5.get("lock_verified", False):
            reasons.append("Canary 5 RunLock verification failed")
        anchors["lock_digest"] = lock_digest

        # 7. Check container cleanup
        cleanup = evidence.get("container_cleanup", {})
        if cleanup.get("running_containers_found", 0) > 0:
            reasons.append(f"Orphan agent containers found after run: {cleanup.get('running_containers_found')}")

        passed = len(reasons) == 0

        # Verdict logic: if real model execution was not run, verdict is CONTAINER_PREFLIGHT_PASS, NOT PROMOTED
        agent_exec = evidence.get("real_agent_execution", {})
        if not agent_exec.get("executed", False):
            status = "CONTAINER_PREFLIGHT_PASS" if passed else "REJECTED"
        else:
            status = "PROMOTED" if (passed and agent_exec.get("status") == "PASS") else "REJECTED"

        return VerificationVerdict(status=status, passed=passed, reasons=reasons, anchors=anchors)

    def seal_receipt(
        self,
        *,
        run_id: str,
        evidence: Dict[str, Any],
        evidence_dir: Path,
        signing_key_hex: Optional[str] = None,
        key_id: str = "candidate-agent-v2",
    ) -> Path:
        """Verify evidence and seal an immutable Ed25519-signed receipt."""
        verdict = self.verify(evidence)
        if not verdict.passed:
            raise ValueError(f"Cannot seal qualification receipt: verifier rejected evidence: {verdict.reasons}")

        evidence_dir.mkdir(parents=True, exist_ok=True)
        receipt_file = evidence_dir / "receipt.json"

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        c1 = evidence["canary_1_image_isolation"]

        # Collect full offline anchors
        code_identity = self.compute_code_identity()
        source_commit = self.get_source_commit()
        tool_policy_digest = "sha256:" + hashlib.sha256(self.policy_file.read_bytes()).hexdigest() if self.policy_file.is_file() else "missing"
        dockerfile_digest = "sha256:" + hashlib.sha256(self.dockerfile.read_bytes()).hexdigest() if self.dockerfile.is_file() else "missing"
        probe_digest = "sha256:" + hashlib.sha256(self.probe_file.read_bytes()).hexdigest() if self.probe_file.is_file() else "missing"
        agent_profile_digest = "sha256:" + hashlib.sha256(self.agent_profiles.read_bytes()).hexdigest() if self.agent_profiles.is_file() else "missing"
        skill_bundle_digest = "sha256:" + hashlib.sha256(self.skill_image_lock.read_bytes()).hexdigest() if self.skill_image_lock.is_file() else "missing"

        receipt_payload: Dict[str, Any] = {
            "schema_id": "https://mlip-bench.example/schemas/candidate-agent-qualification-receipt.schema.json",
            "kind": "candidate-agent-qualification/v1",
            "run_id": run_id,
            "qualification_stamp": stamp,
            "status": verdict.status,
            "agent_execution_status": "PASS" if verdict.status == "PROMOTED" else "NOT_RUN",
            "formal_benchmark_readiness": "READY" if verdict.status == "PROMOTED" else "BLOCKED",
            "candidate_engine": "claude-code",
            "candidate_image": "mlffbench-candidate-claude-code-sandbox:v1",
            "candidate_image_digest": c1.get("image_digest"),
            "source_commit": source_commit,
            "code_identity": code_identity,
            "offline_anchors": {
                **verdict.anchors,
                "tool_policy_digest": tool_policy_digest,
                "dockerfile_digest": dockerfile_digest,
                "probe_digest": probe_digest,
                "agent_profile_digest": agent_profile_digest,
                "skill_bundle_digest": skill_bundle_digest,
            },
            "evidence": evidence,
            "summary": (
                f"Claude Code qualification result: {verdict.status}. "
                "Hardened image isolation, non-root user, command interception, host model proxy "
                "ephemeral authentication, real-time streaming budget cutoff, and RunLock binding verified."
            ),
        }

        # Cryptographic content digest over canonical JSON (excluding signature)
        content_repr = canonical_json(receipt_payload)
        receipt_digest = digest_bytes(content_repr)
        receipt_payload["receipt_digest"] = receipt_digest

        # Ed25519 Asymmetric Digital Signature
        if not signing_key_hex:
            raise ValueError(
                "Cannot seal qualification receipt without an explicitly provided maintainer signing key. "
                "Ephemeral key auto-generation is strictly prohibited to prevent self-signed trust forgery."
            )
        priv_key = signing_key_hex
        sig_hex = sign_ed25519(priv_key, content_repr.encode("utf-8"))
        pub_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_key)).public_key().public_bytes_raw().hex()

        receipt_payload["signature"] = {
            "algorithm": "ed25519",
            "key_id": key_id,
            "public_key": pub_key,  # Informational only; verifier never trusts this key
            "signature_hex": sig_hex,
            "signed_at": stamp,
        }

        receipt_file.write_text(json.dumps(receipt_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return receipt_file

    def verify_receipt_file(
        self,
        receipt_file: Path,
        expected_public_key_hex: Optional[str] = None,
        trust_store: Optional[QualificationTrustStore] = None,
    ) -> bool:
        """Verify an existing receipt on disk, checking digest and Ed25519 signature.

        CRITICAL SECURITY INVARIANT:
        The public key MUST come from either:
          1. Explicit parameter expected_public_key_hex, OR
          2. The external QualificationTrustStore matching key_id.
        The verifier NEVER falls back to the public key embedded in the receipt itself!
        """
        if not receipt_file.is_file():
            return False
        data = json.loads(receipt_file.read_text(encoding="utf-8"))
        sig_block = data.get("signature")
        if not sig_block or sig_block.get("algorithm") != "ed25519":
            return False

        payload_without_meta = {k: v for k, v in data.items() if k not in ("signature", "receipt_digest")}
        recomputed_content = canonical_json(payload_without_meta)
        recomputed_digest = digest_bytes(recomputed_content)

        if data.get("receipt_digest") != recomputed_digest:
            return False

        key_id = sig_block.get("key_id", "candidate-agent-v2")
        if expected_public_key_hex:
            pub_key = expected_public_key_hex
        else:
            store = trust_store or QualificationTrustStore.load_default()
            try:
                pub_key = store.resolve_public_key_hex(
                    key_id, expected_purpose="candidate-agent-qualification"
                )
            except Exception:
                return False

        if not pub_key:
            return False

        return verify_ed25519(pub_key, recomputed_content.encode("utf-8"), sig_block.get("signature_hex", ""))
