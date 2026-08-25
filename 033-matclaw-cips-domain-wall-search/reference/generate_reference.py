#!/opt/matclaw/bin/python
"""Thin CLI wrapper over the single hidden verifier implementation in tests/.

The full check (analysis + verification) lives in tests/verifier.py so there is
exactly one implementation; this script only preserves the oracle CLI
(--submission/--profile/--write) used to produce regenerated_reference.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CASE / "tests"))

from verifier import verify  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", type=Path, default=Path("/app"))
    parser.add_argument("--profile", choices=("smoke", "paper"))
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    report = verify(args.submission.resolve(), args.profile)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.write:
        args.write.write_text(rendered)
    print(rendered, end="")
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
