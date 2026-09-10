"""Benchmark execution runner."""

from __future__ import annotations

from bench.core.harness import *  # noqa: F401, F403


def run_benchmark_case(*args, **kwargs):
    """Facade for running a benchmark case."""
    from bench.core.harness import run_experiment
    return run_experiment(*args, **kwargs)
