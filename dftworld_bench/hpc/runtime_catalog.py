"""TrustedRuntimeCatalog: gates QUALIFIED runtime status behind cryptographically verified receipts.

Invariants:
- Lock files and SiteProfiles cannot self-assert QUALIFIED status.
- A runtime is QUALIFIED if and only if it has a valid qualification receipt
  that passes verify_site_receipt with the trusted trust_store and trusted_site_profiles.
- If no valid receipt exists, runtime remains UNBUILT (if missing artifact) or
  BUILT_NOT_QUALIFIED.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from dftworld_bench.hpc.runtime_resolution import (
    RuntimeLockEntry,
    RuntimeResolver,
    RuntimeStatus,
)


class TrustedRuntimeCatalog:
    """Catalog of runtimes with qualification status verified against cryptographic receipts."""

    def __init__(
        self,
        lock_dir: Path | str,
        *,
        qualification_root: Path | str | None = None,
        trust_store: Any | None = None,
        trusted_site_profiles: Mapping[str, Any] | None = None,
        repo_root: Path | str | None = None,
    ) -> None:
        self.lock_dir = Path(lock_dir)
        self.qualification_root = Path(qualification_root) if qualification_root else None
        self.trust_store = trust_store
        self.trusted_site_profiles = trusted_site_profiles or {}
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
        self._entries: dict[str, RuntimeLockEntry] = {}
        self._load()

    @classmethod
    def load(
        cls,
        lock_dir: Path | str,
        *,
        qualification_root: Path | str | None = None,
        trust_store: Any | None = None,
        trusted_site_profiles: Mapping[str, Any] | None = None,
        repo_root: Path | str | None = None,
    ) -> "TrustedRuntimeCatalog":
        return cls(
            lock_dir=lock_dir,
            qualification_root=qualification_root,
            trust_store=trust_store,
            trusted_site_profiles=trusted_site_profiles,
            repo_root=repo_root,
        )

    def _load(self) -> None:
        if not self.lock_dir.is_dir():
            return

        from dftworld_bench.experiments.compute_profile_qualification import verify_site_receipt

        for path in sorted(self.lock_dir.glob("*-runtime.lock.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue

            cap = path.name[:-len("-runtime.lock.json")]
            entry = RuntimeLockEntry.from_lock_doc(cap, doc, source=str(path))

            # If UNBUILT, keep as UNBUILT
            if entry.status == RuntimeStatus.UNBUILT:
                self._entries[cap] = entry
                continue

            # Check if there is a qualification receipt
            promoted = False
            qual = doc.get("qualification") or {}
            receipt_rel = qual.get("receipt_path") or entry.qualification_receipt_path
            receipt_file: Path | None = None

            if self.qualification_root is not None and receipt_rel:
                candidate = self.qualification_root / receipt_rel
                if candidate.is_file():
                    receipt_file = candidate
            if receipt_file is None and receipt_rel:
                candidate = path.parent / receipt_rel
                if candidate.is_file():
                    receipt_file = candidate
            if receipt_file is None and self.qualification_root is not None:
                candidate = self.qualification_root / f"{entry.provider}-site-receipt.json"
                if candidate.is_file():
                    receipt_file = candidate

            if receipt_file is not None and receipt_file.is_file():
                try:
                    raw_data = json.loads(receipt_file.read_text(encoding="utf-8"))
                    site_prof = None
                    if entry.site_profile_id and entry.site_profile_id in self.trusted_site_profiles:
                        site_prof = self.trusted_site_profiles[entry.site_profile_id]
                    elif entry.provider in self.trusted_site_profiles:
                        site_prof = self.trusted_site_profiles[entry.provider]

                    vr = verify_site_receipt(
                        raw_data,
                        scheduler=entry.provider,
                        site_profile=site_prof,
                        root=self.repo_root,
                        receipt_dir=receipt_file.parent,
                        trust_store=self.trust_store,
                        trusted_site_profile=site_prof,
                    )
                    derived = vr.get("derived") or {}
                    if derived.get("qualification_status") == "PASS":
                        entry = RuntimeLockEntry(
                            capability=entry.capability,
                            image_name=entry.image_name,
                            sif_path=entry.sif_path,
                            sif_sha256=entry.sif_sha256,
                            source=entry.source,
                            artifact_kind=entry.artifact_kind,
                            artifact_path_or_id=entry.artifact_path_or_id,
                            digest=entry.digest,
                            status=RuntimeStatus.QUALIFIED,
                            lock_digest=entry.lock_digest,
                            qualification_receipt_path=str(receipt_file),
                            qualification_receipt_digest=str(raw_data.get("digest") or ""),
                            software_versions=entry.software_versions,
                            provider=entry.provider,
                            site_profile_id=entry.site_profile_id,
                            runtime_profile_id=entry.runtime_profile_id,
                            qualification_verified=True,
                        )
                        promoted = True
                except Exception:
                    promoted = False

            if not promoted:
                entry = RuntimeLockEntry(
                    capability=entry.capability,
                    image_name=entry.image_name,
                    sif_path=entry.sif_path,
                    sif_sha256=entry.sif_sha256,
                    source=entry.source,
                    artifact_kind=entry.artifact_kind,
                    artifact_path_or_id=entry.artifact_path_or_id,
                    digest=entry.digest,
                    status=RuntimeStatus.BUILT_NOT_QUALIFIED,
                    lock_digest=entry.lock_digest,
                    qualification_receipt_path=entry.qualification_receipt_path,
                    qualification_receipt_digest=entry.qualification_receipt_digest,
                    software_versions=entry.software_versions,
                    provider=entry.provider,
                    site_profile_id=entry.site_profile_id,
                    runtime_profile_id=entry.runtime_profile_id,
                    qualification_verified=False,
                )

            self._entries[cap] = entry

    def get(self, capability: str) -> RuntimeLockEntry | None:
        return self._entries.get(capability)

    def qualified_entries(self) -> list[RuntimeLockEntry]:
        return [e for e in self._entries.values() if e.status == RuntimeStatus.QUALIFIED]

    def qualified_capabilities(self) -> list[str]:
        return sorted(e.capability for e in self.qualified_entries())

    def all_capabilities(self) -> list[str]:
        return sorted(self._entries.keys())

    def to_resolver(self) -> RuntimeResolver:
        return RuntimeResolver(self._entries.values())
