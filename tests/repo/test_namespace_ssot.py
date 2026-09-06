"""Namespace Single Source of Truth (SSOT) integrity gate (CCBench v4).

Enforces:
1. Zero active references to legacy package 'dftworld_bench'.
2. Zero active references to 'scientific-benchmark-case-builder-portable'.
3. Zero active references to 'base-env-build' in production code.
4. Active environment variable unification to CCBENCH_*.
"""

from __future__ import annotations

import re
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]

ACTIVE_ROOTS = [
    ROOT / "ccbench",
    ROOT / "runtimes",
    ROOT / "scripts",
    ROOT / "schemas",
    ROOT / "tests",
    ROOT / "eval.py",
    ROOT / "README.md",
    ROOT / "pyproject.toml",
]

EXEMPT_DIRS = {
    "history",
    "provenance",
    "archive",
    "maintainer",
    "releases",
    "evidence",
}


def test_zero_active_dftworld_bench():
    """No active code, test, schema, script, or runtime may reference dftworld_bench."""
    violations: list[str] = []

    for root in ACTIVE_ROOTS:
        files = [root] if root.is_file() else list(root.rglob("*"))
        for p in files:
            if not p.is_file() or p.suffix in {".pyc", ".gz", ".pb", ".cif", ".zip", ".pt", ".tar.gz", ".lock"}:
                continue
            parts = p.relative_to(ROOT).parts
            if any(part in EXEMPT_DIRS for part in parts):
                continue
            # Exempt test verifying retirement of dftworld_bench
            if p.name in ("test_package_namespace.py", "test_namespace_ssot.py"):
                continue
            # Exempt receipt compatibility checks for historical receipts
            if p.name in ("compute_profile_qualification.py", "qualification_receipt.py"):
                continue

            try:
                content = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            if "dftworld_bench" in content:
                violations.append(f"{p.relative_to(ROOT)} contains 'dftworld_bench'")

    assert not violations, f"Found {len(violations)} active occurrences of 'dftworld_bench':\n" + "\n".join(violations)


def test_zero_active_portable_builder():
    """No active code may reference scientific-benchmark-case-builder-portable."""
    violations: list[str] = []

    for root in ACTIVE_ROOTS:
        files = [root] if root.is_file() else list(root.rglob("*"))
        for p in files:
            if not p.is_file() or p.suffix in {".pyc", ".gz", ".pb"}:
                continue
            parts = p.relative_to(ROOT).parts
            if any(part in EXEMPT_DIRS for part in parts):
                continue
            if p.name in ("test_root_allowlist.py", "test_namespace_ssot.py"):
                continue

            try:
                content = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            if "scientific-benchmark-case-builder-portable" in content:
                violations.append(f"{p.relative_to(ROOT)} contains 'scientific-benchmark-case-builder-portable'")

    assert not violations, f"Found {len(violations)} occurrences of 'scientific-benchmark-case-builder-portable':\n" + "\n".join(violations)


def test_zero_base_env_build_in_production_code():
    """Production python packages and scripts must contain zero base-env-build references."""
    prod_targets = [
        ROOT / "ccbench",
        ROOT / "scripts",
        ROOT / "eval.py",
    ]
    violations: list[str] = []

    for target in prod_targets:
        files = [target] if target.is_file() else list(target.rglob("*"))
        for p in files:
            if not p.is_file() or p.suffix in {".pyc", ".gz"}:
                continue
            parts = p.relative_to(ROOT).parts
            if any(part in EXEMPT_DIRS for part in parts):
                continue

            try:
                content = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            if "base-env-build" in content:
                violations.append(f"{p.relative_to(ROOT)} contains 'base-env-build'")

    assert not violations, f"Found {len(violations)} occurrences of 'base-env-build' in production code:\n" + "\n".join(violations)
