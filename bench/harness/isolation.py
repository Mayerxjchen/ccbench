"""Physical and filesystem isolation harness."""

from __future__ import annotations

from bench.core.quarantine import *  # noqa: F401, F403


def isolate_workspace(*args, **kwargs):
    """Facade for setting up an isolated workspace."""
    from bench.core.quarantine import QuarantineContext
    return QuarantineContext(*args, **kwargs)
