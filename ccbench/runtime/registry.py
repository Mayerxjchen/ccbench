"""Runtime registry: resolve runtime requirements to a concrete RuntimeSet.

A runtime profile declares what it *provides* (capability name, family,
version), its platform, the execution classes it serves, and the immutable
image identity for one runtime role (candidate / control / compute /
verifier).  The resolver matches a run's requirements against the store,
computes set coverage over version constraints, and fails closed on zero,
ambiguous, or conflicting matches — it never guesses an image.

Resolution is case-agnostic: the caller states what the run needs and how it
executes; no case id or task name ever enters the registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ccbench.contracts.case import RuntimeRequirement  # canonical definition

# The four runtime roles a benchmark run needs.
ROLES = ("candidate", "control", "compute", "verifier")


class RuntimeRegistryError(ValueError):
    """Requirements could not be resolved to exactly one RuntimeSet."""


@dataclass(frozen=True)
class RuntimeProfile:
    """One immutable runtime identity for a single role."""

    name: str
    role: str
    family: str
    provides: tuple[str, ...]
    version: str
    platform: str
    execution_classes: frozenset[str]
    image: str
    digest: str | None = None  # sha256:... once the image is locked


@dataclass(frozen=True)
class RuntimeIdentity:
    """One role's resolved identity inside a RuntimeSet."""

    role: str
    profile: str
    image: str
    digest: str | None = None


@dataclass(frozen=True)
class RuntimeSet:
    """The four immutable runtime identities for one run."""

    candidate: RuntimeIdentity
    control: RuntimeIdentity
    compute: RuntimeIdentity
    verifier: RuntimeIdentity


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in value.replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _cmp(a: str, b: str) -> int:
    ta, tb = _version_tuple(a), _version_tuple(b)
    return (ta > tb) - (ta < tb)


def satisfies(version: str, constraint: str) -> bool:
    """True when *version* satisfies *constraint*.

    Supports ``==``, ``>=``, ``>``, ``<=``, ``<``, a bare dotted version
    (exact match), and ``*`` (any).  Dependency-free so the same code runs
    inside the controller images (stdlib only).
    """
    constraint = constraint.strip()
    if not constraint or constraint == "*":
        return True
    for op in (">=", "<=", "==", ">", "<"):
        if constraint.startswith(op):
            c = constraint[len(op):].strip()
            r = _cmp(version, c)
            if op == ">":
                return r > 0
            if op == "<":
                return r < 0
            if op == ">=":
                return r >= 0
            if op == "<=":
                return r <= 0
            return r == 0
    return _cmp(version, constraint) == 0


def default_profiles() -> tuple[RuntimeProfile, ...]:
    """The in-repo runtime store, grounded in the runtimes/recipes images.

    Digests are filled by qualification (scripts/infra/qualify_runtimes.py)
    and locked afterwards; until then the identity is the immutable image tag.
    """
    return (
        # -- compute ---------------------------------------------------------
        RuntimeProfile(
            name="dpmp-jax-v1",
            role="compute",
            family="deepmd-jax",
            provides=("dpmp",),
            version="0.2.1",
            platform="linux/amd64",
            execution_classes=frozenset({"local_sandbox", "hpc_controller"}),
            image="dftworld-base-deepmd-jax:0.1.0-cpu",
        ),
        RuntimeProfile(
            name="matclaw-cips-v2",
            role="compute",
            family="matclaw-cips",
            provides=("matclaw-cips",),
            version="2.2.11",
            platform="linux/amd64",
            execution_classes=frozenset({"local_sandbox", "hpc_controller"}),
            image="dftworld-base-matclaw-cips:2.2.11-cpu",
        ),
        RuntimeProfile(
            name="ai2kit-runtime-v1",
            role="compute",
            family="ai2kit",
            provides=("ai2kit",),
            version="1.1.0",
            platform="linux/amd64",
            execution_classes=frozenset({"local_sandbox", "hpc_controller"}),
            image="ai2kit-runtime-v1",
        ),
        # -- control ---------------------------------------------------------
        RuntimeProfile(
            name="bench-hpc-control-v1",
            role="control",
            family="bench-hpc",
            provides=("control",),
            version="1.0",
            platform="linux/amd64",
            execution_classes=frozenset({"hpc_controller"}),
            image="dftworld-base-matclaw-cips:2.2.11-controller",
        ),
        RuntimeProfile(
            name="local-control-v1",
            role="control",
            family="local",
            provides=("control",),
            version="1.0",
            platform="linux/amd64",
            execution_classes=frozenset({"local_sandbox"}),
            image="dftworld-base",
        ),
        # -- candidate -------------------------------------------------------
        RuntimeProfile(
            name="candidate-controller-v1",
            role="candidate",
            family="candidate",
            provides=("candidate",),
            version="1.0",
            platform="linux/amd64",
            execution_classes=frozenset({"hpc_controller"}),
            image="dftworld-base-matclaw-cips:2.2.11-controller",
        ),
        RuntimeProfile(
            name="candidate-local-v1",
            role="candidate",
            family="candidate",
            provides=("candidate",),
            version="1.0",
            platform="linux/amd64",
            execution_classes=frozenset({"local_sandbox"}),
            image="dftworld-base",
        ),
        # -- verifier --------------------------------------------------------
        RuntimeProfile(
            name="verifier-base-v1",
            role="verifier",
            family="verifier",
            provides=("verifier",),
            version="1.0",
            platform="linux/amd64",
            execution_classes=frozenset({"local_sandbox", "hpc_controller"}),
            image="dftworld-base",
        ),
    )


class RuntimeRegistry:
    """Maps requirements + execution class to exactly one RuntimeSet."""

    def __init__(self, profiles: Iterable[RuntimeProfile] | None = None) -> None:
        self._profiles = tuple(profiles) if profiles is not None else default_profiles()

    @property
    def profiles(self) -> tuple[RuntimeProfile, ...]:
        return self._profiles

    def resolve(
        self,
        requirements: list[RuntimeRequirement],
        execution_class: str,
    ) -> RuntimeSet:
        if not requirements:
            raise RuntimeRegistryError("no runtime requirements")
        control = self._exactly_one("control", execution_class)
        candidate = self._exactly_one("candidate", execution_class)
        verifier = self._exactly_one("verifier", execution_class)

        compute: RuntimeProfile | None = None
        for req in requirements:
            matches = [
                p for p in self._profiles
                if p.role == "compute"
                and p.family == req.family
                and req.name in p.provides
                and satisfies(p.version, req.version)
                and execution_class in p.execution_classes
            ]
            if not matches:
                raise RuntimeRegistryError(
                    f"no compute runtime satisfies {req.family}/{req.name} {req.version}"
                    f" for {execution_class!r}"
                )
            if len(matches) > 1:
                names = ", ".join(sorted(p.name for p in matches))
                raise RuntimeRegistryError(
                    f"ambiguous compute runtime for {req.family}/{req.name} {req.version}"
                    f" for {execution_class!r}: {names}"
                )
            if compute is not None and compute.name != matches[0].name:
                raise RuntimeRegistryError(
                    f"requirements resolve to conflicting compute runtimes: "
                    f"{compute.name} vs {matches[0].name}"
                )
            compute = matches[0]
        assert compute is not None  # guaranteed by the empty-requirement guard
        return RuntimeSet(
            candidate=self._identity(candidate),
            control=self._identity(control),
            compute=self._identity(compute),
            verifier=self._identity(verifier),
        )

    def _exactly_one(self, role: str, execution_class: str) -> RuntimeProfile:
        matches = [
            p for p in self._profiles
            if p.role == role and execution_class in p.execution_classes
        ]
        if len(matches) != 1:
            raise RuntimeRegistryError(
                f"execution class {execution_class!r} must map to exactly one "
                f"{role} runtime, found {len(matches)}"
            )
        return matches[0]

    @staticmethod
    def _identity(profile: RuntimeProfile) -> RuntimeIdentity:
        return RuntimeIdentity(
            role=profile.role,
            profile=profile.name,
            image=profile.image,
            digest=profile.digest,
        )
