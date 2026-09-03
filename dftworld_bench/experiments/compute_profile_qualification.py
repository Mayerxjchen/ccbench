"""Layer 2: ComputeProfile qualification derivation and zero-orphan cloud recycling gate.

Invariants:
- A ComputeProfile binds abstract classes (cpu, gpu) to qualified SiteProfiles.
- Cloud GPU qualification mandates complete lifecycle execution:
  inventory check -> instance create -> remote task -> logs/fetch -> settlement termination.
- Zero-Orphan Gate: Qualification CANNOT PASS if any running or billing instance remains.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jsonschema

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "compute-profile-qualification-receipt.schema.json"
)


class ComputeProfileQualificationError(Exception):
    """Error validating or deriving ComputeProfile qualification receipt."""


@dataclass
class ComputeProfileQualificationVerdict:
    passed: bool
    compute_profile_id: str
    routes_valid: bool
    cpu_site_qualified: bool
    gpu_site_qualified: bool
    cloud_recycling_passed: bool
    active_instances_count: int
    orphan_instances_count: int
    errors: list[str] = field(default_factory=list)


def compute_receipt_digest(doc: dict[str, Any]) -> str:
    """Compute sha256 over canonical JSON without the digest field."""
    clone = dict(doc)
    clone.pop("digest", None)
    canon = json.dumps(clone, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canon.encode('utf-8')).hexdigest()}"


def verify_and_derive_qualification(
    receipt_doc: dict[str, Any],
    *,
    active_instances_checker: Callable[[], list[str]] | None = None,
) -> ComputeProfileQualificationVerdict:
    """Verify schema, content digest, and derive Layer 2 qualification verdict."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=receipt_doc, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ComputeProfileQualificationError(
            f"Receipt failed schema validation: {exc.message}"
        ) from exc

    # Verify content digest
    claimed_digest = receipt_doc.get("digest")
    if claimed_digest:
        expected_digest = compute_receipt_digest(receipt_doc)
        if claimed_digest != expected_digest:
            raise ComputeProfileQualificationError(
                f"Receipt digest mismatch: claimed {claimed_digest} != computed {expected_digest}"
            )

    errors: list[str] = []
    pid = receipt_doc["compute_profile_id"]
    routes = receipt_doc["routes"]
    site_receipts = receipt_doc.get("site_receipts", {})

    routes_valid = "cpu" in routes and "gpu" in routes
    if not routes_valid:
        errors.append("Routes missing cpu or gpu mapping")

    cpu_site = routes.get("cpu", "")
    gpu_site = routes.get("gpu", "")

    cpu_qualified = bool(cpu_site and site_receipts.get(cpu_site))
    if not cpu_qualified:
        errors.append(f"CPU site {cpu_site!r} lacks a qualified site receipt")

    gpu_qualified = bool(gpu_site and site_receipts.get(gpu_site))
    if not gpu_qualified:
        errors.append(f"GPU site {gpu_site!r} lacks a qualified site receipt")

    # Cloud recycling gate
    evidence = receipt_doc["cloud_recycling_evidence"]
    active_count = evidence.get("active_instances_count", 0)
    orphan_count = evidence.get("orphan_instances_count", 0)
    settlement_term = evidence.get("settlement_terminated", False)
    credentials_iso = evidence.get("credentials_isolated", False)

    recycling_passed = True
    if active_count > 0:
        recycling_passed = False
        errors.append(
            f"Zero-Orphan Gate failed: {active_count} cloud instances remain active/billing"
        )
    if orphan_count > 0:
        recycling_passed = False
        errors.append(
            f"Zero-Orphan Gate failed: {orphan_count} orphan instances recorded"
        )
    if not settlement_term:
        recycling_passed = False
        errors.append("Settlement termination not confirmed")
    if not credentials_iso:
        recycling_passed = False
        errors.append("CompShare credentials not verified as isolated from candidate")

    # Live cloud probe check if checker provided
    if active_instances_checker is not None:
        live_active = active_instances_checker()
        if live_active:
            recycling_passed = False
            errors.append(
                f"Live cloud check detected active instances: {live_active}"
            )

    overall_pass = (
        routes_valid
        and cpu_qualified
        and gpu_qualified
        and recycling_passed
        and len(errors) == 0
    )

    return ComputeProfileQualificationVerdict(
        passed=overall_pass,
        compute_profile_id=pid,
        routes_valid=routes_valid,
        cpu_site_qualified=cpu_qualified,
        gpu_site_qualified=gpu_qualified,
        cloud_recycling_passed=recycling_passed,
        active_instances_count=active_count,
        orphan_instances_count=orphan_count,
        errors=errors,
    )

