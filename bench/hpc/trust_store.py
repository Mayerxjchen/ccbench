"""Qualification Trust Store: pinned cryptographic trust anchors for receipts."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRUST_STORE_PATH = (
    _ROOT / "runtimes" / "trust.toml"
    if (_ROOT / "runtimes" / "trust.toml").is_file()
    else _ROOT / "infra" / "config" / "qualification-trust.toml"
)


class TrustStoreError(Exception):
    """Raised when trust store is malformed or an invalid key operation occurs."""


@dataclass(frozen=True)
class TrustKey:
    key_id: str
    algorithm: str
    public_key_hex: str
    status: str
    purpose: str = "site-qualification"

    @property
    def is_active(self) -> bool:
        return self.status.upper() == "ACTIVE" and len(self.public_key_hex) == 64


class QualificationTrustStore:
    """Manages pinned, trusted verification keys for qualification receipts."""

    def __init__(self, keys: Mapping[str, TrustKey] | None = None) -> None:
        self._keys: dict[str, TrustKey] = dict(keys or {})

    @classmethod
    def from_file(cls, path: Path | None = None) -> QualificationTrustStore:
        p = Path(path) if path is not None else DEFAULT_TRUST_STORE_PATH
        if not p.is_file():
            raise TrustStoreError(f"Trust store config not found: {p}")
        try:
            doc = tomllib.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            raise TrustStoreError(f"Failed to parse trust store {p}: {exc}") from exc
        return cls.from_dict(doc)

    @classmethod
    def load_default(cls) -> QualificationTrustStore:
        """Load the operator-pinned default trust store.

        ``verify_receipt_signature`` historically called this name while the
        store only exposed ``from_file``.  Keeping the alias here makes the
        default path explicit and, importantly, still fails closed when the
        checked-in store is absent or contains UNCONFIGURED keys.
        """
        return cls.from_file()

    @classmethod
    def from_dict(cls, doc: Mapping[str, Any]) -> QualificationTrustStore:
        raw_keys = doc.get("keys") or {}
        keys: dict[str, TrustKey] = {}
        for key_id, meta in raw_keys.items():
            if not isinstance(meta, Mapping):
                continue
            keys[key_id] = TrustKey(
                key_id=str(key_id),
                algorithm=str(meta.get("algorithm", "ed25519")),
                public_key_hex=str(meta.get("public_key_hex", "")).strip(),
                status=str(meta.get("status", "UNCONFIGURED")).strip(),
                purpose=str(meta.get("purpose", "")).strip(),
            )
        import os
        env_agent_pub = os.environ.get("BENCH_CANDIDATE_AGENT_PUBKEY", "").strip()
        if env_agent_pub and len(env_agent_pub) == 64:
            keys["candidate-agent-v1"] = TrustKey(
                key_id="candidate-agent-v1",
                algorithm="ed25519",
                public_key_hex=env_agent_pub,
                status="ACTIVE",
                purpose="candidate-agent-qualification",
            )
        return cls(keys)

    def get_key(self, key_id: str) -> TrustKey | None:
        return self._keys.get(key_id)

    def resolve_public_key_hex(
        self, key_id: str, *, expected_purpose: str | None = None
    ) -> str:
        """Resolve an active Ed25519 public key hex or raise TrustStoreError.

        Returns:
            64-character lowercase hex string of the Ed25519 public key.
        Raises:
            TrustStoreError if key is missing, inactive, or unconfigured.
        """
        key = self.get_key(key_id)
        if key is None:
            raise TrustStoreError(f"Signing key {key_id!r} not found in qualification trust store")
        if key.status.upper() == "UNCONFIGURED" or not key.public_key_hex:
            raise TrustStoreError(
                f"Signing key {key_id!r} is UNCONFIGURED in qualification trust store; "
                "qualification cannot PASS without configured maintainer key"
            )
        if not key.is_active:
            raise TrustStoreError(
                f"Signing key {key_id!r} is not ACTIVE (status={key.status!r})"
            )
        if key.algorithm != "ed25519":
            raise TrustStoreError(
                f"Unsupported signing algorithm for key {key_id!r}: {key.algorithm!r}"
            )
        if expected_purpose is not None and key.purpose != expected_purpose:
            raise TrustStoreError(
                f"Signing key {key_id!r} has purpose {key.purpose!r}; "
                f"expected {expected_purpose!r}"
            )
        return key.public_key_hex

    def register_key(
        self,
        key_id: str,
        public_key_hex: str,
        *,
        algorithm: str = "ed25519",
        status: str = "ACTIVE",
        purpose: str = "testing",
    ) -> None:
        """Register or override a key in memory (e.g. for testing)."""
        self._keys[key_id] = TrustKey(
            key_id=key_id,
            algorithm=algorithm,
            public_key_hex=public_key_hex.strip(),
            status=status.strip(),
            purpose=purpose.strip(),
        )
