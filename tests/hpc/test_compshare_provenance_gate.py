"""Regression and negative tests for CompShare provenance mechanical bindings.

Validates the triple-equality invariants:
1. receipt image_id == lifecycle/job image_id == runtime-lock image_id
2. receipt recipe_digest == runtime-lock recipe_digest == canonical recipe.lock digest
3. receipt asset SHA == recipe.lock asset SHA == in-container measured SHA
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from dftworld_bench.experiments.compute_profile_qualification import (
    build_compshare_site_qualification_receipt,
    verify_site_receipt,
)
from dftworld_bench.hpc.site_profile import HpcSiteProfile
from dftworld_bench.hpc.trust_store import QualificationTrustStore

ROOT = Path(__file__).resolve().parents[2]
PROD_LOCK_DIR = ROOT / "runtimes" / "locks"
SITE_PROFILE_PATH = Path.home() / ".config" / "mlffbench" / "sites" / "compshare-gpu-production.json"
TRUST_STORE_PATH = Path.home() / ".config" / "mlffbench" / "trust" / "qualification-trust.toml"
KEY_PATH = Path.home() / ".config" / "mlffbench" / "keys" / "compshare-site-v1.priv"


@pytest.fixture
def site_context():
    if not SITE_PROFILE_PATH.is_file() or not TRUST_STORE_PATH.is_file() or not KEY_PATH.is_file():
        pytest.skip("Site profile, trust store, or signing key missing")

    site_doc = json.loads(SITE_PROFILE_PATH.read_text(encoding="utf-8"))
    site_obj = HpcSiteProfile.from_dict(site_doc)
    trust_store = QualificationTrustStore.from_file(TRUST_STORE_PATH)
    raw_key = KEY_PATH.read_bytes()
    key_hex = raw_key.hex() if len(raw_key) == 32 else raw_key.decode("utf-8").strip()

    return {
        "site_doc": site_doc,
        "site_obj": site_obj,
        "trust_store": trust_store,
        "key_hex": key_hex,
    }


def hashlib_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_mock_receipt_dir(
    tmp_path: Path,
    *,
    image_id: str,
    asset_sha: str = "b50c7318305db3deab3f033190210239dd275d73b0c0f06e02cd8a51463dd638",
    include_asset: bool = True,
) -> Path:
    receipt_dir = tmp_path / "jax"
    receipt_dir.mkdir(parents=True, exist_ok=True)

    canary_report = {
        "gpu_visible": True,
        "parity_ok": True,
        "gpu_name": "RTX 4090",
    }
    if include_asset:
        canary_report["assets"] = {
            "jax_md-0.2.29.tar.gz": asset_sha,
        }
    (receipt_dir / "canary_report.json").write_text(json.dumps(canary_report), encoding="utf-8")

    (receipt_dir / "settlement_report.json").write_text(
        json.dumps({
            "run_id": "mock-run",
            "stop_confirmed": True,
            "delete_confirmed": True,
            "orphan_count": 0,
        }),
        encoding="utf-8",
    )

    from dftworld_bench.hpc.audit import GatewayAudit
    audit_file = receipt_dir / "audit_events.jsonl"
    ledger = GatewayAudit(audit_file)
    ledger.append({"kind": "INSTANCE_CREATE_INTENT", "run_id": "mock-run", "image_id": image_id})
    ledger.append({"kind": "INSTANCE_CREATE_ACCEPTED", "run_id": "mock-run", "instance_id": "mock-inst"})
    ledger.append({"kind": "INSTANCE_READY", "run_id": "mock-run", "instance_id": "mock-inst"})
    ledger.append({"kind": "INSTANCE_STOP_ACCEPTED", "run_id": "mock-run", "instance_id": "mock-inst"})
    ledger.append({"kind": "INSTANCE_DELETE_ACCEPTED", "run_id": "mock-run", "instance_id": "mock-inst"})
    ledger.append({"kind": "INSTANCE_DELETE_CONFIRMED", "run_id": "mock-run", "instance_id": "mock-inst"})
    ledger.append({"kind": "ZERO_ORPHAN_QUERY", "run_id": "mock-run", "active_count": 0})
    ledger.append({"kind": "SETTLEMENT_COMPLETE", "run_id": "mock-run", "settlement_digest": "sha256:mock"})

    return receipt_dir


def test_old_image_id_with_new_recipe_fails(site_context):
    """Verifies that pairing superseded image compshareImage-1uwv0ijzwej6 with new recipe fails closed."""
    old_image_id = "compshareImage-1uwv0ijzwej6"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        receipt_dir = _create_mock_receipt_dir(tmp_path, image_id=old_image_id)

        lock_doc = json.loads((PROD_LOCK_DIR / "jax-runtime.lock.json").read_text(encoding="utf-8"))
        lock_doc["artifact"]["image_id"] = old_image_id
        lock_doc["provenance"]["recipe_digest"] = "sha256:766bacfeb5f1c306181302d8b3c5cb1b6f4f192bee2e4cdfb1427a9d95c7118f"

        recipe_rel = lock_doc["provenance"]["recipe_path"]
        (tmp_path / recipe_rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / recipe_rel).write_text((ROOT / recipe_rel).read_text(encoding="utf-8"), encoding="utf-8")

        mock_lock_path = tmp_path / "jax-runtime.lock.json"
        mock_lock_path.write_text(json.dumps(lock_doc), encoding="utf-8")

        from dftworld_bench.hpc.runtime_resolution import canonical_lock_digest
        lock_digest = canonical_lock_digest(lock_doc)

        from dftworld_bench.hpc.audit import GatewayAudit
        ledger = GatewayAudit(receipt_dir / "audit_events.jsonl")
        tail_digest = ledger.tail_digest()

        evidence = {
            "credential_isolation": {"verified": True},
            "instance_lifecycle": {
                "instance_id": "mock-inst",
                "image_id": old_image_id,
                "stop_confirmed": True,
                "delete_confirmed": True,
            },
            "jobs": [
                {"probe_class": "gpu", "job_id": "job-01", "image_id": old_image_id, "accounting": {"state": "COMPLETED", "exit_code": 0}}
            ],
            "fetch": {"artifacts": [{"path": "canary_report.json", "sha256": f"sha256:{hashlib_sha(receipt_dir / 'canary_report.json')}", "size_bytes": (receipt_dir / 'canary_report.json').stat().st_size}]},
            "settlement": {"report_path": "settlement_report.json", "digest": f"sha256:{hashlib_sha(receipt_dir / 'settlement_report.json')}", "terminated": True},
            "orphan_check": {"method": "mock", "active_total": 0},
        }

        receipt = build_compshare_site_qualification_receipt(
            run_id="mock-run",
            site_profile_id=site_context["site_obj"].site_id,
            site_profile_digest=site_context["site_obj"].digest,
            source_commit="0a6cafa9808d47788f9f2c55e040ffbfef932b79",
            code_identity={"dftworld_bench/hpc/dispatcher.py": "sha256:dc508211ba3cd82da220101f583712e93f5572d8cdc1f195c3b793c263c93269"},
            runtime_lock={
                "path": "jax-runtime.lock.json",
                "digest": lock_digest,
                "image_id": old_image_id,
                "recipe_digest": lock_doc["provenance"]["recipe_digest"],
            },
            evidence=evidence,
            audit_log="audit_events.jsonl",
            audit_tail_digest=tail_digest,
            private_key_hex=site_context["key_hex"],
        )

        res = verify_site_receipt(
            receipt,
            root=tmp_path,
            receipt_dir=receipt_dir,
            scheduler="compshare",
            trusted_site_profile=site_context["site_doc"],
            trust_store=site_context["trust_store"],
        )

        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("superseded image" in p for p in res["problems"])


def test_mismatched_asset_sha_fails(site_context):
    """Verifies that an altered in-container asset SHA probe fails verification."""
    clean_image_id = "compshareImage-newclean123"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bad_asset_sha = "6fe8f038aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        receipt_dir = _create_mock_receipt_dir(tmp_path, image_id=clean_image_id, asset_sha=bad_asset_sha)

        lock_doc = json.loads((PROD_LOCK_DIR / "jax-runtime.lock.json").read_text(encoding="utf-8"))
        lock_doc["artifact"]["image_id"] = clean_image_id
        lock_doc["provenance"]["recipe_digest"] = "sha256:766bacfeb5f1c306181302d8b3c5cb1b6f4f192bee2e4cdfb1427a9d95c7118f"

        recipe_rel = lock_doc["provenance"]["recipe_path"]
        (tmp_path / recipe_rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / recipe_rel).write_text((ROOT / recipe_rel).read_text(encoding="utf-8"), encoding="utf-8")

        mock_lock_path = tmp_path / "jax-runtime.lock.json"
        mock_lock_path.write_text(json.dumps(lock_doc), encoding="utf-8")

        from dftworld_bench.hpc.runtime_resolution import canonical_lock_digest
        lock_digest = canonical_lock_digest(lock_doc)

        from dftworld_bench.hpc.audit import GatewayAudit
        ledger = GatewayAudit(receipt_dir / "audit_events.jsonl")
        tail_digest = ledger.tail_digest()

        evidence = {
            "credential_isolation": {"verified": True},
            "instance_lifecycle": {
                "instance_id": "mock-inst",
                "image_id": clean_image_id,
                "stop_confirmed": True,
                "delete_confirmed": True,
            },
            "jobs": [
                {"probe_class": "gpu", "job_id": "job-01", "image_id": clean_image_id, "accounting": {"state": "COMPLETED", "exit_code": 0}}
            ],
            "fetch": {"artifacts": [{"path": "canary_report.json", "sha256": f"sha256:{hashlib_sha(receipt_dir / 'canary_report.json')}", "size_bytes": (receipt_dir / 'canary_report.json').stat().st_size}]},
            "settlement": {"report_path": "settlement_report.json", "digest": f"sha256:{hashlib_sha(receipt_dir / 'settlement_report.json')}", "terminated": True},
            "orphan_check": {"method": "mock", "active_total": 0},
        }

        receipt = build_compshare_site_qualification_receipt(
            run_id="mock-run",
            site_profile_id=site_context["site_obj"].site_id,
            site_profile_digest=site_context["site_obj"].digest,
            source_commit="0a6cafa9808d47788f9f2c55e040ffbfef932b79",
            code_identity={"dftworld_bench/hpc/dispatcher.py": "sha256:dc508211ba3cd82da220101f583712e93f5572d8cdc1f195c3b793c263c93269"},
            runtime_lock={
                "path": "jax-runtime.lock.json",
                "digest": lock_digest,
                "image_id": clean_image_id,
                "recipe_digest": lock_doc["provenance"]["recipe_digest"],
            },
            evidence=evidence,
            audit_log="audit_events.jsonl",
            audit_tail_digest=tail_digest,
            private_key_hex=site_context["key_hex"],
        )

        res = verify_site_receipt(
            receipt,
            root=tmp_path,
            receipt_dir=receipt_dir,
            scheduler="compshare",
            trusted_site_profile=site_context["site_doc"],
            trust_store=site_context["trust_store"],
        )

        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("Asset jax_md-0.2.29.tar.gz in-image measured SHA" in p for p in res["problems"])


def test_tampered_recipe_digest_fails(site_context):
    """Verifies that tampering with recipe_digest fails closed."""
    clean_image_id = "compshareImage-newclean123"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        receipt_dir = _create_mock_receipt_dir(tmp_path, image_id=clean_image_id)

        lock_doc = json.loads((PROD_LOCK_DIR / "jax-runtime.lock.json").read_text(encoding="utf-8"))
        lock_doc["artifact"]["image_id"] = clean_image_id
        # Tampered recipe digest
        lock_doc["provenance"]["recipe_digest"] = "sha256:0000000000000000000000000000000000000000000000000000000000000000"

        recipe_rel = lock_doc["provenance"]["recipe_path"]
        (tmp_path / recipe_rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / recipe_rel).write_text((ROOT / recipe_rel).read_text(encoding="utf-8"), encoding="utf-8")

        mock_lock_path = tmp_path / "jax-runtime.lock.json"
        mock_lock_path.write_text(json.dumps(lock_doc), encoding="utf-8")

        from dftworld_bench.hpc.runtime_resolution import canonical_lock_digest
        lock_digest = canonical_lock_digest(lock_doc)

        from dftworld_bench.hpc.audit import GatewayAudit
        ledger = GatewayAudit(receipt_dir / "audit_events.jsonl")
        tail_digest = ledger.tail_digest()

        evidence = {
            "credential_isolation": {"verified": True},
            "instance_lifecycle": {
                "instance_id": "mock-inst",
                "image_id": clean_image_id,
                "stop_confirmed": True,
                "delete_confirmed": True,
            },
            "jobs": [
                {"probe_class": "gpu", "job_id": "job-01", "image_id": clean_image_id, "accounting": {"state": "COMPLETED", "exit_code": 0}}
            ],
            "fetch": {"artifacts": [{"path": "canary_report.json", "sha256": f"sha256:{hashlib_sha(receipt_dir / 'canary_report.json')}", "size_bytes": (receipt_dir / 'canary_report.json').stat().st_size}]},
            "settlement": {"report_path": "settlement_report.json", "digest": f"sha256:{hashlib_sha(receipt_dir / 'settlement_report.json')}", "terminated": True},
            "orphan_check": {"method": "mock", "active_total": 0},
        }

        receipt = build_compshare_site_qualification_receipt(
            run_id="mock-run",
            site_profile_id=site_context["site_obj"].site_id,
            site_profile_digest=site_context["site_obj"].digest,
            source_commit="0a6cafa9808d47788f9f2c55e040ffbfef932b79",
            code_identity={"dftworld_bench/hpc/dispatcher.py": "sha256:dc508211ba3cd82da220101f583712e93f5572d8cdc1f195c3b793c263c93269"},
            runtime_lock={
                "path": "jax-runtime.lock.json",
                "digest": lock_digest,
                "image_id": clean_image_id,
                "recipe_digest": lock_doc["provenance"]["recipe_digest"],
            },
            evidence=evidence,
            audit_log="audit_events.jsonl",
            audit_tail_digest=tail_digest,
            private_key_hex=site_context["key_hex"],
        )

        res = verify_site_receipt(
            receipt,
            root=tmp_path,
            receipt_dir=receipt_dir,
            scheduler="compshare",
            trusted_site_profile=site_context["site_doc"],
            trust_store=site_context["trust_store"],
        )

        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("recipe_digest mismatch" in p for p in res["problems"])


def test_missing_recipe_digest_in_receipt_fails(site_context):
    """Verifies that a receipt omitting runtime_lock.recipe_digest fails closed."""
    clean_image_id = "compshareImage-1uyaneriamfz"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        receipt_dir = _create_mock_receipt_dir(tmp_path, image_id=clean_image_id)

        lock_doc = json.loads((PROD_LOCK_DIR / "jax-runtime.lock.json").read_text(encoding="utf-8"))
        lock_doc["artifact"]["image_id"] = clean_image_id

        recipe_rel = lock_doc["provenance"]["recipe_path"]
        (tmp_path / recipe_rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / recipe_rel).write_text((ROOT / recipe_rel).read_text(encoding="utf-8"), encoding="utf-8")

        mock_lock_path = tmp_path / "jax-runtime.lock.json"
        mock_lock_path.write_text(json.dumps(lock_doc), encoding="utf-8")

        from dftworld_bench.hpc.runtime_resolution import canonical_lock_digest
        lock_digest = canonical_lock_digest(lock_doc)

        from dftworld_bench.hpc.audit import GatewayAudit
        ledger = GatewayAudit(receipt_dir / "audit_events.jsonl")
        tail_digest = ledger.tail_digest()

        evidence = {
            "credential_isolation": {"verified": True},
            "instance_lifecycle": {
                "instance_id": "mock-inst",
                "image_id": clean_image_id,
                "stop_confirmed": True,
                "delete_confirmed": True,
            },
            "jobs": [
                {"probe_class": "gpu", "job_id": "job-01", "image_id": clean_image_id, "accounting": {"state": "COMPLETED", "exit_code": 0}}
            ],
            "fetch": {"artifacts": [{"path": "canary_report.json", "sha256": f"sha256:{hashlib_sha(receipt_dir / 'canary_report.json')}", "size_bytes": (receipt_dir / 'canary_report.json').stat().st_size}]},
            "settlement": {"report_path": "settlement_report.json", "digest": f"sha256:{hashlib_sha(receipt_dir / 'settlement_report.json')}", "terminated": True},
            "orphan_check": {"method": "mock", "active_total": 0},
        }

        receipt = build_compshare_site_qualification_receipt(
            run_id="mock-missing-rd",
            site_profile_id=site_context["site_obj"].site_id,
            site_profile_digest=site_context["site_obj"].digest,
            source_commit="0a6cafa9808d47788f9f2c55e040ffbfef932b79",
            code_identity={"dftworld_bench/hpc/dispatcher.py": "sha256:dc508211ba3cd82da220101f583712e93f5572d8cdc1f195c3b793c263c93269"},
            runtime_lock={
                "path": "jax-runtime.lock.json",
                "digest": lock_digest,
                "image_id": clean_image_id,
            },
            evidence=evidence,
            audit_log="audit_events.jsonl",
            audit_tail_digest=tail_digest,
            private_key_hex=site_context["key_hex"],
        )

        res = verify_site_receipt(
            receipt,
            root=tmp_path,
            receipt_dir=receipt_dir,
            scheduler="compshare",
            trusted_site_profile=site_context["site_doc"],
            trust_store=site_context["trust_store"],
        )

        assert res["derived"]["qualification_status"] == "INVALID"
        assert any("recipe_digest" in p for p in res["problems"])
