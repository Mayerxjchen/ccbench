"""Test configuration for case-specific unit tests.

These tests evaluate deep scientific artifacts and are executed inside their
corresponding container runtimes (e.g. dftworld-base-matclaw-cips, deepmd-jax).
When executed on a lightweight host environment without scientific libraries,
collection is gracefully skipped.
"""

from __future__ import annotations

try:
    import numpy  # noqa: F401
except ImportError:
    collect_ignore_glob = ["*.py", "**/*.py"]
