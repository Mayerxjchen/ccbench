"""dftworld Case Factory CLI.

Commands:
    render   CASE --target dftworld [--force-generated]
    validate CASE --target dftworld
    diff     CASE --target dftworld
    smoke    CASE --target dftworld [--candidate docker|audit] [--runner docker|audit] [--runs-dir DIR]

JSON stdout, nonzero exit on any failure.  Render goes into a private temp
directory, validates there, then replaces only generated files as a
transaction.  Exactly one explicit Case directory per invocation; HOME and
shared scratch are never scanned.

Default ``render`` refuses to overwrite generated files that drifted from the
adapter's expected bytes — an explicit ``--force-generated`` shows the diff and
overwrites.  The generated set is committed transactionally: every existing
file is backed up first and rolled back whole if any step fails, with the lock
committed last.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

import yaml

from dftworld_bench.case_factory.dftworld_target import (
    DftworldTargetAdapter,
    RenderError,
)
from dftworld_bench.case_factory.smoke import SmokeError, run_smoke
from dftworld_bench.case_factory.state import LOCK_RELPATH, read_factory_state
from dftworld_bench.contracts.result import FailureCode

ADAPTERS = {"dftworld": DftworldTargetAdapter()}

DESIGN_FILE = "case-design.yaml"


def _adapter(target: str) -> DftworldTargetAdapter:
    if target not in ADAPTERS:
        raise ValueError(f"unknown target {target!r}; expected {sorted(ADAPTERS)}")
    return ADAPTERS[target]


def _load_design(case_dir: Path) -> dict:
    path = case_dir / DESIGN_FILE
    if not path.is_file():
        raise FileNotFoundError(f"{case_dir}: missing {DESIGN_FILE}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{case_dir}: {DESIGN_FILE} is not a YAML mapping")
    return raw


def _report(payload: dict, ok: bool) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))
    sys.exit(0 if ok else 1)


def _replace_file(src: Path, dst: Path) -> None:
    """Atomic single-file replace via same-dir temp + rename."""
    tmp_atomic = dst.parent / f".{dst.name}.case-factory.tmp"
    shutil.copy2(src, tmp_atomic)
    tmp_atomic.replace(dst)


def _replace_bytes(dst: Path, content: bytes) -> None:
    tmp_atomic = dst.parent / f".{dst.name}.case-factory.tmp"
    tmp_atomic.write_bytes(content)
    tmp_atomic.replace(dst)


def _generated_status(
    case_dir: Path, adapter: DftworldTargetAdapter, design: dict
) -> tuple[list[str], list[str]]:
    """Return (drifted, missing) generated paths vs the adapter expectation."""
    drifted: list[str] = []
    missing: list[str] = []
    for gf in adapter.expected_output(case_dir, design):
        disk = case_dir / gf.path
        if not disk.is_file():
            missing.append(gf.path.as_posix())
        elif disk.read_bytes() != gf.content:
            drifted.append(gf.path.as_posix())
    return drifted, missing


def _commit_generated(
    case_dir: Path,
    tmp_dir: Path,
    generated,
    lock_gf,
) -> None:
    """Transactional commit of the generated set.

    Backs up every existing generated file first, replaces the non-lock files,
    commits the lock last, and on ANY exception restores the whole set: files
    that existed before the transaction return byte-for-byte and files created
    by this commit are removed.  This is exception atomicity, not crash
    atomicity — SIGKILL/power loss cannot run the rollback and is out of scope
    here (a startup recovery pass would be the recovery mechanism).
    """
    backups: list[tuple[Path, bytes]] = []
    created: list[Path] = []
    for gf in generated:
        dst = case_dir / gf.path
        if dst.is_file():
            backups.append((dst, dst.read_bytes()))
        else:
            created.append(dst)
    try:
        for gf in generated:
            if gf.path.as_posix() == LOCK_RELPATH.as_posix():
                continue
            src = tmp_dir / gf.path
            if not src.is_file():
                raise RenderError(f"internal: missing {gf.path}")
            dst = case_dir / gf.path
            dst.parent.mkdir(parents=True, exist_ok=True)
            _replace_file(src, dst)
        # Lock is committed last; its digest hashes only the sibling files.
        dst = case_dir / lock_gf.path
        dst.parent.mkdir(parents=True, exist_ok=True)
        _replace_bytes(dst, lock_gf.content)
    except BaseException:
        for dst, content in backups:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(content)
            except OSError:  # rollback best-effort; surface the original error
                pass
        for dst in created:
            try:
                dst.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def cmd_render(case_dir: Path, target: str, force_generated: bool = False) -> None:
    adapter = _adapter(target)
    design = _load_design(case_dir)
    verdict = adapter.validate_design(case_dir, design)
    if not verdict.valid:
        _report({"command": "render", "case": str(case_dir), "target": target,
                 "valid": False, "errors": list(verdict.errors)}, ok=False)

    # Default refuses to overwrite drifted generated files.  Only an explicit
    # --force-generated (after the diff is shown) may overwrite them.
    drifted, _missing = _generated_status(case_dir, adapter, design)
    if drifted and not force_generated:
        _report({"command": "render", "case": str(case_dir), "target": target,
                 "valid": False, "errors": [
                     "generated files drifted from adapter expectation; "
                     "pass --force-generated to overwrite"],
                 "drifted": drifted}, ok=False)

    # Capture prior runtime/smoke gates BEFORE the lock is overwritten so
    # later independent checks never false-drift the lock.
    prior = read_factory_state(case_dir)

    # Render into a private temp dir first; never a partial write into the case.
    with tempfile.TemporaryDirectory(prefix="case-factory-") as tmp:
        tmp_dir = Path(tmp)
        generated = adapter.render(tmp_dir, design)
        for gf in generated:
            (tmp_dir / gf.path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_dir / gf.path).write_bytes(gf.content)

        # Validate inside the temp dir before touching the real case.
        verdict = adapter.validate_output(tmp_dir, design)
        if not verdict.valid:
            _report({"command": "render", "case": str(case_dir), "target": target,
                     "valid": False, "errors": list(verdict.errors)}, ok=False)

        # Committed lock: adapter valid, prior runtime/smoke preserved.
        lock_gf = adapter.commit_lock(design, prior)
        _commit_generated(case_dir, tmp_dir, generated, lock_gf)

    gates = read_factory_state(case_dir)
    _report({"command": "render", "case": str(case_dir), "target": target,
             "valid": True, "errors": [], "drifted": drifted,
             "factory_gates": gates.as_dict(),
             "benchmark_valid": False}, ok=True)


def cmd_validate(case_dir: Path, target: str) -> None:
    adapter = _adapter(target)
    design = _load_design(case_dir)
    verdict = adapter.validate_design(case_dir, design)
    if not verdict.valid:
        _report({"command": "validate", "case": str(case_dir), "target": target,
                 "valid": False, "errors": list(verdict.errors),
                 "factory_gates": verdict.gates.as_dict()}, ok=False)
    out = adapter.validate_output(case_dir, design)
    _report({"command": "validate", "case": str(case_dir), "target": target,
             "valid": out.valid, "errors": list(out.errors),
             "factory_gates": out.gates.as_dict()}, ok=out.valid)


def cmd_diff(case_dir: Path, target: str) -> None:
    adapter = _adapter(target)
    design = _load_design(case_dir)
    verdict = adapter.validate_design(case_dir, design)
    if not verdict.valid:
        _report({"command": "diff", "case": str(case_dir), "target": target,
                 "valid": False, "errors": list(verdict.errors)}, ok=False)
    drifted, missing = _generated_status(case_dir, adapter, design)
    _report({"command": "diff", "case": str(case_dir), "target": target,
             "valid": not (drifted or missing), "drifted": drifted,
             "missing": missing}, ok=not (drifted or missing))


def cmd_smoke(case_dir: Path, target: str, runner: str = "docker",
              candidate: str = "audit",
              runs_dir: Optional[Path] = None) -> None:
    adapter = _adapter(target)
    design = _load_design(case_dir)
    verdict = adapter.validate_design(case_dir, design)
    if not verdict.valid:
        _report({"command": "smoke", "case": str(case_dir), "target": target,
                 "valid": False, "errors": list(verdict.errors)}, ok=False)
    case = Path(case_dir)
    run_id = f"{case.name}-case-construction-smoke-0001"
    try:
        result = run_smoke(case, run_id=run_id, runner=runner,
                           candidate=candidate, runs_dir=runs_dir)
    except (SmokeError, ValueError, FileNotFoundError, RenderError) as exc:
        _report({"command": "smoke", "case": str(case), "target": target,
                 "valid": False, "errors": [str(exc)]}, ok=False)
    # A smoke "succeeds" only on a real PASS.  is_counted_scientifically alone
    # is true for SCIENTIFIC_FAIL too — gates would stay unset while the CLI
    # claimed valid and exited 0, misleading callers.
    smoke_passed = (
        result.is_counted_scientifically
        and result.failure_code is FailureCode.PASS
    )
    gates = read_factory_state(case)
    payload = {"command": "smoke", "case": str(case), "target": target,
               "valid": smoke_passed,
               "result": result.to_dict(),
               "factory_gates": gates.as_dict(),
               "benchmark_valid": False}
    if runs_dir is not None:
        rd = Path(runs_dir)
        record = rd / run_id / "run-record.json"
        sealed = rd / case.name / "sealed-submission"
        payload["evidence"] = {
            "runs_dir": str(rd),
            "run_record_path": str(record),
            "run_record_sha256": _sha256_file(record),
            "sealed_submission_sha256": _sha256_tree(sealed),
            "verifier_log_path": str(rd / case.name / "verifier-logs"),
        }
    _report(payload, ok=smoke_passed)


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _sha256_tree(root: Path) -> str:
    """Deterministic content hash of a directory tree (sorted relpaths)."""
    import hashlib

    digest = hashlib.sha256()
    for rel in sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file()):
        digest.update(str(rel).encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / rel).read_bytes())
    return digest.hexdigest()


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m dftworld_bench.case_factory",
        description="dftworld Case Factory — render/validate/diff/smoke generated files",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render")
    p_render.add_argument("case_dir", type=Path)
    p_render.add_argument("--target", required=True, choices=sorted(ADAPTERS))
    p_render.add_argument(
        "--force-generated", action="store_true",
        help="overwrite generated files even if they drifted (diff shown by --force only)",
    )

    for name in ("validate", "diff"):
        p = sub.add_parser(name)
        p.add_argument("case_dir", type=Path)
        p.add_argument("--target", required=True, choices=sorted(ADAPTERS))

    p_smoke = sub.add_parser("smoke")
    p_smoke.add_argument("case_dir", type=Path)
    p_smoke.add_argument("--target", required=True, choices=sorted(ADAPTERS))
    p_smoke.add_argument(
        "--runner", choices=("docker", "audit"), default="docker",
        help="verifier container executor: docker (real) or audit (CI, asserts argv)",
    )
    p_smoke.add_argument(
        "--candidate", choices=("docker", "audit"), default="audit",
        help="candidate executor: docker (builds + runs the generated image in "
             "an isolated container) or audit (isolated subprocess).  Full "
             "runtime gates promote only when BOTH --candidate docker and "
             "--runner docker are used",
    )
    p_smoke.add_argument(
        "--runs-dir", type=Path, default=None,
        help="persist RunRecord, sealed submission and verifier logs here; "
             "without it evidence stays in a private temp dir",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            cmd_render(args.case_dir, args.target, force_generated=args.force_generated)
        elif args.command == "validate":
            cmd_validate(args.case_dir, args.target)
        elif args.command == "smoke":
            cmd_smoke(args.case_dir, args.target, runner=args.runner,
                      candidate=args.candidate, runs_dir=args.runs_dir)
        else:
            cmd_diff(args.case_dir, args.target)
    except (ValueError, FileNotFoundError, RenderError, SmokeError) as exc:
        _report({"command": args.command, "case": str(args.case_dir),
                 "valid": False, "errors": [str(exc)]}, ok=False)
    return 0  # _report always exits


if __name__ == "__main__":
    raise SystemExit(main())
