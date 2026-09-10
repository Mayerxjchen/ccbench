"""Environment helpers for the canonical ``BENCH_*`` namespace."""

from __future__ import annotations

import os
from typing import Mapping


def get_env(
    name: str,
    default: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    env = os.environ if environ is None else environ
    return env.get(name, default)


def get_env_int(
    name: str,
    default: int,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    value = get_env(name, environ=environ)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
