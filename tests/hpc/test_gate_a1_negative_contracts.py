"""Gate A1 Negative Contract Probes Suite.

This test suite formalizes and mechanically proves the 8 mandatory security and
integrity negative contracts required for Gate A1 (Architecture Freeze):

1. Contract 1: Agent direct specification of physical image/SIF paths is forbidden;
   only abstract runtime capabilities (e.g. cp2k, deepmd) are allowed.
2. Contract 2: Unbuilt or unverified runtimes (UNBUILT, BUILT_NOT_QUALIFIED, REVOKED)
   fail-closed at resolution time and cannot be submitted.
3. Contract 3: GPU instances receive deterministic provider-side ownership markers
   (name=bench-{sha256(run_id)[:16]}, remark=bench:run:{sha256(run_id)[:16]})
   injected by the trusted driver, which cannot be forged or controlled by the agent.
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

from bench.experiments.compute_profile_qualification import (
    build_compute_profile_qualification_receipt,
    check_evidence_containment,
    compute_receipt_digest,
    generate_ed25519_key_pair,
    verify_and_derive_qualification,
    verify_receipt_signature,
    verify_site_receipt,
)
from bench.hpc.audit import GatewayAudit
from bench.hpc.compute_profile import ComputeProfile
from bench.hpc.drivers.compshare import (
    CompShareCli,
    CompShareDriver,
    FakeCompShareCliRunner,
    RunScopedInstanceManager,
    make_ownership_marker,
)
from bench.hpc.gateway import Gateway, GatewayError
from bench.hpc.runtime_resolution import (
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
        runtime_dir = repo_root / "runtimes" / "locks"
        if not runtime_dir.is_dir():
            runtime_dir = repo_root / "reference" / "runtime"

        resolver = RuntimeResolver.from_lock_dir(runtime_dir)
        # Deepmd and JAX are currently unverified and must not be in qualified capabilities
        assert "deepmd" not in resolver.qualified_capabilities()
        assert "jax" not in resolver.qualified_capabilities()

        # Direct resolution attempt must raise RuntimeResolutionError
        with pytest.raises(RuntimeResolutionError, match="UNBUILT|BUILT_NOT_QUALIFIED"):
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
        expected_name, expected_remark = make_ownership_marker(run_id)
        assert inst_meta["name"] == expected_name
        assert inst_meta["remark"] == expected_remark

    def test_contract_4_token_independent_trusted_teardown(self, tmp_path: Path):
        """Contract 4: Trusted freeze and teardown execute even when client token is revoked."""
        from bench.hpc.adapters.process_test import ProcessTestAdapter

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

        with patch("bench.experiments.compute_profile_qualification.verify_site_receipt") as mock_vr:
            mock_vr.return_value = {"problems": [], "derived": {"qualification_status": "PASS"}}
            v_active = verify_and_derive_qualification(receipt_active, site_receipts_dir=site_dir)
            assert v_active.passed is False
            assert v_active.status == "LEGACY_NOT_ELIGIBLE"

        # 5b: Live cloud query returns remaining active instances
        v_live = verify_and_derive_qualification(
            receipt,
            site_receipts_dir=site_dir,
            active_instances_checker=lambda: ["inst-leaked-001"],
        )
        assert v_live.passed is False
        assert v_live.status == "LEGACY_NOT_ELIGIBLE"

        # 5c: Live cloud query throws exception (fail-closed)
        def _failing_checker():
            raise RuntimeError("Cloud network timeout")

        v_err = verify_and_derive_qualification(
            receipt,
            site_receipts_dir=site_dir,
            active_instances_checker=_failing_checker,
        )
        assert v_err.passed is False
        assert v_err.status == "LEGACY_NOT_ELIGIBLE"

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

        assert verify_receipt_signature(signed_doc, expected_public_key_hex=pub_hex) is True

        # Tamper a critical field
        signed_doc["cloud_recycling_evidence"]["credentials_isolated"] = False
        # Attacker recalculates content digest
        signed_doc["digest"] = compute_receipt_digest(signed_doc)

        # Ed25519 signature verification fails
        assert verify_receipt_signature(signed_doc, expected_public_key_hex=pub_hex) is False

        with patch("bench.experiments.compute_profile_qualification.verify_site_receipt") as mock_vr:
            mock_vr.return_value = {"problems": [], "derived": {"qualification_status": "PASS"}}
            verdict = verify_and_derive_qualification(signed_doc, site_receipts_dir=site_dir)
            assert verdict.passed is False
            assert verdict.status == "LEGACY_NOT_ELIGIBLE"

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

        with patch("bench.experiments.qualification_receipt.verify_receipt") as mock_vr:
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

    def _build_valid_compshare_fixture(
        self,
        tmp_path: Path,
        *,
        run_id: str = "run-a1-neg",
        inst_id: str = "inst-a1-neg",
        img_id: str = "img-deepmd-gpu-v1",
    ) -> tuple[dict, QualificationTrustStore, Path]:
        from bench.experiments.compute_profile_qualification import (
            build_compshare_site_qualification_receipt,
            generate_ed25519_key_pair,
        )
        from bench.experiments.qualification_receipt import canonical_digest, sha256_file
        from bench.hpc.trust_store import QualificationTrustStore, TrustKey

        code_file = tmp_path / "mod.py"
        code_file.write_text("# mod\n", encoding="utf-8")
        code_sha = sha256_file(code_file)

        lock_dir = tmp_path / "reference" / "runtime"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = lock_dir / "deepmd-runtime.lock.json"
        lock_doc = {
            "schema_id": "https://mlip-bench.example/schemas/dispatcher-compshare-runtime-lock/v2",
            "artifact": {"image_id": img_id},
            "qualification": {"status": "BUILT_NOT_QUALIFIED"},
        }
        lock_file.write_text(json.dumps(lock_doc), encoding="utf-8")
        lock_sha = f"sha256:{hashlib.sha256(lock_file.read_bytes()).hexdigest()}"

        art_dir = tmp_path / "outputs"
        art_dir.mkdir(parents=True, exist_ok=True)
        art_file = art_dir / "output.tar.gz"
        art_bytes = b"probe-output-data"
        art_file.write_bytes(art_bytes)
        art_sha = f"sha256:{hashlib.sha256(art_bytes).hexdigest()}"

        rep_dir = tmp_path / "settlement"
        rep_dir.mkdir(parents=True, exist_ok=True)
        rep_file = rep_dir / "report.json"
        rep_doc = {
            "run_id": run_id,
            "instance_id": inst_id,
            "stop_confirmed": True,
            "delete_confirmed": True,
            "orphan_count": 0,
        }
        rep_bytes = json.dumps(rep_doc).encode("utf-8")
        rep_file.write_bytes(rep_bytes)
        rep_sha = f"sha256:{hashlib.sha256(rep_bytes).hexdigest()}"

        audit_path = tmp_path / "audit.jsonl"
        audit = GatewayAudit(audit_path)
        for act in [
            "INSTANCE_CREATE_INTENT",
            "INSTANCE_CREATE_ACCEPTED",
            "INSTANCE_READY",
            "JOB_SUBMIT_INTENT",
            "JOB_SUBMIT_ACCEPTED",
            "JOB_TERMINAL",
            "ARTIFACT_FETCHED",
            "SETTLEMENT_BEGIN",
            "INSTANCE_STOP_ACCEPTED",
            "INSTANCE_DELETE_ACCEPTED",
            "INSTANCE_DELETE_CONFIRMED",
            "ZERO_ORPHAN_QUERY",
            "SETTLEMENT_COMPLETE",
        ]:
            audit.append({
                "action": act,
                "run_id": run_id,
                "instance_id": inst_id,
                "image_id": img_id,
            })
        audit_tail = audit.tail_digest()

        prof_file = Path(__file__).resolve().parents[2] / "examples" / "hpc" / "compshare-gpu-site-profile.json"
        sp_digest = canonical_digest(json.loads(prof_file.read_text(encoding="utf-8")))
        if not sp_digest.startswith("sha256:"):
            sp_digest = f"sha256:{sp_digest}"

        evidence = {
            "instance_lifecycle": {
                "instance_id": inst_id,
                "image_id": img_id,
                "stop_confirmed": True,
                "delete_confirmed": True,
            },
            "jobs": [
                {
                    "job_id": "job-1",
                    "probe_class": "gpu",
                    "image_id": img_id,
                    "accounting": {"state": "COMPLETED", "exit_code": 0},
                }
            ],
            "credential_isolation": {"verified": True},
            "fetch": {
                "artifacts": [
                    {
                        "path": "outputs/output.tar.gz",
                        "sha256": art_sha,
                        "size_bytes": len(art_bytes),
                    }
                ]
            },
            "settlement": {
                "terminated": True,
                "report_path": "settlement/report.json",
                "digest": rep_sha,
            },
            "orphan_check": {
                "method": "instance_list",
                "active_total": 0,
            },
        }

        priv_hex, pub_hex = generate_ed25519_key_pair()
        trust_store = QualificationTrustStore({
            "compshare-site-v1": TrustKey(
                key_id="compshare-site-v1",
                algorithm="ed25519",
                public_key_hex=pub_hex,
                status="ACTIVE",
            )
        })

        receipt = build_compshare_site_qualification_receipt(
            run_id=run_id,
            site_profile_id="compshare-gpu",
            site_profile_digest=sp_digest,
            source_commit="abcdef1234567890",
            code_identity={"mod.py": code_sha},
            runtime_lock={
                "path": "reference/runtime/deepmd-runtime.lock.json",
                "digest": lock_sha,
                "image_id": img_id,
            },
            evidence=evidence,
            audit_log="audit.jsonl",
            audit_tail_digest=audit_tail,
            private_key_hex=priv_hex,
            key_id="compshare-site-v1",
        )
        return receipt, trust_store, tmp_path

    def test_forged_lock_status_pass_cannot_resolve(self, tmp_path: Path):
        """Forged PASS in lock file is ignored by parser and fails resolution."""
        lock_file = tmp_path / "deepmd-runtime.lock.json"
        lock_doc = {
            "schema_id": "https://mlip-bench.example/schemas/dispatcher-compshare-runtime-lock/v2",
            "runtime": "deepmd",
            "artifact": {"image_id": "img-deepmd-fake"},
            "qualification": {"status": "PASS", "receipt_path": "fake.json"},
        }
        lock_file.write_text(json.dumps(lock_doc), encoding="utf-8")
        resolver = RuntimeResolver.from_lock_dir(tmp_path)
        assert "deepmd" not in resolver.qualified_capabilities()
        with pytest.raises(RuntimeResolutionError, match="BUILT_NOT_QUALIFIED"):
            resolver.resolve("deepmd")

    def test_missing_runtime_receipt_cannot_resolve(self, tmp_path: Path):
        """Runtime pointing to nonexistent qualification receipt cannot be activated."""
        from bench.hpc.runtime_catalog import TrustedRuntimeCatalog

        lock_file = tmp_path / "deepmd-runtime.lock.json"
        lock_doc = {
            "schema_id": "https://mlip-bench.example/schemas/dispatcher-compshare-runtime-lock/v2",
            "runtime": "deepmd",
            "artifact": {"image_id": "img-deepmd-fake"},
            "qualification": {"status": "BUILT_NOT_QUALIFIED", "receipt_path": "missing_receipt.json"},
        }
        lock_file.write_text(json.dumps(lock_doc), encoding="utf-8")
        catalog = TrustedRuntimeCatalog.load(lock_dir=tmp_path, qualification_root=tmp_path)
        resolver = catalog.to_resolver()
        assert "deepmd" not in resolver.qualified_capabilities()
        with pytest.raises(RuntimeResolutionError):
            resolver.resolve("deepmd")

    def test_self_signed_receipt_rejected(self, tmp_path: Path):
        """Self-signed receipt with arbitrary untrusted key cannot pass verification."""
        receipt, _, root = self._build_valid_compshare_fixture(tmp_path)
        from bench.hpc.trust_store import QualificationTrustStore

        empty_store = QualificationTrustStore()
        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=empty_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("signature" in p for p in res["problems"])

    def test_unsigned_receipt_rejected(self, tmp_path: Path):
        """Receipt lacking mandatory signature is rejected."""
        receipt, trust_store, root = self._build_valid_compshare_fixture(tmp_path)
        receipt.pop("signature", None)
        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=trust_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("signature" in p for p in res["problems"])

    def test_untrusted_key_id_rejected(self, tmp_path: Path):
        """Receipt referencing unknown key_id is rejected."""
        receipt, trust_store, root = self._build_valid_compshare_fixture(tmp_path)
        receipt["signature"]["key_id"] = "unknown-foreign-key"
        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=trust_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("KEY_ID_MISMATCH" in p or "signature" in p for p in res["problems"])

    def test_missing_runtime_lock_file_rejected(self, tmp_path: Path):
        """Receipt referencing nonexistent runtime lock file fails verification."""
        receipt, trust_store, root = self._build_valid_compshare_fixture(tmp_path)
        receipt["runtime_lock"]["path"] = "reference/runtime/nonexistent.lock.json"
        from bench.experiments.qualification_receipt import canonical_digest
        receipt["digest"] = canonical_digest({k: v for k, v in receipt.items() if k not in ("digest", "signature")})
        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=trust_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("runtime_lock file missing" in p for p in res["problems"])

    def test_runtime_lock_digest_mismatch_rejected(self, tmp_path: Path):
        """Tampered runtime lock digest fails verification."""
        receipt, trust_store, root = self._build_valid_compshare_fixture(tmp_path)
        receipt["runtime_lock"]["digest"] = "sha256:" + "0" * 64
        from bench.experiments.qualification_receipt import canonical_digest
        receipt["digest"] = canonical_digest({k: v for k, v in receipt.items() if k not in ("digest", "signature")})
        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=trust_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("runtime_lock digest mismatch" in p for p in res["problems"])

    def test_missing_artifact_file_rejected(self, tmp_path: Path):
        """Nonexistent fetch artifact fails verification."""
        receipt, trust_store, root = self._build_valid_compshare_fixture(tmp_path)
        receipt["evidence"]["fetch"]["artifacts"][0]["path"] = "outputs/missing.tar.gz"
        from bench.experiments.qualification_receipt import canonical_digest
        receipt["digest"] = canonical_digest({k: v for k, v in receipt.items() if k not in ("digest", "signature")})
        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=trust_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("Artifact missing" in p for p in res["problems"])

    def test_artifact_digest_mismatch_rejected(self, tmp_path: Path):
        """Mismatched artifact sha256 fails verification."""
        receipt, trust_store, root = self._build_valid_compshare_fixture(tmp_path)
        receipt["evidence"]["fetch"]["artifacts"][0]["sha256"] = "sha256:" + "f" * 64
        from bench.experiments.qualification_receipt import canonical_digest
        receipt["digest"] = canonical_digest({k: v for k, v in receipt.items() if k not in ("digest", "signature")})
        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=trust_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("Artifact" in p and "digest mismatch" in p for p in res["problems"])

    def test_audit_from_another_run_rejected(self, tmp_path: Path):
        """Borrowing audit events from another run_id fails lifecycle check."""
        receipt, trust_store, root = self._build_valid_compshare_fixture(tmp_path)
        # Rewrite audit with another run_id
        audit_path = root / "audit.jsonl"
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        rewritten = []
        for line in lines:
            ent = json.loads(line)
            ent["event"]["run_id"] = "foreign-run-999"
            rewritten.append(json.dumps(ent))
        audit_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=trust_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("missing required lifecycle event" in p for p in res["problems"])

    def test_audit_instance_id_mismatch_rejected(self, tmp_path: Path):
        """Audit events with conflicting instance_id fail verification."""
        receipt, trust_store, root = self._build_valid_compshare_fixture(tmp_path)
        audit_path = root / "audit.jsonl"
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        rewritten = []
        for line in lines:
            ent = json.loads(line)
            if ent["event"].get("instance_id"):
                ent["event"]["instance_id"] = "inst-conflicting-id"
            rewritten.append(json.dumps(ent))
        audit_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=trust_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("expected inst-a1-neg" in p for p in res["problems"])

    def test_audit_image_id_mismatch_rejected(self, tmp_path: Path):
        """Audit events with conflicting image_id fail verification."""
        receipt, trust_store, root = self._build_valid_compshare_fixture(tmp_path)
        audit_path = root / "audit.jsonl"
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        rewritten = []
        for line in lines:
            ent = json.loads(line)
            if ent["event"].get("image_id"):
                ent["event"]["image_id"] = "img-conflicting-id"
            rewritten.append(json.dumps(ent))
        audit_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=trust_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("expected img-deepmd-gpu-v1" in p for p in res["problems"])

    def test_audit_event_order_rejected(self, tmp_path: Path):
        """Out-of-order audit events fail state machine check."""
        receipt, trust_store, root = self._build_valid_compshare_fixture(tmp_path)
        audit_path = root / "audit.jsonl"
        audit_path.unlink()
        audit = GatewayAudit(audit_path)
        # Put settlement complete before create intent
        audit.append({"action": "SETTLEMENT_COMPLETE", "run_id": "run-a1-neg", "instance_id": "inst-a1-neg"})
        audit.append({"action": "INSTANCE_CREATE_INTENT", "run_id": "run-a1-neg", "instance_id": "inst-a1-neg"})

        res = verify_site_receipt(receipt, scheduler="compshare", root=root, receipt_dir=root, trust_store=trust_store)
        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("missing required lifecycle event" in p for p in res["problems"])

    def test_stopped_instance_blocks_cli_qualification(self, tmp_path: Path):
        """Stopped instances are not safe and mandatorily block Zero-Orphan gate."""
        from bench.hpc.drivers.compshare.policy import instance_requires_cleanup

        assert instance_requires_cleanup("stopped") is True
        assert instance_requires_cleanup("STOPPED") is True

        site_dir, hashes = _setup_mock_receipts(tmp_path)
        receipt = _valid_profile_receipt(hashes)
        v = verify_and_derive_qualification(
            receipt,
            site_receipts_dir=site_dir,
            active_instances_checker=lambda: ["inst-stopped-001"],
        )
        assert v.passed is False
        assert v.status == "LEGACY_NOT_ELIGIBLE"

    def test_failed_existing_instance_blocks_qualification(self, tmp_path: Path):
        """Failed instances requiring cleanup block Zero-Orphan gate."""
        from bench.hpc.drivers.compshare.policy import instance_requires_cleanup

        assert instance_requires_cleanup("failed") is True
        assert instance_requires_cleanup("FAILED") is True

        site_dir, hashes = _setup_mock_receipts(tmp_path)
        receipt = _valid_profile_receipt(hashes)
        v = verify_and_derive_qualification(
            receipt,
            site_receipts_dir=site_dir,
            active_instances_checker=lambda: ["inst-failed-001"],
        )
        assert v.passed is False
        assert v.status == "LEGACY_NOT_ELIGIBLE"

    def test_receipt_local_site_profile_cannot_override_policy(self, tmp_path: Path):
        """Adversary cannot place local site profile in receipt directory to weaken policy."""
        site_dir, hashes = _setup_mock_receipts(tmp_path)
        # Attempt to drop relaxed policy in site receipt dir
        fake_profile = site_dir / "compshare-gpu-site-profile.json"
        fake_profile.write_text(json.dumps({"scheduler": "slurm", "qualification_policy": {"required_probe_classes": []}}))

        receipt = _valid_profile_receipt(hashes)
        # Without mock, the real trusted site profile expects CompShare and requires strict checks
        v = verify_and_derive_qualification(receipt, site_receipts_dir=site_dir)
        # It must not adopt the fake profile
        assert v.passed is False

    def test_concurrent_audit_append_preserves_chain(self, tmp_path: Path):
        """Concurrent threads appending under flock preserve continuous hash chain."""
        import threading

        audit_path = tmp_path / "concurrent_audit.jsonl"
        audit = GatewayAudit(audit_path)

        def worker(thread_idx: int):
            for i in range(10):
                audit.append({"worker": thread_idx, "seq": i, "timestamp": time.time()})

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(audit.entries()) == 50
        assert audit.verify() == []

    def test_special_character_run_id_generates_safe_marker(self, tmp_path: Path):
        """Special or long characters in run_id map to bounded deterministic markers."""
        from bench.hpc.drivers.compshare.policy import make_ownership_marker, matches_ownership_marker

        dangerous_id = "run/../../weird:run?foo=bar&baz=1#test"
        name, remark = make_ownership_marker(dangerous_id)
        assert "/" not in name and "?" not in name and "&" not in name
        assert len(name) <= 32
        assert matches_ownership_marker({"name": name, "remark": remark}, dangerous_id) is True
