#!/usr/bin/env python3
"""Deterministic case scaffolding from a validated design.

Applies overlays in order common -> execution class -> category and writes a
draft case directory. Refuses a non-empty output directory without --force.
Refuses a blocked design unless --allow-blocked. benchmark_valid is always
written false; only check_release.py (Task 9) may derive it true.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc

from validate_case import validate_case  # sibling common script

SCHEMA_VERSION = 1
ALLOWED_EXECUTION = ("local_sandbox", "hpc_controller")
EXECUTION_OVERLAY = {
    "local_sandbox": "local-sandbox",
    "hpc_controller": "hpc-controller",
}
DRAFT_OPEN_GATES = [f"G{i}" for i in range(13)]  # G0..G12 open at draft


def load_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: root must be a mapping")
    return value


def fail(message: str) -> None:
    raise SystemExit(f"init_case: {message}")


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _find_dftworld_root(start: Path) -> Path | None:
    """Walk up from the case dir to the dftworld repository root."""
    for parent in (Path(start).resolve(), *Path(start).resolve().parents):
        if (parent / "ccbench").is_dir():
            return parent
    return None


def run_dftworld_target(out: Path) -> None:
    """Render executable draft files via the dftworld Case Factory CLI.

    Runs as a subprocess from the repository root so the portable Builder never
    duplicates adapter logic.  The adapter renders atomically (private temp
    directory, then replace) so a failure leaves no partial runtime files.  The
    scaffold files written above are fresh placeholders, not user edits, so the
    render passes --force-generated to let the adapter replace the generated set.
    """
    root = _find_dftworld_root(out)
    if root is None:
        fail(
            "target dftworld requires the dftworld repository (ccbench "
            "not found); rerun without --target for the portable scaffold"
        )
    proc = subprocess.run(
        [sys.executable, "-m", "ccbench.case_factory", "render",
         str(out), "--target", "dftworld", "--force-generated"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or "adapter render failed"
        fail(f"dftworld target render failed: {detail[:400]}")


def registry(skill: Path) -> dict:
    return load_yaml(skill / "references/category-registry.yaml")


def is_blocked(design: dict) -> bool:
    if design.get("readiness") == "blocked":
        return True
    for blocker in design.get("blockers") or []:
        if not isinstance(blocker, dict):
            return True
        if blocker.get("status", "unresolved") != "resolved":
            return True
    return False


def copy_tree(src: Path, dst: Path) -> None:
    if not src.is_dir():
        fail(f"template overlay missing: {src}")
    shutil.copytree(src, dst, dirs_exist_ok=True)


def render_case_design(design: dict, category: str, case_kind: str, exec_class: str) -> dict:
    rendered = dict(design)
    rendered.update({
        "schema_version": SCHEMA_VERSION,
        "category": category,
        "case_kind": case_kind,
        "case_status": "draft",
        "execution": {"class": exec_class},
    })
    return rendered


def render_task_toml(exec_class: str) -> str:
    """Render a canonical v2 manifest so CaseSpec.load accepts the draft.

    The execution class comes from the validated design (never inferred), and
    public inputs keep the `public/` prefix so instruction paths match the
    packaged bundle. hpc_controller additionally needs the [hpc] block.
    """
    lines = [
        "schema_version = 1",
        "",
        "[task]",
        'name = "draft-case"',
        'title = "draft task"',
        "",
        "[execution]",
        f'class = "{exec_class}"',
        "",
        "[candidate]",
        'instruction = "instruction.md"',
        'submission_root = "."',
        "legacy_submission_layout = false",
        "files = [",
        '  { source = "public/**", destination = "." },',
        "]",
    ]
    if exec_class == "hpc_controller":
        lines += [
            "",
            "[hpc]",
            'contract_version = "1"',
            'required_capabilities = ["batch_jobs"]',
        ]
    return "\n".join(lines) + "\n"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_yaml(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true", help="overwrite a non-empty output directory")
    parser.add_argument("--allow-blocked", action="store_true", help="scaffold a draft with blockers")
    parser.add_argument("--skill-root", type=Path,
                        help="alternate skill root (test-only category registration)")
    parser.add_argument("--target", choices=["dftworld"],
                        help="after scaffolding, render executable draft files with the "
                             "dftworld Case Factory (optional)")
    args = parser.parse_args()

    skill = Path(args.skill_root) if args.skill_root else skill_root()
    reg = registry(skill)
    entry = (reg.get("categories") or {}).get(args.category)
    if entry is None:
        supported = sorted((reg.get("categories") or {}))
        fail(f"unknown category; supported: {supported}")

    design = load_yaml(args.design)
    if is_blocked(design) and not args.allow_blocked:
        fail("design is blocked; use --allow-blocked to scaffold a draft with blockers")

    exec_class = (design.get("execution") or {}).get("class")
    if exec_class not in ALLOWED_EXECUTION:
        fail(f"unknown execution class: {exec_class!r}")

    out = Path(args.output)
    if out.exists() and any(out.iterdir()) and not args.force:
        fail(f"output directory not empty: {out} (use --force to overwrite)")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    assets = skill / "assets/case-template"
    copy_tree(assets / "common", out)
    copy_tree(assets / "execution" / EXECUTION_OVERLAY[exec_class], out)
    copy_tree(assets / "categories" / args.category, out)

    write_yaml(out / "case-design.yaml", render_case_design(design, args.category, args.kind, exec_class))
    (out / "task.toml").write_text(render_task_toml(exec_class), encoding="utf-8")
    write_json(out / "VALIDATION.json", {
        "schema_version": SCHEMA_VERSION,
        "case_status": "draft",
        "benchmark_valid": False,
        "open_gates": DRAFT_OPEN_GATES,
        "evidence_pointers": {},
    })
    write_json(out / "benchmark_valid.json", {
        "schema_version": SCHEMA_VERSION,
        "benchmark_valid": False,
        "derived_by": None,
    })

    verdict = validate_case(out)
    if not verdict["valid"]:
        fail(f"scaffold did not validate: {verdict['errors']}")

    if args.target == "dftworld":
        run_dftworld_target(out)

    summary = {
        "case_dir": str(out),
        "category": args.category,
        "case_kind": args.kind,
        "execution_class": exec_class,
        "case_status": "draft",
        "benchmark_valid": False,
        "target": args.target,
        "valid": True,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
