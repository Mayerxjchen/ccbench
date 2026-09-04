"""Trusted runtime resolution: capability -> concrete locked runtime.

Per the Architecture Freeze (docs/architecture/ARCHITECTURE-FREEZE-2026-09-03.md
§3), an Agent's runtime field names a **capability** (``cp2k``, ``ai2kit``,
``deepmd-jax``, ``matclaw-cips``, ...), never a SIF path or digest. The digest
is infra truth: it lives in a runtime lock file (``reference/runtime/*.lock.json``
in production; site-captured copies elsewhere) and is resolved server-side at
the gateway into a :class:`ResolvedRuntime`. Digests then appear only in
resolved specs, Slurm evidence, and RunRecords — never in Agent-authored text.

The legacy digest-shaped declaration (``name@sha256:<64 hex>``) stays accepted
as a hidden compatibility path until Phase 8: when the resolver knows the
declared name, the declared digest must match the lock (an assertion checked
by trusted infra, not a free choice); unknown names pass through so the frozen
qualification driver keeps working.

Fail-closed rules:
- an unknown capability token is refused;
- a locked runtime with an empty captured digest is refused (the gate is
  NOT_RUN until the site captures the SIF);
- a locked runtime whose SIF path is still a placeholder is refused;
- a declared digest that contradicts the lock is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from dftworld_bench.contracts.case import RUNTIME_FAMILY_CAPABILITIES

# name is the same token grammar the execution-request schema accepts; the
# digest suffix is optional (its presence marks the legacy compat form).
RUNTIME_DECL_RE = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9./_-]*)(?:@(?:sha256:)?(?P<digest>[0-9a-f]{64}|img-[a-z0-9._-]+))?$"
)
CAPABILITY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_LOCK_SUFFIX = "-runtime.lock.json"


class RuntimeResolutionError(Exception):
    """A runtime declaration could not be resolved against locked infra truth."""


def split_runtime(decl: str) -> tuple[str, str | None]:
    """Return ``(name, digest_or_None)`` for a runtime declaration.

    Raises :class:`RuntimeResolutionError` for anything outside the accepted
    token grammar (shell metacharacters, uppercase, tags with ``:``, empty).
    """
    match = RUNTIME_DECL_RE.match(decl or "")
    if match is None:
        raise RuntimeResolutionError(
            f"malformed runtime declaration: {decl!r} (expected a capability "
            "token or name@sha256:<64 hex>)"
        )
    return match.group("name"), match.group("digest")


def qualification_for(capability: str) -> str:
    """Capability-matrix gate name a runtime capability satisfies."""
    return RUNTIME_FAMILY_CAPABILITIES.get(capability, f"runtime.{capability}")


class RuntimeStatus:
    UNBUILT = "UNBUILT"
    BUILT_NOT_QUALIFIED = "BUILT_NOT_QUALIFIED"
    QUALIFIED = "QUALIFIED"
    FAILED = "FAILED"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class RuntimeLockEntry:
    """One locked runtime identity, read from a ``<capability>-runtime.lock.json`` or SiteProfile."""

    capability: str
    image_name: str
    sif_path: str = ""
    sif_sha256: str = ""
    source: str = ""
    artifact_kind: str = "sif"  # "sif" | "compshare_image"
    artifact_path_or_id: str = ""
    digest: str = ""
    status: str = RuntimeStatus.UNBUILT
    lock_digest: str = ""
    qualification_receipt_path: str = ""
    qualification_receipt_digest: str = ""
    software_versions: dict[str, str] = field(default_factory=dict, hash=False)
    provider: str = "slurm"
    site_profile_id: str = ""
    runtime_profile_id: str = ""

    def __post_init__(self) -> None:
        if self.artifact_kind == "sif":
            if not self.sif_path and self.artifact_path_or_id:
                object.__setattr__(self, "sif_path", self.artifact_path_or_id)
            elif self.sif_path and not self.artifact_path_or_id:
                object.__setattr__(self, "artifact_path_or_id", self.sif_path)
            if not self.sif_sha256 and self.digest:
                object.__setattr__(self, "sif_sha256", self.digest)
            elif self.sif_sha256 and not self.digest:
                object.__setattr__(self, "digest", self.sif_sha256)
        elif self.artifact_kind == "compshare_image":
            if not self.digest and self.sif_sha256:
                object.__setattr__(self, "digest", self.sif_sha256)
            if not self.artifact_path_or_id and self.sif_path:
                object.__setattr__(self, "artifact_path_or_id", self.sif_path)

    @property
    def image_id(self) -> str:
        return self.artifact_path_or_id if self.artifact_kind == "compshare_image" else ""

    @classmethod
    def from_lock_doc(
        cls, capability: str, doc: Mapping[str, object], *, source: str
    ) -> "RuntimeLockEntry":
        image_name = str(doc.get("image_name") or capability)
        runtime_profile_id = str(doc.get("runtime_profile_id") or doc.get("image_name") or capability)

        # Compute content-addressed canonical digest of the lock doc itself
        canon_lock = json.dumps(doc, sort_keys=True, separators=(",", ":"))
        lock_digest = f"sha256:{hashlib.sha256(canon_lock.encode('utf-8')).hexdigest()}"

        # Schema v2: dispatcher-compshare-runtime-lock/v2
        if doc.get("schema") == "dispatcher-compshare-runtime-lock/v2":
            artifact = doc.get("artifact") or {}
            provenance = doc.get("provenance") or {}
            qual = doc.get("qualification") or {}
            raw_image_id = artifact.get("image_id")
            image_id = str(raw_image_id) if raw_image_id is not None else ""
            qual_status = str(qual.get("status") or "NOT_RUN").upper()
            receipt_path = qual.get("receipt_path")
            receipt_digest = qual.get("receipt_digest")

            if not image_id or is_placeholder_artifact(image_id):
                status = RuntimeStatus.UNBUILT
                image_id = ""
            elif qual_status != "PASS" or not receipt_path or not receipt_digest:
                status = RuntimeStatus.BUILT_NOT_QUALIFIED
            else:
                status = RuntimeStatus.QUALIFIED

            return cls(
                capability=capability,
                image_name=image_name,
                source=source,
                artifact_kind="compshare_image",
                artifact_path_or_id=image_id,
                digest=str(receipt_digest or ""),
                status=status,
                lock_digest=lock_digest,
                qualification_receipt_path=str(receipt_path or ""),
                qualification_receipt_digest=str(receipt_digest or ""),
                software_versions=dict(provenance.get("software_versions") or {}),
                provider=str(doc.get("provider") or "compshare"),
                runtime_profile_id=runtime_profile_id,
            )

        runtime = doc.get("runtime")
        if not isinstance(runtime, Mapping):
            raise RuntimeResolutionError(
                f"lock {source}: missing 'runtime' block for capability {capability!r}"
            )
        software_versions = dict(doc.get("software_versions") or runtime.get("software_versions") or {})

        # CompShare custom image lock (v1 / legacy)
        if runtime.get("artifact_kind") == "compshare_image" or "image_id" in runtime:
            raw_image_id = runtime.get("image_id")
            image_id = str(raw_image_id) if raw_image_id is not None else ""
            if not image_id or is_placeholder_artifact(image_id):
                status = RuntimeStatus.UNBUILT
                image_id = ""
            else:
                status = RuntimeStatus.BUILT_NOT_QUALIFIED
            provider = str(runtime.get("provider") or "compshare")
            return cls(
                capability=capability,
                image_name=image_name,
                source=source,
                artifact_kind="compshare_image",
                artifact_path_or_id=image_id,
                digest="",  # No pseudo-digest derivation!
                status=status,
                lock_digest=lock_digest,
                software_versions=software_versions,
                provider=provider,
                runtime_profile_id=runtime_profile_id,
            )

        # Standard SIF lock
        sif_path = str(runtime.get("sif_path_remote") or "")
        sif_sha = str(runtime.get("sif_sha256") or "")
        provider = str(runtime.get("provider") or "slurm")
        if not sif_path or is_placeholder_artifact(sif_path) or not sif_sha or is_placeholder_artifact(sif_sha):
            status = RuntimeStatus.UNBUILT
        else:
            status = RuntimeStatus.QUALIFIED

        return cls(
            capability=capability,
            image_name=image_name,
            sif_path=sif_path,
            sif_sha256=sif_sha,
            source=source,
            artifact_kind="sif",
            artifact_path_or_id=sif_path,
            digest=sif_sha,
            status=status,
            lock_digest=lock_digest,
            software_versions=software_versions,
            provider=provider,
            runtime_profile_id=runtime_profile_id,
        )

    def profile_digest(self) -> str:
        """Stable identity of the resolved profile (excludes wall-clock data)."""
        if self.artifact_kind == "compshare_image":
            canon = json.dumps(
                {
                    "artifact_kind": self.artifact_kind,
                    "capability": self.capability,
                    "digest": self.digest,
                    "image_id": self.artifact_path_or_id,
                    "image_name": self.image_name,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        else:
            canon = json.dumps(
                {
                    "capability": self.capability,
                    "image_name": self.image_name,
                    "sif_path": self.sif_path or self.artifact_path_or_id,
                    "sif_sha256": self.sif_sha256 or self.digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResolvedRuntime:
    """Server-side answer to 'which concrete runtime runs this capability'."""

    capability: str
    sif_path: str = ""
    sif_sha256: str = ""
    runtime_profile_digest: str = ""
    qualification: str = ""
    provider: str = "slurm"
    site_profile_id: str = ""
    runtime_profile_id: str = ""
    artifact_kind: str = "sif"
    artifact_path_or_id: str = ""
    digest: str = ""
    software_versions: dict[str, str] = field(default_factory=dict, hash=False)
    status: str = RuntimeStatus.QUALIFIED
    qualification_verified: bool = True
    qualification_receipt_digest: str = ""

    def __post_init__(self) -> None:
        if self.artifact_kind == "sif":
            if not self.sif_path and self.artifact_path_or_id:
                object.__setattr__(self, "sif_path", self.artifact_path_or_id)
            elif self.sif_path and not self.artifact_path_or_id:
                object.__setattr__(self, "artifact_path_or_id", self.sif_path)
            if not self.sif_sha256 and self.digest:
                object.__setattr__(self, "sif_sha256", self.digest)
            elif self.sif_sha256 and not self.digest:
                object.__setattr__(self, "digest", self.sif_sha256)
        elif self.artifact_kind == "compshare_image":
            if not self.artifact_path_or_id and self.sif_path:
                object.__setattr__(self, "artifact_path_or_id", self.sif_path)
            if not self.digest and self.sif_sha256:
                object.__setattr__(self, "digest", self.sif_sha256)

    @property
    def image_id(self) -> str:
        return self.artifact_path_or_id if self.artifact_kind == "compshare_image" else ""

    @property
    def declaration(self) -> str:
        """The sealed digest-shaped form downstream contracts expect."""
        if self.artifact_kind == "sif":
            return f"{self.capability}@sha256:{self.digest or self.sif_sha256}"
        return f"{self.capability}@{self.artifact_path_or_id}"

    def to_response(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "provider": self.provider,
            "site_profile_id": self.site_profile_id,
            "runtime_profile_id": self.runtime_profile_id,
            "artifact_kind": self.artifact_kind,
            "artifact_id_or_path": self.artifact_path_or_id,
            "digest": self.digest,
            "software_versions": self.software_versions,
            "qualification": self.qualification,
            "sif_sha256": self.sif_sha256,
            "runtime_profile_digest": self.runtime_profile_digest,
        }


_PLACEHOLDER_SUBSTRINGS = (
    "<",
    "placeholder",
    "unqualified",
    "dummy",
    "not validated",
    "unverified",
)


def is_placeholder_artifact(val: str | None) -> bool:
    """Check if an artifact identifier contains unresolvable placeholders or unverified tags."""
    if not val:
        return True
    lower = val.lower().strip()
    return any(p in lower for p in _PLACEHOLDER_SUBSTRINGS)


class RuntimeResolver:
    """Maps capability (and lock image names) to locked runtime identities."""

    def __init__(self, entries: Iterable[RuntimeLockEntry]) -> None:
        self._by_name: dict[str, RuntimeLockEntry] = {}
        for entry in entries:
            for alias in {entry.capability, entry.image_name}:
                existing = self._by_name.get(alias)
                if existing is not None and existing.source != entry.source:
                    raise RuntimeResolutionError(
                        f"runtime name {alias!r} locked by two sources: "
                        f"{existing.source} and {entry.source}"
                    )
                self._by_name[alias] = entry

    @classmethod
    def from_lock_dir(cls, lock_dir: Path) -> "RuntimeResolver":
        """Load every ``<capability>-runtime.lock.json`` in ``lock_dir``."""
        lock_dir = Path(lock_dir)
        if not lock_dir.is_dir():
            raise RuntimeResolutionError(f"runtime lock dir not found: {lock_dir}")
        entries: list[RuntimeLockEntry] = []
        for path in sorted(lock_dir.glob(f"*{_LOCK_SUFFIX}")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeResolutionError(
                    f"runtime lock {path.name} unreadable: {exc}"
                ) from exc
            capability = path.name[: -len(_LOCK_SUFFIX)]
            entries.append(
                RuntimeLockEntry.from_lock_doc(
                    capability, doc, source=str(path)
                )
            )
        return cls(entries)

    @classmethod
    def from_site_profile(cls, site_profile: Any) -> "RuntimeResolver":
        """Load resolver from an HpcSiteProfile or compatible mapping."""
        policy = getattr(site_profile, "runtime_policy", {}) or {}
        entries: list[RuntimeLockEntry] = []
        lock_dir = policy.get("runtime_lock_dir")
        if lock_dir and Path(lock_dir).is_dir():
            resolver = cls.from_lock_dir(Path(lock_dir))
            entries.extend(resolver._by_name.values())
        images = policy.get("images") or {}
        site_id = getattr(site_profile, "site_id", "")
        scheduler = getattr(site_profile, "scheduler", "slurm")
        for cap, img in images.items():
            if isinstance(img, Mapping):
                art_id = str(img.get("image_id") or img.get("sif_path") or "")
                dig = str(img.get("image_sha256") or img.get("digest") or img.get("sif_sha256") or "")
                qual_verified = bool(img.get("qualification_verified") or img.get("qualified"))
                if not art_id or is_placeholder_artifact(art_id):
                    st = RuntimeStatus.UNBUILT
                elif qual_verified or (dig and not is_placeholder_artifact(dig)):
                    st = RuntimeStatus.QUALIFIED
                else:
                    st = RuntimeStatus.BUILT_NOT_QUALIFIED
                entries.append(
                    RuntimeLockEntry(
                        capability=cap,
                        image_name=str(img.get("image_name") or cap),
                        source=f"site_profile:{site_id}",
                        artifact_kind=str(img.get("artifact_kind") or ("compshare_image" if scheduler == "compshare" else "sif")),
                        artifact_path_or_id=art_id,
                        digest=dig,
                        status=st,
                        software_versions=dict(img.get("software_versions") or {}),
                        provider=scheduler,
                        site_profile_id=site_id,
                        runtime_profile_id=str(img.get("runtime_profile_id") or cap),
                    )
                )
        return cls(entries)

    def qualified_capabilities(self) -> list[str]:
        """Capability tokens agents may name (strictly QUALIFIED runtimes only)."""
        result = set()
        for entry in self._by_name.values():
            if not CAPABILITY_RE.match(entry.capability):
                continue
            if entry.status == RuntimeStatus.QUALIFIED:
                result.add(entry.capability)
        return sorted(result)

    def all_capabilities(self) -> list[str]:
        """All capability tokens parsed from lock files regardless of qualification status."""
        return sorted(
            {
                entry.capability
                for entry in self._by_name.values()
                if CAPABILITY_RE.match(entry.capability)
            }
        )

    def capabilities(self, *, resolved_only: bool = True) -> list[str]:
        """Capability tokens agents may name (delegates to qualified_capabilities)."""
        return self.qualified_capabilities()

    def runtime_store(self) -> dict[str, str]:
        """Digest -> SIF path map for adapters (locked runtimes only)."""
        return {
            entry.sif_sha256: entry.sif_path
            for entry in set(self._by_name.values())
            if entry.sif_sha256 and entry.sif_path
            and not is_placeholder_artifact(entry.sif_path)
            and entry.status == RuntimeStatus.QUALIFIED
        }

    def resolve(
        self,
        decl: str,
        *,
        site_profile: Any = None,
        provider: str | None = None,
    ) -> ResolvedRuntime:
        name, digest = split_runtime(decl)
        entry = self._by_name.get(name)
        site_id = getattr(site_profile, "site_id", "") if site_profile else ""
        eff_provider = provider or (getattr(site_profile, "scheduler", "") if site_profile else "")

        if entry is None:
            raise RuntimeResolutionError(
                f"unknown runtime capability {name!r}; this site resolves: "
                f"{', '.join(self.qualified_capabilities())}"
            )

        if entry.status != RuntimeStatus.QUALIFIED:
            raise RuntimeResolutionError(
                f"runtime {name!r} ({entry.source}) is {entry.status}; only QUALIFIED runtimes can be resolved"
            )

        eff_provider = eff_provider or entry.provider or "slurm"
        site_id = site_id or entry.site_profile_id

        if entry.artifact_kind == "sif":
            if not entry.sif_sha256 or is_placeholder_artifact(entry.sif_sha256):
                raise RuntimeResolutionError(
                    f"locked runtime {name!r} ({entry.source}) has no captured "
                    "SIF digest (or placeholder/unqualified); the qualification gate stays "
                    "NOT_RUN until the site records it"
                )
            if not entry.sif_path or is_placeholder_artifact(entry.sif_path):
                raise RuntimeResolutionError(
                    f"locked runtime {name!r} ({entry.source}) has no concrete "
                    "SIF path on this site (placeholder/unqualified rejected)"
                )
            if digest is not None and digest != entry.sif_sha256:
                raise RuntimeResolutionError(
                    f"declared digest for {name!r} does not match the locked "
                    "runtime; digests are infra assertions, not Agent choices"
                )
            return ResolvedRuntime(
                capability=entry.capability,
                sif_path=entry.sif_path,
                sif_sha256=entry.sif_sha256,
                runtime_profile_digest=entry.profile_digest(),
                qualification=qualification_for(entry.capability),
                provider=eff_provider,
                site_profile_id=site_id,
                runtime_profile_id=entry.runtime_profile_id,
                artifact_kind="sif",
                artifact_path_or_id=entry.sif_path,
                digest=entry.sif_sha256,
                software_versions=entry.software_versions,
            )

        elif entry.artifact_kind == "compshare_image":
            if not entry.artifact_path_or_id or is_placeholder_artifact(entry.artifact_path_or_id):
                raise RuntimeResolutionError(
                    f"locked runtime {name!r} ({entry.source}) has no concrete "
                    "CompShare ImageId on this site (placeholder/unqualified rejected)"
                )
            if not entry.digest or is_placeholder_artifact(entry.digest):
                raise RuntimeResolutionError(
                    f"locked runtime {name!r} ({entry.source}) has no captured "
                    "image digest (placeholder/unqualified rejected)"
                )
            if digest is not None and digest != entry.digest:
                raise RuntimeResolutionError(
                    f"declared digest for {name!r} does not match the locked "
                    "runtime; digests are infra assertions, not Agent choices"
                )
            return ResolvedRuntime(
                capability=entry.capability,
                sif_path="",
                sif_sha256=entry.digest,
                runtime_profile_digest=entry.profile_digest(),
                qualification=qualification_for(entry.capability),
                provider=eff_provider,
                site_profile_id=site_id,
                runtime_profile_id=entry.runtime_profile_id,
                artifact_kind="compshare_image",
                artifact_path_or_id=entry.artifact_path_or_id,
                digest=entry.digest,
                software_versions=entry.software_versions,
            )

        return ResolvedRuntime(
            capability=entry.capability,
            sif_path=entry.sif_path,
            sif_sha256=entry.digest,
            runtime_profile_digest=entry.profile_digest(),
            qualification=qualification_for(entry.capability),
            provider=eff_provider,
            site_profile_id=site_id,
            runtime_profile_id=entry.runtime_profile_id,
            artifact_kind=entry.artifact_kind,
            artifact_path_or_id=entry.artifact_path_or_id,
            digest=entry.digest,
            software_versions=entry.software_versions,
        )


def default_resolver(lock_dir: Path | None = None) -> RuntimeResolver:
    """Return resolver initialized from reference/runtime directory."""
    if lock_dir is None:
        lock_dir = Path(__file__).resolve().parents[2] / "reference" / "runtime"
    return RuntimeResolver.from_lock_dir(lock_dir)
