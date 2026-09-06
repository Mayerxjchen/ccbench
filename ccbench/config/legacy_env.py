"""Legacy environment variable migration and deprecation layer for ccbench.

Single source of truth for translating deprecated DFTWORLD_* and MLFFBENCH_*
environment variables to their canonical CCBENCH_* equivalents with explicit
deprecation warnings.
"""

from __future__ import annotations

import os
import warnings
from typing import Mapping

# Mapping from canonical CCBENCH_* name to list of deprecated legacy fallback names
CANONICAL_TO_LEGACY: dict[str, tuple[str, ...]] = {
    "CCBENCH_API_KEY": ("DFTWORLD_API_KEY", "MLFFBENCH_API_KEY"),
    "CCBENCH_BASE_URL": ("DFTWORLD_API_ENDPOINT", "MLFFBENCH_BASE_URL"),
    "CCBENCH_OFFLINE": ("DFTWORLD_OFFLINE", "MLFFBENCH_OFFLINE"),
    "CCBENCH_PYTHON": ("DFTWORLD_PYTHON", "MLFFBENCH_PYTHON"),
    "CCBENCH_BASE_IMAGE": ("DFTWORLD_BASE_IMAGE", "MLFFBENCH_BASE_IMAGE"),
    "CCBENCH_VERIFY_MAX_FILES": ("DFTWORLD_VERIFY_MAX_FILES",),
    "CCBENCH_VERIFY_MAX_SINGLE_BYTES": ("DFTWORLD_VERIFY_MAX_SINGLE_BYTES",),
    "CCBENCH_VERIFY_MAX_TOTAL_BYTES": ("DFTWORLD_VERIFY_MAX_TOTAL_BYTES",),
    "CCBENCH_SMOKE_ENDPOINT": ("DFTWORLD_SMOKE_ENDPOINT",),
    "CCBENCH_SMOKE_CREDENTIAL": ("DFTWORLD_SMOKE_CREDENTIAL",),
}

# Inverted mapping for fast legacy lookup
LEGACY_TO_CANONICAL: dict[str, str] = {
    legacy: canonical
    for canonical, leg_list in CANONICAL_TO_LEGACY.items()
    for legacy in leg_list
}


def get_env(
    name: str,
    default: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    warn: bool = True,
) -> str | None:
    """Retrieve an environment variable value with transparent legacy fallback.

    Args:
        name: Canonical (preferred) or legacy environment variable name.
        default: Default value if neither canonical nor legacy variable is set.
        environ: Environment mapping to query (defaults to os.environ).
        warn: Whether to emit a DeprecationWarning if a legacy variable is used.

    Returns:
        The resolved environment variable value or default.
    """
    env = os.environ if environ is None else environ

    # If queried by canonical name
    if name in CANONICAL_TO_LEGACY:
        canonical = name
        if canonical in env:
            return env[canonical]
        for legacy in CANONICAL_TO_LEGACY[canonical]:
            if legacy in env:
                if warn:
                    warnings.warn(
                        f"Environment variable '{legacy}' is deprecated; use '{canonical}' instead.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
                return env[legacy]
        return default

    # If queried directly by legacy name
    if name in LEGACY_TO_CANONICAL:
        canonical = LEGACY_TO_CANONICAL[name]
        if canonical in env:
            return env[canonical]
        if name in env:
            if warn:
                warnings.warn(
                    f"Environment variable '{name}' is deprecated; use '{canonical}' instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            return env[name]
        return default

    # Plain env query
    return env.get(name, default)


def get_env_int(
    name: str,
    default: int,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Retrieve an integer environment variable with legacy fallback."""
    val = get_env(name, environ=environ)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default
