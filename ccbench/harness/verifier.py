"""Case verifier execution wrapper."""

from __future__ import annotations

from ccbench.core.verifier import *  # noqa: F401, F403


def run_case_verifier(*args, **kwargs):
    """Facade for running a case verifier."""
    from ccbench.core.verifier import run_verifier
    return run_verifier(*args, **kwargs)
