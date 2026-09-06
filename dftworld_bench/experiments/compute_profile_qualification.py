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
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import jsonschema

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = (
    _REPO_ROOT
    / "schemas"
    / "compute-profile-qualification-receipt.schema.json"
)
_SCHEMA_V2_PATH = (
    _REPO_ROOT
    / "schemas"
    / "compute-profile-qualification-receipt.v2.schema.json"
)

# Layer-1 site receipts and Layer-2 ComputeProfile receipts deliberately use
# different trust anchors.  The checked-in trust store keeps this key
# UNCONFIGURED; an operator must inject/configure the real public key before a
# v2 ComputeProfile receipt can become eligible.
COMPUTE_PROFILE_SIGNING_KEY_ID = "compute-profile-v2"
COMPUTE_PROFILE_SIGNING_PURPOSE = "compute-profile-qualification"
COMPUTE_PROFILE_V2_KIND = "hpc-compute-profile-qualification/v2"
COMPUTE_PROFILE_V2_SCHEMA_ID = (
    "https://mlip-bench.example/schemas/"
    "compute-profile-qualification-receipt.v2.schema.json"
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
    # ``LEGACY_NOT_ELIGIBLE`` is intentionally distinct from ``INVALID``:
    # v1 material remains readable for migration/audit tooling, but it is
    # never an attestation that may promote a ComputeProfile.
    status: str = "INVALID"


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


def sign_receipt(
    doc: dict[str, Any],
    private_key_hex: str,
    *,
    key_id: str = "compshare-site-v1",
    purpose: str | None = None,
) -> dict[str, Any]:
    """Sign receipt dict with Ed25519 private key; returns the signature dict.

    ``purpose`` is a domain-separation value.  When present it is included in
    the signed canonical bytes as well as in the returned signature metadata,
    so changing the metadata cannot move a signature between trust domains.
    """
    from cryptography.hazmat.primitives.asymmetric import ed25519

    body = {k: v for k, v in doc.items() if k != "signature"}
    if purpose is not None:
        body["__signature_purpose__"] = purpose
    canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    sig = priv.sign(canon)
    pub_hex = priv.public_key().public_bytes_raw().hex()
    signature = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "public_key": pub_hex,
        "signature_hex": sig.hex(),
    }
    if purpose is not None:
        signature["purpose"] = purpose
    return signature


def verify_ed25519_signature_bytes(
    document: dict[str, Any] | bytes,
    public_key_hex: str,
    signature_hex: str | None = None,
    *,
    signed_purpose: str | None = None,
) -> bool:
    """Low-level Ed25519 raw signature verification over canonical document bytes."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    if isinstance(document, bytes):
        canon = document
    else:
        body = {k: v for k, v in document.items() if k != "signature"}
        if signed_purpose is not None:
            body["__signature_purpose__"] = signed_purpose
        canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if signature_hex is None:
            sig = document.get("signature") or {}
            signature_hex = sig.get("signature_hex")

    if not signature_hex or not public_key_hex:
        return False
    try:
        pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pub.verify(bytes.fromhex(signature_hex), canon)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class SignatureVerificationResult:
    valid: bool
    status: str
    error: str = ""
    key_id: str = ""

    def __bool__(self) -> bool:
        return self.valid


def verify_receipt_signature(
    doc: dict[str, Any],
    expected_public_key_hex: str | None = None,
    *,
    expected_key_id: str | None = None,
    expected_purpose: str | None = None,
    trust_store: Any = None,
    detailed: bool = False,
) -> bool | SignatureVerificationResult:
    """Verify receipt signature against pinned key or qualification trust store.

    If expected_public_key_hex is provided, verifies against that pinned key.
    Otherwise, resolves the public key from trust_store using expected_key_id or
    the key_id in the receipt. If the key is UNCONFIGURED or mismatched, fails closed.
    """
    sig = doc.get("signature")
    if not isinstance(sig, dict):
        res = SignatureVerificationResult(
            valid=False, status="MALFORMED", error="Receipt missing 'signature' object"
        )
        return res if detailed else False
    if sig.get("algorithm") != "ed25519":
        res = SignatureVerificationResult(
            valid=False, status="MALFORMED", error=f"Unsupported algorithm: {sig.get('algorithm')!r}"
        )
        return res if detailed else False

    if expected_purpose is not None and sig.get("purpose") != expected_purpose:
        res = SignatureVerificationResult(
            valid=False,
            status="PURPOSE_MISMATCH",
            error=(
                f"Signature purpose mismatch: receipt claims {sig.get('purpose')!r}, "
                f"expected {expected_purpose!r}"
            ),
            key_id=str(sig.get("key_id") or ""),
        )
        return res if detailed else False

    trusted_pub_hex: str | None = None
    target_key_id = expected_key_id or sig.get("key_id") or ""

    if expected_public_key_hex is not None:
        trusted_pub_hex = expected_public_key_hex
        if expected_key_id and sig.get("key_id") and sig.get("key_id") != expected_key_id:
            res = SignatureVerificationResult(
                valid=False,
                status="KEY_MISMATCH",
                error=f"Signature key_id mismatch: receipt claims {sig.get('key_id')!r}, expected {expected_key_id!r}",
                key_id=str(sig.get("key_id")),
            )
            return res if detailed else False
        receipt_pub_hex = sig.get("public_key")
        if receipt_pub_hex and receipt_pub_hex.lower() != trusted_pub_hex.lower():
            res = SignatureVerificationResult(
                valid=False,
                status="KEY_MISMATCH",
                error=(
                    f"Receipt embeds self-authored public_key {receipt_pub_hex} which does not "
                    f"match expected public_key {trusted_pub_hex}"
                ),
                key_id=target_key_id,
            )
            return res if detailed else False
    else:
        if trust_store is None:
            from dftworld_bench.hpc.trust_store import QualificationTrustStore

            try:
                trust_store = QualificationTrustStore.load_default()
            except Exception as exc:
                res = SignatureVerificationResult(
                    valid=False,
                    status="TRUST_STORE_ERROR",
                    error=f"Cannot load default trust store: {exc}",
                    key_id=target_key_id,
                )
                return res if detailed else False

        if not target_key_id:
            res = SignatureVerificationResult(
                valid=False, status="MALFORMED", error="No key_id specified or found in receipt"
            )
            return res if detailed else False

        if expected_key_id and sig.get("key_id") and sig.get("key_id") != expected_key_id:
            res = SignatureVerificationResult(
                valid=False,
                status="KEY_MISMATCH",
                error=f"Signature key_id mismatch: receipt claims {sig.get('key_id')!r}, expected {expected_key_id!r}",
                key_id=str(sig.get("key_id")),
            )
            return res if detailed else False

        try:
            try:
                trusted_pub_hex = trust_store.resolve_public_key_hex(
                    target_key_id, expected_purpose=expected_purpose
                )
            except TypeError:
                # Test/in-process trust stores written before purpose binding
                # may expose the old one-argument method.  A formal v2 path
                # never relies on this compatibility branch: it first checks
                # that the configured store carries the required purpose.
                if expected_purpose is not None:
                    raise
                trusted_pub_hex = trust_store.resolve_public_key_hex(target_key_id)
        except Exception as exc:
            status = "UNCONFIGURED_KEY" if "UNCONFIGURED" in str(exc) else "KEY_NOT_FOUND"
            res = SignatureVerificationResult(
                valid=False,
                status=status,
                error=str(exc),
                key_id=target_key_id,
            )
            return res if detailed else False

        receipt_pub_hex = sig.get("public_key")
        if receipt_pub_hex and receipt_pub_hex.lower() != trusted_pub_hex.lower():
            res = SignatureVerificationResult(
                valid=False,
                status="KEY_MISMATCH",
                error=(
                    f"Receipt embeds self-authored public_key {receipt_pub_hex} which does not "
                    f"match pinned public_key {trusted_pub_hex} in trust store for {target_key_id!r}"
                ),
                key_id=target_key_id,
            )
            return res if detailed else False

    sig_hex = sig.get("signature_hex")
    if not sig_hex:
        res = SignatureVerificationResult(
            valid=False, status="MALFORMED", error="Missing signature_hex in signature object", key_id=target_key_id
        )
        return res if detailed else False

    # For formal purpose-bound signatures, use the caller's expected purpose;
    # direct callers may omit it, in which case the receipt metadata supplies
    # the domain to verify.  Legacy signatures have no purpose and retain the
    # original canonicalization.
    signed_purpose = expected_purpose or sig.get("purpose") or None
    if not verify_ed25519_signature_bytes(
        doc, trusted_pub_hex, sig_hex, signed_purpose=signed_purpose
    ):
        res = SignatureVerificationResult(
            valid=False, status="INVALID_SIGNATURE", error="Ed25519 signature verification failed", key_id=target_key_id
        )
        return res if detailed else False

    res = SignatureVerificationResult(valid=True, status="VALID", key_id=target_key_id)
    return res if detailed else True


def verify_receipt_signature_detailed(
    doc: dict[str, Any],
    *,
    expected_public_key_hex: str | None = None,
    expected_key_id: str | None = None,
    expected_purpose: str | None = None,
    trust_store: Any = None,
) -> SignatureVerificationResult:
    """Convenience wrapper returning SignatureVerificationResult."""
    result = verify_receipt_signature(
        doc,
        expected_public_key_hex=expected_public_key_hex,
        expected_key_id=expected_key_id,
        expected_purpose=expected_purpose,
        trust_store=trust_store,
        detailed=True,
    )
    assert isinstance(result, SignatureVerificationResult)
    return result


def check_evidence_containment(evidence_root: Path, file_path: str | Path) -> Path:
    """Validate that file_path is relative, non-symlink, and strictly contained in evidence_root."""
    p = Path(file_path)
    if p.is_absolute():
        raise ValueError(f"Absolute evidence path forbidden: {file_path}")
    if ".." in p.parts:
        raise ValueError(f"Evidence path escapes evidence root: {file_path}")
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


def make_evidence_file_record(
    evidence_root: Path | str,
    file_path: Path | str,
    *,
    role: str,
) -> dict[str, Any]:
    """Build a v2 evidence-file binding from an already materialized file.

    This helper is intentionally strict and has no copy/fallback behavior: the
    caller must first materialize the evidence below ``evidence_root``.  The
    returned path is root-relative and the digest/size are measured from the
    bytes on disk that the verifier will later re-read.
    """
    root = Path(evidence_root)
    path = check_evidence_containment(root, file_path)
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise ValueError(f"evidence file is not readable: {file_path}") from exc
    if path.is_symlink() or not stat.S_ISREG(mode):
        raise ValueError(f"evidence file must be a regular non-symlink file: {file_path}")
    rel = Path(file_path)
    data = path.read_bytes()
    return {
        "path": rel.as_posix(),
        "sha256": f"sha256:{hashlib.sha256(data).hexdigest()}",
        "size": len(data),
        "role": role,
    }


def _resolve_evidence_root(
    evidence_root: str,
    *,
    receipt_dir: Path | None,
    fallback_root: Path | None,
) -> Path:
    """Resolve the v2 root against the receipt's trusted materialization dir."""
    root = Path(evidence_root)
    if not root.is_absolute():
        root = (receipt_dir or fallback_root or Path.cwd()) / root
    try:
        root_lstat = root.lstat()
    except OSError as exc:
        raise ValueError(f"evidence_root is missing: {root}") from exc
    if root.is_symlink() or not stat.S_ISDIR(root_lstat.st_mode):
        raise ValueError(f"evidence_root must be a real directory: {root}")
    resolved_root = root.resolve()
    # A receipt may name an absolute materialization directory, but when the
    # caller supplies the trusted receipt directory it must still be inside
    # that directory.  Otherwise an attacker could point a valid-looking
    # evidence bundle at an unrelated filesystem tree.
    trusted_base = receipt_dir or fallback_root
    if trusted_base is not None:
        resolved_base = Path(trusted_base).resolve()
        if resolved_root != resolved_base and resolved_base not in resolved_root.parents:
            raise ValueError(
                f"evidence_root escapes trusted receipt directory: {resolved_root}"
            )
    return resolved_root


def _verify_materialized_evidence_files(
    receipt_doc: Mapping[str, Any],
    *,
    receipt_dir: Path | None,
    fallback_root: Path | None,
) -> tuple[Path | None, list[str], Path | None]:
    """Verify every structured v2 evidence binding and the audit tail.

    Returns ``(root, errors, audit_path)``.  All failures are reported rather
    than silently omitted so a formal verifier can return a failed verdict
    while retaining a useful audit trail for operators.
    """
    errors: list[str] = []
    evidence_root_value = receipt_doc.get("evidence_root")
    if not isinstance(evidence_root_value, str) or not evidence_root_value:
        return None, ["v2 receipt is missing evidence_root"], None
    try:
        root = _resolve_evidence_root(
            evidence_root_value,
            receipt_dir=receipt_dir,
            fallback_root=fallback_root,
        )
    except ValueError as exc:
        return None, [f"evidence_root verification failed: {exc}"], None

    records = receipt_doc.get("evidence_files")
    if not isinstance(records, list) or not records:
        return root, ["v2 receipt requires non-empty evidence_files"], None

    seen: set[str] = set()
    audit_path: Path | None = None
    audit_rel = receipt_doc.get("audit_log")
    for index, record in enumerate(records):
        prefix = f"evidence_files[{index}]"
        if not isinstance(record, Mapping):
            errors.append(f"{prefix} must be a structured object")
            continue
        rel_value = record.get("path")
        digest = record.get("sha256")
        size = record.get("size")
        role = record.get("role")
        if not isinstance(rel_value, str) or not rel_value:
            errors.append(f"{prefix}.path must be a non-empty relative path")
            continue
        rel = Path(rel_value)
        if rel.as_posix() in seen:
            errors.append(f"{prefix}.path is duplicated: {rel_value!r}")
        seen.add(rel.as_posix())
        if not isinstance(digest, str) or not SHA256_PATTERN.match(digest):
            errors.append(f"{prefix}.sha256 is not a sha256 digest")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            errors.append(f"{prefix}.size must be a non-negative integer")
        if not isinstance(role, str) or not role:
            errors.append(f"{prefix}.role must be non-empty")
        try:
            path = check_evidence_containment(root, rel)
        except (ValueError, OSError) as exc:
            errors.append(f"{prefix} path containment failure: {exc}")
            continue
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            errors.append(f"{prefix} cannot be read: {exc}")
            continue
        if path.is_symlink() or not stat.S_ISREG(mode):
            errors.append(f"{prefix} must be a regular non-symlink file: {rel_value!r}")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            errors.append(f"{prefix} cannot be read: {exc}")
            continue
        actual_digest = f"sha256:{hashlib.sha256(data).hexdigest()}"
        if isinstance(digest, str) and digest != actual_digest:
            errors.append(
                f"{prefix} sha256 mismatch: declared {digest} != actual {actual_digest}"
            )
        if isinstance(size, int) and not isinstance(size, bool) and size != len(data):
            errors.append(
                f"{prefix} size mismatch: declared {size} != actual {len(data)}"
            )
        if isinstance(audit_rel, str) and rel.as_posix() == Path(audit_rel).as_posix():
            audit_path = path
            if role != "audit_log":
                errors.append(
                    f"{prefix}.role must be 'audit_log' for the audit_log binding"
                )

    if not isinstance(audit_rel, str) or not audit_rel:
        errors.append("v2 receipt is missing audit_log")
    elif audit_path is None:
        errors.append("audit_log must name one structured evidence_files entry")
    else:
        declared_tail = receipt_doc.get("audit_tail_digest")
        if not isinstance(declared_tail, str) or not re.fullmatch(
            r"(?:sha256:)?[0-9a-f]{64}", declared_tail
        ):
            errors.append("audit_tail_digest is malformed")
        try:
            from dftworld_bench.hpc.audit import GatewayAudit

            ledger = GatewayAudit(audit_path)
            broken = ledger.verify()
            if broken:
                errors.append(f"audit log hash chain broken at {broken}")
            actual_tail = ledger.tail_digest()
            expected_tail = (
                declared_tail.removeprefix("sha256:")
                if isinstance(declared_tail, str)
                else ""
            )
            if not actual_tail or actual_tail != expected_tail:
                errors.append(
                    "audit_tail_digest mismatch: "
                    f"declared {declared_tail!r} != actual {actual_tail!r}"
                )
        except Exception as exc:
            errors.append(f"audit log verification failed: {exc}")
    return root, errors, audit_path


def _trusted_profile_document(profile: Any) -> dict[str, Any] | None:
    """Return a validated profile document for verifier-only operations."""
    if profile is None:
        return None
    if hasattr(profile, "to_trusted_dict"):
        value = profile.to_trusted_dict()
        return dict(value) if isinstance(value, Mapping) else None
    if isinstance(profile, Mapping):
        return dict(profile)
    return None


def _trusted_profile_digest(profile: Any) -> str:
    """Use the immutable HpcSiteProfile digest, never a receipt-supplied hash."""
    if hasattr(profile, "digest") and getattr(profile, "digest"):
        return str(getattr(profile, "digest"))
    if isinstance(profile, Mapping) and profile.get("digest"):
        return str(profile["digest"])
    from dftworld_bench.experiments.qualification_receipt import canonical_digest

    return canonical_digest(dict(profile)) if isinstance(profile, Mapping) else ""


def verify_site_receipt(
    receipt_data: dict[str, Any],
    *,
    scheduler: str | None = None,
    site_profile: dict[str, Any] | None = None,
    root: Path,
    receipt_dir: Path,
    required_probe_classes: set[str] | None = None,
    trust_store: Any | None = None,
    trusted_site_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scheduler-aware site receipt verification.

    For Slurm sites (``scheduler == "slurm"``), delegates to the full
    ``qualification_receipt.verify_receipt`` which checks SIF runtime locks,
    SiteProfile rebuild from ``cluster_profile.toml``, and required canaries.

    For CompShare sites (``scheduler == "compshare"``), performs verification that checks:
    - Schema against compshare-site-qualification-receipt.schema.json
    - Content-addressed digest
    - Ed25519 signature anchored in QualificationTrustStore (fail-closed if UNCONFIGURED or KEY_MISMATCH)
    - source_commit and code_identity provenance
    - Materialized runtime_lock file existence, non-symlink, digest, and image_id
    - Materialized fetch artifacts existence, non-symlink, sha256, and size_bytes
    - Materialized settlement report existence, non-symlink, digest, and terminal confirmation
    - Image ID consistency across runtime_lock, instance_lifecycle, and jobs
    - Full GatewayAudit hash chain, audit_tail_digest binding, and complete lifecycle event lineage

    Returns the same structure as ``verify_receipt``: ``{"problems": {...}, "derived": {...}}``.
    """
    from dftworld_bench.experiments.qualification_receipt import (
        canonical_digest,
        code_identity,
        sha256_file,
        source_commit,
    )

    # ``site_profile`` is retained for legacy/read-only Slurm callers, but a
    # formal CompShare receipt is never allowed to select its own policy.  The
    # only accepted policy source there is the explicit trusted registry value.
    strict_trusted_profile = trusted_site_profile is not None
    eff_profile = _trusted_profile_document(trusted_site_profile or site_profile)
    if scheduler is None:
        if eff_profile and eff_profile.get("scheduler"):
            scheduler = eff_profile["scheduler"]
        elif receipt_data.get("scheduler"):
            scheduler = receipt_data["scheduler"]
        elif (receipt_data.get("evidence") or {}).get("instance_lifecycle"):
            scheduler = "compshare"
        else:
            scheduler = "slurm"
    if scheduler == "compshare" and not strict_trusted_profile:
        # Do not use an untrusted profile mapping even to determine policy.
        eff_profile = None

    problems: dict[str, list[str]] = {}

    def problem(gate: str, msg: str) -> None:
        problems.setdefault(gate, []).append(msg)

    if scheduler == "compshare" and not strict_trusted_profile:
        problem(
            "provenance",
            "formal CompShare qualification requires an explicit trusted SiteProfile",
        )
    if strict_trusted_profile and eff_profile is None:
        problem("provenance", "trusted SiteProfile is malformed")
    if strict_trusted_profile and eff_profile is not None:
        if receipt_data.get("site_profile_id") != eff_profile.get("site_id"):
            problem(
                "provenance",
                "receipt site_profile_id does not match the trusted SiteProfile",
            )
    if (
        strict_trusted_profile
        and eff_profile is not None
        and scheduler is not None
        and eff_profile.get("scheduler") != scheduler
    ):
        problem(
            "provenance",
            "scheduler does not match the explicitly trusted SiteProfile",
        )

    # The required canary classes are a SiteProfile policy, not something a
    # receipt may infer by listing whichever jobs happened to run.
    if strict_trusted_profile and eff_profile is not None:
        policy = eff_profile.get("qualification_policy") or {}
        policy_classes = policy.get("required_probe_classes")
        if not isinstance(policy_classes, list) or not policy_classes:
            problem(
                "qualification_policy",
                "trusted SiteProfile must declare non-empty qualification_policy.required_probe_classes",
            )
        elif required_probe_classes is None:
            required_probe_classes = set(policy_classes)
        elif set(required_probe_classes) != set(policy_classes):
            problem(
                "qualification_policy",
                "requested probe classes do not equal trusted SiteProfile policy",
            )

    # -- Universal checks (both schedulers) ------------------------------------
    # Content-addressed digest
    declared = receipt_data.get("digest")
    recomputed = canonical_digest(
        {k: v for k, v in receipt_data.items() if k not in ("digest", "signature")}
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
        from dftworld_bench.experiments.qualification_receipt import verify_receipt

        # Determine required probe classes from site_profile or receipt evidence
        if required_probe_classes is None:
            if eff_profile:
                q_pol = eff_profile.get("qualification_policy") or {}
                if "required_probe_classes" in q_pol:
                    required_probe_classes = set(q_pol["required_probe_classes"])
                elif "gpu" not in (eff_profile.get("queues") or {}):
                    required_probe_classes = {"cpu"}
            if required_probe_classes is None:
                evidence_jobs = evidence.get("jobs") or []
                probe_classes_seen = {j.get("probe_class") for j in evidence_jobs}
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
        if problems:
            vr = dict(vr)
            prior = list(vr.get("problems") or [])
            prior.extend(
                f"[{gate}] {message}" for gate, messages in problems.items()
                for message in messages
            )
            vr["problems"] = prior
            derived = dict(vr.get("derived") or {})
            derived["qualification_status"] = "INVALID"
            derived["formal_qualified"] = False
            vr["derived"] = derived
        return vr

    elif scheduler == "compshare":
        # Schema validation against standalone compshare site qualification receipt schema
        site_schema_path = _REPO_ROOT / "schemas" / "compshare-site-qualification-receipt.schema.json"
        if site_schema_path.is_file():
            try:
                site_schema = json.loads(site_schema_path.read_text(encoding="utf-8"))
                jsonschema.validate(instance=receipt_data, schema=site_schema)
            except jsonschema.ValidationError as exc:
                problem("schema", f"CompShare receipt schema validation failed: {exc.message}")

        kind = receipt_data.get("kind")
        if kind not in _VALID_SITE_KINDS:
            problem("schema", f"CompShare receipt missing or invalid kind: {kind!r}")

        # Ed25519 signature verification anchored in QualificationTrustStore (mandatory)
        sig = receipt_data.get("signature")
        if not sig or not isinstance(sig, dict):
            problem("signature", "CompShare receipt missing mandatory signature object")
        else:
            expected_key_id = "compshare-site-v1"
            if eff_profile:
                q_pol = eff_profile.get("qualification_policy") or {}
                if "signing_key_id" in q_pol:
                    expected_key_id = q_pol["signing_key_id"]

            sig_res = verify_receipt_signature_detailed(
                receipt_data,
                expected_key_id=expected_key_id,
                trust_store=trust_store,
            )
            if not sig_res.valid:
                problem("signature", f"CompShare receipt signature invalid: {sig_res.status} ({sig_res.error})")

        # code_identity must be present and non-empty
        if not identity:
            problem("provenance", "CompShare receipt must include code_identity")

        # SiteProfile digest check
        site_digest = receipt_data.get("site_profile_digest")
        if not site_digest or not SHA256_PATTERN.match(site_digest):
            problem("provenance", f"CompShare receipt missing or invalid site_profile_digest: {site_digest!r}")
        if eff_profile is not None:
            expected_sp_digest = _trusted_profile_digest(eff_profile)
            if not expected_sp_digest.startswith("sha256:"):
                expected_sp_digest = f"sha256:{expected_sp_digest}"
            norm_actual_site_digest = site_digest if site_digest and site_digest.startswith("sha256:") else f"sha256:{site_digest}"
            if norm_actual_site_digest != expected_sp_digest:
                problem(
                    "provenance",
                    f"CompShare receipt site_profile_digest mismatch: receipt claims {site_digest} != trusted {expected_sp_digest}",
                )

        # Check canary jobs.  The explicit SiteProfile policy is authoritative
        # when present; never infer required coverage from receipt contents.
        jobs = evidence.get("jobs") or []
        policy_classes = set(required_probe_classes or ())
        if not policy_classes and eff_profile is not None:
            policy_classes = set(
                (eff_profile.get("qualification_policy") or {}).get(
                    "required_probe_classes", []
                )
            )
        if not policy_classes:
            policy_classes = {"gpu"}
        classes_seen = {j.get("probe_class") for j in jobs}
        for missing in sorted(policy_classes - classes_seen):
            problem(
                "canary_coverage",
                f"CompShare receipt missing SiteProfile-required {missing!r} canary job",
            )
        gpu_jobs = [j for j in jobs if j.get("probe_class") == "gpu"]
        for job in jobs:
            if job.get("probe_class") in policy_classes:
                accounting = job.get("accounting") or {}
                state = accounting.get("state", "").upper()
                if state not in ("COMPLETED", "COMPLETING", "SUCCEEDED"):
                    problem("job_status", f"Canary job not in terminal state: {state!r}")
                exit_code = accounting.get("exit_code")
                if exit_code is not None and exit_code != 0:
                    problem("job_status", f"Canary job non-zero exit code: {exit_code}")

        # Materialize and verify runtime_lock
        runtime_lock = receipt_data.get("runtime_lock") or {}
        rl_rel = runtime_lock.get("path")
        if not rl_rel:
            problem("provenance", "CompShare receipt missing runtime_lock.path")
        else:
            try:
                # Formal receipts resolve all materialized evidence from the
                # trusted root.  The legacy receipt-dir branch is retained
                # only for old non-formal callers and can never promote a
                # runtime through TrustedRuntimeCatalog.
                lock_root = root if strict_trusted_profile else receipt_dir
                rl_path = check_evidence_containment(lock_root, rl_rel)

                if not rl_path.is_file():
                    problem("runtime_lock", f"runtime_lock file missing: {rl_rel}")
                elif rl_path.is_symlink():
                    problem("runtime_lock", f"runtime_lock file is a symlink: {rl_rel}")
                else:
                    want_lock_sha = runtime_lock.get("digest")
                    try:
                        lock_doc = json.loads(rl_path.read_text(encoding="utf-8"))
                        from dftworld_bench.hpc.runtime_resolution import canonical_lock_digest

                        canonical_lock_sha = canonical_lock_digest(lock_doc)
                        raw_lock_sha = f"sha256:{hashlib.sha256(rl_path.read_bytes()).hexdigest()}"
                        lock_schema = str(lock_doc.get("schema") or lock_doc.get("schema_id") or "")
                        expected_lock_sha = canonical_lock_sha if "compshare-runtime-lock/v2" in lock_schema else raw_lock_sha
                        if want_lock_sha != expected_lock_sha:
                            problem(
                                "runtime_lock",
                                f"runtime_lock digest mismatch: declared {want_lock_sha} != expected {expected_lock_sha}",
                            )
                        art = lock_doc.get("artifact") or {}
                        doc_img = art.get("image_id") or lock_doc.get("image_id") or (lock_doc.get("runtime") or {}).get("image_id")
                        if doc_img != runtime_lock.get("image_id"):
                            problem(
                                "runtime_lock",
                                f"runtime_lock file image_id mismatch: file has {doc_img!r}, receipt has {runtime_lock.get('image_id')!r}",
                            )

                        # Recipe provenance verification
                        provenance = lock_doc.get("provenance") or {}
                        lock_recipe_digest = provenance.get("recipe_digest")
                        recipe_rel = provenance.get("recipe_path")
                        receipt_rd = runtime_lock.get("recipe_digest")

                        if lock_recipe_digest:
                            if not recipe_rel:
                                problem("recipe_provenance", "runtime_lock has recipe_digest but missing recipe_path")
                            else:
                                recipe_path = root / recipe_rel
                                if not recipe_path.is_file():
                                    problem("recipe_provenance", f"recipe file missing: {recipe_rel}")
                                else:
                                    from scripts.infra.audit_compshare_image_recipe import canonical_recipe_digest
                                    recipe_doc = json.loads(recipe_path.read_text(encoding="utf-8"))
                                    canonical_rd = canonical_recipe_digest(recipe_doc)
                                    if canonical_rd != lock_recipe_digest:
                                        problem(
                                            "recipe_provenance",
                                            f"recipe_digest mismatch: lock claims {lock_recipe_digest} != canonical {canonical_rd}",
                                        )
                                    if receipt_rd and receipt_rd != lock_recipe_digest:
                                        problem(
                                            "recipe_provenance",
                                            f"receipt recipe_digest mismatch: receipt has {receipt_rd} != lock {lock_recipe_digest}",
                                        )

                                    # Build evidence linkage
                                    build_ev_path = root / "base-env-build" / "jax-gpu" / "build_evidence.json" if "jax" in recipe_rel else None
                                    if build_ev_path and build_ev_path.is_file():
                                        try:
                                            build_ev = json.loads(build_ev_path.read_text(encoding="utf-8"))
                                            if build_ev.get("recipe_digest") == lock_recipe_digest:
                                                expected_img = build_ev.get("image_id")
                                                if expected_img and doc_img != expected_img:
                                                    problem(
                                                        "build_lineage",
                                                        f"Image ID {doc_img} does not match build record image_id {expected_img} for recipe {lock_recipe_digest}",
                                                    )
                                        except Exception as b_exc:
                                            problem("build_lineage", f"build_evidence unreadable: {b_exc}")

                                    # Superseded image protection
                                    if doc_img == "compshareImage-1uwv0ijzwej6" and lock_recipe_digest == "sha256:766bacfeb5f1c306181302d8b3c5cb1b6f4f192bee2e4cdfb1427a9d95c7118f":
                                        problem(
                                            "build_lineage",
                                            "Image compshareImage-1uwv0ijzwej6 is a superseded image built from legacy recipe and cannot qualify new recipe",
                                        )

                                    # In-container asset SHA probe verification
                                    recipe_assets = recipe_doc.get("assets") or []
                                    canary_report_path = receipt_dir / "canary_report.json"
                                    if canary_report_path.is_file():
                                        try:
                                            canary_data = json.loads(canary_report_path.read_text(encoding="utf-8"))
                                            probed_assets = canary_data.get("assets") or {}
                                            if "jax" in recipe_rel:
                                                if "jax_md-0.2.29.tar.gz" not in probed_assets:
                                                    problem("asset_provenance", "Canary report missing mandatory probed asset jax_md-0.2.29.tar.gz")
                                            for a_name, a_val in probed_assets.items():
                                                actual_sha = (a_val.get("sha256") if isinstance(a_val, dict) else str(a_val)).removeprefix("sha256:")
                                                expected_sha = next(
                                                    (a["sha256"].removeprefix("sha256:") for a in recipe_assets if a.get("name") == a_name),
                                                    None,
                                                )
                                                if expected_sha and actual_sha != expected_sha:
                                                    problem(
                                                        "asset_provenance",
                                                        f"Asset {a_name} in-image measured SHA ({actual_sha}) != recipe.lock declared SHA ({expected_sha})",
                                                    )
                                        except Exception as c_exc:
                                            problem("asset_provenance", f"Failed to parse canary report for asset validation: {c_exc}")
                    except Exception as exc:
                        problem("runtime_lock", f"runtime_lock JSON parse error: {exc}")
            except ValueError as exc:
                problem("runtime_lock", f"runtime_lock path containment violation: {exc}")
            except Exception as exc:
                problem("runtime_lock", f"runtime_lock verification error: {exc}")

        # Materialize and verify fetch artifacts
        fetch_evidence = evidence.get("fetch") or {}
        artifacts = fetch_evidence.get("artifacts") or []
        if not artifacts:
            problem("fetch", "CompShare receipt must include fetched artifacts")
        else:
            for artifact in artifacts:
                art_rel = artifact.get("path") or artifact.get("name")
                if not art_rel:
                    problem("fetch", "Artifact missing path")
                    continue
                try:
                    art_path = check_evidence_containment(receipt_dir, art_rel)
                    if not art_path.is_file() or art_path.is_symlink():
                        problem("fetch", f"Artifact missing or is symlink: {art_rel}")
                        continue
                    content = art_path.read_bytes()
                    have_sha = f"sha256:{hashlib.sha256(content).hexdigest()}"
                    want_sha = artifact.get("sha256")
                    if want_sha and have_sha != want_sha:
                        problem("fetch", f"Artifact {art_rel} digest mismatch: declared {want_sha} != actual {have_sha}")
                    have_size = len(content)
                    want_size = artifact.get("size_bytes")
                    if want_size is not None and have_size != want_size:
                        problem("fetch", f"Artifact {art_rel} size mismatch: declared {want_size} != actual {have_size}")
                except ValueError as exc:
                    problem("fetch", f"Artifact path escapes receipt dir: {exc}")

        # Materialize and verify settlement report
        settlement = evidence.get("settlement") or {}
        if not settlement.get("terminated"):
            problem("settlement", "CompShare receipt must show terminated settlement")
        rep_rel = settlement.get("report_path")
        if not rep_rel:
            problem("settlement", "CompShare receipt missing settlement.report_path")
        else:
            try:
                rep_path = check_evidence_containment(receipt_dir, rep_rel)
                if not rep_path.is_file() or rep_path.is_symlink():
                    problem("settlement", f"Settlement report file missing or is symlink: {rep_rel}")
                else:
                    rep_bytes = rep_path.read_bytes()
                    have_rep_sha = f"sha256:{hashlib.sha256(rep_bytes).hexdigest()}"
                    want_rep_sha = settlement.get("digest")
                    if want_rep_sha and have_rep_sha != want_rep_sha:
                        problem("settlement", f"Settlement report digest mismatch: declared {want_rep_sha} != actual {have_rep_sha}")
                    try:
                        rep_data = json.loads(rep_bytes.decode("utf-8"))
                        if not rep_data.get("stop_confirmed", False):
                            problem("settlement", "Settlement report shows stop_confirmed is not true")
                        if not rep_data.get("delete_confirmed", False):
                            problem("settlement", "Settlement report shows delete_confirmed is not true")
                        if rep_data.get("orphan_count", 0) != 0:
                            problem("settlement", f"Settlement report shows orphan_count={rep_data.get('orphan_count')}, expected 0")
                    except Exception as exc:
                        problem("settlement", f"Settlement report unreadable JSON: {exc}")
            except ValueError as exc:
                problem("settlement", f"Settlement report path escapes receipt dir: {exc}")

        # Instance lifecycle & Image ID consistency
        instance_evidence = evidence.get("instance_lifecycle") or {}
        if not instance_evidence.get("instance_id"):
            problem("instance_lifecycle", "CompShare receipt must include instance_id")
        if not instance_evidence.get("stop_confirmed"):
            problem("instance_lifecycle", "CompShare receipt missing stop_confirmed")
        if not instance_evidence.get("delete_confirmed"):
            problem("instance_lifecycle", "CompShare receipt missing delete_confirmed")

        lock_image_id = runtime_lock.get("image_id")
        inst_image_id = instance_evidence.get("image_id")
        if lock_image_id and inst_image_id and lock_image_id != inst_image_id:
            problem("runtime_consistency", f"Runtime image_id mismatch: lock has {lock_image_id}, instance has {inst_image_id}")
        for job in gpu_jobs:
            job_image_id = job.get("image_id")
            if lock_image_id and job_image_id and lock_image_id != job_image_id:
                problem("runtime_consistency", f"Runtime image_id mismatch: lock has {lock_image_id}, job has {job_image_id}")

        # Credential isolation
        cred_evidence = evidence.get("credential_isolation") or {}
        if not cred_evidence.get("verified"):
            problem("credential_isolation", "CompShare receipt must show credential isolation verified")

        # Orphan check
        orphan = evidence.get("orphan_check") or {}
        if not isinstance(orphan, dict) or not orphan.get("method"):
            problem("orphan_check", "CompShare receipt must include orphan_check evidence")
        if orphan.get("active_total") is not None and orphan.get("active_total") != 0:
            problem("orphan_check", f"Orphan check shows active_total={orphan.get('active_total')}, expected 0")

        # GatewayAudit hash chain, tail digest binding, and full lifecycle lineage
        audit_rel = receipt_data.get("audit_log") or evidence.get("audit_log")
        audit_tail_digest = receipt_data.get("audit_tail_digest")
        if not audit_rel:
            problem("audit_ledger", "CompShare receipt missing mandatory audit_log")
        else:
            try:
                audit_path = check_evidence_containment(receipt_dir, audit_rel)
                if not audit_path.is_file() or audit_path.is_symlink():
                    problem("audit_ledger", f"CompShare audit log missing or is symlink: {audit_path}")
                else:
                    from dftworld_bench.hpc.audit import GatewayAudit

                    ledger = GatewayAudit(audit_path)
                    broken = ledger.verify()
                    if broken:
                        problem("audit_ledger", f"GatewayAudit hash chain broken at {broken}")
                    actual_tail = ledger.tail_digest()
                    if audit_tail_digest and actual_tail != audit_tail_digest:
                        problem("audit_ledger", f"audit_tail_digest mismatch: receipt declared {audit_tail_digest} != ledger actual {actual_tail}")

                    target_run_id = receipt_data.get("run_id")
                    run_events = [
                        e.get("event", {}) for e in ledger.entries()
                        if isinstance(e.get("event"), dict) and e["event"].get("run_id") == target_run_id
                    ]
                    event_actions = [e.get("kind") or e.get("action") for e in run_events]

                    required_sequence = [
                        "INSTANCE_CREATE_INTENT",
                        "INSTANCE_CREATE_ACCEPTED",
                        "INSTANCE_READY",
                        "INSTANCE_STOP_ACCEPTED",
                        "INSTANCE_DELETE_ACCEPTED",
                        "INSTANCE_DELETE_CONFIRMED",
                        "ZERO_ORPHAN_QUERY",
                        "SETTLEMENT_COMPLETE",
                    ]
                    cur_idx = -1
                    for req in required_sequence:
                        try:
                            found_idx = event_actions.index(req, cur_idx + 1)
                            cur_idx = found_idx
                        except ValueError:
                            problem("audit_ledger", f"Audit log missing required lifecycle event: {req}")

                    expected_inst_id = instance_evidence.get("instance_id")
                    expected_img_id = instance_evidence.get("image_id")
                    for ev in run_events:
                        ev_act = ev.get("kind") or ev.get("action")
                        if ev_act != "INSTANCE_CREATE_INTENT":
                            if expected_inst_id and ev.get("instance_id") and ev.get("instance_id") != expected_inst_id:
                                problem("audit_ledger", f"Audit event {ev_act} has instance_id={ev.get('instance_id')}, expected {expected_inst_id}")
                        if expected_img_id and ev.get("image_id") and ev.get("image_id") != expected_img_id:
                            problem("audit_ledger", f"Audit event {ev_act} has image_id={ev.get('image_id')}, expected {expected_img_id}")

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
    trust_store: Any | None = None,
    trusted_site_profiles: Mapping[str, Any] | None = None,
    receipt_dir: Path | None = None,
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
    - SiteProfile digest mismatch against trusted registry / configuration.
    """
    kind = receipt_doc.get("kind")
    is_v2 = kind == COMPUTE_PROFILE_V2_KIND
    schema_path = _SCHEMA_V2_PATH if is_v2 else _SCHEMA_PATH
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
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

    # Bind the receipt to an explicitly supplied ComputeProfile before the
    # legacy read-only return as well.  This keeps migration diagnostics useful
    # without granting v1 any eligibility authority.
    if expected_profile is not None:
        pid = receipt_doc.get("compute_profile_id")
        if pid != expected_profile.profile_id:
            raise ComputeProfileQualificationError(
                f"Receipt compute profile ID mismatch: receipt has {pid!r}, expected {expected_profile.profile_id!r}"
            )
        exp_digest = getattr(expected_profile, "digest", "")
        rcpt_prof_digest = str(receipt_doc.get("compute_profile_digest", "")).replace("sha256:", "")
        exp_prof_digest = str(exp_digest).replace("sha256:", "")
        if rcpt_prof_digest != exp_prof_digest:
            raise ComputeProfileQualificationError(
                f"Receipt compute profile digest mismatch: receipt has {rcpt_prof_digest!r}, expected {exp_prof_digest!r}"
            )
        if receipt_doc.get("routes") != getattr(expected_profile, "routes", {}):
            raise ComputeProfileQualificationError(
                f"Receipt routes {receipt_doc.get('routes')} do not match expected profile routes {getattr(expected_profile, 'routes', {})}"
            )

    # v1 remains readable for migration and incident review, but is never an
    # eligibility attestation.  Do this before any policy/evidence resolution
    # so a legacy receipt cannot gain authority from a newly supplied trust
    # store or SiteProfile registry.
    if not is_v2:
        legacy_errors = [
            "LEGACY_NOT_ELIGIBLE: compute-profile qualification receipt v1 is read-only; "
            "materialize and sign a v2 receipt before qualification"
        ]
        legacy_evidence = receipt_doc.get("cloud_recycling_evidence") or {}
        return ComputeProfileQualificationVerdict(
            passed=False,
            compute_profile_id=str(receipt_doc.get("compute_profile_id") or ""),
            routes_valid=False,
            cpu_site_qualified=False,
            gpu_site_qualified=False,
            cloud_recycling_passed=False,
            active_instances_count=int(legacy_evidence.get("active_instances_count") or 0),
            orphan_instances_count=int(legacy_evidence.get("orphan_instances_count") or 0),
            errors=legacy_errors,
            status="LEGACY_NOT_ELIGIBLE",
        )

    errors: list[str] = []
    pid = receipt_doc["compute_profile_id"]
    routes = receipt_doc["routes"]
    site_receipts = receipt_doc.get("site_receipts", {})

    # A formal Layer-2 receipt cannot discover trust anchors from repository
    # defaults, examples, or the receipt itself.  Keep these checks explicit
    # even when later verification would fail for another reason, so callers
    # receive a stable reason rather than an accidental default-store result.
    if trust_store is None:
        errors.append(
            "formal v2 ComputeProfile qualification requires an explicit trust_store"
        )
    if trusted_site_profiles is None:
        errors.append(
            "formal v2 ComputeProfile qualification requires an explicit trusted SiteProfile registry"
        )

    routes_valid = "cpu" in routes and "gpu" in routes
    if not routes_valid:
        errors.append("Routes missing cpu or gpu mapping")

    cpu_site = routes.get("cpu", "")
    gpu_site = routes.get("gpu", "")

    # Site receipts are meaningful only against an operator-supplied registry.
    # Do not search examples or the receipt directory for a profile: those
    # locations are evidence, not trust anchors.
    registry: Mapping[str, Any] = trusted_site_profiles or {}
    if hasattr(registry, "as_mapping"):
        registry = registry.as_mapping()  # type: ignore[assignment]
    # Normalize explicit registry entries through HpcSiteProfile so a raw
    # evidence mapping cannot become a trusted policy merely by its name.
    normalized_registry: dict[str, Any] = {}
    from dftworld_bench.hpc.site_profile import HpcSiteProfile

    for registry_name, registry_profile in registry.items():
        try:
            if isinstance(registry_profile, HpcSiteProfile):
                profile_obj = registry_profile
            elif isinstance(registry_profile, Mapping):
                profile_raw = dict(registry_profile)
                profile_raw.pop("digest", None)
                profile_obj = HpcSiteProfile.from_dict(profile_raw)
            else:
                raise TypeError(type(registry_profile).__name__)
            if profile_obj.site_id != registry_name:
                raise ValueError(
                    f"registry key {registry_name!r} != site_id {profile_obj.site_id!r}"
                )
            normalized_registry[registry_name] = profile_obj
        except Exception as exc:
            errors.append(
                f"Trusted SiteProfile {registry_name!r} is invalid: {exc}"
            )
    registry = normalized_registry
    if not registry:
        errors.append(
            "Hybrid compute profile qualification requires an explicit trusted SiteProfile registry"
        )

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
                r_data = json.loads(raw_bytes.decode("utf-8"))
                r_canonical_sha = compute_receipt_digest(r_data)

                # A receipt reference is a canonical digest, never a digest of
                # a mutable transport copy of the file.
                if s_digest and s_digest != r_canonical_sha:
                    errors.append(
                        f"Site receipt for {s_name} digest mismatch: recorded {s_digest} != computed {r_canonical_sha}"
                    )
                    if s_name == cpu_site:
                        cpu_qualified = False
                    if s_name == gpu_site:
                        gpu_qualified = False

                site_receipt_dir = r_file.parent
                root = site_receipt_dir.parent.parent
                if not (root / "dftworld_bench").is_dir():
                    root = Path(__file__).resolve().parents[2]

                site_profile = registry.get(s_name)
                trusted_profile_doc = _trusted_profile_document(site_profile)
                if site_profile is None:
                    errors.append(
                        f"SiteProfile {s_name!r} is not present in the explicit trusted registry"
                    )
                    if s_name == cpu_site:
                        cpu_qualified = False
                    if s_name == gpu_site:
                        gpu_qualified = False
                    continue

                if "site_profile_digest" in r_data:
                    exp_sp_sha = _trusted_profile_digest(site_profile)
                    if not exp_sp_sha.startswith("sha256:"):
                        exp_sp_sha = f"sha256:{exp_sp_sha}"
                    rec_sp_sha = str(r_data.get("site_profile_digest") or "")
                    if not rec_sp_sha.startswith("sha256:"):
                        rec_sp_sha = f"sha256:{rec_sp_sha}"
                    if rec_sp_sha != exp_sp_sha:
                        errors.append(
                            f"Site receipt for {s_name} site_profile_digest mismatch: receipt claims {rec_sp_sha} != trusted {exp_sp_sha}"
                        )
                        if s_name == cpu_site:
                            cpu_qualified = False
                        if s_name == gpu_site:
                            gpu_qualified = False
                        continue

                scheduler = None
                required_probe_classes = None
                if site_profile:
                    scheduler = trusted_profile_doc.get("scheduler") if trusted_profile_doc else None
                    q_pol = (trusted_profile_doc or {}).get("qualification_policy") or {}
                    if "required_probe_classes" in q_pol:
                        required_probe_classes = set(q_pol["required_probe_classes"])

                vr = verify_site_receipt(
                    r_data,
                    scheduler=scheduler,
                    site_profile=trusted_profile_doc,
                    root=root,
                    receipt_dir=site_receipt_dir,
                    required_probe_classes=required_probe_classes,
                    trust_store=trust_store,
                    trusted_site_profile=trusted_profile_doc,
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

    # Layer-2 v2 signature verification is mandatory and must resolve an
    # independently configured compute-profile key with the dedicated purpose.
    # Never let verify_receipt_signature load its legacy default store here.
    if trust_store is None:
        sig_res = SignatureVerificationResult(
            valid=False,
            status="TRUST_STORE_REQUIRED",
            error="no explicit trust store was supplied",
            key_id=COMPUTE_PROFILE_SIGNING_KEY_ID,
        )
    else:
        sig_res = verify_receipt_signature_detailed(
            receipt_doc,
            expected_key_id=COMPUTE_PROFILE_SIGNING_KEY_ID,
            expected_purpose=COMPUTE_PROFILE_SIGNING_PURPOSE,
            trust_store=trust_store,
        )
    if not sig_res.valid:
        recycling_passed = False
        errors.append(
            "ComputeProfile v2 receipt signature verification failed: "
            f"{sig_res.status} ({sig_res.error})"
        )

    # Every v2 evidence record is a materialized, structured binding.  This
    # single verifier covers path containment, symlink/type checks, byte hash,
    # exact size, audit chain and audit_tail_digest.
    _evidence_root, evidence_errors, _audit_path = _verify_materialized_evidence_files(
        receipt_doc,
        receipt_dir=receipt_dir,
        fallback_root=site_receipts_dir,
    )
    if evidence_errors:
        recycling_passed = False
        errors.extend(evidence_errors)

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
        status="PASS" if overall_pass else "INVALID",
    )


def sign_compute_profile_receipt(
    doc: dict[str, Any],
    private_key_hex: str,
    *,
    key_id: str = COMPUTE_PROFILE_SIGNING_KEY_ID,
) -> dict[str, Any]:
    """Sign a Layer-2 receipt with the dedicated compute-profile purpose."""
    if not private_key_hex:
        raise ComputeProfileQualificationError(
            "v2 ComputeProfile receipts require a private signing key"
        )
    if key_id != COMPUTE_PROFILE_SIGNING_KEY_ID:
        raise ComputeProfileQualificationError(
            "v2 ComputeProfile receipts require the dedicated compute-profile signing key"
        )
    return sign_receipt(
        doc,
        private_key_hex,
        key_id=key_id,
        purpose=COMPUTE_PROFILE_SIGNING_PURPOSE,
    )


def build_compute_profile_qualification_receipt_v2(
    *,
    compute_profile_id: str,
    compute_profile_digest: str,
    routes: dict[str, str],
    site_receipts: dict[str, str],
    cloud_recycling_evidence: dict[str, Any],
    evidence_root: str,
    evidence_files: list[Mapping[str, Any]],
    audit_log: str,
    audit_tail_digest: str,
    private_key_hex: str,
    key_id: str = COMPUTE_PROFILE_SIGNING_KEY_ID,
) -> dict[str, Any]:
    """Build a formal v2 ComputeProfile receipt.

    The formal builder has no optional evidence/signature arguments.  It
    accepts only structured evidence records because a string path without a
    measured digest and size is not an auditable attestation.
    """
    if not evidence_root or not audit_log or not audit_tail_digest:
        raise ComputeProfileQualificationError(
            "v2 receipt requires evidence_root, audit_log, and audit_tail_digest"
        )
    if not evidence_files:
        raise ComputeProfileQualificationError(
            "v2 receipt requires non-empty structured evidence_files"
        )
    if not private_key_hex:
        raise ComputeProfileQualificationError(
            "v2 receipt requires private_key_hex for its mandatory signature"
        )
    records = [dict(record) for record in evidence_files]
    for index, record in enumerate(records):
        required = {"path", "sha256", "size", "role"}
        missing = sorted(required - set(record))
        if missing:
            raise ComputeProfileQualificationError(
                f"evidence_files[{index}] missing required fields: {missing}"
            )
        if set(record) != required:
            extra = sorted(set(record) - required)
            raise ComputeProfileQualificationError(
                f"evidence_files[{index}] has unsupported fields: {extra}"
            )
        if not isinstance(record["path"], str) or not record["path"]:
            raise ComputeProfileQualificationError(
                f"evidence_files[{index}].path must be non-empty"
            )
        if not isinstance(record["size"], int) or isinstance(record["size"], bool) or record["size"] < 0:
            raise ComputeProfileQualificationError(
                f"evidence_files[{index}].size must be a non-negative integer"
            )
        if not isinstance(record["role"], str) or not record["role"]:
            raise ComputeProfileQualificationError(
                f"evidence_files[{index}].role must be non-empty"
            )
        if not isinstance(record["sha256"], str) or not SHA256_PATTERN.match(record["sha256"]):
            raise ComputeProfileQualificationError(
                f"evidence_files[{index}].sha256 must be a sha256 digest"
            )

    doc: dict[str, Any] = {
        "kind": COMPUTE_PROFILE_V2_KIND,
        "schema_id": COMPUTE_PROFILE_V2_SCHEMA_ID,
        "compute_profile_id": compute_profile_id,
        "compute_profile_digest": compute_profile_digest,
        "routes": dict(routes),
        "site_receipts": dict(site_receipts),
        "cloud_recycling_evidence": dict(cloud_recycling_evidence),
        "evidence_root": evidence_root,
        "evidence_files": records,
        "audit_log": audit_log,
        "audit_tail_digest": audit_tail_digest,
    }
    doc["digest"] = compute_receipt_digest(doc)
    doc["signature"] = sign_compute_profile_receipt(
        doc, private_key_hex, key_id=key_id
    )
    try:
        schema = json.loads(_SCHEMA_V2_PATH.read_text(encoding="utf-8"))
        jsonschema.validate(instance=doc, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ComputeProfileQualificationError(
            f"built v2 receipt failed schema validation: {exc.message}"
        ) from exc
    return doc


def build_compute_profile_qualification_receipt(
    *,
    compute_profile_id: str,
    compute_profile_digest: str,
    routes: dict[str, str],
    site_receipts: dict[str, str],
    cloud_recycling_evidence: dict[str, Any],
    evidence_root: str | None = None,
    evidence_files: list[Any] | None = None,
    audit_log: str | None = None,
    audit_tail_digest: str | None = None,
    private_key_hex: str | None = None,
    key_id: str = COMPUTE_PROFILE_SIGNING_KEY_ID,
) -> dict[str, Any]:
    """Compatibility façade for receipt construction.

    Supplying the complete evidence/signature set dispatches to the strict v2
    builder.  The argument-minimal form is retained solely to let migration
    tooling write/read v1 material; such output is always rejected as
    ``LEGACY_NOT_ELIGIBLE`` by :func:`verify_and_derive_qualification`.
    New callers should use :func:`build_compute_profile_qualification_receipt_v2`
    so missing formal arguments fail immediately.
    """
    formal_values = (evidence_root, evidence_files, audit_log, audit_tail_digest, private_key_hex)
    if all(value is not None for value in formal_values):
        return build_compute_profile_qualification_receipt_v2(
            compute_profile_id=compute_profile_id,
            compute_profile_digest=compute_profile_digest,
            routes=routes,
            site_receipts=site_receipts,
            cloud_recycling_evidence=cloud_recycling_evidence,
            evidence_root=str(evidence_root),
            evidence_files=list(evidence_files or []),
            audit_log=str(audit_log),
            audit_tail_digest=str(audit_tail_digest),
            private_key_hex=str(private_key_hex),
            key_id=key_id,
        )
    # Partial structured-v2 arguments are almost certainly a caller mistake.
    # Preserve only the historical v1 signing shape and string-list migration
    # shape; neither can be used for a PASS verdict.
    if any(value is not None for value in formal_values) and not (
        (
            evidence_files is None
            and evidence_root is None
            and audit_log is None
            and audit_tail_digest is None
            and private_key_hex is not None
        )
        or (
            evidence_files is not None
            and all(isinstance(item, str) for item in evidence_files)
            and audit_tail_digest is None
        )
    ):
        raise ComputeProfileQualificationError(
            "formal v2 receipt arguments are all required: evidence_root, "
            "evidence_files, audit_log, audit_tail_digest, private_key_hex"
        )

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
        doc["signature"] = sign_receipt(doc, private_key_hex, key_id="compshare-site-v1")
    return doc


def build_compshare_site_qualification_receipt(
    *,
    run_id: str,
    site_profile_id: str,
    site_profile_digest: str,
    source_commit: str,
    code_identity: dict[str, str],
    runtime_lock: dict[str, str],
    evidence: dict[str, Any],
    audit_log: str,
    audit_tail_digest: str,
    private_key_hex: str | None = None,
    key_id: str = "compshare-site-v1",
) -> dict[str, Any]:
    """Helper to construct a fully valid and sealed CompShare site qualification receipt."""
    from dftworld_bench.experiments.qualification_receipt import canonical_digest

    doc: dict[str, Any] = {
        "schema_id": "https://mlip-bench.example/schemas/compshare-site-qualification-receipt.schema.json",
        "kind": "compshare-site-qualification/v1",
        "run_id": run_id,
        "site_profile_id": site_profile_id,
        "site_profile_digest": site_profile_digest,
        "source_commit": source_commit,
        "code_identity": dict(code_identity),
        "runtime_lock": dict(runtime_lock),
        "evidence": dict(evidence),
        "audit_log": audit_log,
        "audit_tail_digest": audit_tail_digest,
    }
    from dftworld_bench.experiments.qualification_receipt import canonical_digest

    doc["digest"] = canonical_digest({k: v for k, v in doc.items() if k not in ("digest", "signature")})

    if private_key_hex is not None:
        doc["signature"] = sign_receipt(doc, private_key_hex, key_id=key_id)

    return doc
