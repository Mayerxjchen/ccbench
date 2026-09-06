"""ccbench case CLI — Benchmark case factory commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from ccbench.builder.state import derive_state
from ccbench.builder.source_lock import SourceTier, build_sources_lock
from ccbench.builder.design import load_case_ir
from ccbench.builder.scaffold import compile_case_ir_to_draft
from ccbench.builder.runnable import check_runnable_draft
from ccbench.builder.discovery import DiscoveryDecision, record_discovery_result
from ccbench.builder.release import check_release_validity
from ccbench.builder.publish import publish_case


def add_case_subparsers(subparsers: argparse._SubParsersAction) -> None:
    case_parser = subparsers.add_parser("case", help="Case builder lifecycle operations")
    case_subs = case_parser.add_subparsers(dest="case_cmd", required=True)

    # 1. intake
    p_intake = case_subs.add_parser("intake", help="Intake and lock case source files")
    p_intake.add_argument("--run-dir", required=True, type=Path)
    p_intake.add_argument("--category", default="mlp")

    # 2. design
    p_design = case_subs.add_parser("design", help="Validate Case IR design")
    p_design.add_argument("--ir", required=True, type=Path)

    # 3. build
    p_build = case_subs.add_parser("build", help="Scaffold draft from Case IR")
    p_build.add_argument("--run-dir", required=True, type=Path)

    # 4. validate
    p_val = case_subs.add_parser("validate", help="Run verifier smoke and derive RUNNABLE_DRAFT")
    p_val.add_argument("--run-dir", required=True, type=Path)

    # 5. discovery
    p_disc = case_subs.add_parser("discovery", help="Record discovery classification")
    p_disc.add_argument("--run-dir", required=True, type=Path)
    p_disc.add_argument("--decision", choices=["PROMOTED", "REFINE", "REJECT"], required=True)
    p_disc.add_argument("--notes", default="")

    # 6. release-check
    p_rel = case_subs.add_parser("release-check", help="Check benchmark validity")
    p_rel.add_argument("--run-dir", required=True, type=Path)

    # 7. publish
    p_pub = case_subs.add_parser("publish", help="Publish valid case to cases/<case-id>")
    p_pub.add_argument("--run-dir", required=True, type=Path)
    p_pub.add_argument("--case-id", required=True)
    p_pub.add_argument("--force", action="store_true")


def handle_case_cmd(args: argparse.Namespace) -> int:
    cmd = args.case_cmd
    if cmd == "intake":
        run_dir = args.run_dir
        source_dir = run_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "category.txt").write_text(args.category, encoding="utf-8")
        intake_doc = {"category": args.category, "status": "INTAKE_RECORDED"}
        (source_dir / "intake.json").write_text(json.dumps(intake_doc, indent=2), encoding="utf-8")
        state = derive_state(run_dir)
        state.save(run_dir)
        print(f"Intake complete. Current state: {state.current_state}")
        return 0

    if cmd == "design":
        ir = load_case_ir(args.ir)
        print(f"Case IR is valid: {ir['identity']['title']} ({ir['identity']['category']})")
        return 0

    if cmd == "build":
        run_dir = args.run_dir
        ir_file = run_dir / "design" / "case.ir.yaml"
        if not ir_file.is_file():
            ir_file = run_dir / "design" / "case.ir.json"
        ir = load_case_ir(ir_file)
        draft_dir = run_dir / "draft"
        artifacts = compile_case_ir_to_draft(ir, draft_dir)
        state = derive_state(run_dir)
        state.save(run_dir)
        print(f"Build complete. Compiled artifacts: {list(artifacts.keys())}. State: {state.current_state}")
        return 0

    if cmd == "validate":
        rep = check_runnable_draft(args.run_dir)
        state = derive_state(args.run_dir)
        state.save(args.run_dir)
        print(f"Validation report: passed={rep['passed']}. Current state: {state.current_state}")
        return 0 if rep["passed"] else 1

    if cmd == "discovery":
        decision = DiscoveryDecision(args.decision)
        record_discovery_result(args.run_dir, decision, notes=args.notes)
        state = derive_state(args.run_dir)
        state.save(args.run_dir)
        print(f"Discovery recorded: {decision.value}. Current state: {state.current_state}")
        return 0

    if cmd == "release-check":
        rep = check_release_validity(args.run_dir)
        state = derive_state(args.run_dir)
        state.save(args.run_dir)
        print(f"Release check: valid={rep['valid']}. Current state: {state.current_state}")
        return 0 if rep["valid"] else 1

    if cmd == "publish":
        published = publish_case(args.run_dir, args.case_id, force=args.force)
        print(f"Successfully published case to: {published}")
        return 0

    return 1
