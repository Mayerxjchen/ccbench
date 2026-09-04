"""Receipt-backed runtime catalog used by trusted production composition.

The parser for a runtime lock only learns that an artifact exists.  This
module is the *only* layer allowed to turn that fact into ``QUALIFIED``: it
requires a materialized receipt, a pinned SiteProfile from an explicit
registry, and exact bindings between the lock, receipt, artifact and
qualification signature.
"""

from __future__ import annotations

import hashlib
import json
import dataclasses
from pathlib import Path
from typing import Any, Mapping

from dftworld_bench.hpc.runtime_resolution import (
    RuntimeLockEntry,
    RuntimeResolutionError,
    RuntimeResolver,
    RuntimeStatus,
)


_LOCK_SUFFIX = "-runtime.lock.json"


def _canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _profile_object(value: Any) -> tuple[Any, dict[str, Any], str]:
    """Normalize an explicit registry value to ``(object, document, digest)``."""
    from dftworld_bench.hpc.site_profile import HpcSiteProfile

    if isinstance(value, HpcSiteProfile):
        return value, value.to_trusted_dict(), value.digest
    if isinstance(value, Mapping):
        # Raw mappings are accepted only because the caller supplied them in
        # the explicit registry argument.  Parse them through the immutable
        # SiteProfile constructor so schema/credential/digest checks run.
        raw = dict(value)
        asserted_digest = raw.pop("digest", None)
        profile = HpcSiteProfile.from_dict(raw)
        if asserted_digest is not None and asserted_digest != profile.digest:
            raise RuntimeResolutionError(
                f"trusted SiteProfile {profile.site_id!r} digest assertion does "
                f"not match recomputed digest"
            )
        return profile, profile.to_trusted_dict(), profile.digest
    raise RuntimeResolutionError(
        "trusted SiteProfile registry values must be HpcSiteProfile objects "
        f"or validated mappings, got {type(value).__name__}"
    )


class TrustedRuntimeCatalog:
    """Catalog of lock entries promoted only by verified materialized receipts."""

    def __init__(
        self,
        lock_dir: Path | str,
        *,
        qualification_root: Path | str | None = None,
        trust_store: Any | None = None,
        trusted_site_profiles: Mapping[str, Any] | Any | None = None,
        repo_root: Path | str | None = None,
    ) -> None:
        self.lock_dir = Path(lock_dir)
        self.qualification_root = (
            Path(qualification_root) if qualification_root is not None else None
        )
        self.trust_store = trust_store
        if trusted_site_profiles is not None and hasattr(
            trusted_site_profiles, "as_mapping"
        ):
            trusted_site_profiles = trusted_site_profiles.as_mapping()
        self.trusted_site_profiles: dict[str, tuple[Any, dict[str, Any], str]] = {}
        for name, profile in (trusted_site_profiles or {}).items():
            obj, doc, digest = _profile_object(profile)
            if obj.site_id != name:
                raise RuntimeResolutionError(
                    f"trusted SiteProfile registry key {name!r} does not match "
                    f"site_id {obj.site_id!r}"
                )
            self.trusted_site_profiles[name] = (obj, doc, digest)
        self.repo_root = (
            Path(repo_root)
            if repo_root is not None
            else Path(__file__).resolve().parents[2]
        )
        self._entries: dict[str, RuntimeLockEntry] = {}
        self._errors: dict[str, str] = {}
        self._receipt_bindings: dict[Path, str] = {}
        self._load()

    @classmethod
    def load(
        cls,
        lock_dir: Path | str,
        *,
        qualification_root: Path | str | None = None,
        trust_store: Any | None = None,
        trusted_site_profiles: Mapping[str, Any] | Any | None = None,
        repo_root: Path | str | None = None,
    ) -> "TrustedRuntimeCatalog":
        return cls(
            lock_dir=lock_dir,
            qualification_root=qualification_root,
            trust_store=trust_store,
            trusted_site_profiles=trusted_site_profiles,
            repo_root=repo_root,
        )

    @property
    def errors(self) -> dict[str, str]:
        """Qualification failures keyed by capability (read-only copy)."""
        return dict(self._errors)

    @property
    def is_trusted(self) -> bool:
        """Marker consumed by composition code before injecting a resolver."""
        return True

    def _load(self) -> None:
        if not self.lock_dir.is_dir():
            raise RuntimeResolutionError(f"runtime lock dir not found: {self.lock_dir}")
        if self.qualification_root is not None and not self.qualification_root.is_dir():
            raise RuntimeResolutionError(
                f"qualification root not found: {self.qualification_root}"
            )

        from dftworld_bench.experiments.compute_profile_qualification import (
            verify_site_receipt,
        )
        from dftworld_bench.experiments.qualification_receipt import canonical_digest

        for path in sorted(self.lock_dir.glob(f"*{_LOCK_SUFFIX}")):
            cap = path.name[: -len(_LOCK_SUFFIX)]
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(doc, Mapping):
                    raise ValueError("lock document must be an object")
                entry = RuntimeLockEntry.from_lock_doc(cap, doc, source=str(path))
            except Exception as exc:
                self._errors[cap] = f"lock unreadable: {exc}"
                continue

            # Missing artifacts remain UNBUILT.  A complete lock artifact is
            # still only BUILT_NOT_QUALIFIED until every check below passes.
            if entry.status == RuntimeStatus.UNBUILT:
                self._entries[cap] = entry
                continue

            qual = doc.get("qualification")
            qual = qual if isinstance(qual, Mapping) else {}
            receipt_rel = qual.get("receipt_path") or entry.qualification_receipt_path
            receipt_file: Path | None = None
            failure = ""

            try:
                receipt_file = self._contained_materialized_file(receipt_rel)
                if receipt_file is None:
                    failure = (
                        "qualification receipt is missing or qualification_root "
                        "was not supplied"
                    )
                else:
                    previous = self._receipt_bindings.get(receipt_file)
                    if previous is not None and previous != cap:
                        raise RuntimeResolutionError(
                            f"qualification receipt {receipt_file} is bound to "
                            f"multiple runtimes ({previous!r}, {cap!r})"
                        )
                    self._receipt_bindings[receipt_file] = cap
            except Exception as exc:
                failure = str(exc)

            promoted = False
            if not failure and receipt_file is not None:
                try:
                    raw_data = json.loads(receipt_file.read_text(encoding="utf-8"))
                    if not isinstance(raw_data, dict):
                        raise ValueError("receipt document must be an object")

                    # The lock itself must name the receipt by its canonical
                    # digest.  A missing or raw-file digest is not enough.
                    expected_receipt_digest = str(
                        qual.get("receipt_digest")
                        or entry.qualification_receipt_digest
                        or ""
                    )
                    recomputed_receipt_digest = canonical_digest(
                        {
                            k: v
                            for k, v in raw_data.items()
                            if k not in ("digest", "signature")
                        }
                    )
                    if raw_data.get("digest") != recomputed_receipt_digest:
                        raise ValueError(
                            "qualification receipt canonical digest mismatch"
                        )
                    if not expected_receipt_digest or expected_receipt_digest != raw_data.get(
                        "digest"
                    ):
                        raise ValueError(
                            "lock qualification.receipt_digest is missing or does "
                            "not equal the receipt canonical digest"
                        )

                    try:
                        expected_lock_rel = path.resolve().relative_to(
                            self.repo_root.resolve(strict=True)
                        ).as_posix()
                    except ValueError:
                        expected_lock_rel = path.resolve().relative_to(
                            self._root_path().resolve(strict=True)
                        ).as_posix()
                    receipt_lock = raw_data.get("runtime_lock")
                    if not isinstance(receipt_lock, Mapping):
                        raise ValueError("receipt runtime_lock binding is missing")
                    if not self._safe_relative(receipt_lock.get("path")):
                        raise ValueError(
                            "receipt runtime_lock.path is not safely relative"
                        )
                    if Path(str(receipt_lock["path"])).as_posix() != expected_lock_rel:
                        raise ValueError(
                            f"receipt runtime_lock.path {receipt_lock.get('path')!r} "
                            f"does not name {expected_lock_rel!r}"
                        )
                    # Runtime lock digest is canonical, not a hash of whatever
                    # bytes happened to be copied into the receipt directory.
                    if receipt_lock.get("digest") != entry.lock_digest:
                        raise ValueError(
                            "receipt runtime_lock.digest does not equal canonical lock digest"
                        )
                    image_id = entry.image_id
                    if image_id and receipt_lock.get("image_id") != image_id:
                        raise ValueError(
                            f"receipt image_id {receipt_lock.get('image_id')!r} "
                            f"does not equal lock image_id {image_id!r}"
                        )

                    site_id = str(
                        doc.get("site_profile_id")
                        or qual.get("site_profile_id")
                        or entry.site_profile_id
                        or raw_data.get("site_profile_id")
                        or ""
                    )
                    if not site_id or site_id not in self.trusted_site_profiles:
                        raise ValueError(
                            f"runtime {cap!r} has no explicitly trusted SiteProfile "
                            f"for site_profile_id={site_id!r}"
                        )
                    _profile_obj, profile_doc, profile_digest = self.trusted_site_profiles[
                        site_id
                    ]
                    if raw_data.get("site_profile_id") != site_id:
                        raise ValueError("receipt site_profile_id is not bound to lock site")
                    if raw_data.get("site_profile_digest") != profile_digest:
                        raise ValueError(
                            "receipt site_profile_digest does not equal trusted SiteProfile digest"
                        )

                    verified = verify_site_receipt(
                        raw_data,
                        scheduler=entry.provider,
                        root=self.repo_root,
                        receipt_dir=receipt_file.parent,
                        trust_store=self.trust_store,
                        trusted_site_profile=profile_doc,
                    )
                    problems = verified.get("problems") or []
                    if problems or (verified.get("derived") or {}).get(
                        "qualification_status"
                    ) != "PASS":
                        raise ValueError(
                            "qualification receipt did not derive PASS: "
                            + "; ".join(str(p) for p in problems[:3])
                        )
                    promoted = True
                    entry = self._promoted_entry(
                        entry,
                        receipt_path=receipt_file,
                        receipt_digest=str(raw_data["digest"]),
                        site_profile_id=site_id,
                    )
                except Exception as exc:
                    failure = str(exc)

            if not promoted:
                # FAILED distinguishes a materialized-but-invalid receipt from
                # a runtime which has not been qualified yet.  Neither status
                # can be advertised or resolved.
                status = (
                    RuntimeStatus.FAILED
                    if receipt_file is not None
                    else RuntimeStatus.BUILT_NOT_QUALIFIED
                )
                entry = self._unqualified_entry(entry, status=status)
                if failure:
                    self._errors[cap] = failure
            self._entries[cap] = entry

    def _root_path(self) -> Path:
        if self.qualification_root is None:
            raise RuntimeResolutionError(
                "qualification_root is required for receipt-backed runtime resolution"
            )
        return self.qualification_root

    @staticmethod
    def _safe_relative(value: Any) -> bool:
        if not isinstance(value, str) or not value:
            return False
        path = Path(value)
        return not path.is_absolute() and ".." not in path.parts

    def _contained_materialized_file(self, value: Any) -> Path | None:
        if not self._safe_relative(value):
            if value in (None, ""):
                return None
            raise RuntimeResolutionError(
                f"qualification receipt path must be a safe relative path: {value!r}"
            )
        root = self._root_path().resolve(strict=True)
        relative = Path(str(value))
        candidate = root / relative
        # Check every path component before resolving: an in-root symlink is
        # still an untrusted indirection and is not a materialized receipt.
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise RuntimeResolutionError(
                    f"qualification receipt path contains symlink: {value!r}"
                )
        resolved = candidate.resolve(strict=False)
        if resolved != root and root not in resolved.parents:
            raise RuntimeResolutionError(
                f"qualification receipt path escapes qualification_root: {value!r}"
            )
        return resolved if resolved.is_file() else None

    @staticmethod
    def _promoted_entry(
        entry: RuntimeLockEntry,
        *,
        receipt_path: Path,
        receipt_digest: str,
        site_profile_id: str,
    ) -> RuntimeLockEntry:
        return dataclasses.replace(
            entry,
            status=RuntimeStatus.QUALIFIED,
            qualification_receipt_path=str(receipt_path),
            qualification_receipt_digest=receipt_digest,
            site_profile_id=site_profile_id,
            qualification_verified=True,
        )

    @staticmethod
    def _unqualified_entry(
        entry: RuntimeLockEntry, *, status: str
    ) -> RuntimeLockEntry:
        return dataclasses.replace(
            entry, status=status, qualification_verified=False
        )

    def get(self, capability: str) -> RuntimeLockEntry | None:
        return self._entries.get(capability)

    def qualified_entries(self) -> list[RuntimeLockEntry]:
        return [e for e in self._entries.values() if e.status == RuntimeStatus.QUALIFIED]

    def qualified_capabilities(self) -> list[str]:
        return sorted(e.capability for e in self.qualified_entries())

    def all_capabilities(self) -> list[str]:
        return sorted(self._entries.keys())

    def to_resolver(self) -> RuntimeResolver:
        """Return the only resolver allowed to advertise catalog entries."""
        return RuntimeResolver(self._entries.values(), trusted=True)
