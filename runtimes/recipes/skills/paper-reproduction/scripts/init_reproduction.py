#!/usr/bin/env python
"""Scaffold a paper-reproduction workspace.

Usage (from the directory where the line of work should live):

    uv run scripts/init_reproduction.py <slug> \
        --paper "10.xxxx/xxxxx" --paper-title "Short citation"

Creates:

    reproduction/<slug>/
    ├── reproduction-contract.v1.yaml   (copy of templates/reproduction-contract.yaml)
    ├── plan.md                          (empty DAG plan placeholder)
    ├── input/                           (hash-pinned inputs go here)
    ├── runs/                            (run-001/, run-002/, ...)
    ├── diagnosis/discrepancy-log.md
    ├── comparison/comparison-table.md
    ├── report/final-report.md
    └── export/benchmark-export.md

The contract copy is a *draft* (empty freeze_hash). Freeze it only after it is
complete:

    uv run scripts/validate_reproduction.py contract \
        reproduction/<slug>/reproduction-contract.v1.yaml --freeze
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
TEMPLATE_DIR = SKILL_DIR / "templates"

SIMPLE_TEMPLATES = {
    "diagnosis/discrepancy-log.md": "discrepancy-log.md",
    "comparison/comparison-table.md": "comparison-table.md",
    "report/final-report.md": "final-report.md",
    "export/benchmark-export.md": "benchmark-export.md",
}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="kebab-case workspace slug, e.g. li-huang-2024-ads")
    parser.add_argument("--paper", default="", help="DOI of the reproduced paper")
    parser.add_argument("--paper-title", default="", help="short citation / title")
    args = parser.parse_args()

    if not _SLUG_RE.match(args.slug):
        parser.error(f"slug must be kebab-case [a-z0-9-]: {args.slug!r}")
    if args.paper:
        args.paper = args.paper.strip()

    workdir = Path.cwd() / "reproduction" / args.slug
    if workdir.exists() and any(workdir.iterdir()):
        print(f"ERROR: {workdir} already exists and is not empty; refusing to overwrite.")
        return 1
    workdir.mkdir(parents=True, exist_ok=True)

    # Contract draft copied from the skill template.
    contract_src = TEMPLATE_DIR / "reproduction-contract.yaml"
    contract_dst = workdir / "reproduction-contract.v1.yaml"
    text = contract_src.read_text(encoding="utf-8")
    if args.paper:
        text = text.replace(
            'doi: "10.xxxx/xxxxx"', f'doi: "{args.paper}"', 1
        )
    if args.paper_title:
        text = re.sub(
            r'citation: "Author, A\. et al\. Title\. Journal YYYY\."',
            f'citation: "{args.paper_title}"',
            text,
            count=1,
        )
    contract_dst.write_text(text, encoding="utf-8")

    # Workspace subdirs and simple template copies.
    for sub in ("runs", "input"):
        (workdir / sub).mkdir(parents=True, exist_ok=True)
    for dst_rel, tpl in SIMPLE_TEMPLATES.items():
        dst = workdir / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(
            (TEMPLATE_DIR / tpl).read_text(encoding="utf-8"), encoding="utf-8"
        )
    plan = workdir / "plan.md"
    plan.write_text(
        "# Execution DAG plan\n\nFill per research-state.md: stages with inputs, "
        "commands, outputs, and verification, starting from the frozen contract.\n",
        encoding="utf-8",
    )

    print(f"Created paper-reproduction workspace: {workdir}")
    print(f"  contract draft: {contract_dst.name}  (freeze after it is complete)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
