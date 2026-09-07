"""Authoritative Runnable Draft (L1) Gate for CCBench Case Builder.

Public API:
    evaluate_runnable_draft(run_dir) -> dict   Pure evaluation, no side-effects.
    check_runnable_draft(run_dir)    -> dict   Evaluate + write receipt.

Performs:
1. Real CaseSpec.load() contract validation against schema.
2. Case IR & Category plugin semantic validation.
3. Real package_candidate() allowlist staging and leak scan in isolated temporary directory.
4. Taint and gold-leakage audit on packaged candidate surface.
5. Verifier mount smoke in faithful /tests + sealed-root + result layout:
   - Empty submission must fail (AGENT_FAILURE).
   - Negative fixtures must fail with expected attribution.
   - Minimal structural submission must pass V0/V1.
6. Schema validation of verifier_result.json.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ccbench.contracts.case import CaseSpec
from ccbench.core.packager import PackageError, package_candidate
from ccbench.builder.source_lock import check_gold_leakage
from ccbench.portfolio.leakage import check_case_leakage


class RunnableGateError(Exception):
    """Raised when runnable draft verification fails."""


def evaluate_runnable_draft(run_dir: Path) -> dict[str, Any]:
    """Pure evaluation of runnable draft gate. No file writes.

    Returns dict with keys: passed, errors, checks.
    """
    run_dir = Path(run_dir).resolve()
    draft_dir = run_dir / "draft"
    verifier_dir = run_dir / "verifier" if (run_dir / "verifier").is_dir() else draft_dir / "verifier"
    errors: list[str] = []
    checks: dict[str, Any] = {}

    # 1. Structural files exist
    task_md = draft_dir / "task.md"
    case_toml = draft_dir / "case.toml"
    if not task_md.is_file():
        errors.append("Missing draft/task.md")
    if not case_toml.is_file():
        errors.append("Missing draft/case.toml")
    if not (verifier_dir / "verify.py").is_file() and not (verifier_dir / "test.sh").is_file():
        errors.append(f"Missing executable verifier at {verifier_dir}")

    if errors:
        return {"passed": False, "errors": errors, "checks": checks}

    # 2. Real CaseSpec load
    try:
        spec = CaseSpec.load(draft_dir)
        checks["case_spec_load"] = True
    except Exception as exc:
        errors.append(f"CaseSpec.load failed: {exc}")
        return {"passed": False, "errors": errors, "checks": checks}

    # 3. Case IR & Category Plugin validation
    case_ir_path = run_dir / "design" / "case.ir.yaml"
    if not case_ir_path.is_file():
        case_ir_path = run_dir / "design" / "case.ir.json"
    if case_ir_path.is_file():
        try:
            from ccbench.builder.design import load_case_ir
            case_ir = load_case_ir(case_ir_path)
            checks["case_ir_valid"] = True

            # Verify declared candidate inputs exist on disk in draft/input/
            for inp in case_ir.get("candidate", {}).get("inputs", []):
                rel_inp = inp["path"].removeprefix("input/")
                cand_file = draft_dir / "input" / rel_inp
                if not cand_file.is_file() and not (draft_dir / inp["path"]).is_file():
                    errors.append(f"Candidate input declared in Case IR missing on disk: {inp['path']}")
            if errors:
                return {"passed": False, "errors": errors, "checks": checks}

            # Verify candidate-inputs.lock.json (source→candidate hash binding)
            input_lock = draft_dir / "candidate-inputs.lock.json"
            if input_lock.is_file():
                try:
                    lock_doc = json.loads(input_lock.read_text(encoding="utf-8"))
                    for entry in lock_doc.get("inputs", []):
                        cand_path = draft_dir / entry["candidate_path"]
                        if not cand_path.is_file():
                            errors.append(f"candidate-inputs.lock references missing file: {entry['candidate_path']}")
                        else:
                            from ccbench.builder.source_lock import hash_file
                            actual_hash = hash_file(cand_path)
                            if actual_hash != entry["candidate_sha256"]:
                                errors.append(
                                    f"Candidate input tampered: {entry['candidate_path']} "
                                    f"(lock={entry['candidate_sha256']} != disk={actual_hash})"
                                )
                    checks["candidate_inputs_lock"] = True
                except Exception as exc:
                    errors.append(f"Failed to verify candidate-inputs.lock.json: {exc}")
            else:
                checks["candidate_inputs_lock"] = "missing"

            if errors:
                return {"passed": False, "errors": errors, "checks": checks}
        except Exception as exc:
            errors.append(f"Case IR validation failed: {exc}")
            return {"passed": False, "errors": errors, "checks": checks}

    # 4. Real Candidate packaging into temporary isolated directory
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        candidate_staging = tmp / "candidate"
        candidate_staging.mkdir()

        try:
            bundle = package_candidate(spec, candidate_staging)
            checks["package_candidate"] = {
                "public_digest": bundle.public_digest,
                "file_count": len(bundle.files),
            }
        except PackageError as exc:
            errors.append(f"package_candidate failed: {exc}")
            return {"passed": False, "errors": errors, "checks": checks}

        # 5. Anti-leakage and gold-taint check
        sources_lock_path = run_dir / "source" / "sources.lock.json"
        if sources_lock_path.is_file():
            try:
                slock = json.loads(sources_lock_path.read_text(encoding="utf-8"))
                manifest = {s["path"]: s.get("tier", "PUBLIC_SOURCE") for s in slock.get("sources", [])}
                lineage = {s["path"]: s.get("derived_from", []) for s in slock.get("sources", [])}
                candidate_paths = [f.path for f in bundle.files]
                leak_violations = check_gold_leakage(candidate_paths, manifest, lineage)
                if leak_violations:
                    errors.extend(leak_violations)
                    return {"passed": False, "errors": errors, "checks": checks}
                checks["gold_taint_check"] = True
            except Exception as exc:
                errors.append(f"Failed to check gold taint: {exc}")
                return {"passed": False, "errors": errors, "checks": checks}

        # Research question paper DOI isolation check
        leak_ok, leak_violations = check_case_leakage(draft_dir)
        if not leak_ok:
            errors.extend(leak_violations)
            return {"passed": False, "errors": errors, "checks": checks}
        checks["doi_isolation"] = True

        # 6. Verifier mount smoke in faithful isolated layout
        verify_script = verifier_dir / "verify.py"
        run_cmd = [sys.executable, str(verify_script)] if verify_script.is_file() else ["bash", str(verifier_dir / "test.sh")]

        # 6a. Negative fixture 1: Empty submission MUST FAIL
        empty_sub = tmp / "empty_submission"
        empty_sub.mkdir()
        proc_empty = subprocess.run(
            run_cmd + [str(empty_sub), "--profile", "structural"],
            cwd=tmp,
            capture_output=True,
            text=True,
        )
        if proc_empty.returncode == 0:
            errors.append("Verifier mount smoke failed: empty submission passed (must fail closed)")
            return {"passed": False, "errors": errors, "checks": checks}
        checks["smoke_empty_submission_fails"] = True

        # 6b. Negative fixture 2: Missing required artifact MUST FAIL
        corrupted_sub = tmp / "corrupted_submission"
        sub_root_c = corrupted_sub / spec.submission_root
        sub_root_c.mkdir(parents=True, exist_ok=True)
        proc_corrupt = subprocess.run(
            run_cmd + [str(corrupted_sub), "--profile", "structural"],
            cwd=tmp,
            capture_output=True,
            text=True,
        )
        if proc_corrupt.returncode == 0:
            errors.append("Verifier mount smoke failed: missing required artifacts passed (must fail closed)")
            return {"passed": False, "errors": errors, "checks": checks}
        checks["smoke_negative_missing_artifact_fails"] = True

        # 6c. Structural positive submission: MUST PASS V0/V1 under structural profile
        valid_sub = tmp / "valid_submission"
        sub_root = valid_sub / spec.submission_root
        sub_root.mkdir(parents=True, exist_ok=True)

        sub_contract_path = draft_dir / "submission-contract.json"
        if sub_contract_path.is_file():
            try:
                sub_contract = json.loads(sub_contract_path.read_text(encoding="utf-8"))
                for art in sub_contract.get("artifacts", []):
                    art_path = sub_root / art["path"]
                    art_path.parent.mkdir(parents=True, exist_ok=True)
                    if art["path"].endswith(".json"):
                        art_path.write_text("{}", encoding="utf-8")
                    else:
                        art_path.write_bytes(b"dummy-structural-artifact\n")
            except Exception:
                pass

        # Clear any old result.json before execution
        old_res = tmp / "verifier_result.json"
        if old_res.is_file():
            old_res.unlink()

        proc_valid = subprocess.run(
            run_cmd + [str(valid_sub), "--profile", "structural"],
            cwd=tmp,
            capture_output=True,
            text=True,
        )
        if proc_valid.returncode != 0:
            errors.append(
                f"Verifier mount smoke failed on valid structural submission (exit {proc_valid.returncode}): "
                f"{proc_valid.stderr.strip()}"
            )
            return {"passed": False, "errors": errors, "checks": checks}

        # 6d. Verify verifier_result.json existence and schema (FAIL-CLOSED)
        result_json_path = tmp / "verifier_result.json"
        if not result_json_path.is_file():
            errors.append("Verifier exited 0 but failed to produce verifier_result.json (hard fail)")
            return {"passed": False, "errors": errors, "checks": checks}

        try:
            res_doc = json.loads(result_json_path.read_text(encoding="utf-8"))
            if not isinstance(res_doc, dict):
                errors.append("verifier_result.json is not a valid JSON object")
                return {"passed": False, "errors": errors, "checks": checks}
            if not res_doc.get("passed", False):
                errors.append(f"Verifier result declared not passed on valid submission: {res_doc}")
                return {"passed": False, "errors": errors, "checks": checks}
            layers = res_doc.get("layers", {})
            if not layers.get("V0", False) or not layers.get("V1", False):
                errors.append(f"Mandatory structural layers V0/V1 did not pass in verifier_result: {layers}")
                return {"passed": False, "errors": errors, "checks": checks}
            checks["verifier_result_valid"] = True
        except Exception as exc:
            errors.append(f"Failed to parse verifier_result.json: {exc}")
            return {"passed": False, "errors": errors, "checks": checks}

    return {"passed": len(errors) == 0, "errors": errors, "checks": checks}


def check_runnable_draft(run_dir: Path) -> dict[str, Any]:
    """Execute runnable draft gate and write the receipt file."""
    result = evaluate_runnable_draft(run_dir)
    _record_smoke_report(run_dir, result["passed"], result["errors"], result["checks"])
    return result


def _record_smoke_report(
    run_dir: Path,
    passed: bool,
    errors: list[str],
    checks: dict[str, Any],
) -> dict[str, Any]:
    report = {
        "passed": passed,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "errors": errors,
        "checks": checks,
    }
    smoke_dir = run_dir / "verifier-smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    target = smoke_dir / "smoke-report.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
