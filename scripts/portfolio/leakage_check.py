#!/usr/bin/env python3
"""Conditional DOI/paper-identity leakage check for research-question cases.

ONLY applies to cases with ``coverage.paradigm == "research_question"``.
Standard benchmark cases (MLP training/validation) are expected to reference
DOIs and paper titles — that's normal provenance.  Research-question cases
need paper-identity isolation so the agent cannot cheat by looking up the
source paper.

Usage::

    python scripts/portfolio/leakage_check.py --case cases/001-some-case

Exit codes: 0 = no leakage found (or not a research-question case);
1 = leakage detected; 2 = structural error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dftworld_bench.contracts.case import CaseSpec

# DOI pattern: 10.XXXX/... (broad match, not RFC-complete)
DOI_RE = re.compile(
    rb"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+",
    re.IGNORECASE,
)

# Patterns that suggest paper title / author identity leakage
PAPER_TITLE_PATTERNS = [
    re.compile(rb"(?i)\bdoi\s*:\s*10\.\d{4,9}/"),
    re.compile(rb"(?i)\barxiv\s*:\s*\d{4}\.\d{4,5}"),
    re.compile(rb"(?i)\b(et\s+al\.?|and\s+colleagues?)\b.*\(\d{4}\)"),
]


def _is_research_question(case_dir: Path) -> bool:
    """Check if this case uses the research-question paradigm.

    Looks for coverage.paradigm or a selection/ directory with
    research-question artifacts.
    """
    case_toml = case_dir / "case.toml"
    if not case_toml.exists():
        return False
    try:
        import tomllib
        raw = tomllib.loads(case_toml.read_text(encoding="utf-8"))
        coverage = raw.get("coverage") or {}
        if coverage.get("paradigm") == "research_question":
            return True
        if raw.get("paradigm") == "research_question":
            return True
        if "research_question" in raw:
            return True
    except Exception:
        pass
    # Also check for selection directory (research-question workflow artifact)
    selection_dir = case_dir / "selection"
    if selection_dir.is_dir() and (selection_dir / "state.yaml").exists():
        return True
    return False


def _safe_relative(path: Path, base: Path) -> str:
    """Return relative path string, falling back to the filename if not relative."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def check_file_for_dois(file_path: Path) -> list[dict]:
    """Check a single file for DOI identifiers."""
    findings = []
    try:
        content = file_path.read_bytes()
    except (OSError, PermissionError):
        return findings
    for match in DOI_RE.finditer(content):
        doi = match.group().decode("utf-8", errors="replace")
        # Compute line number
        line_num = content[:match.start()].count(b"\n") + 1
        findings.append({
            "file": _safe_relative(file_path, ROOT),
            "line": line_num,
            "doi": doi,
            "type": "doi_reference",
        })
    return findings


def check_file_for_paper_identity(file_path: Path) -> list[dict]:
    """Check a file for patterns suggesting paper title/author leakage."""
    findings = []
    try:
        content = file_path.read_bytes()
    except (OSError, PermissionError):
        return findings
    for pattern in PAPER_TITLE_PATTERNS:
        for match in pattern.finditer(content):
            snippet = match.group().decode("utf-8", errors="replace")[:80]
            line_num = content[:match.start()].count(b"\n") + 1
            findings.append({
                "file": _safe_relative(file_path, ROOT),
                "line": line_num,
                "snippet": snippet,
                "type": "paper_identity_pattern",
            })
    return findings


def get_candidate_visible_files(case_dir: Path) -> set[Path]:
    """Resolve all files visible to the candidate based on case contract allowlist.

    If CaseSpec can be loaded:
    - spec.instruction_path (candidate.instruction)
    - All files matching spec.public_files rules (candidate.files source patterns)
    Fallback (non-canonical cases):
    - task.md / instruction.md
    - input/ directory
    """
    visible_files: set[Path] = set()

    spec = None
    try:
        spec = CaseSpec.load(case_dir)
    except Exception:
        pass

    if spec is not None:
        # 1. Instruction file
        instr = case_dir / spec.instruction_path
        if instr.is_file():
            visible_files.add(instr.resolve())

        # 2. Public files declared in [candidate.files] (or legacy public rule)
        for rule in spec.public_files:
            src_str = rule.source.strip()
            matched_paths = list(case_dir.glob(src_str))
            if not matched_paths:
                direct = case_dir / src_str
                if direct.exists():
                    matched_paths = [direct]

            for p in matched_paths:
                if p.is_file() and not p.name.startswith("."):
                    visible_files.add(p.resolve())
                elif p.is_dir():
                    for sub in p.rglob("*"):
                        if sub.is_file() and not sub.name.startswith("."):
                            visible_files.add(sub.resolve())
    else:
        # Fallback for non-canonical case directories
        for name in ("task.md", "instruction.md"):
            p = case_dir / name
            if p.is_file():
                visible_files.add(p.resolve())
        input_dir = case_dir / "input"
        if input_dir.is_dir():
            for p in input_dir.rglob("*"):
                if p.is_file() and not p.name.startswith("."):
                    visible_files.add(p.resolve())

    return visible_files


def check_case_leakage(case_dir: Path) -> list[dict]:
    """Run leakage checks on a case's candidate-visible files.

    Dynamically resolves candidate-visible files from CaseSpec (candidate.instruction
    and candidate.files allowlist rules), preventing leakage via custom public paths.
    Only checks files that would be visible to the agent candidate:
    - instruction/task files
    - public allowlist files
    - NOT reference/, solution/, tests/, verifier/
    """
    if not _is_research_question(case_dir):
        return []

    findings = []
    visible_files = get_candidate_visible_files(case_dir)

    for path in sorted(visible_files):
        findings.extend(check_file_for_dois(path))
        findings.extend(check_file_for_paper_identity(path))

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", type=Path, required=True,
                        help="Case directory to check")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write JSON findings to this path")
    parser.add_argument("--all-cases", type=Path, default=None,
                        help="Check all cases under this directory")
    args = parser.parse_args(argv)

    cases_to_check = []
    if args.all_cases:
        for d in sorted(args.all_cases.iterdir()):
            if d.is_dir() and (d / "case.toml").exists():
                cases_to_check.append(d)
    else:
        cases_to_check.append(args.case)

    all_findings = []
    for case_dir in cases_to_check:
        findings = check_case_leakage(case_dir)
        if findings:
            for f in findings:
                f["case"] = case_dir.name
            all_findings.extend(findings)
            print(f"[leakage] {case_dir.name}: {len(findings)} finding(s)")
        else:
            is_rq = _is_research_question(case_dir)
            status = "not research-question (skipped)" if is_rq is False else "clean"
            print(f"[leakage] {case_dir.name}: {status}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"findings": all_findings}, indent=2) + "\n",
            encoding="utf-8",
        )

    if all_findings:
        print(f"\n[leakage] Total: {len(all_findings)} finding(s) across "
              f"{len({f['case'] for f in all_findings})} case(s)")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
