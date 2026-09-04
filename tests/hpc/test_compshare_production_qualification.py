"""Tests for CompShare production runtime locks and TrustedRuntimeCatalog promotion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dftworld_bench.hpc.runtime_catalog import TrustedRuntimeCatalog
from dftworld_bench.hpc.runtime_resolution import RuntimeStatus
from dftworld_bench.hpc.site_profile import HpcSiteProfile
from dftworld_bench.hpc.trust_store import QualificationTrustStore

ROOT = Path(__file__).resolve().parents[2]
PROD_LOCK_DIR = ROOT / "reference" / "production-runtime"
DEFAULT_EVIDENCE_DIR = Path.home() / ".config" / "mlffbench" / "evidence" / "gate_c" / "20260904T174600Z"
SITE_PROFILE_PATH = Path.home() / ".config" / "mlffbench" / "sites" / "compshare-gpu-production.json"
TRUST_STORE_PATH = Path.home() / ".config" / "mlffbench" / "trust" / "qualification-trust.toml"


def test_production_runtime_locks_exist_and_conform():
    """Verify production runtime locks exist and are well-formed."""
    assert (PROD_LOCK_DIR / "deepmd-runtime.lock.json").is_file()
    assert (PROD_LOCK_DIR / "matclaw-cips-runtime.lock.json").is_file()

    deepmd = json.loads((PROD_LOCK_DIR / "deepmd-runtime.lock.json").read_text(encoding="utf-8"))
    assert deepmd["artifact"]["image_id"] == "compshareImage-1uw6sd44931i"
    assert deepmd["qualification"]["status"] == "BUILT_NOT_QUALIFIED"
    assert deepmd["qualification"]["receipt_digest"].startswith("sha256:")

    matclaw = json.loads((PROD_LOCK_DIR / "matclaw-cips-runtime.lock.json").read_text(encoding="utf-8"))
    assert matclaw["artifact"]["image_id"] == "compshareImage-1uw6sd44931i"
    assert matclaw["qualification"]["status"] == "BUILT_NOT_QUALIFIED"
    assert matclaw["qualification"]["receipt_digest"].startswith("sha256:")


@pytest.mark.skipif(
    not DEFAULT_EVIDENCE_DIR.is_dir() or not SITE_PROFILE_PATH.is_file(),
    reason="Production evidence and site profile required for live promotion check",
)
def test_production_catalog_promotes_both_capabilities():
    """Verify TrustedRuntimeCatalog verifies formal receipts and promotes both runtimes."""
    site_doc = json.loads(SITE_PROFILE_PATH.read_text(encoding="utf-8"))
    site_obj = HpcSiteProfile.from_dict(site_doc)
    trust_store = QualificationTrustStore.from_file(TRUST_STORE_PATH)

    catalog = TrustedRuntimeCatalog.load(
        lock_dir=PROD_LOCK_DIR,
        qualification_root=DEFAULT_EVIDENCE_DIR,
        trust_store=trust_store,
        trusted_site_profiles={site_obj.site_id: site_obj},
        repo_root=ROOT,
    )

    assert not catalog.errors
    qualified = catalog.qualified_capabilities()
    assert "deepmd" in qualified
    assert "matclaw-cips" in qualified

    resolver = catalog.to_resolver()
    deepmd_resolved = resolver.resolve("deepmd")
    assert deepmd_resolved.status == RuntimeStatus.QUALIFIED
    assert deepmd_resolved.qualification_verified is True
    assert deepmd_resolved.image_id == "compshareImage-1uw6sd44931i"

    matclaw_resolved = resolver.resolve("matclaw-cips")
    assert matclaw_resolved.status == RuntimeStatus.QUALIFIED
    assert matclaw_resolved.qualification_verified is True
    assert matclaw_resolved.image_id == "compshareImage-1uw6sd44931i"
