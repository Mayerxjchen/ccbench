"""Tests for bench unified package namespace convergence (SSOT)."""

from __future__ import annotations
import pytest


def test_bench_primary_package_importable():
    """bench must be importable as the primary package namespace."""
    import bench

    assert hasattr(bench, "__path__")


def test_submodule_resolution_via_bench():
    """Core submodules must be cleanly accessible via bench namespace."""
    import bench.cli as cc_cli
    assert callable(cc_cli.main)

    from bench.contracts.case import CaseSpec
    assert CaseSpec is not None


def test_dftworld_bench_is_retired():
    """Legacy package dftworld_bench must not exist or be importable."""
    with pytest.raises(ModuleNotFoundError):
        import dftworld_bench  # type: ignore[import-not-found]

