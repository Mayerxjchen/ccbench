"""Tests for ccbench unified package namespace and backwards compatibility (R3-17)."""

from __future__ import annotations


def test_ccbench_and_dftworld_bench_both_importable():
    """Both ccbench and dftworld_bench must be importable as first-class namespaces."""
    import dftworld_bench
    import ccbench

    assert hasattr(ccbench, "__path__")
    assert dftworld_bench.__path__[0] in ccbench.__path__


def test_submodule_resolution_via_ccbench():
    """Core submodules must be cleanly accessible via ccbench namespace."""
    import ccbench.cli as cc_cli
    import dftworld_bench.cli as df_cli

    assert cc_cli.main is df_cli.main

    from ccbench.contracts.case import CaseSpec as CCCaseSpec
    from dftworld_bench.contracts.case import CaseSpec as DFCaseSpec

    assert CCCaseSpec is DFCaseSpec
