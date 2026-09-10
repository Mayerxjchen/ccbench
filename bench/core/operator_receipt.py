"""Signed handoff receipts for operator-produced compute outputs.

The Candidate never receives an operator credential.  An operator (or a
trusted coordinator wrapping the operator) signs the small manifest below;
the pilot verifies it before copying any bytes into the run workspace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519


class OperatorReceiptError(ValueError):
    """The operator handoff is missing, forged, or bound to another run."""


MAX_RECEIPT_BYTES = 256 * 1024
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _signed_payload(receipt: dict[str, Any]) -> bytes:
    body = {k: v for k, v in receipt.items() if k not in {"signature", "receipt_digest"}}
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def _safe_relative(path: Any) -> str:
    if not isinstance(path, str) or not path or "\\" in path:
        raise OperatorReceiptError("operator receipt contains an invalid output path")
    candidate = Path(path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise OperatorReceiptError("operator receipt output path must be relative")
    return candidate.as_posix()


def _read(path: Path) -> dict[str, Any]:
    try:
        stat = os.lstat(path)
        if stat.st_nlink != 1 or not os.path.isfile(path) or stat.st_size > MAX_RECEIPT_BYTES:
            raise OSError("receipt is not a safe regular file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            payload = json.loads(stream.read(MAX_RECEIPT_BYTES + 1))
    except (OSError, ValueError, TypeError) as exc:
        raise OperatorReceiptError(f"invalid operator receipt: {exc}") from exc
    if not isinstance(payload, dict):
        raise OperatorReceiptError("operator receipt must be a JSON object")
    return payload


def make_operator_receipt(
    *, run_id: str, attempt: int, compute_backend: str,
    request_digests: dict[str, str], outputs: list[dict[str, Any]],
    signing_key_hex: str, key_id: str = "operator-v1",
    compute_profile: str | None = None,
) -> dict[str, Any]:
    """Create the canonical signed manifest an operator returns to the pilot."""
    if not run_id or attempt < 1 or not compute_backend or not isinstance(request_digests, dict):
        raise OperatorReceiptError("operator receipt run binding is incomplete")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in outputs:
        if not isinstance(item, dict):
            raise OperatorReceiptError("operator receipt output entries must be objects")
        path = _safe_relative(item.get("path"))
        digest = str(item.get("sha256", ""))
        if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", digest):
            raise OperatorReceiptError(f"invalid output digest: {path}")
        digest = digest if digest.startswith("sha256:") else "sha256:" + digest
        size = item.get("size")
        if not isinstance(size, int) or size < 0 or path in seen:
            raise OperatorReceiptError(f"invalid or duplicate output: {path}")
        seen.add(path)
        normalized.append({"path": path, "sha256": digest, "size": size})
    normalized.sort(key=lambda item: item["path"])
    body: dict[str, Any] = {
        "schema_version": "bench.operator-receipt/v1",
        "run_id": run_id,
        "attempt": attempt,
        "compute_backend": compute_backend,
        "request_digests": dict(sorted(request_digests.items())),
        "outputs": normalized,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    if compute_profile is not None:
        body["compute_profile"] = compute_profile
    try:
        key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signing_key_hex))
    except Exception as exc:
        raise OperatorReceiptError("invalid operator signing key") from exc
    body["signature"] = {
        "algorithm": "ed25519", "key_id": key_id,
        "signature_hex": key.sign(_signed_payload(body)).hex(),
    }
    body["receipt_digest"] = _digest(body)
    return body


def verify_operator_receipt(
    path: Path, *, trusted_public_key_hex: str, trusted_key_id: str,
    expected: dict[str, Any], expected_outputs: list[str],
) -> tuple[str, dict[str, Any]]:
    """Verify signature, run binding, and the exact declared output set."""
    receipt = _read(Path(path).expanduser().resolve())
    claimed_digest = receipt.get("receipt_digest")
    without_digest = dict(receipt)
    without_digest.pop("receipt_digest", None)
    if not isinstance(claimed_digest, str) or _digest(without_digest) != claimed_digest:
        raise OperatorReceiptError("operator receipt digest mismatch")
    signature = receipt.get("signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != "ed25519" or signature.get("key_id") != trusted_key_id:
        raise OperatorReceiptError("operator receipt key is not pinned")
    try:
        public = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(trusted_public_key_hex))
        public.verify(bytes.fromhex(str(signature.get("signature_hex", ""))), _signed_payload(receipt))
    except Exception as exc:
        raise OperatorReceiptError("operator receipt signature is not trusted") from exc
    if receipt.get("schema_version") != "bench.operator-receipt/v1":
        raise OperatorReceiptError("unsupported operator receipt schema")
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise OperatorReceiptError(f"operator receipt binding mismatch: {key}")
    outputs = receipt.get("outputs")
    if not isinstance(outputs, list):
        raise OperatorReceiptError("operator receipt has no outputs")
    actual_paths = [_safe_relative(item.get("path")) if isinstance(item, dict) else "" for item in outputs]
    wanted = sorted(_safe_relative(item) for item in expected_outputs)
    if sorted(actual_paths) != wanted or len(actual_paths) != len(set(actual_paths)):
        raise OperatorReceiptError("operator receipt output set does not match requests")
    for item in outputs:
        if not isinstance(item, dict) or not _DIGEST.fullmatch(str(item.get("sha256", ""))) or not isinstance(item.get("size"), int) or item["size"] < 0:
            raise OperatorReceiptError("operator receipt output metadata is invalid")
    return claimed_digest, receipt
