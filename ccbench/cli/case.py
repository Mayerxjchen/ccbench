"""ccbench case CLI — Benchmark case factory commands."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

from ccbench.builder.state import derive_state
from ccbench.builder.source_lock import SourceTier, build_sources_lock
from ccbench.builder.design import load_case_ir, validate_case_ir
from ccbench.builder.scaffold import compile_case_ir_to_draft
from ccbench.builder.verifier_plan import VerifierPlan
from ccbench.builder.verifier_compile import compile_verifier
from ccbench.builder.admission import AdmissionDecision, evaluate_admission
from ccbench.builder.runnable import check_runnable_draft
from ccbench.builder.discovery import (
    DiscoveryDecision,
    DiscoveryEvidenceError,
    classify_discovery_evidence,
    record_discovery_result,
)
from ccbench.builder.release import check_release_validity
from ccbench.builder.publish import publish_case


def add_case_subparsers(subparsers: argparse._SubParsersAction) -> None:
    case_parser = subparsers.add_parser("case", help="Case builder lifecycle operations")
    case_subs = case_parser.add_subparsers(dest="case_cmd", required=True)

    # 1. intake
    p_intake = case_subs.add_parser("intake", help="Intake and lock case source files")
    p_intake.add_argument("--run-dir", required=True, type=Path)
    p_intake.add_argument("--category", default="mlp")
    p_intake.add_argument("--title", default="Untitled Benchmark Case")
    p_intake.add_argument("--system", default="Generic System")
    p_intake.add_argument("--objective", default="Benchmark Objective")

    # 2. design
    p_design = case_subs.add_parser("design", help="Validate Case IR design and save to run workspace")
    p_design.add_argument("--ir", required=True, type=Path)
    p_design.add_argument("--run-dir", type=Path, default=None)

    # 3. build
    p_build = case_subs.add_parser("build", help="Scaffold draft and compile verifier from Case IR")
    p_build.add_argument("--run-dir", required=True, type=Path)

    # 4. validate
    p_val = case_subs.add_parser("validate", help="Run verifier mount smoke and derive RUNNABLE_DRAFT")
    p_val.add_argument("--run-dir", required=True, type=Path)

    # 5. discovery
    p_disc = case_subs.add_parser("discovery", help="Classify discovery run from evidence")
    p_disc.add_argument("--run-dir", required=True, type=Path)
    p_disc.add_argument(
        "--metrics-dir", type=Path, default=None,
        help="Directory containing discovery run metric JSON files (auto-classifies)",
    )
    p_disc.add_argument("--reject", action="store_true", help="Manually reject the case")
    p_disc.add_argument("--refine", action="store_true", help="Request refinement")
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
        run_dir = Path(args.run_dir).resolve()
        source_dir = run_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "category.txt").write_text(args.category, encoding="utf-8")

        # Admission gate evaluation
        proposal = {
            "scientific_target": {"system": args.system, "objective": args.objective},
            "category": args.category,
            "runtime": {"candidate_image": "ccbench-agent:v1"},
        }
        admission_rep = evaluate_admission(proposal)
        adm_path = source_dir / "admission-report.json"
        adm_path.write_text(
            json.dumps({
                "decision": admission_rep.decision.value,
                "reasons": admission_rep.reasons,
                "scores": admission_rep.scores,
            }, indent=2),
            encoding="utf-8",
        )

        if admission_rep.decision == AdmissionDecision.REJECT:
            print(f"Admission rejected: {'; '.join(admission_rep.reasons)}", file=sys.stderr)
            return 1

        intake_doc = {
            "category": args.category,
            "title": args.title,
            "system": args.system,
            "objective": args.objective,
            "status": "INTAKE_RECORDED",
        }
        (source_dir / "intake.json").write_text(json.dumps(intake_doc, indent=2), encoding="utf-8")

        # Scan existing files in source/ and build initial sources.lock.json
        files = {
            p.relative_to(source_dir): SourceTier.PUBLIC_SOURCE
            for p in source_dir.rglob("*")
            if p.is_file() and p.name not in ("intake.json", "sources.lock.json", "admission-report.json")
        }
        build_sources_lock(source_dir, files)

        state = derive_state(run_dir)
        state.save(run_dir)
        print(f"Intake complete (Admission: {admission_rep.decision.value}). Current state: {state.current_state}")
        return 0

    if cmd == "design":
        ir = load_case_ir(args.ir)
        print(f"Case IR is valid: {ir['identity']['title']} ({ir['identity']['category']})")
        if args.run_dir:
            run_dir = Path(args.run_dir).resolve()
            design_dir = run_dir / "design"
            design_dir.mkdir(parents=True, exist_ok=True)
            dest_ir = design_dir / "case.ir.yaml"
            shutil.copy2(args.ir, dest_ir)
            state = derive_state(run_dir)
            state.save(run_dir)
            print(f"Case IR saved to workspace. Current state: {state.current_state}")
        return 0

    if cmd == "build":
        run_dir = Path(args.run_dir).resolve()
        ir_file = run_dir / "design" / "case.ir.yaml"
        if not ir_file.is_file():
            ir_file = run_dir / "design" / "case.ir.json"
        ir = load_case_ir(ir_file)

        # 1. Compile draft files and materialize candidate inputs from source
        draft_dir = run_dir / "draft"
        artifacts = compile_case_ir_to_draft(ir, draft_dir, source_dir=run_dir / "source")

        # 2. Derive verifier plan and compile verifier
        verifier_plan = VerifierPlan.from_case_ir(ir)
        verifier_dir = run_dir / "verifier"
        verifier_artifacts = compile_verifier(verifier_plan, verifier_dir)
        # Also mirror verifier to draft/verifier
        shutil.copytree(verifier_dir, draft_dir / "verifier", dirs_exist_ok=True)

        state = derive_state(run_dir)
        state.save(run_dir)
        print(f"Build complete. Scaffold + Verifier compiled. State: {state.current_state}")
        return 0

    if cmd == "validate":
        rep = check_runnable_draft(args.run_dir)
        state = derive_state(args.run_dir)
        state.save(args.run_dir)
        if rep["passed"]:
            print(f"Runnable Draft validation passed. Current state: {state.current_state}")
            return 0
        else:
            print(f"Runnable Draft validation failed: {rep['errors']}", file=sys.stderr)
            return 1

    if cmd == "discovery":
        if args.reject and args.refine:
            print("ERROR: cannot specify both --reject and --refine", file=sys.stderr)
            return 1

        if args.reject:
            decision = DiscoveryDecision.REJECT
            evidence: dict[str, Any] = {"manual": True}
            record_discovery_result(args.run_dir, decision, evidence, notes=args.notes)
        elif args.refine:
            decision = DiscoveryDecision.REFINE
            evidence = {"manual": True}
            record_discovery_result(args.run_dir, decision, evidence, notes=args.notes)
        elif args.metrics_dir:
            try:
                decision, evidence = classify_discovery_evidence(args.metrics_dir)
            except DiscoveryEvidenceError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            record_discovery_result(args.run_dir, decision, evidence, notes=args.notes)
        else:
            print(
                "ERROR: must specify --metrics-dir (for evidence-based classification), "
                "--reject, or --refine. Direct PROMOTED is not allowed.",
                file=sys.stderr,
            )
            return 1

        state = derive_state(args.run_dir)
        state.save(args.run_dir)
        print(f"Discovery recorded: {decision.value}. Current state: {state.current_state}")
        return 0

    if cmd == "release-check":
        rep = check_release_validity(args.run_dir)
        state = derive_state(args.run_dir)
        state.save(args.run_dir)
        if rep["valid"]:
            print(f"Release check valid: Benchmark is valid for release. State: {state.current_state}")
            return 0
        else:
            print(f"Release check failed with {len(rep['errors'])} error(s): {rep['errors']}", file=sys.stderr)
            return 1

    if cmd == "publish":
        published = publish_case(args.run_dir, args.case_id, force=args.force)
        print(f"Successfully published case to: {published}")
        return 0

    return 1
