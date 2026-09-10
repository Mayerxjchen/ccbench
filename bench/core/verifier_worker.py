"""Two-stage, private verifier worker boundary.

The Candidate never calls this module.  A coordinator gives the worker a
sealed submission and the case's private verifier bundle, both read-only;
the worker has no network, Docker socket, Candidate workspace, or secret
environment.  The command builder is deliberately pure so the isolation
contract can be tested without Docker.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric import ed25519
from bench.contracts.result import BenchmarkResult
from bench.core.digests import sha256_file
from bench.core.verifier import VerifierSpec, build_verifier_command, run_verifier


class VerifierWorkerError(ValueError):
    """The private verifier worker admission or receipt was unsafe."""


MAX_ADMISSION_BYTES = 256 * 1024


def _read_json_object(path: Path, *, label: str, max_bytes: int) -> dict[str, Any]:
    """Read a coordinator file without following symlinks or special files."""
    try:
        node = os.lstat(path)
        if not os.path.isfile(path) or node.st_nlink != 1 or node.st_size > max_bytes:
            raise OSError(f"{label} is not a safe regular file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            payload = json.loads(stream.read(max_bytes + 1))
    except (OSError, ValueError, TypeError) as exc:
        raise VerifierWorkerError(f"invalid {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise VerifierWorkerError(f"{label} must be an object")
    return payload


@dataclass(frozen=True)
class VerifierWorkerSpec:
    case_id: str
    verifier_profile: str
    verifier_profile_digest: str
    image: str
    image_digest: str
    submission: Path
    verifier_bundle: Path
    logs: Path
    timeout_sec: float
    run_id: str = ""
    attempt: int = 1
    case_version: str = ""
    public_digest: str = ""
    verifier_bundle_digest: str = ""
    reference_digest: str = ""
    solution_digest: str = ""
    reference: Path | None = None
    solution: Path | None = None
    # A candidate image is never a valid verifier image.  Keeping this in the
    # spec makes accidental image reuse fail closed in callers and tests.
    candidate_image: str | None = None
    candidate_image_digest: str | None = None
    candidate_qualification_receipt_digest: str | None = None
    sidecar_image: str | None = None
    sidecar_image_digest: str | None = None
    sidecar_qualification_receipt_digest: str | None = None
    formal_admission_digest: str | None = None
    # Runtime resource policy is part of the qualified verifier profile.  Keep
    # it in the worker identity/receipt so a larger tmpfs cannot be swapped in
    # without changing the profile digest.
    tmpfs: str = "/tmp:rw,noexec,nosuid,size=64m"
    cpus: float = 4.0
    memory: str = "8g"
    pids_limit: int = 512


def _regular_dir(path: Path, label: str) -> Path:
    raw = Path(path).expanduser()
    if raw.is_symlink():
        raise VerifierWorkerError(f"{label} must be a real directory")
    resolved = raw.resolve()
    if not resolved.is_dir():
        raise VerifierWorkerError(f"{label} must be a real directory")
    for child in resolved.rglob("*"):
        if child.is_symlink():
            raise VerifierWorkerError(f"{label} contains an unsafe symlink")
    return resolved


def _immutable_image(spec: VerifierWorkerSpec) -> str:
    if not spec.image or not re.fullmatch(r"sha256:[0-9a-f]{64}", spec.image_digest):
        raise VerifierWorkerError("verifier image must have an immutable sha256 digest")
    # Docker's local image ID is already an immutable runnable reference.  It
    # must not be treated as a repository name and suffixed with another
    # digest (``sha256:id@sha256:id``).  Registry references, by contrast,
    # are pinned as ``repo@sha256:digest``.
    if spec.image.startswith("sha256:"):
        if spec.image != spec.image_digest:
            raise VerifierWorkerError("local verifier image ID does not match its qualified digest")
        return spec.image
    if "@sha256:" in spec.image and spec.image.rsplit("@", 1)[-1] != spec.image_digest:
        raise VerifierWorkerError("verifier image digest does not match its qualified digest")
    if spec.candidate_image and spec.image == spec.candidate_image:
        raise VerifierWorkerError("verifier image must be independent from Candidate image")
    if (
        spec.candidate_image_digest
        and spec.image_digest == spec.candidate_image_digest
    ):
        raise VerifierWorkerError(
            "verifier image digest must be independent from Candidate image digest"
        )
    # Docker accepts image@sha256:digest and will not silently resolve a tag
    # that moved after qualification.
    return spec.image if "@sha256:" in spec.image else f"{spec.image}@{spec.image_digest}"


def build_verifier_worker_command(spec: VerifierWorkerSpec) -> list[str]:
    """Build a networkless, read-only Docker command for one case."""
    submission = _regular_dir(spec.submission, "sealed submission")
    bundle = _regular_dir(spec.verifier_bundle, "verifier bundle")
    reference = _regular_dir(spec.reference, "verifier reference") if spec.reference is not None else None
    solution = _regular_dir(spec.solution, "verifier solution") if spec.solution is not None else None
    logs = Path(spec.logs).expanduser().resolve()
    if logs.exists():
        raise VerifierWorkerError("verifier logs must be a fresh path")
    if not spec.case_id or not spec.verifier_profile or not spec.run_id or not spec.case_version:
        raise VerifierWorkerError("case, run, and case version are required")
    if spec.attempt < 1 or not spec.public_digest or not spec.verifier_bundle_digest:
        raise VerifierWorkerError("run attempt and content digests are required")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", spec.verifier_profile_digest):
        raise VerifierWorkerError("verifier profile must be content-addressed")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", spec.verifier_bundle_digest):
        raise VerifierWorkerError("verifier bundle must be content-addressed")
    if spec.reference is not None and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", spec.reference_digest
    ):
        raise VerifierWorkerError("verifier reference must be content-addressed")
    if spec.solution is not None and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", spec.solution_digest
    ):
        raise VerifierWorkerError("verifier solution must be content-addressed")
    if spec.timeout_sec <= 0:
        raise VerifierWorkerError("verifier timeout must be positive")
    if not isinstance(spec.tmpfs, str) or not re.fullmatch(
        r"/tmp:rw,noexec,nosuid,size=[1-9][0-9]*(?:[kKmMgG])", spec.tmpfs
    ):
        raise VerifierWorkerError("verifier tmpfs policy is invalid")
    if spec.cpus <= 0 or not isinstance(spec.memory, str) or not spec.memory or spec.pids_limit < 1:
        raise VerifierWorkerError("verifier resource policy is invalid")
    command = build_verifier_command(
        VerifierSpec(
            image=_immutable_image(spec),
            timeout_sec=spec.timeout_sec,
            env={},
            tests_dir=bundle,
            reference_dir=reference,
            solution_dir=solution,
            tmpfs=spec.tmpfs,
            cpus=spec.cpus,
            memory=spec.memory,
            pids_limit=spec.pids_limit,
            container_name=f"bench-verifier-{spec.run_id}-{spec.attempt}",
        ),
        submission,
        logs,
    )
    joined = "\0".join(command)
    required = ("--network\0none", "--read-only", "/submission:ro", "/tests:ro")
    if any(token not in joined for token in required):
        raise VerifierWorkerError("verifier command lost required isolation flags")
    if "/var/run/docker.sock" in joined or "ANTHROPIC_API_KEY" in joined:
        raise VerifierWorkerError("verifier command contains forbidden host access")
    return command


def _submission_digest(submission: Path) -> str:
    manifest = submission / "manifest.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise VerifierWorkerError("sealed submission manifest is missing or unsafe")
    return sha256_file(manifest)


def directory_digest(path: Path) -> str:
    """Content digest for a private verifier bundle (paths and bytes only)."""
    root = _regular_dir(path, "verifier bundle")
    records: list[str] = []
    for child in sorted(root.rglob("*")):
        if child.is_file():
            rel = child.relative_to(root).as_posix()
            records.append(f"{rel}\0{child.stat().st_size}\0{sha256_file(child)}\n")
    return _digest_payload({"files": records})


def _digest_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _signature_payload(receipt: dict[str, Any]) -> bytes:
    signed = {key: value for key, value in receipt.items() if key not in {"signature", "receipt_digest"}}
    return json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()


def verify_formal_admission(
    path: Path, *, trusted_public_key_hex: str, expected: dict[str, Any],
    trusted_key_id: str = "coordinator-v1",
) -> str:
    """Verify a coordinator-signed admission before a run is called FORMAL."""
    payload = _read_json_object(Path(path), label="formal admission", max_bytes=MAX_ADMISSION_BYTES)
    signature = payload.get("signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != "ed25519" or signature.get("key_id") != trusted_key_id:
        raise VerifierWorkerError("formal admission key is not pinned")
    body = {key: value for key, value in payload.items() if key != "signature"}
    if any(body.get(key) != value for key, value in expected.items()):
        raise VerifierWorkerError("formal admission binding mismatch")
    for field in ("issued_at", "expires_at", "nonce"):
        if not isinstance(body.get(field), str) or not body[field]:
            raise VerifierWorkerError(f"formal admission missing {field}")
    try:
        issued = datetime.fromisoformat(body["issued_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
    except ValueError as exc:
        raise VerifierWorkerError("formal admission timestamps are invalid") from exc
    resume_until_raw = body.get("resume_until")
    if resume_until_raw is not None:
        if not isinstance(resume_until_raw, str) or not resume_until_raw:
            raise VerifierWorkerError("formal admission resume_until is invalid")
        try:
            resume_until = datetime.fromisoformat(resume_until_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise VerifierWorkerError("formal admission resume_until is invalid") from exc
        # The coordinator must make the extended horizon explicit and sign it;
        # the runner never extends an admission on its own.
        if issued > now or expires <= issued or resume_until <= now or resume_until < expires:
            raise VerifierWorkerError("formal admission is expired or not yet valid")
    elif issued > now or expires <= now or expires <= issued:
        raise VerifierWorkerError("formal admission is expired or not yet valid")
    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(trusted_public_key_hex))
        public_key.verify(bytes.fromhex(str(signature.get("signature_hex", ""))), _signature_payload(body))
    except Exception as exc:
        raise VerifierWorkerError("formal admission signature is not trusted") from exc
    return _digest_payload(payload)


def make_verifier_receipt(
    spec: VerifierWorkerSpec, result: BenchmarkResult | dict[str, Any],
    *, signing_key_hex: str, key_id: str = "verifier-coordinator-v1",
) -> dict[str, Any]:
    """Create a content-bound receipt without exposing private verifier data."""
    result_payload = result.to_dict() if isinstance(result, BenchmarkResult) else dict(result)
    if not spec.run_id or not spec.case_version or not spec.public_digest or not spec.verifier_bundle_digest:
        raise VerifierWorkerError("receipt requires run, case version, public and verifier bundle digests")
    if spec.attempt < 1:
        raise VerifierWorkerError("receipt attempt must be positive")
    try:
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signing_key_hex))
    except Exception as exc:
        raise VerifierWorkerError("invalid verifier signing key") from exc
    result_digest = _digest_payload(result_payload)
    actual_bundle_digest = directory_digest(Path(spec.verifier_bundle).expanduser().resolve())
    if actual_bundle_digest != spec.verifier_bundle_digest:
        raise VerifierWorkerError("verifier bundle changed before receipt")
    for path_key, digest_key in (
        ("reference", "reference_digest"),
        ("solution", "solution_digest"),
    ):
        path = getattr(spec, path_key)
        expected_digest = getattr(spec, digest_key)
        if path is not None and directory_digest(path) != expected_digest:
            raise VerifierWorkerError(f"verifier {path_key} changed before receipt")
    receipt = {
        "schema_version": "1",
        "run_id": spec.run_id,
        "attempt": spec.attempt,
        "case_id": spec.case_id,
        "case_version": spec.case_version,
        "public_digest": spec.public_digest,
        "verifier_profile": spec.verifier_profile,
        "verifier_profile_digest": spec.verifier_profile_digest,
        "verifier_bundle_digest": spec.verifier_bundle_digest,
        "verifier_image": spec.image,
        "verifier_image_digest": spec.image_digest,
        "submission_manifest_digest": _submission_digest(Path(spec.submission).expanduser().resolve()),
        "result_digest": result_digest,
        "result": result_payload,
    }
    # Bind the verifier result to the admitted Candidate/sidecar route as
    # well as to the private verifier.  These fields are optional for legacy
    # external runs, but are mandatory whenever the caller supplies them for
    # Formal admission.
    for key in (
        "candidate_image", "candidate_image_digest", "candidate_qualification_receipt_digest",
        "sidecar_image", "sidecar_image_digest",
        "sidecar_qualification_receipt_digest", "formal_admission_digest",
        "tmpfs", "cpus", "memory", "pids_limit",
    ):
        value = getattr(spec, key)
        if value is not None:
            receipt[key] = value
    if spec.reference is not None:
        receipt["reference_digest"] = spec.reference_digest
    if spec.solution is not None:
        receipt["solution_digest"] = spec.solution_digest
    receipt["signature"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "signature_hex": private_key.sign(_signature_payload(receipt)).hex(),
    }
    receipt["receipt_digest"] = _digest_payload(receipt)
    return receipt


def verify_verifier_receipt(
    receipt: dict[str, Any], *, spec: VerifierWorkerSpec,
    trusted_public_key_hex: str,
    trusted_key_id: str = "verifier-coordinator-v1",
    expected_result: dict[str, Any] | None = None,
) -> None:
    """Reject a receipt whose case, private profile, submission, or result drifted."""
    if not isinstance(receipt, dict):
        raise VerifierWorkerError("verifier receipt must be an object")
    claimed = dict(receipt)
    digest = claimed.pop("receipt_digest", None)
    if not isinstance(digest, str) or _digest_payload(claimed) != digest:
        raise VerifierWorkerError("verifier receipt digest mismatch")
    signature = receipt.get("signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != "ed25519":
        raise VerifierWorkerError("verifier receipt has no Ed25519 signature")
    if signature.get("key_id") != trusted_key_id:
        raise VerifierWorkerError("verifier receipt key is not pinned")
    try:
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(trusted_public_key_hex))
        public_key.verify(bytes.fromhex(str(signature.get("signature_hex", ""))), _signature_payload(receipt))
    except Exception as exc:
        raise VerifierWorkerError("verifier receipt signature is not trusted") from exc
    expected = {
        "case_id": spec.case_id,
        "run_id": spec.run_id,
        "attempt": spec.attempt,
        "case_version": spec.case_version,
        "public_digest": spec.public_digest,
        "verifier_profile": spec.verifier_profile,
        "verifier_profile_digest": spec.verifier_profile_digest,
        "verifier_bundle_digest": spec.verifier_bundle_digest,
        "verifier_image": spec.image,
        "verifier_image_digest": spec.image_digest,
        "submission_manifest_digest": _submission_digest(Path(spec.submission).expanduser().resolve()),
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise VerifierWorkerError(f"verifier receipt binding mismatch: {key}")
    for key in (
        "candidate_image", "candidate_image_digest", "candidate_qualification_receipt_digest",
        "sidecar_image", "sidecar_image_digest",
        "sidecar_qualification_receipt_digest", "formal_admission_digest",
        "tmpfs", "cpus", "memory", "pids_limit",
    ):
        expected_value = getattr(spec, key)
        if expected_value is not None and receipt.get(key) != expected_value:
            raise VerifierWorkerError(f"verifier receipt binding mismatch: {key}")
    for path_key, digest_key in (("reference", "reference_digest"), ("solution", "solution_digest")):
        path = getattr(spec, path_key)
        if path is not None:
            expected_digest = getattr(spec, digest_key) or directory_digest(path)
            if receipt.get(digest_key) != expected_digest:
                raise VerifierWorkerError(f"verifier receipt binding mismatch: {digest_key}")
    result_value = receipt.get("result")
    if not isinstance(result_value, dict) or receipt.get("result_digest") != _digest_payload(result_value):
        raise VerifierWorkerError("verifier receipt result digest mismatch")
    if expected_result is not None and receipt.get("result") != expected_result:
        raise VerifierWorkerError("verifier receipt result mismatch")


def write_verifier_receipt(path: Path, receipt: dict[str, Any]) -> Path:
    """Atomically create a receipt; never overwrite an existing one."""
    destination = Path(path).expanduser().resolve()
    if destination.exists() or destination.is_symlink():
        raise VerifierWorkerError("refusing to overwrite verifier receipt")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(destination)
    return destination


def run_verifier_worker(
    spec: VerifierWorkerSpec, *, runner: Callable[..., Any] | None = None,
    signing_key_hex: str, key_id: str = "verifier-coordinator-v1",
) -> tuple[BenchmarkResult, dict[str, Any]]:
    """Run the existing isolated verifier and emit its bound receipt."""
    # Re-check the seal and private bundle immediately before execution; a
    # coordinator must not rely on an earlier preflight after a filesystem
    # handoff.
    from bench.mvp import verify_sealed_submission
    verify_sealed_submission(Path(spec.submission).expanduser().resolve())
    if directory_digest(Path(spec.verifier_bundle).expanduser().resolve()) != spec.verifier_bundle_digest:
        raise VerifierWorkerError("verifier bundle changed before execution")
    for path_key, digest_key in (
        ("reference", "reference_digest"),
        ("solution", "solution_digest"),
    ):
        path = getattr(spec, path_key)
        if path is not None and directory_digest(path) != getattr(spec, digest_key):
            raise VerifierWorkerError(f"verifier {path_key} changed before execution")
    build_verifier_worker_command(spec)
    result = run_verifier(
        VerifierSpec(
            image=_immutable_image(spec),
            timeout_sec=spec.timeout_sec,
            env={},
            tests_dir=Path(spec.verifier_bundle).expanduser().resolve(),
            reference_dir=spec.reference,
            solution_dir=spec.solution,
            tmpfs=spec.tmpfs,
            cpus=spec.cpus,
            memory=spec.memory,
            pids_limit=spec.pids_limit,
            container_name=f"bench-verifier-{spec.run_id}-{spec.attempt}",
        ),
        Path(spec.submission).expanduser().resolve(),
        Path(spec.logs).expanduser().resolve(),
        run_id=spec.run_id,
        runner=runner,
    )
    return result, make_verifier_receipt(
        spec, result, signing_key_hex=signing_key_hex, key_id=key_id
    )
