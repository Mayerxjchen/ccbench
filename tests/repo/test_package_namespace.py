"""Tests for ccbench unified package namespace convergence (SSOT)."""

from __future__ import annotations
import pytest


def test_ccbench_primary_package_importable():
    """ccbench must be importable as the primary package namespace."""
    import ccbench

    assert hasattr(ccbench, "__path__")


def test_submodule_resolution_via_ccbench():
    """Core submodules must be cleanly accessible via ccbench namespace."""
    import ccbench.cli as cc_cli
    assert callable(cc_cli.main)

    from ccbench.contracts.case import CaseSpec
    assert CaseSpec is not None


def test_dftworld_bench_is_retired():
    """Legacy package dftworld_bench must not exist or be importable."""
    with pytest.raises(ModuleNotFoundError):
        import dftworld_bench  # type: ignore[import-not-found]

