"""Runtime identities: registry (requirements -> RuntimeSet) and qualification."""

from ccbench.runtime.qualify import (
    QualificationReport,
    qualify_runtime,
)
from ccbench.runtime.registry import (
    ROLES,
    RuntimeIdentity,
    RuntimeProfile,
    RuntimeRegistry,
    RuntimeRegistryError,
    RuntimeRequirement,
    RuntimeSet,
    default_profiles,
    satisfies,
)

__all__ = [
    "ROLES",
    "QualificationReport",
    "RuntimeIdentity",
    "RuntimeProfile",
    "RuntimeRegistry",
    "RuntimeRegistryError",
    "RuntimeRequirement",
    "RuntimeSet",
    "default_profiles",
    "qualify_runtime",
    "satisfies",
]
