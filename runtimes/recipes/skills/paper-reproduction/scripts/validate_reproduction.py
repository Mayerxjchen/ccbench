#!/usr/bin/env python
"""Deterministic validator for the paper-reproduction skill.

Subcommands
-----------
    validate_reproduction.py contract <contract.yaml> [--freeze]
        VALID DRAFT / VALID FROZEN / INVALID
    validate_reproduction.py run <run-record.yaml> [--contract <contract.yaml>]
        VALID RUN / INVALID
    validate_reproduction.py export <benchmark-export.md>
        VALID EXPORT / INVALID

Contract Freeze behaviour (contract-freeze.md):
  - draft missing fields              -> exit 0, "VALID DRAFT" + warnings
  - complete draft                    -> exit 0, "VALID DRAFT"
  - --freeze with a missing critical  -> nonzero, "INVALID"
  - --freeze on a complete contract   -> "VALID FROZEN" + freeze hash
  - content change after freeze       -> hash mismatch, "INVALID"
  - unknown information/verdict value -> "INVALID"

A run record must bind to a frozen contract: its freeze_hash must equal the
frozen contract's computed hash (checked when --contract is given) and must
not be blank. A benchmark export that leaks private/gold ground truth into the
candidate draft spec is INVALID.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

SCHEMA_VERSION = "1.0"

ALLOWED_INFORMATION_LEVELS = {
    "L1_full_reproduction",
    "L2_workflow_reconstruction",
    "L3_underspecified_reproduction",
}
ALLOWED_VERDICTS = {
    "reproduced",
    "scientifically_equivalent",
    "partially_reproduced",
    "reconstructed_result",
    "inconclusive",
    "contradicted",
}
ALLOWED_RUN_REASONS = {"initial", "faithful_fix", "gap_closure", "sensitivity_test"}
ALLOWED_OUTCOME_STATUS = {"completed", "failed", "contaminated", "cancelled"}
ALLOWED_CLASSIFICATIONS = {
    "PUBLIC_SOURCE",
    "MAINTAINER_SOURCE",
    "GOLD_SOURCE",
    "REFERENCE_SOURCE",
}

# Top-level keys that must be present and non-blank for a contract to freeze.
CONTRACT_REQUIRED = (
    "schema_version",
    "contract_version",
    "paper_identity",
    "evidence_sources",
    "information_level",
    "reproduction_scope",
    "non_goals",
    "target_claim",
    "method_fingerprint",
    "assumptions",
    "comparison_rule",
    "acceptance_criterion",
    "tolerance",
    "input_files",
)

RUN_REQUIRED = ("run_id", "contract_version", "freeze_hash", "run_reason")


def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def load_yaml(path: Path):
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return None, f"invalid YAML: {exc}"
    if doc is None:
        # Empty or comment-only document: a blank draft. Required-field checks
        # downstream decide whether that is merely draft-warned or invalid.
        return {}, None
    if not isinstance(doc, dict):
        return None, f"expected a YAML mapping at top level, got {type(doc).__name__}"
    return doc, None


def freeze_hash_of(doc: dict) -> str:
    """SHA-256 over the canonical JSON of the contract, excluding freeze_hash.

    Excluding the self-referential freeze_hash field makes the digest stable
    under --freeze stamping while still catching every semantic content change
    (a value edited, a field added/removed, an evidence source changed).
    """
    payload = {k: v for k, v in doc.items() if k != "freeze_hash"}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stamp_freeze_hash(path: Path, digest: str) -> None:
    """Write the freeze hash into the top-level `freeze_hash:` line in place,
    preserving comments and the rest of the file byte-for-byte."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if not line.startswith("freeze_hash:") or line[:1].isspace():
            continue
        indent = line[: len(line) - len(line.lstrip())]
        lines[i] = f"{indent}freeze_hash: \"{digest}\"\n"
        break
    path.write_text("".join(lines), encoding="utf-8")


def enum_errors(doc: dict) -> list[str]:
    """Enum-domain violations. These are INVALID whether the doc is draft or frozen."""
    errs: list[str] = []
    info = doc.get("information_level")
    if not _blank(info) and info not in ALLOWED_INFORMATION_LEVELS:
        errs.append(f"unknown information_level: {info!r}")
    verdict = doc.get("final_verdict", doc.get("verdict"))
    if verdict is not None and not _blank(verdict) and verdict not in ALLOWED_VERDICTS:
        errs.append(f"unknown verdict: {verdict!r}")
    for i, src in enumerate(doc.get("evidence_sources") or []):
        if isinstance(src, dict):
            cls = src.get("classification")
            if not _blank(cls) and cls not in ALLOWED_CLASSIFICATIONS:
                errs.append(f"evidence_sources[{i}].classification unknown: {cls!r}")
    return errs


def contract_missing_fields(doc: dict) -> list[str]:
    missing: list[str] = []
    for key in CONTRACT_REQUIRED:
        if _blank(doc.get(key)):
            missing.append(key)
    tc = doc.get("target_claim") or {}
    for key in ("observable", "system", "reported_value"):
        if _blank(tc.get(key)):
            missing.append(f"target_claim.{key}")
    for key in ("value", "unit"):
        if _blank((doc.get("tolerance") or {}).get(key)):
            missing.append(f"tolerance.{key}")
    for i, src in enumerate(doc.get("evidence_sources") or []):
        if not isinstance(src, dict):
            missing.append(f"evidence_sources[{i}] (must be a mapping)")
            continue
        for key in ("id", "description", "classification", "sha256"):
            if _blank(src.get(key)):
                missing.append(f"evidence_sources[{i}].{key}")
    for i, inp in enumerate(doc.get("input_files") or []):
        if not isinstance(inp, dict):
            missing.append(f"input_files[{i}] (must be a mapping)")
            continue
        for key in ("path", "role"):
            if _blank(inp.get(key)):
                missing.append(f"input_files[{i}].{key}")
    if not _blank(doc.get("schema_version")) and doc.get("schema_version") != SCHEMA_VERSION:
        missing.append(f"schema_version (expected {SCHEMA_VERSION}, got {doc.get('schema_version')!r})")
    return missing


def cmd_contract(args) -> int:
    path = Path(args.path)
    if not path.is_file():
        print(f"INVALID: contract file not found: {path}")
        return 1
    doc, err = load_yaml(path)
    if err is not None:
        print(f"INVALID: {err}")
        return 1

    errs = enum_errors(doc)
    if errs:
        print("INVALID:")
        for e in errs:
            print(f"  - {e}")
        return 1

    missing = contract_missing_fields(doc)
    stored = (doc.get("freeze_hash") or "").strip()

    if args.freeze:
        if missing:
            print("INVALID: freeze requires a complete contract; missing fields:")
            for m in missing:
                print(f"  - {m}")
            return 1
        digest = freeze_hash_of(doc)
        stamp_freeze_hash(path, digest)
        print(f"VALID FROZEN")
        print(f"freeze_hash: {digest}")
        print(f"contract_version: {doc.get('contract_version')}")
        return 0

    if stored:
        if freeze_hash_of(doc) != stored:
            print("INVALID: frozen contract content changed after freeze (freeze_hash mismatch).")
            print("  Do not edit a frozen contract in place. Write a new contract version instead.")
            return 1
        print("VALID FROZEN")
        print(f"freeze_hash: {stored}")
        return 0

    print("VALID DRAFT")
    if missing:
        print("warnings:")
        for m in missing:
            print(f"  - missing/incomplete: {m}")
    return 0


def cmd_run(args) -> int:
    path = Path(args.path)
    if not path.is_file():
        print(f"INVALID: run record not found: {path}")
        return 1
    doc, err = load_yaml(path)
    if err is not None:
        print(f"INVALID: {err}")
        return 1

    missing = [k for k in RUN_REQUIRED if _blank(doc.get(k))]
    if missing:
        print("INVALID: run record missing required binding fields:")
        for m in missing:
            print(f"  - {m}")
        return 1

    reason = doc["run_reason"]
    if reason not in ALLOWED_RUN_REASONS:
        print(f"INVALID: unknown run_reason: {reason!r} (no tune_to_target route).")
        return 1

    status = (doc.get("outcome") or {}).get("status")
    if status is not None and not _blank(status) and status not in ALLOWED_OUTCOME_STATUS:
        print(f"INVALID: unknown outcome.status: {status!r}")
        return 1

    if args.contract:
        contract_path = Path(args.contract)
        if not contract_path.is_file():
            print(f"INVALID: --contract file not found: {contract_path}")
            return 1
        cdoc, cerr = load_yaml(contract_path)
        if cerr is not None:
            print(f"INVALID: --contract {cerr}")
            return 1
        if _blank(cdoc.get("freeze_hash")):
            print("INVALID: --contract is not frozen (no freeze_hash); runs cannot bind to a draft.")
            return 1
        expected = cdoc["freeze_hash"]
        if freeze_hash_of(cdoc) != expected:
            print("INVALID: --contract content no longer matches its own freeze_hash.")
            return 1
        if doc["freeze_hash"] != expected:
            print(
                "INVALID: run-record contract hash mismatch "
                f"(record {doc['freeze_hash'][:12]}… vs contract {expected[:12]}…)."
            )
            return 1
    elif _blank(doc.get("freeze_hash")):
        print("INVALID: run-record freeze_hash is blank; every run must bind a frozen contract.")
        return 1

    print(f"VALID RUN: {doc['run_id']} (contract v{doc['contract_version']}, reason={reason})")
    return 0


LEAK_TOKENS = ("GOLD_SOURCE", "gold ground", "hidden answer", "reference answer", "private ground")


def _table_rows(md: str):
    for line in md.splitlines():
        if line.lstrip().startswith("|"):
            yield [c.strip() for c in line.strip().strip("|").split("|")]


def cmd_export(args) -> int:
    path = Path(args.path)
    if not path.is_file():
        print(f"INVALID: export not found: {path}")
        return 1
    md = path.read_text(encoding="utf-8")

    # Rule 1: no GOLD_SOURCE row may be marked candidate-visible in the source
    # classification summary table. Locate header cells by name.
    rows = list(_table_rows(md))
    header_i = next(
        (i for i, r in enumerate(rows) if any("candidate-visible" in c for c in r)),
        None,
    )
    if header_i is not None:
        header = rows[header_i]
        cls_col = next((j for j, c in enumerate(header) if "classif" in c.lower()), None)
        vis_col = next((j for j, c in enumerate(header) if "candidate-visible" in c.lower()), None)
        for r in rows[header_i + 1 :]:
            if len(r) <= max(cls_col or 0, vis_col or 0):
                continue
            classification = r[cls_col] if cls_col is not None else ""
            visible = r[vis_col] if vis_col is not None else ""
            if classification == "GOLD_SOURCE" and visible == "yes":
                print(
                    "INVALID: export leaks private/gold ground truth "
                    f"(GOLD_SOURCE artifact {r[0]!r} marked candidate-visible)."
                )
                return 1

    # Rule 2: the candidate draft spec (section 4, until the next heading) must
    # not reference gold/private material at all.
    lines = md.splitlines()
    start = next(
        (i for i, l in enumerate(lines) if l.startswith("## 4") or l.startswith("## 4.")),
        None,
    )
    if start is not None:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        spec = "\n".join(lines[start:end])
        hits = [t for t in LEAK_TOKENS if t.lower() in spec.lower()]
        if hits:
            print("INVALID: candidate draft spec references private/gold material:")
            for h in hits:
                print(f"  - contains {h!r}")
            return 1

    print("VALID EXPORT")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_contract = sub.add_parser("contract", help="validate / freeze a reproduction contract")
    p_contract.add_argument("path", help="reproduction-contract.vN.yaml")
    p_contract.add_argument("--freeze", action="store_true", help="attempt to freeze the contract")
    p_contract.set_defaults(func=cmd_contract)

    p_run = sub.add_parser("run", help="validate a run record against its contract binding")
    p_run.add_argument("path", help="runs/run-NNN/run-record.yaml")
    p_run.add_argument("--contract", default=None, help="frozen contract to check the binding against")
    p_run.set_defaults(func=cmd_run)

    p_export = sub.add_parser("export", help="validate a benchmark export for private/gold leakage")
    p_export.add_argument("path", help="export/benchmark-export.md")
    p_export.set_defaults(func=cmd_export)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
