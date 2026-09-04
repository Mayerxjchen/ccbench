"""Formal Layer-2 ComputeProfile qualification v2 contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dftworld_bench.experiments import compute_profile_qualification as cpq
from dftworld_bench.hpc.audit import GatewayAudit
from dftworld_bench.hpc.site_profile import HpcSiteProfile
from dftworld_bench.hpc.trust_store import QualificationTrustStore, TrustKey


def _profiles() -> dict[str, HpcSiteProfile]:
    root = Path(__file__).resolve().parents[2]
    cpu = HpcSiteProfile.from_dict(
        json.loads((root / "examples/hpc/generic-slurm-site-profile.json").read_text())
        | {"site_id": "ikkem-cpu", "qualification_policy": {"required_probe_classes": ["cpu"]}}
    )
    gpu = HpcSiteProfile.from_dict(
        json.loads((root / "examples/hpc/compshare-gpu-site-profile.json").read_text())
    )
    return {cpu.site_id: cpu, gpu.site_id: gpu}


def _fixture(
    tmp_path: Path,
) -> tuple[dict, QualificationTrustStore, dict[str, HpcSiteProfile], Path]:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    audit_path = evidence / "audit.jsonl"
    audit = GatewayAudit(audit_path)
    audit.append({"kind": "qualification_probe", "run_id": "run-v2"}, durable=True)
    artifact = evidence / "probe.out"
    artifact.write_bytes(b"formal evidence\n")
    records = [
        cpq.make_evidence_file_record(evidence, "audit.jsonl", role="audit_log"),
        cpq.make_evidence_file_record(evidence, "probe.out", role="artifact"),
    ]
    private, public = cpq.generate_ed25519_key_pair()
    store = QualificationTrustStore(
        {
            cpq.COMPUTE_PROFILE_SIGNING_KEY_ID: TrustKey(
                key_id=cpq.COMPUTE_PROFILE_SIGNING_KEY_ID,
                algorithm="ed25519",
                public_key_hex=public,
                status="ACTIVE",
                purpose=cpq.COMPUTE_PROFILE_SIGNING_PURPOSE,
            )
        }
    )
    profiles = _profiles()
    site_dir = tmp_path / "site_receipts"
    site_dir.mkdir()
    site_receipts: dict[str, str] = {}
    for site_id in profiles:
        data = {"site_id": site_id, "verdict": "PASS"}
        path = site_dir / f"{site_id}.receipt.json"
        path.write_text(json.dumps(data, sort_keys=True))
        site_receipts[site_id] = cpq.compute_receipt_digest(data)

    receipt = cpq.build_compute_profile_qualification_receipt_v2(
        compute_profile_id="compute-v2",
        compute_profile_digest="sha256:" + "a" * 64,
        routes={"cpu": "ikkem-cpu", "gpu": "compshare-gpu"},
        site_receipts=site_receipts,
        cloud_recycling_evidence={
            "stock_checked": True,
            "instance_id": "instance-v2",
            "image_id": "image-v2",
            "gpu_type": "4090",
            "gpu_vram_gb": 24,
            "task_executed": True,
            "fetch_verified": True,
            "credentials_isolated": True,
            "settlement_terminated": True,
            "active_instances_count": 0,
            "orphan_instances_count": 0,
        },
        evidence_root=str(evidence),
        evidence_files=records,
        audit_log="audit.jsonl",
        audit_tail_digest=audit.tail_digest(),
        private_key_hex=private,
    )
    return receipt, store, profiles, site_dir


def _verify(
    receipt: dict,
    store: QualificationTrustStore | None,
    profiles: dict[str, HpcSiteProfile] | None,
    site_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    # Site receipt Layer-1 verification is independently covered.  This test
    # isolates the Layer-2 v2 materialization/trust contract without replacing
    # the v2 verifier itself.
    monkeypatch.setattr(
        cpq,
        "verify_site_receipt",
        lambda receipt, **kwargs: {
            "problems": [],
            "derived": {"qualification_status": "PASS"},
        },
    )
    return cpq.verify_and_derive_qualification(
        receipt,
        site_receipts_dir=site_dir,
        receipt_dir=tmp_path,
        trust_store=store,
        trusted_site_profiles=profiles,
    )


def test_v2_builder_and_formal_verifier_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    receipt, store, profiles, site_dir = _fixture(tmp_path)
    verdict = _verify(receipt, store, profiles, site_dir, tmp_path, monkeypatch)
    assert verdict.passed is True
    assert verdict.status == "PASS"
    assert receipt["signature"]["purpose"] == cpq.COMPUTE_PROFILE_SIGNING_PURPOSE
    assert all(isinstance(item, dict) for item in receipt["evidence_files"])


def test_v1_is_readable_but_not_eligible(tmp_path: Path):
    doc = {
        "kind": "hpc-compute-profile-qualification/v1",
        "schema_id": "https://mlip-bench.example/schemas/compute-profile-qualification-receipt.schema.json",
        "compute_profile_id": "legacy",
        "compute_profile_digest": "a" * 64,
        "routes": {"cpu": "cpu", "gpu": "gpu"},
        "site_receipts": {"cpu": "sha256:" + "1" * 64, "gpu": "sha256:" + "2" * 64},
        "cloud_recycling_evidence": {
            "stock_checked": True,
            "instance_id": "i",
            "image_id": "img",
            "gpu_type": "4090",
            "task_executed": True,
            "fetch_verified": True,
            "credentials_isolated": True,
            "settlement_terminated": True,
            "active_instances_count": 0,
            "orphan_instances_count": 0,
        },
    }
    doc["digest"] = cpq.compute_receipt_digest(doc)
    verdict = cpq.verify_and_derive_qualification(doc)
    assert verdict.passed is False
    assert verdict.status == "LEGACY_NOT_ELIGIBLE"
    assert any("LEGACY_NOT_ELIGIBLE" in error for error in verdict.errors)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_store",
        "bad_tail",
        "bad_hash",
        "missing_file",
        "symlink",
        "outside_root",
        "active_count",
        "false_evidence",
    ],
)
def test_v2_materialization_and_trust_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
):
    receipt, store, profiles, site_dir = _fixture(tmp_path)
    if mutation == "missing_store":
        store = None
    elif mutation == "bad_tail":
        receipt["audit_tail_digest"] = "0" * 64
    elif mutation == "bad_hash":
        receipt["evidence_files"][1]["sha256"] = "sha256:" + "0" * 64
    elif mutation == "missing_file":
        (Path(receipt["evidence_root"]) / "probe.out").unlink()
    elif mutation == "symlink":
        target = Path(receipt["evidence_root"]) / "probe.out"
        target.unlink()
        target.symlink_to(Path(receipt["evidence_root"]) / "audit.jsonl")
    elif mutation == "outside_root":
        receipt["evidence_root"] = str(tmp_path.parent)
    elif mutation == "active_count":
        receipt["cloud_recycling_evidence"]["active_instances_count"] = 1
    elif mutation == "false_evidence":
        receipt["cloud_recycling_evidence"]["credentials_isolated"] = False
    # Mutating a signed body is intentional: the verifier must report both
    # signature/digest failure and the materialization failure where relevant.
    receipt["digest"] = cpq.compute_receipt_digest(receipt)
    verdict = _verify(receipt, store, profiles, site_dir, tmp_path, monkeypatch)
    assert verdict.passed is False
    assert verdict.status == "INVALID"
    if mutation == "missing_store":
        assert any("trust_store" in error for error in verdict.errors)
    elif mutation == "bad_tail":
        assert any("audit_tail_digest mismatch" in error for error in verdict.errors)
    elif mutation == "bad_hash":
        assert any("sha256 mismatch" in error for error in verdict.errors)
    elif mutation == "missing_file":
        assert any("cannot be read" in error or "regular" in error for error in verdict.errors)
    elif mutation == "symlink":
        assert any("symlink" in error.lower() or "sha256 mismatch" in error for error in verdict.errors)
    elif mutation == "outside_root":
        assert any("escapes trusted receipt directory" in error for error in verdict.errors)
    elif mutation == "active_count":
        assert any("cloud instances remain active/billing" in error for error in verdict.errors)
    else:
        assert any("Evidence failure" in error for error in verdict.errors)


def test_v2_builder_requires_all_formal_arguments():
    with pytest.raises(cpq.ComputeProfileQualificationError, match="requires evidence_root"):
        cpq.build_compute_profile_qualification_receipt_v2(
            compute_profile_id="p",
            compute_profile_digest="a" * 64,
            routes={"cpu": "c", "gpu": "g"},
            site_receipts={"c": "sha256:" + "1" * 64, "g": "sha256:" + "2" * 64},
            cloud_recycling_evidence={},
            evidence_root="",
            evidence_files=[],
            audit_log="audit.jsonl",
            audit_tail_digest="0" * 64,
            private_key_hex="",
        )


def test_compute_profile_signature_purpose_is_domain_bound():
    private, public = cpq.generate_ed25519_key_pair()
    body = {"kind": "purpose-bound-test", "value": "v2"}
    sig = cpq.sign_receipt(body, private, key_id="compute-profile-v2", purpose="domain-a")
    signed = {**body, "signature": {**sig, "purpose": "domain-a"}}

    assert cpq.verify_receipt_signature(
        signed,
        expected_public_key_hex=public,
        expected_purpose="domain-a",
    ) is True

    moved = {**signed, "signature": {**signed["signature"], "purpose": "domain-b"}}
    assert cpq.verify_receipt_signature(
        moved,
        expected_public_key_hex=public,
        expected_purpose="domain-b",
    ) is False


def test_cli_requires_explicit_trust_store_and_site_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """The public compute-qualify entry point may not use repository defaults."""
    from dftworld_bench import cli

    profile_path = tmp_path / "profile.json"
    profile_path.write_text(
        (Path(__file__).resolve().parents[2] / "examples/hpc/maintainer-hybrid-compute-profile.json")
        .read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{}", encoding="utf-8")
    site_dir = tmp_path / "site_receipts"
    site_dir.mkdir()

    code = cli.main(
        [
            "compute",
            "qualify",
            "--profile",
            str(profile_path),
            "--receipt",
            str(receipt_path),
            "--site-receipts-dir",
            str(site_dir),
        ]
    )
    assert code == 2
    assert "--trust-store" in capsys.readouterr().err

    trust_store = tmp_path / "trust.toml"
    trust_store.write_text("[keys]\n", encoding="utf-8")
    code = cli.main(
        [
            "compute",
            "qualify",
            "--profile",
            str(profile_path),
            "--receipt",
            str(receipt_path),
            "--site-receipts-dir",
            str(site_dir),
            "--trust-store",
            str(trust_store),
        ]
    )
    assert code == 2
    assert "--site-profile-registry" in capsys.readouterr().err
