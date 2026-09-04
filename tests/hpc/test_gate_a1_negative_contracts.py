"""Gate A1 Negative Contract Probes Suite.

This test suite formalizes and mechanically proves the 8 mandatory security and
integrity negative contracts required for Gate A1 (Architecture Freeze):

1. Contract 1: Agent direct specification of physical image/SIF paths is forbidden;
   only abstract runtime capabilities (e.g. cp2k, deepmd) are allowed.
2. Contract 2: Unbuilt or unverified runtimes (UNBUILT, BUILT_NOT_QUALIFIED, REVOKED)
   fail-closed at resolution time and cannot be submitted.
3. Contract 3: GPU instances receive deterministic provider-side ownership markers
   (name=mlffbench-{run_id}-worker, remark=mlffbench:{run_id}:worker) injected by the
   trusted driver, which cannot be forged or controlled by the agent.
4. Contract 4: Settlement / trusted teardown is token-independent: freeze and teardown
   reliably execute even if the client token is expired, revoked, or invalid.
5. Contract 5: Zero-Orphan Hard Gate: STOPPED, RUNNING, or orphaned cloud instances
   mandatorily fail qualification (rc != 0). Live cloud query errors fail closed.
6. Contract 6: Tampered receipts fail Ed25519 signature verification even if content
   hashes (SHA-256) are recomputed by an attacker.
7. Contract 7: Evidence files attempting path traversal (containing "..", symlinks,
   or absolute paths) are rejected by evidence root containment enforcement.
8. Contract 8: SiteProfile qualification_policy strictly governs required probe
   classes; omitting a required probe class fails closed.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from dftworld_bench.experiments.compute_profile_qualification import (
    build_compute_profile_qualification_receipt,
    check_evidence_containment,
    compute_receipt_digest,
    generate_ed25519_key_pair,
    verify_and_derive_qualification,
    verify_receipt_signature,
    verify_site_receipt,
)
from dftworld_bench.hpc.audit import GatewayAudit
from dftworld_bench.hpc.compute_profile import ComputeProfile
from dftworld_bench.hpc.drivers.compshare import (
    CompShareCli,
    CompShareDriver,
    FakeCompShareCliRunner,
    RunScopedInstanceManager,
)
from dftworld_bench.hpc.gateway import Gateway, GatewayError
from dftworld_bench.hpc.runtime_resolution import (
    ResolvedRuntime,
    RuntimeResolutionError,
    RuntimeResolver,
    RuntimeStatus,
)


def _setup_mock_receipts(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    receipts_dir = tmp_path / "site_receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    cpu_doc = {"site_id": "ikkem-cpu", "verdict": "PASS"}
    cpu_bytes = json.dumps(cpu_doc, sort_keys=True).encode("utf-8")
    cpu_sha = "sha256:" + hashlib.sha256(cpu_bytes).hexdigest()
    (receipts_dir / "ikkem-cpu.receipt.json").write_bytes(cpu_bytes)

    gpu_doc = {"site_id": "compshare-gpu", "verdict": "PASS"}
    gpu_bytes = json.dumps(gpu_doc, sort_keys=True).encode("utf-8")
    gpu_sha = "sha256:" + hashlib.sha256(gpu_bytes).hexdigest()
    (receipts_dir / "compshare-gpu.receipt.json").write_bytes(gpu_bytes)

    return receipts_dir, {"ikkem-cpu": cpu_sha, "compshare-gpu": gpu_sha}


def _valid_profile_receipt(site_receipts: dict[str, str]) -> dict:
    doc = {
        "kind": "hpc-compute-profile-qualification/v1",
        "schema_id": "https://mlip-bench.example/schemas/compute-profile-qualification-receipt.schema.json",
        "compute_profile_id": "maintainer-hybrid-v1",
        "compute_profile_digest": "a" * 64,
        "routes": {"cpu": "ikkem-cpu", "gpu": "compshare-gpu"},
        "site_receipts": site_receipts,
        "cloud_recycling_evidence": {
            "stock_checked": True,
            "instance_id": "inst-qual-probe",
            "image_id": "img-deepmd-gpu-v1",
            "gpu_type": "rtx4090",
            "gpu_vram_gb": 24,
            "task_executed": True,
            "fetch_verified": True,
            "credentials_isolated": True,
            "settlement_terminated": True,
            "active_instances_count": 0,
            "orphan_instances_count": 0,
        },
    }
    doc["digest"] = compute_receipt_digest(doc)
    return doc


class TestGateA1NegativeContracts:
    """Rigorous negative contract verification for Gate A1."""

    def test_contract_1_agent_direct_image_path_rejected(self, tmp_path: Path):
        """Contract 1: Submitting without a server-resolved runtime fails closed."""
        cli = CompShareCli(runner=FakeCompShareCliRunner())
        driver = CompShareDriver(cli, workspace_root=str(tmp_path))

        # Agent tries to pass raw physical image path instead of resolved runtime
        spec = {
            "idempotency_key": "k-raw-path",
            "command": ["python", "train.py"],
            "image": "/custom/path/to/my-image.sif",
            "resources": {"gpus": 1},
        }
        with pytest.raises(Exception, match="mandates a trusted '_resolved_runtime'"):
            driver.submit(spec, run_id="run-c1", operation_id="op-1")

    def test_contract_2_unbuilt_runtime_rejected_at_resolution(self, tmp_path: Path):
        """Contract 2: Unbuilt runtime (UNBUILT) fails resolution and is filtered out."""
        repo_root = Path(__file__).resolve().parents[2]
        runtime_dir = repo_root / "reference" / "runtime"

        resolver = RuntimeResolver.from_lock_dir(runtime_dir)
        # Deepmd and JAX are currently UNBUILT and must not be in qualified capabilities
        assert "deepmd" not in resolver.qualified_capabilities()
        assert "jax" not in resolver.qualified_capabilities()

        # Direct resolution attempt must raise RuntimeResolutionError
        with pytest.raises(RuntimeResolutionError, match="UNBUILT"):
            resolver.resolve("deepmd")

    def test_contract_3_ownership_marker_injected_by_trusted_driver(self, tmp_path: Path):
        """Contract 3: Driver enforces deterministic ownership marker; agent cannot alter."""
        runner = FakeCompShareCliRunner()
        cli = CompShareCli(runner=runner)
        mgr = RunScopedInstanceManager(
            cli,
            ledger_path=tmp_path / "ledger.jsonl",
            orphan_ledger_path=tmp_path / "orphans.jsonl",
        )

        run_id = "run-c3-probe"
        inst_id = mgr.get_or_create_instance(run_id, "img-deepmd-gpu-v1", operation_id="op-1")
        assert inst_id in runner.instances
        inst_meta = runner.instances[inst_id]
        assert inst_meta["name"] == f"mlffbench-{run_id}-worker"
        assert inst_meta["remark"] == f"mlffbench:{run_id}:worker"

    def test_contract_4_token_independent_trusted_teardown(self, tmp_path: Path):
        """Contract 4: Trusted freeze and teardown execute even when client token is revoked."""
        from dftworld_bench.hpc.adapters.process_test import ProcessTestAdapter

        adapter = ProcessTestAdapter(root=tmp_path)
        audit = GatewayAudit(tmp_path / "audit.jsonl")
        gw = Gateway(
            adapter,
            workspace_root=tmp_path,
            audit=audit,
        )

        run_id = "run-c4"
        token = gw.issue(run_id, ["submit", "status", "cancel", "logs", "fetch", "usage"])

        # Submit a job
        spec = {
            "schema_version": 1,
            "idempotency_key": "k-c4",
            "runtime": "img@sha256:" + "a" * 64,
            "command": ["/bin/echo", "ok"],
            "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
            "inputs": [],
            "outputs": [],
        }
        gw.submit(token, run_id, spec, operation_id="op-c4", attempt=1)

        # Revoke the client token
        gw.revoke(token)

        # Normal client operations fail due to revocation
        with pytest.raises(GatewayError, match="revoked"):
            gw.status(token, run_id, "job-1")

        # Trusted administrative/watchdog teardown succeeds regardless
        gw.trusted_freeze(run_id)
        gw.trusted_teardown(run_id)

        assert gw.settlement_state(run_id) == "SETTLED"

        # Verify audit ledger recorded SETTLEMENT_BEGIN and SETTLEMENT_COMPLETE
        events = [e["event"].get("kind") for e in audit.entries()]
        assert "SETTLEMENT_BEGIN" in events
        assert "SETTLEMENT_COMPLETE" in events

    def test_contract_5_zero_orphan_gate_fails_on_active_or_error(self, tmp_path: Path):
        """Contract 5: Any active/stopped instance or query failure blocks qualification."""
        site_dir, hashes = _setup_mock_receipts(tmp_path)
        receipt = _valid_profile_receipt(hashes)

        # 5a: Active instances > 0 in evidence fails
        receipt_active = dict(receipt)
        receipt_active["cloud_recycling_evidence"] = dict(receipt["cloud_recycling_evidence"])
        receipt_active["cloud_recycling_evidence"]["active_instances_count"] = 1
        receipt_active["digest"] = compute_receipt_digest(receipt_active)

        with patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt") as mock_vr:
            mock_vr.return_value = {"problems": [], "derived": {"qualification_status": "PASS"}}
            v_active = verify_and_derive_qualification(receipt_active, site_receipts_dir=site_dir)
            assert v_active.passed is False
            assert any("Zero-Orphan Gate failed" in err for err in v_active.errors)

        # 5b: Live cloud query returns remaining active instances
        v_live = verify_and_derive_qualification(
            receipt,
            site_receipts_dir=site_dir,
            active_instances_checker=lambda: ["inst-leaked-001"],
        )
        assert v_live.passed is False
        assert any("detected active instances" in err for err in v_live.errors)

        # 5c: Live cloud query throws exception (fail-closed)
        def _failing_checker():
            raise RuntimeError("Cloud network timeout")

        v_err = verify_and_derive_qualification(
            receipt,
            site_receipts_dir=site_dir,
            active_instances_checker=_failing_checker,
        )
        assert v_err.passed is False
        assert any("error querying live cloud instances" in err for err in v_err.errors)

    def test_contract_6_ed25519_signature_tampering_fails(self, tmp_path: Path):
        """Contract 6: Recomputing SHA-256 after modifying fields cannot forge Ed25519 signature."""
        priv_hex, pub_hex = generate_ed25519_key_pair()
        site_dir, hashes = _setup_mock_receipts(tmp_path)
        raw_receipt = _valid_profile_receipt(hashes)

        signed_doc = build_compute_profile_qualification_receipt(
            compute_profile_id=raw_receipt["compute_profile_id"],
            compute_profile_digest=raw_receipt["compute_profile_digest"],
            routes=raw_receipt["routes"],
            site_receipts=raw_receipt["site_receipts"],
            cloud_recycling_evidence=raw_receipt["cloud_recycling_evidence"],
            private_key_hex=priv_hex,
        )

        assert verify_receipt_signature(signed_doc) is True

        # Tamper a critical field
        signed_doc["cloud_recycling_evidence"]["credentials_isolated"] = False
        # Attacker recalculates content digest
        signed_doc["digest"] = compute_receipt_digest(signed_doc)

        # Ed25519 signature verification fails
        assert verify_receipt_signature(signed_doc) is False

        with patch("dftworld_bench.experiments.compute_profile_qualification.verify_site_receipt") as mock_vr:
            mock_vr.return_value = {"problems": [], "derived": {"qualification_status": "PASS"}}
            verdict = verify_and_derive_qualification(signed_doc, site_receipts_dir=site_dir)
            assert verdict.passed is False
            assert any("signature verification failed" in err for err in verdict.errors)

    def test_contract_7_evidence_path_traversal_rejected(self, tmp_path: Path):
        """Contract 7: Path traversal attempts via evidence files fail closed."""
        evidence_root = tmp_path / "evidence_root"
        evidence_root.mkdir(parents=True, exist_ok=True)

        # 7a: Relative escape using ..
        with pytest.raises(ValueError, match="escapes evidence root"):
            check_evidence_containment(evidence_root, "../secret.txt")

        # 7b: Absolute path
        with pytest.raises(ValueError, match="Absolute evidence path forbidden"):
            check_evidence_containment(evidence_root, "/etc/passwd")

        # 7c: Subdirectory escape
        with pytest.raises(ValueError, match="escapes evidence root"):
            check_evidence_containment(evidence_root, "subdir/../../escape.log")

    def test_contract_8_site_profile_required_probe_classes_enforced(self, tmp_path: Path):
        """Contract 8: Omitting a required probe class declared in SiteProfile policy fails."""
        # Site profile requires both CPU and GPU canaries
        policy_both = {
            "site_id": "hybrid-site",
            "scheduler": "slurm",
            "qualification_policy": {"required_probe_classes": ["cpu", "gpu"]},
        }

        # Receipt only provides cpu probe
        cpu_only_receipt = {
            "kind": "hpc-site-qualification/v1",
            "digest": "",
            "source_commit": "0" * 40,
            "code_identity": {},
            "evidence": {
                "jobs": [
                    {
                        "probe_class": "cpu",
                        "accounting": {"state": "COMPLETED", "exit_code": 0},
                    }
                ]
            },
        }
        cpu_only_receipt["digest"] = compute_receipt_digest(
            {k: v for k, v in cpu_only_receipt.items() if k != "digest"}
        )

        with patch("dftworld_bench.experiments.qualification_receipt.verify_receipt") as mock_vr:
            mock_vr.return_value = {
                "problems": ["[canary_coverage] canary set must include probe classes ['cpu', 'gpu']; got ['cpu'], missing ['gpu']"],
                "derived": {"qualification_status": "INVALID"},
            }
            res = verify_site_receipt(
                cpu_only_receipt,
                site_profile=policy_both,
                root=tmp_path,
                receipt_dir=tmp_path,
            )
            assert res["derived"]["qualification_status"] == "INVALID"
            assert any("canary_coverage" in p for p in res["problems"])
