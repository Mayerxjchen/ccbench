"""ccbench — Computational Chemistry Benchmark package alias shim.

Provides unified `ccbench` package namespace while maintaining backwards compatibility
with `dftworld_bench`.
"""

from __future__ import annotations

import importlib
import sys
from importlib.machinery import ModuleSpec
import dftworld_bench


class _ProxyLoader:
    def __init__(self, mod):
        self.mod = mod

    def create_module(self, spec):
        return self.mod

    def exec_module(self, module):
        pass


class _CcbenchAliasFinder:
    """Redirects imports from ccbench.* directly to dftworld_bench.* with 1:1 module identity."""

    @classmethod
    def find_spec(cls, fullname: str, path=None, target=None):
        if fullname.startswith("ccbench."):
            tgt = "dftworld_bench." + fullname.removeprefix("ccbench.")
            try:
                mod = importlib.import_module(tgt)
                return ModuleSpec(fullname, _ProxyLoader(mod))
            except ImportError:
                return None
        return None


if not any(
    isinstance(finder, type) and finder.__name__ == "_CcbenchAliasFinder"
    for finder in sys.meta_path
):
    sys.meta_path.insert(0, _CcbenchAliasFinder)

__path__ = list(dftworld_bench.__path__)
__all__ = getattr(dftworld_bench, "__all__", [])
