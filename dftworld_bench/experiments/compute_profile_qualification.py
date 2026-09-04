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

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = (
    _REPO_ROOT
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


def sign_receipt(
    doc: dict[str, Any],
    private_key_hex: str,
    *,
    key_id: str = "compshare-site-v1",
) -> dict[str, Any]:
    """Sign receipt dict with Ed25519 private key; returns the signature dict."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    body = {k: v for k, v in doc.items() if k != "signature"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    priv = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    sig = priv.sign(canon)
    pub_hex = priv.public_key().public_bytes_raw().hex()
    return {
        "algorithm": "ed25519",
        "key_id": key_id,
        "public_key": pub_hex,
        "signature_hex": sig.hex(),
    }


def verify_ed25519_signature_bytes(
    document: dict[str, Any] | bytes,
    public_key_hex: str,
    signature_hex: str | None = None,
) -> bool:
    """Low-level Ed25519 raw signature verification over canonical document bytes."""
    from cryptography.hazmat.primitives.asymmetric import ed25519

    if isinstance(document, bytes):
        canon = document
    else:
        body = {k: v for k, v in document.items() if k != "signature"}
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

    if not verify_ed25519_signature_bytes(doc, trusted_pub_hex, sig_hex):
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
    trust_store: Any = None,
) -> SignatureVerificationResult:
    """Convenience wrapper returning SignatureVerificationResult."""
    result = verify_receipt_signature(
        doc,
        expected_public_key_hex=expected_public_key_hex,
        expected_key_id=expected_key_id,
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

    eff_profile = trusted_site_profile or site_profile
    if scheduler is None:
        if eff_profile and eff_profile.get("scheduler"):
            scheduler = eff_profile["scheduler"]
        elif receipt_data.get("scheduler"):
            scheduler = receipt_data["scheduler"]
        elif (receipt_data.get("evidence") or {}).get("instance_lifecycle"):
            scheduler = "compshare"
        else:
            scheduler = "slurm"

    problems: dict[str, list[str]] = {}

    def problem(gate: str, msg: str) -> None:
        problems.setdefault(gate, []).append(msg)

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
            expected_sp_digest = canonical_digest(eff_profile)
            if not expected_sp_digest.startswith("sha256:"):
                expected_sp_digest = f"sha256:{expected_sp_digest}"
            norm_actual_site_digest = site_digest if site_digest and site_digest.startswith("sha256:") else f"sha256:{site_digest}"
            if norm_actual_site_digest != expected_sp_digest:
                problem(
                    "provenance",
                    f"CompShare receipt site_profile_digest mismatch: receipt claims {site_digest} != trusted {expected_sp_digest}",
                )

        # Check canary jobs
        jobs = evidence.get("jobs") or []
        gpu_jobs = [j for j in jobs if j.get("probe_class") == "gpu"]
        if not gpu_jobs:
            problem("canary_coverage", "CompShare receipt must include at least one GPU canary job")
        else:
            for job in gpu_jobs:
                accounting = job.get("accounting") or {}
                state = accounting.get("state", "").upper()
                if state not in ("COMPLETED", "COMPLETING", "SUCCEEDED"):
                    problem("job_status", f"GPU canary job not in terminal state: {state!r}")
                exit_code = accounting.get("exit_code")
                if exit_code is not None and exit_code != 0:
                    problem("job_status", f"GPU canary job non-zero exit code: {exit_code}")

        # Materialize and verify runtime_lock
        runtime_lock = receipt_data.get("runtime_lock") or {}
        rl_rel = runtime_lock.get("path")
        if not rl_rel:
            problem("provenance", "CompShare receipt missing runtime_lock.path")
        else:
            try:
                if (root / rl_rel).exists():
                    rl_path = check_evidence_containment(root, rl_rel)
                else:
                    rl_path = check_evidence_containment(receipt_dir, rl_rel)

                if not rl_path.is_file():
                    problem("runtime_lock", f"runtime_lock file missing: {rl_rel}")
                elif rl_path.is_symlink():
                    problem("runtime_lock", f"runtime_lock file is a symlink: {rl_rel}")
                else:
                    have_lock_sha = f"sha256:{hashlib.sha256(rl_path.read_bytes()).hexdigest()}"
                    want_lock_sha = runtime_lock.get("digest")
                    if want_lock_sha and have_lock_sha != want_lock_sha:
                        problem("runtime_lock", f"runtime_lock digest mismatch: declared {want_lock_sha} != actual {have_lock_sha}")
                    try:
                        lock_doc = json.loads(rl_path.read_text(encoding="utf-8"))
                        art = lock_doc.get("artifact") or {}
                        doc_img = art.get("image_id") or lock_doc.get("image_id") or (lock_doc.get("runtime") or {}).get("image_id")
                        if doc_img != runtime_lock.get("image_id"):
                            problem(
                                "runtime_lock",
                                f"runtime_lock file image_id mismatch: file has {doc_img!r}, receipt has {runtime_lock.get('image_id')!r}",
                            )
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

                site_receipt_dir = r_file.parent
                root = site_receipt_dir.parent.parent
                if not (root / "dftworld_bench").is_dir():
                    root = Path(__file__).resolve().parents[2]

                # R6: Load SiteProfile strictly from trusted registry / examples;
                # NEVER load untrusted site profiles from site_receipt_dir!
                site_profile = None
                if trusted_site_profiles and s_name in trusted_site_profiles:
                    site_profile = trusted_site_profiles[s_name]
                else:
                    repo_example = Path(__file__).resolve().parents[2] / "examples" / "hpc" / f"{s_name}-site-profile.json"
                    if repo_example.is_file():
                        try:
                            site_profile = json.loads(repo_example.read_text(encoding="utf-8"))
                        except Exception:
                            pass

                if site_profile is not None and "site_profile_digest" in r_data:
                    from dftworld_bench.experiments.qualification_receipt import canonical_digest
                    exp_sp_sha = canonical_digest(site_profile)
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
                    scheduler = site_profile.get("scheduler")
                    q_pol = site_profile.get("qualification_policy") or {}
                    if "required_probe_classes" in q_pol:
                        required_probe_classes = set(q_pol["required_probe_classes"])

                vr = verify_site_receipt(
                    r_data,
                    scheduler=scheduler,
                    site_profile=site_profile,
                    root=root,
                    receipt_dir=site_receipt_dir,
                    required_probe_classes=required_probe_classes,
                    trust_store=trust_store,
                    trusted_site_profile=site_profile,
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

    # Ed25519 signature verification if present
    if "signature" in receipt_doc:
        sig_ok = verify_receipt_signature(
            receipt_doc,
            trust_store=trust_store,
        )
        if not sig_ok:
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
