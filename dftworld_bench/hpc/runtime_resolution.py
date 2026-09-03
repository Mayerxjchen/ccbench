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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from dftworld_bench.contracts.case import RUNTIME_FAMILY_CAPABILITIES

# name is the same token grammar the execution-request schema accepts; the
# digest suffix is optional (its presence marks the legacy compat form).
RUNTIME_DECL_RE = re.compile(
    r"^(?P<name>[a-z0-9][a-z0-9./_-]*)(?:@sha256:(?P<digest>[0-9a-f]{64}))?$"
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


@dataclass(frozen=True)
class RuntimeLockEntry:
    """One locked runtime identity, read from a ``<capability>-runtime.lock.json``."""

    capability: str
    image_name: str
    sif_path: str
    sif_sha256: str
    source: str

    @classmethod
    def from_lock_doc(
        cls, capability: str, doc: Mapping[str, object], *, source: str
    ) -> "RuntimeLockEntry":
        runtime = doc.get("runtime")
        if not isinstance(runtime, Mapping):
            raise RuntimeResolutionError(
                f"lock {source}: missing 'runtime' block for capability {capability!r}"
            )
        image_name = str(doc.get("image_name") or capability)
        sif_path = str(runtime.get("sif_path_remote") or "")
        sif_sha = str(runtime.get("sif_sha256") or "")
        return cls(
            capability=capability,
            image_name=image_name,
            sif_path=sif_path,
            sif_sha256=sif_sha,
            source=source,
        )

    def profile_digest(self) -> str:
        """Stable identity of the resolved profile (excludes wall-clock data)."""
        canon = json.dumps(
            {
                "capability": self.capability,
                "image_name": self.image_name,
                "sif_path": self.sif_path,
                "sif_sha256": self.sif_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResolvedRuntime:
    """Server-side answer to 'which concrete runtime runs this capability'."""

    capability: str
    sif_path: str
    sif_sha256: str
    runtime_profile_digest: str
    qualification: str

    @property
    def declaration(self) -> str:
        """The sealed digest-shaped form downstream contracts expect."""
        return f"{self.capability}@sha256:{self.sif_sha256}"

    def to_response(self) -> dict[str, str]:
        return {
            "capability": self.capability,
            "sif_sha256": self.sif_sha256,
            "runtime_profile_digest": self.runtime_profile_digest,
            "qualification": self.qualification,
        }


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

    def capabilities(self) -> list[str]:
        """Capability tokens agents may name (image-name aliases excluded)."""
        return sorted(
            {
                entry.capability
                for entry in self._by_name.values()
                if CAPABILITY_RE.match(entry.capability)
            }
        )

    def runtime_store(self) -> dict[str, str]:
        """Digest -> SIF path map for adapters (locked runtimes only)."""
        return {
            entry.sif_sha256: entry.sif_path
            for entry in set(self._by_name.values())
            if entry.sif_sha256 and entry.sif_path
            and "<" not in entry.sif_path
        }

    def resolve(self, decl: str) -> ResolvedRuntime:
        name, digest = split_runtime(decl)
        entry = self._by_name.get(name)
        if entry is None:
            if digest is not None:
                # Hidden compat path (removal deferred to Phase 8): a legacy
                # digest-shaped declaration for a runtime this site does not
                # lock passes through unchanged. Capability tokens never get
                # this exemption.
                return ResolvedRuntime(
                    capability=name,
                    sif_path="",
                    sif_sha256=digest,
                    runtime_profile_digest="",
                    qualification=qualification_for(name),
                )
            raise RuntimeResolutionError(
                f"unknown runtime capability {name!r}; this site resolves: "
                f"{', '.join(self.capabilities())}"
            )
        if not entry.sif_sha256:
            raise RuntimeResolutionError(
                f"locked runtime {name!r} ({entry.source}) has no captured "
                "SIF digest; the qualification gate stays NOT_RUN until the "
                "site records it"
            )
        if digest is not None and digest != entry.sif_sha256:
            raise RuntimeResolutionError(
                f"declared digest for {name!r} does not match the locked "
                "runtime; digests are infra assertions, not Agent choices"
            )
        if not entry.sif_path or "<" in entry.sif_path:
            raise RuntimeResolutionError(
                f"locked runtime {name!r} ({entry.source}) has no concrete "
                "SIF path on this site"
            )
        return ResolvedRuntime(
            capability=entry.capability,
            sif_path=entry.sif_path,
            sif_sha256=entry.sif_sha256,
            runtime_profile_digest=entry.profile_digest(),
            qualification=qualification_for(entry.capability),
        )
