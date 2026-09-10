"""bench paths — Single source of truth for canonical repository paths."""

from __future__ import annotations

from pathlib import Path

# Repository root (parent of the bench/ package directory)
ROOT: Path = Path(__file__).resolve().parents[1]

RUNTIMES_DIR: Path = ROOT / "runtimes"
RUNTIME_RECIPES_DIR: Path = RUNTIMES_DIR / "recipes"
RUNTIME_LOCKS_DIR: Path = RUNTIMES_DIR / "locks"
RUNTIME_PROVENANCE_DIR: Path = RUNTIMES_DIR / "provenance"
SCHEMAS_DIR: Path = ROOT / "schemas"
SCRIPTS_DIR: Path = ROOT / "scripts"
TESTS_DIR: Path = ROOT / "tests"
