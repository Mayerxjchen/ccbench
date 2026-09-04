"""Layer 2: ComputeProfile qualification derivation and zero-orphan cloud recycling gate.

Invariants:
- A ComputeProfile binds abstract classes (cpu, gpu) to qualified SiteProfiles.
- Cloud GPU qualification mandates complete, verifiable lifecycle execution:
  inventory check -> instance create -> remote task -> logs/fetch -> settlement termination.
- Zero-Orphan Gate: Qualification CANNOT PASS if any running or billing instance remains.
- All evidence fields (stock_checked, task_executed, fetch_verified, credentials_isolated,
  settlement_terminated) must be explicitly True. False evidence fails closed.
- Canonical receipt digest is mandatory and mechanically verified.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import jsonschema

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "schemas"
    / "compute-profile-qualification-receipt.schema.json"
)

SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

_VALID_SITE_KINDS = {
    "hpc-site-qualification/v1",
    "dispatcher-site-qualification/v1",
    "compshare-site-qualification/v1",
    "compshare-gpu-site-qualification",
    "compshare-gpu-site-qualification/v1",
    "hpc-dispatcher-qualification/site-v1",
}


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
    """Compute sha256 over canonical JSON without the digest or signature fields."""
    clone = {k: v for k, v in doc.items() if k != "digest" and k != "signature"}
    canon = json.dumps(clone, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canon.encode('utf-8')).hexdigest()}"


def generate_ed25519_key_pair() -> tuple[str, str]:
    """Generate a new Ed25519 key pair; returns (private_key_hex, public_key_hex)."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    priv = ed25519.Ed25519PrivateKey.generate()
    pub = priv.public_key()
    return priv.private_bytes_raw().hex(), pub.public_bytes_raw().hex()


def sign_receipt(doc: dict[str, Any], private_key_hex: str) -> dict[str, Any]:
    """Sign receipt dict with Ed25519 private key; returns the signature dict."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    body = {k: v for k, v in doc.items() if k != "signature"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    sig = priv.sign(canon)
    pub_hex = priv.public_key().public_bytes_raw().hex()
    return {
        "algorithm": "ed25519",
        "public_key": pub_hex,
        "signature_hex": sig.hex(),
    }


def verify_receipt_signature(
    doc: dict[str, Any], expected_public_key_hex: str | None = None
) -> bool:
    """Verify Ed25519 signature on receipt dict."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    sig = doc.get("signature")
    if not isinstance(sig, dict):
        return False
    if sig.get("algorithm") != "ed25519":
        return False
    pub_hex = sig.get("public_key")
    sig_hex = sig.get("signature_hex")
    if not pub_hex or not sig_hex:
        return False
    if expected_public_key_hex and pub_hex != expected_public_key_hex:
        return False
    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        body = {k: v for k, v in doc.items() if k != "signature"}
        canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        pub.verify(bytes.fromhex(sig_hex), canon)
        return True
    except Exception:
        return False


def check_evidence_containment(evidence_root: Path, file_path: str | Path) -> Path:
    """Validate that file_path is relative, non-symlink, and strictly contained in evidence_root."""
    p = Path(file_path)
    if p.is_absolute():
        raise ValueError(f"Absolute evidence path forbidden: {file_path}")
    resolved = (evidence_root / p).resolve()
    real_root = evidence_root.resolve()
    if real_root not in resolved.parents and resolved != real_root:
        raise ValueError(f"Evidence path escapes evidence root: {file_path}")
    curr = evidence_root
    for part in p.parts:
        curr = curr / part
        if curr.is_symlink():
            raise ValueError(f"Symlink forbidden in evidence path: {file_path}")
    return resolved


def verify_site_receipt(
    receipt_data: dict[str, Any],
    *,
    scheduler: str,
    root: Path,
    receipt_dir: Path,
) -> dict[str, Any]:
    """Scheduler-aware site receipt verification.

    For Slurm sites (``scheduler == "slurm"``), delegates to the full
    ``qualification_receipt.verify_receipt`` which checks SIF runtime locks,
    SiteProfile rebuild from ``cluster_profile.toml``, and both CPU/GPU canaries.

    For CompShare sites (``scheduler == "compshare"``), performs a lighter
    verification that checks:
    - Schema and content-addressed digest
    - source_commit and code_identity provenance
    - CompShare-specific evidence (instance lifecycle, settlement)
    - No SIF/Apptainer requirements

    Returns the same structure as ``verify_receipt``: ``{"problems": {...}, "derived": {...}}``.
    """
    from dftworld_bench.experiments.qualification_receipt import (
        canonical_digest,
        code_identity,
        sha256_file,
        source_commit,
    )

    problems: dict[str, list[str]] = {}

    def problem(gate: str, msg: str) -> None:
        problems.setdefault(gate, []).append(msg)

    # -- Universal checks (both schedulers) ------------------------------------
    # Content-addressed digest
    declared = receipt_data.get("digest")
    recomputed = canonical_digest(
        {k: v for k, v in receipt_data.items() if k != "digest"}
    )
    if declared != recomputed:
        problem("schema", "digest mismatch: receipt is not content-addressed")

    # source_commit
    import re
    _COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
    commit = receipt_data.get("source_commit")
    if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
        problem("provenance", f"source_commit malformed: {commit!r}")

    # code_identity
    identity = receipt_data.get("code_identity") or {}
    for rel, want in sorted(identity.items()):
        path = root / rel
        if not path.is_file():
            problem("provenance", f"code identity file missing: {rel}")
            continue
        have = sha256_file(path)
        if have != want:
            problem(
                "provenance",
                f"code identity changed since qualification: {rel} "
                f"(receipt={want} disk={have})",
            )

    # -- Scheduler-specific checks --------------------------------------------
    evidence = receipt_data.get("evidence") or {}

    if scheduler == "slurm":
        # Full Slurm verification: SIF lock, SiteProfile rebuild, required canaries
        # CPU-only sites only need dispatcher.cpu; hybrid sites need both.
        from dftworld_bench.experiments.qualification_receipt import verify_receipt

        # Determine required probe classes from receipt evidence
        evidence_jobs = evidence.get("jobs") or []
        probe_classes_seen = {j.get("probe_class") for j in evidence_jobs}
        # If receipt only has CPU jobs, require only CPU; otherwise require both
        if probe_classes_seen == {"cpu"}:
            required_probe_classes = {"cpu"}
        else:
            required_probe_classes = {"cpu", "gpu"}

        vr = verify_receipt(
            receipt_data,
            root=root,
            receipt_dir=receipt_dir,
            required_probe_classes=required_probe_classes,
        )
        return vr

    elif scheduler == "compshare":
        # CompShare-specific: no SIF, no cluster_profile.toml, GPU-only canary
        # Check receipt structure
        kind = receipt_data.get("kind")
        if kind not in _VALID_SITE_KINDS:
            problem("schema", f"CompShare receipt missing or invalid kind: {kind!r}")

        # Ed25519 signature verification if present
        if "signature" in receipt_data:
            if not verify_receipt_signature(receipt_data):
                problem("signature", "CompShare receipt signature verification failed")

        # code_identity must be present and non-empty
        if not identity:
            problem("provenance", "CompShare receipt must include code_identity")

        # SiteProfile digest must be present
        site_digest = receipt_data.get("site_profile_digest")
        if not site_digest or not SHA256_PATTERN.match(site_digest):
            problem("provenance", f"CompShare receipt missing or invalid site_profile_digest: {site_digest!r}")

        # Check CompShare evidence blocks
        jobs = evidence.get("jobs") or []
        gpu_jobs = [j for j in jobs if j.get("probe_class") == "gpu"]
        if not gpu_jobs:
            problem("canary_coverage", "CompShare receipt must include at least one GPU canary job")
        else:
            # Check job terminal status
            for job in gpu_jobs:
                accounting = job.get("accounting") or {}
                state = accounting.get("state", "").upper()
                if state not in ("COMPLETED", "COMPLETING", "SUCCEEDED"):
                    problem("job_status", f"GPU canary job not in terminal state: {state!r}")
                # Check exit code
                exit_code = accounting.get("exit_code")
                if exit_code is not None and exit_code != 0:
                    problem("job_status", f"GPU canary job non-zero exit code: {exit_code}")

        # Check runtime/image identity
        runtime_lock = receipt_data.get("runtime_lock") or {}
        if not runtime_lock.get("path"):
            problem("provenance", "CompShare receipt missing runtime_lock.path")
        lock_image_id = runtime_lock.get("image_id")
        if not runtime_lock.get("sif_sha256") and not lock_image_id:
            problem("provenance", "CompShare receipt missing runtime identity (sif_sha256 or image_id)")

        # Check settlement evidence
        settlement = evidence.get("settlement") or {}
        if not settlement.get("terminated"):
            problem("settlement", "CompShare receipt must show terminated settlement")
        if not settlement.get("digest"):
            problem("settlement", "CompShare receipt missing settlement digest")

        # Check instance lifecycle evidence
        instance_evidence = evidence.get("instance_lifecycle") or {}
        if not instance_evidence.get("instance_id"):
            problem("instance_lifecycle", "CompShare receipt must include instance_id")
        if not instance_evidence.get("stop_confirmed"):
            problem("instance_lifecycle", "CompShare receipt missing stop_confirmed")
        if not instance_evidence.get("delete_confirmed"):
            problem("instance_lifecycle", "CompShare receipt missing delete_confirmed")

        # Runtime image_id consistency across runtime_lock, instance_lifecycle, and jobs
        if lock_image_id:
            inst_image_id = instance_evidence.get("image_id")
            if inst_image_id and inst_image_id != lock_image_id:
                problem(
                    "runtime_consistency",
                    f"Runtime image_id mismatch: lock has {lock_image_id}, instance has {inst_image_id}",
                )
            for job in gpu_jobs:
                job_image_id = job.get("image_id")
                if job_image_id and job_image_id != lock_image_id:
                    problem(
                        "runtime_consistency",
                        f"Runtime image_id mismatch: lock has {lock_image_id}, job has {job_image_id}",
                    )

        # Check credential isolation
        cred_evidence = evidence.get("credential_isolation") or {}
        if not cred_evidence.get("verified"):
            problem("credential_isolation", "CompShare receipt must show credential isolation verified")

        # Check fetch/artifact evidence
        fetch_evidence = evidence.get("fetch") or {}
        if not fetch_evidence.get("artifacts"):
            problem("fetch", "CompShare receipt must include fetched artifacts")
        else:
            for artifact in fetch_evidence.get("artifacts", []):
                if not artifact.get("sha256"):
                    problem("fetch", f"Artifact {artifact.get('name', '?')} missing sha256")
                art_path = artifact.get("path") or artifact.get("name")
                if art_path:
                    try:
                        check_evidence_containment(receipt_dir, art_path)
                    except ValueError as exc:
                        problem("fetch", f"Artifact path escapes receipt dir: {exc}")

        # Check orphan check
        orphan = evidence.get("orphan_check") or {}
        if not isinstance(orphan, dict) or not orphan.get("method"):
            problem("orphan_check", "CompShare receipt must include orphan_check evidence")
        if orphan.get("active_total") is not None and orphan.get("active_total") != 0:
            problem("orphan_check", f"Orphan check shows active_total={orphan.get('active_total')}, expected 0")

        # Check audit log lineage (GatewayAudit)
        audit_rel = evidence.get("audit_log") or receipt_data.get("audit_log")
        if not audit_rel:
            problem("audit_ledger", "CompShare receipt missing mandatory audit_log")
        else:
            try:
                audit_path = check_evidence_containment(receipt_dir, audit_rel)
                if not audit_path.is_file():
                    problem("audit_ledger", f"CompShare audit log missing: {audit_path}")
                else:
                    from dftworld_bench.hpc.audit import GatewayAudit

                    ledger = GatewayAudit(audit_path)
                    broken = ledger.verify()
                    if broken:
                        problem("audit_ledger", f"GatewayAudit hash chain broken at {broken}")
                    kinds = set()
                    for entry in ledger.entries():
                        ev = entry.get("event", {})
                        if isinstance(ev, dict):
                            act = ev.get("kind") or ev.get("action")
                            if act:
                                kinds.add(act)
                    if not ({"SUBMIT_INTENT", "JOB_SUBMIT"} & kinds):
                        problem(
                            "audit_ledger",
                            f"GatewayAudit missing SUBMIT_INTENT/JOB_SUBMIT event (kinds: {kinds})",
                        )
                    if not ({"SETTLEMENT_COMPLETE", "INSTANCE_DELETE"} & kinds):
                        problem(
                            "audit_ledger",
                            f"GatewayAudit missing SETTLEMENT_COMPLETE/INSTANCE_DELETE event (kinds: {kinds})",
                        )
            except ValueError as exc:
                problem("audit_ledger", f"CompShare audit log path escapes receipt dir: {exc}")
            except Exception as exc:
                problem("audit_ledger", f"CompShare audit log unreadable: {exc}")

    else:
        problem("provenance", f"unknown scheduler: {scheduler!r}")

    # Derive qualification status
    # Flatten problems dict to list[str] to match verify_receipt return type
    problems_list = [
        f"[{gate}] {msg}" for gate, msgs in sorted(problems.items())
        for msg in msgs
    ]
    qual_status = "PASS" if not problems_list else "INVALID"
    return {
        "receipt_dir": str(receipt_dir),
        "digest_ok": declared == recomputed,
        "problems": problems_list,
        "derived": {
            "qualification_status": qual_status,
            "formal_qualified": qual_status == "PASS",
            "capabilities": {},
            "gates": {},
        },
    }


def verify_and_derive_qualification(
    receipt_doc: dict[str, Any],
    *,
    expected_profile: Any | None = None,
    active_instances_checker: Callable[[], list[str]] | None = None,
    site_receipts_dir: Path | None = None,
    require_live_check: bool = False,
) -> ComputeProfileQualificationVerdict:
    """Verify schema, content digest, positive evidence, and derive Layer 2 qualification verdict.

    Fails closed on:
    - Missing or non-matching content digest.
    - Mismatch between receipt and expected ComputeProfile (ID, digest, or routes).
    - False or missing positive execution evidence (stock_checked, task_executed, fetch_verified,
      credentials_isolated, settlement_terminated).
    - Any active or orphan cloud instances (> 0).
    - Any live active instances detected by active_instances_checker (or missing checker if require_live_check).
    - Missing, non-matching digest, or non-PASS site qualification receipts.
    """
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(instance=receipt_doc, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ComputeProfileQualificationError(
            f"Receipt failed schema validation: {exc.message}"
        ) from exc

    # Mandatory content digest check
    claimed_digest = receipt_doc.get("digest")
    if not claimed_digest:
        raise ComputeProfileQualificationError("Receipt missing mandatory 'digest' field")
    expected_digest = compute_receipt_digest(receipt_doc)
    if claimed_digest != expected_digest:
        raise ComputeProfileQualificationError(
            f"Receipt digest mismatch: claimed {claimed_digest} != computed {expected_digest}"
        )

    errors: list[str] = []
    pid = receipt_doc["compute_profile_id"]
    routes = receipt_doc["routes"]
    site_receipts = receipt_doc.get("site_receipts", {})

    # Strict binding to expected ComputeProfile if supplied
    if expected_profile is not None:
        if pid != expected_profile.profile_id:
            raise ComputeProfileQualificationError(
                f"Receipt compute profile ID mismatch: receipt has {pid!r}, expected {expected_profile.profile_id!r}"
            )
        exp_digest = getattr(expected_profile, "digest", "")
        rcpt_prof_digest = receipt_doc.get("compute_profile_digest", "").replace("sha256:", "")
        exp_prof_digest = exp_digest.replace("sha256:", "")
        if rcpt_prof_digest != exp_prof_digest:
            raise ComputeProfileQualificationError(
                f"Receipt compute profile digest mismatch: receipt has {rcpt_prof_digest!r}, expected {exp_prof_digest!r}"
            )
        if routes != getattr(expected_profile, "routes", {}):
            raise ComputeProfileQualificationError(
                f"Receipt routes {routes} do not match expected profile routes {getattr(expected_profile, 'routes', {})}"
            )

    routes_valid = "cpu" in routes and "gpu" in routes
    if not routes_valid:
        errors.append("Routes missing cpu or gpu mapping")

    cpu_site = routes.get("cpu", "")
    gpu_site = routes.get("gpu", "")

    # Mechanical verification of site receipts
    cpu_receipt_digest = site_receipts.get(cpu_site)
    cpu_qualified = bool(cpu_receipt_digest and SHA256_PATTERN.match(cpu_receipt_digest))
    if not cpu_qualified:
        errors.append(f"CPU site {cpu_site!r} lacks a valid sha256-anchored receipt digest")

    gpu_receipt_digest = site_receipts.get(gpu_site)
    gpu_qualified = bool(gpu_receipt_digest and SHA256_PATTERN.match(gpu_receipt_digest))
    if not gpu_qualified:
        errors.append(f"GPU site {gpu_site!r} lacks a valid sha256-anchored receipt digest")

    # Mandatory on-disk site receipt verification:
    # A hybrid compute profile cannot qualify on self-asserted digests alone.
    if not site_receipts_dir or not Path(site_receipts_dir).is_dir():
        errors.append(
            "Hybrid compute profile qualification requires a valid --site-receipts-dir containing site receipt files"
        )
        cpu_qualified = False
        gpu_qualified = False
    else:
        for s_name, s_digest in [(cpu_site, cpu_receipt_digest), (gpu_site, gpu_receipt_digest)]:
            if not s_name:
                continue
            r_file = Path(site_receipts_dir) / f"{s_name}-receipt.json"
            if not r_file.is_file():
                r_file = Path(site_receipts_dir) / f"{s_name}.receipt.json"
            if not r_file.is_file():
                errors.append(f"Mandatory site receipt file {s_name} not found in {site_receipts_dir}")
                if s_name == cpu_site:
                    cpu_qualified = False
                if s_name == gpu_site:
                    gpu_qualified = False
                continue
            try:
                raw_bytes = r_file.read_bytes()
                computed_file_sha = f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}"
                r_data = json.loads(raw_bytes.decode("utf-8"))
                # Recompute canonical digest of receipt doc (excluding digest field)
                r_canonical_sha = compute_receipt_digest(r_data)

                # Verify recorded s_digest matches either recomputed canonical digest or raw file sha256
                if s_digest and s_digest != r_canonical_sha and s_digest != computed_file_sha:
                    errors.append(
                        f"Site receipt for {s_name} digest mismatch: recorded {s_digest} != computed {r_canonical_sha} (file sha {computed_file_sha})"
                    )
                    if s_name == cpu_site:
                        cpu_qualified = False
                    if s_name == gpu_site:
                        gpu_qualified = False

                # Scheduler-aware provenance verification.
                # CPU site (ikkem-cpu) uses Slurm; GPU site (compshare-gpu) uses CompShare.
                # Each scheduler has different verification requirements:
                # - Slurm: SIF runtime lock, cluster_profile.toml rebuild, both CPU/GPU canaries
                # - CompShare: instance lifecycle, settlement, GPU-only canary
                site_receipt_dir = r_file.parent
                root = site_receipt_dir.parent.parent  # evidence/hpc-dispatcher/qualification/<site>/../..
                if not (root / "dftworld_bench").is_dir():
                    # Fallback: try repo root
                    root = Path(__file__).resolve().parents[2]

                # Determine scheduler from site name convention
                if s_name == cpu_site:
                    scheduler = "slurm"
                elif s_name == gpu_site:
                    scheduler = "compshare"
                else:
                    scheduler = "slurm"  # default

                vr = verify_site_receipt(
                    r_data,
                    scheduler=scheduler,
                    root=root,
                    receipt_dir=site_receipt_dir,
                )
                vr_problems = vr.get("problems") or []
                if vr_problems:
                    problem_summary = "; ".join(vr_problems[:3])
                    errors.append(f"Site receipt for {s_name} failed full verification: {problem_summary}")
                    if s_name == cpu_site:
                        cpu_qualified = False
                    if s_name == gpu_site:
                        gpu_qualified = False
                    continue

                # Derive qualification status from the verified receipt
                derived = vr.get("derived") or {}
                qual_status = derived.get("qualification_status")
                if qual_status != "PASS":
                    errors.append(f"Site receipt for {s_name} did not derive PASS (status: {qual_status})")
                    if s_name == cpu_site:
                        cpu_qualified = False
                    if s_name == gpu_site:
                        gpu_qualified = False
            except Exception as exc:
                errors.append(f"Could not load site receipt {r_file.name}: {exc}")
                if s_name == cpu_site:
                    cpu_qualified = False
                if s_name == gpu_site:
                    gpu_qualified = False

    # Cloud recycling gate & positive evidence checks
    evidence = receipt_doc["cloud_recycling_evidence"]

    # Positive evidence checks: all must be strictly True
    stock_checked = evidence.get("stock_checked", False)
    task_executed = evidence.get("task_executed", False)
    fetch_verified = evidence.get("fetch_verified", False)
    credentials_iso = evidence.get("credentials_isolated", False)
    settlement_term = evidence.get("settlement_terminated", False)
    active_count = evidence.get("active_instances_count", 0)
    orphan_count = evidence.get("orphan_instances_count", 0)

    recycling_passed = True
    if not stock_checked:
        recycling_passed = False
        errors.append("Evidence failure: pre-creation stock check was false")
    if not task_executed:
        recycling_passed = False
        errors.append("Evidence failure: probe task execution was false")
    if not fetch_verified:
        recycling_passed = False
        errors.append("Evidence failure: output artifact fetch verification was false")
    if not credentials_iso:
        recycling_passed = False
        errors.append("Evidence failure: candidate credential isolation was false")
    if not settlement_term:
        recycling_passed = False
        errors.append("Evidence failure: settlement termination was false")

    # Zero-Orphan hard gate: strictly 0 instances
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

    # Live cloud probe check if active_instances_checker is provided
    if require_live_check and active_instances_checker is None:
        recycling_passed = False
        errors.append(
            "Zero-Orphan Gate failed: live active_instances_checker is required for live verification"
        )
    elif active_instances_checker is not None:
        try:
            live_active = active_instances_checker()
            if live_active:
                recycling_passed = False
                errors.append(
                    f"Zero-Orphan Gate failed: live cloud query detected active instances: {live_active}"
                )
        except Exception as exc:
            recycling_passed = False
            errors.append(f"Zero-Orphan Gate failed: error querying live cloud instances: {exc}")

    # Ed25519 signature verification if present
    if "signature" in receipt_doc:
        if not verify_receipt_signature(receipt_doc):
            recycling_passed = False
            errors.append("ComputeProfile receipt signature verification failed")

    # Evidence root containment check
    ev_root_str = receipt_doc.get("evidence_root")
    if ev_root_str:
        ev_root = Path(ev_root_str)
        for ef in receipt_doc.get("evidence_files", []):
            try:
                check_evidence_containment(ev_root, ef)
            except ValueError as exc:
                recycling_passed = False
                errors.append(f"Evidence file containment error: {exc}")

    # Audit log verification if present at compute profile level
    audit_log_str = receipt_doc.get("audit_log")
    if audit_log_str:
        try:
            if ev_root_str:
                audit_p = check_evidence_containment(Path(ev_root_str), audit_log_str)
            else:
                audit_p = Path(audit_log_str)
            if not audit_p.is_file():
                recycling_passed = False
                errors.append(f"Audit log file not found: {audit_p}")
            else:
                from dftworld_bench.hpc.audit import GatewayAudit
                audit = GatewayAudit(audit_p)
                broken = audit.verify()
                if broken:
                    recycling_passed = False
                    errors.append(f"GatewayAudit hash chain broken at {broken}")
        except ValueError as exc:
            recycling_passed = False
            errors.append(f"Audit log containment error: {exc}")
        except Exception as exc:
            recycling_passed = False
            errors.append(f"Audit log verification error: {exc}")

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


def build_compute_profile_qualification_receipt(
    *,
    compute_profile_id: str,
    compute_profile_digest: str,
    routes: dict[str, str],
    site_receipts: dict[str, str],
    cloud_recycling_evidence: dict[str, Any],
    evidence_root: str | None = None,
    evidence_files: list[str] | None = None,
    audit_log: str | None = None,
    private_key_hex: str | None = None,
) -> dict[str, Any]:
    """Helper to construct a fully sealed qualification receipt with computed digest and optional signature."""
    doc: dict[str, Any] = {
        "kind": "hpc-compute-profile-qualification/v1",
        "schema_id": "https://mlip-bench.example/schemas/compute-profile-qualification-receipt.schema.json",
        "compute_profile_id": compute_profile_id,
        "compute_profile_digest": compute_profile_digest,
        "routes": dict(routes),
        "site_receipts": dict(site_receipts),
        "cloud_recycling_evidence": dict(cloud_recycling_evidence),
    }
    if evidence_root is not None:
        doc["evidence_root"] = evidence_root
    if evidence_files is not None:
        doc["evidence_files"] = list(evidence_files)
    if audit_log is not None:
        doc["audit_log"] = audit_log

    doc["digest"] = compute_receipt_digest(doc)

    if private_key_hex is not None:
        doc["signature"] = sign_receipt(doc, private_key_hex)

    return doc
