#!/usr/bin/env python3
"""Portfolio coverage registry — scan cases and produce a coverage report.

Scans all ``cases/*/case.toml``, reads coverage tags from each, and produces
a portfolio report with:

- Per-case coverage dimensions
- Dimension distribution (how many cases share each value)
- Concentration flags (values with >50% share)
- Gap detection (dimensions with empty values)

Usage::

    python scripts/portfolio/registry.py --cases-dir cases/ --output portfolio-report.json

Exit codes: 0 = report written; 1 = error.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dftworld_bench.contracts.case import CaseSpec, CoverageTags

DIMENSIONS = ("scientific_domain", "method_family", "material_class", "computation_type")


def scan_cases(cases_dir: Path) -> list[dict]:
    """Load all cases and extract coverage information."""
    cases = []
    for case_dir in sorted(cases_dir.iterdir()):
        if not case_dir.is_dir():
            continue
        case_toml = case_dir / "case.toml"
        task_toml = case_dir / "task.toml"
        if not (case_toml.exists() or task_toml.exists()):
            continue
        try:
            spec = CaseSpec.load(case_dir)
        except Exception as exc:
            print(f"[registry] SKIP {case_dir.name}: {exc}", file=sys.stderr)
            continue
        cov = spec.coverage
        cases.append({
            "case_id": case_dir.name,
            "benchmark_id": spec.case_id,
            "execution_class": spec.execution_class,
            "coverage": {
                dim: getattr(cov, dim) for dim in DIMENSIONS
            },
            "has_coverage": any(getattr(cov, dim) for dim in DIMENSIONS),
        })
    return cases


def compute_distribution(cases: list[dict]) -> dict[str, dict[str, int]]:
    """Count cases per value for each dimension."""
    dist: dict[str, dict[str, int]] = {}
    for dim in DIMENSIONS:
        counter: Counter[str] = Counter()
        for c in cases:
            val = c["coverage"].get(dim, "")
            if val:
                counter[val] += 1
        dist[dim] = dict(counter.most_common())
    return dist


def find_concentration(dist: dict[str, dict[str, int]], total: int) -> list[dict]:
    """Flag dimension values with >50% share."""
    flags = []
    for dim, counts in dist.items():
        for value, count in counts.items():
            if total > 0 and count / total > 0.5:
                flags.append({
                    "dimension": dim,
                    "value": value,
                    "count": count,
                    "total": total,
                    "fraction": round(count / total, 2),
                })
    return flags


def find_gaps(cases: list[dict]) -> list[dict]:
    """Find cases with missing coverage tags."""
    gaps = []
    for c in cases:
        if not c["has_coverage"]:
            gaps.append({"case_id": c["case_id"], "reason": "no [coverage] block"})
        else:
            missing = [dim for dim in DIMENSIONS if not c["coverage"].get(dim)]
            if missing:
                gaps.append({"case_id": c["case_id"], "missing_dimensions": missing})
    return gaps


def compute_diversity_score(dist: dict[str, dict[str, int]], total: int) -> dict:
    """Simple diversity metric: unique values per dimension, normalized."""
    if total == 0:
        return {"per_dimension": {}, "overall": 0.0}
    per_dim = {}
    for dim, counts in dist.items():
        unique = len(counts)
        per_dim[dim] = {
            "unique_values": unique,
            "normalized": round(unique / total, 3) if total > 0 else 0.0,
        }
    overall = round(
        sum(d["normalized"] for d in per_dim.values()) / len(per_dim), 3
    ) if per_dim else 0.0
    return {"per_dimension": per_dim, "overall": overall}


def build_report(cases_dir: Path) -> dict:
    """Build the full portfolio report."""
    cases = scan_cases(cases_dir)
    total = len(cases)
    covered = sum(1 for c in cases if c["has_coverage"])
    dist = compute_distribution(cases)
    concentration = find_concentration(dist, total)
    gaps = find_gaps(cases)
    diversity = compute_diversity_score(dist, total)

    return {
        "schema_version": 1,
        "source_dir": str(cases_dir),
        "summary": {
            "total_cases": total,
            "tagged_cases": covered,
            "untagged_cases": total - covered,
        },
        "distribution": dist,
        "concentration_flags": concentration,
        "gaps": gaps,
        "diversity": diversity,
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases-dir", type=Path, default=ROOT / "cases",
                        help="Directory containing case subdirectories")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write JSON report to this path")
    parser.add_argument("--summary", action="store_true",
                        help="Print human-readable summary to stdout")
    args = parser.parse_args(argv)

    if not args.cases_dir.is_dir():
        print(f"[registry] cases directory not found: {args.cases_dir}", file=sys.stderr)
        return 1

    report = build_report(args.cases_dir)
    payload = json.dumps(report, indent=2, sort_keys=False) + "\n"

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"[registry] report written to {args.output}")

    if args.summary or not args.output:
        s = report["summary"]
        print(f"\nPortfolio: {s['total_cases']} cases ({s['tagged_cases']} tagged, "
              f"{s['untagged_cases']} untagged)")
        print(f"\nDiversity score: {report['diversity']['overall']}")
        for dim, info in report["diversity"]["per_dimension"].items():
            print(f"  {dim}: {info['unique_values']} unique values "
                  f"(normalized {info['normalized']})")
        if report["concentration_flags"]:
            print("\nConcentration flags (>50% share):")
            for f in report["concentration_flags"]:
                print(f"  {f['dimension']}={f['value']}: "
                      f"{f['count']}/{f['total']} ({f['fraction']:.0%})")
        if report["gaps"]:
            print("\nGaps:")
            for g in report["gaps"]:
                print(f"  {g['case_id']}: {g.get('reason') or g.get('missing_dimensions')}")

    if args.output and not args.summary:
        print(payload, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
