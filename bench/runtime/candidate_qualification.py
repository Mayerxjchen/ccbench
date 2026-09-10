"""Load and verify local Candidate/sidecar image qualification locks."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519


class CandidateQualificationError(ValueError):
    """Qualification lock/receipt is missing, stale, or untrusted."""


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _read(path: Path, label: str) -> dict[str, Any]:
    try:
        node = os.lstat(path)
        if not path.is_file() or node.st_nlink != 1 or node.st_size > 512 * 1024:
            raise OSError(f"{label} is not a safe regular file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            value = json.loads(stream.read(512 * 1024 + 1))
    except (OSError, ValueError, TypeError) as exc:
        raise CandidateQualificationError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CandidateQualificationError(f"{label} must be an object")
    return value


def load_qualified_runtime(
    lock_path: str | Path, *, role: str, image_name: str,
    trusted_public_key_hex: str, trusted_key_id: str,
) -> dict[str, Any]:
    """Return a verified lock for a locally inspected immutable image ID."""
    lock_file = Path(lock_path).expanduser().resolve()
    lock = _read(lock_file, f"{role} runtime lock")
    if lock.get("schema_version") != "candidate-runtime-lock/v1" or lock.get("status") != "QUALIFIED":
        raise CandidateQualificationError(f"{role} runtime is not qualified")
    if lock.get("role") != role or lock.get("image_name") != image_name:
        raise CandidateQualificationError(f"{role} runtime lock does not match the selected image")
    image_digest = lock.get("image_digest")
    if not isinstance(image_digest, str) or not image_digest.startswith("sha256:"):
        raise CandidateQualificationError(f"{role} runtime lock has no immutable image ID")
    receipt_path = Path(str(lock.get("receipt_path", ""))).expanduser()
    if not receipt_path.is_absolute():
        receipt_path = (lock_file.parent / receipt_path).resolve()
    receipt = _read(receipt_path, f"{role} qualification receipt")
    if lock.get("receipt_digest") != receipt.get("receipt_digest"):
        raise CandidateQualificationError(f"{role} qualification receipt digest mismatch")
    claimed = dict(receipt)
    receipt_digest = claimed.pop("receipt_digest", None)
    if not isinstance(receipt_digest, str) or _digest(claimed) != receipt_digest:
        raise CandidateQualificationError(f"{role} qualification receipt integrity failure")
    signature = receipt.get("signature")
    if (not isinstance(signature, dict) or signature.get("algorithm") != "ed25519"
            or signature.get("key_id") != trusted_key_id):
        raise CandidateQualificationError(f"{role} qualification receipt key is not trusted")
    signed = {key: value for key, value in receipt.items() if key not in {"signature", "receipt_digest"}}
    try:
        key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(trusted_public_key_hex))
        key.verify(bytes.fromhex(str(signature.get("signature_hex", ""))),
                   json.dumps(signed, sort_keys=True, separators=(",", ":")).encode())
    except Exception as exc:
        raise CandidateQualificationError(f"{role} qualification receipt signature is not trusted") from exc
    if any(receipt.get(key) != value for key, value in {
        "role": role, "image_name": image_name, "image_digest": image_digest,
    }.items()):
        raise CandidateQualificationError(f"{role} qualification receipt binding mismatch")
    return lock
