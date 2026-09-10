#!/usr/bin/env python3
"""Compatibility entry point for the public Bench runner.

The lifecycle implementation lives in :mod:`bench.pilot`; this file is kept
as a small, stable command for paper packs that use ``python eval.py CASE``.
It deliberately does not retain the former experiment/harness implementation.
All paths are resolved by the same ``bench run`` CLI used by operators.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence


_COMMANDS = {
    "run", "pilot", "suite", "runtime", "setup", "doctor", "site", "compute",
    "mvp",
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run one case through the canonical ``bench run`` lifecycle.

    Existing callers may pass an explicit Bench subcommand; the usual paper
    pack form (``eval.py 001-case --config ...``) is normalized to ``run``.
    """
    from bench.cli import main as bench_main

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return bench_main(["--help"])
    if args[0] not in _COMMANDS:
        args.insert(0, "run")
    return int(bench_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
