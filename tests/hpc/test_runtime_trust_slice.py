"""Focused negative contracts for the C1-C4 runtime trust slice."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.hpc.gateway import Gateway, GatewayError
from bench.hpc.runtime_catalog import TrustedRuntimeCatalog
from bench.hpc.runtime_resolution import (
    RuntimeResolutionError,
    RuntimeResolver,
    RuntimeStatus,
)
from bench.hpc.site_profile import HpcSiteProfile, TrustedSiteProfileRegistry


def _profile() -> HpcSiteProfile:
    return HpcSiteProfile.from_dict(
        {
            "schema_version": 1,
            "site_id": "site-a",
            "scheduler": "compshare",
            "connection": {
                "credential_profile_id": "profile-key",
                "target_binding": "api.example",
                "remote_user": "bench",
                "remote_root_policy": "/workspace/{run_id}",
            },
            "account": "bench",
            "queues": {
                "gpu": {
                    "partition": "gpu",
                    "qos": "normal",
                    "max_cpus": 16,
                    "max_memory_gb": 64,
                    "max_gpus": 1,
                    "max_walltime_minutes": 60,
                }
            },
            "runtime_policy": {
                "requires_apptainer": False,
                "budget_policy": {
                    "max_budget_cny": 100.0,
                    "max_instance_hours": 1.0,
                    "max_instances": 1,
                },
            },
            "qualification_policy": {
                "required_probe_classes": ["gpu"],
                "signing_key_id": "site-key",
            },
        }
    )


def test_bare_sif_lock_is_not_a_qualification_attestation(tmp_path: Path):
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    (lock_dir / "cp2k-runtime.lock.json").write_text(
        json.dumps(
            {
                "schema": "dispatcher-cp2k-runtime-lock/v1",
                "runtime": {
                    "sif_path_remote": "/site/cp2k.sif",
                    "sif_sha256": "a" * 64,
                },
            }
        ),
        encoding="utf-8",
    )
    resolver = RuntimeResolver.from_lock_dir(lock_dir)
    assert resolver.qualified_capabilities() == []
    assert resolver.get("cp2k").status == RuntimeStatus.BUILT_NOT_QUALIFIED
    with pytest.raises(RuntimeResolutionError, match="BUILT_NOT_QUALIFIED"):
        resolver.resolve("cp2k")


def test_qualification_policy_is_frozen_and_serialized():
    profile = _profile()
    assert profile.qualification_policy["required_probe_classes"] == ["gpu"]
    assert profile.to_public_dict()["qualification_policy"] == profile.qualification_policy
    assert profile.to_trusted_dict()["qualification_policy"] == profile.qualification_policy

    altered = dict(profile.to_trusted_dict())
    altered.pop("digest", None)
    altered["qualification_policy"] = {
        "required_probe_classes": ["gpu"],
        "signing_key_id": "other-key",
    }
    changed = HpcSiteProfile.from_dict(altered)
    assert changed.digest != profile.digest
    TrustedSiteProfileRegistry({profile.site_id: profile}).require(profile.site_id)


def test_catalog_has_no_provider_receipt_fallback(tmp_path: Path):
    lock_dir = tmp_path / "reference" / "runtime"
    lock_dir.mkdir(parents=True)
    (lock_dir / "deepmd-runtime.lock.json").write_text(
        json.dumps(
            {
                "schema": "dispatcher-compshare-runtime-lock/v2",
                "site_profile_id": "site-a",
                "artifact": {"image_id": "img-1"},
                "qualification": {
                    "status": "PASS",
                    "receipt_path": "../compshare-site-receipt.json",
                    "receipt_digest": "sha256:" + "a" * 64,
                },
            }
        ),
        encoding="utf-8",
    )
    # This is the old provider-name fallback location.  It must not be used.
    (tmp_path / "compshare-site-receipt.json").write_text("{}", encoding="utf-8")
    catalog = TrustedRuntimeCatalog(
        lock_dir,
        qualification_root=tmp_path,
        trusted_site_profiles={"site-a": _profile()},
    )
    assert catalog.qualified_capabilities() == []
    assert catalog.get("deepmd").status == RuntimeStatus.BUILT_NOT_QUALIFIED
    assert "deepmd" in catalog.errors


def test_catalog_promotes_only_exactly_bound_receipt(tmp_path: Path, monkeypatch):
    from bench.experiments.qualification_receipt import canonical_digest
    from bench.hpc.runtime_resolution import canonical_lock_digest

    lock_dir = tmp_path / "reference" / "runtime"
    lock_dir.mkdir(parents=True)
    lock_doc = {
        "schema": "dispatcher-compshare-runtime-lock/v2",
        "site_profile_id": "site-a",
        "artifact": {"image_id": "img-1"},
        "qualification": {
            "status": "PASS",
            "receipt_path": "receipts/site-a.json",
        },
    }
    lock_path = lock_dir / "deepmd-runtime.lock.json"
    lock_path.write_text(json.dumps(lock_doc), encoding="utf-8")
    lock_digest = canonical_lock_digest(lock_doc)
    receipt = {
        "kind": "compshare-site-qualification/v1",
        "site_profile_id": "site-a",
        "site_profile_digest": _profile().digest,
        "runtime_lock": {
            "path": "reference/runtime/deepmd-runtime.lock.json",
            "digest": lock_digest,
            "image_id": "img-1",
        },
    }
    receipt["digest"] = canonical_digest(receipt)
    receipt_path = tmp_path / "receipts" / "site-a.json"
    receipt_path.parent.mkdir()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    lock_doc["qualification"]["receipt_digest"] = receipt["digest"]
    lock_path.write_text(json.dumps(lock_doc), encoding="utf-8")

    monkeypatch.setattr(
        "bench.hpc.runtime_catalog.verify_site_receipt",
        lambda *args, **kwargs: {
            "problems": [],
            "derived": {"qualification_status": "PASS"},
        },
        raising=False,
    )
    # The catalog imports the verifier inside _load, so patch the defining
    # module as well as the optional module attribute above.
    monkeypatch.setattr(
        "bench.experiments.compute_profile_qualification.verify_site_receipt",
        lambda *args, **kwargs: {
            "problems": [],
            "derived": {"qualification_status": "PASS"},
        },
    )
    catalog = TrustedRuntimeCatalog(
        lock_dir,
        qualification_root=tmp_path,
        trusted_site_profiles={"site-a": _profile()},
    )
    assert catalog.qualified_capabilities() == ["deepmd"]
    resolved = catalog.to_resolver().resolve("deepmd")
    assert resolved.qualification_verified is True
    assert resolved.qualification_receipt_digest == receipt["digest"]


def test_gateway_does_not_take_resolver_from_adapter(tmp_path: Path):
    from bench.hpc.adapters.process_test import ProcessTestAdapter

    adapter = ProcessTestAdapter(tmp_path / "jobs")
    adapter.runtime_resolver = object()  # type: ignore[attr-defined]
    gateway = Gateway(adapter)
    token = gateway.issue("run-a", ["submit"])
    spec = {
        "schema_version": 1,
        "idempotency_key": "id-a",
        "runtime": "cp2k",
        "command": ["echo", "ok"],
        "resources": {
            "cpus": 1,
            "memory_gb": 1,
            "gpus": 0,
            "walltime_minutes": 1,
        },
        "inputs": [],
        "outputs": [],
    }
    with pytest.raises(GatewayError, match="no runtime resolver"):
        gateway.submit(token, "run-a", spec, operation_id="op-a")
