#!/usr/bin/env python3
"""The single derived entry for `runnable_draft` (Discovery MVP gate, L1).

No other validator may declare a case runnable. This script executes, with
the repository/runtime interpreter (never a substitute syntax parser):

    1. validate_case.py                      structural contract
    2. category spec validation + readiness  semantic evidence (mlp)
    3. derive_verifier_plan.py               layer derivation, compared to plan
    4. generate_fixture_matrix.py            fixture closure
    5. check_draft_consistency.py            cross-layer machine invariants
    6. real CaseSpec.load + package_candidate  actual bundle in a temp dir
    7. bundle agreement                      instruction/manifest/CONTRACT vs
                                             the actual packaged bundle
    8. verifier mount smoke                  tests/test.sh in a faithful
                                             /tests + sealed-root + result-dir
                                             layout: empty -> AGENT_FAILURE,
                                             forged -> AGENT_FAILURE,
                                             structural -> VALID_RESULT,
                                             broken entry -> INFRA_INVALID,
                                             plus the four v3 integrity probes
                                             (missing-artifact, multi-hash,
                                             missing-plus-mismatch,
                                             type-garbage) graded by exact
                                             attribution and retryability
    9. common result schema                  every smoke result.json validated
   10. honesty                                benchmark_valid false; no
                                             fabricated reference/thresholds

Exit 0 only when every check passes. `--derive-state` is the only writer of
case_status=runnable_draft (into VALIDATION.json with its derivation record).
Expert reference, frozen thresholds, hidden V4/V5/V6 science, and G0-G12
closure stay deferred at L1 and are listed as release work, never blocked here.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_COMMON = SKILL_ROOT / "scripts" / "common"
SCRIPTS_MLP = SKILL_ROOT / "scripts" / "categories" / "mlp"

RESULT_FIELDS = {
    "run_id", "result_class", "failure_code", "reason",
    "retryable", "is_counted_scientifically",
}
RESULT_CLASSES = {"VALID_RESULT", "AGENT_FAILURE", "INFRA_INVALID"}
STANDARD_RESULT_PATH = "/logs/verifier/result.json"
REQUIRED_NEGATIVE_FIXTURES = (
    "empty", "forged-manifest", "missing-model", "broken-lineage",
    "missing-artifact", "multi-hash-mismatch", "missing-plus-mismatch",
    "type-garbage-manifest",
)
# v3 integrity probes: (fixture, class, codes, reason needles, retryable).
# These are behavior probes, not existence probes: the negatives must be
# GRADED with this exact attribution, never merely described.
V3_INTEGRITY_PROBES = (
    ("negative/missing-artifact", "AGENT_FAILURE", ("SCIENTIFIC_FAIL",),
     ("declared artifact missing",), False),
    ("negative/multi-hash-mismatch", "AGENT_FAILURE", ("SCIENTIFIC_FAIL",),
     ("integrity mismatch for artifacts/model/student.pb",
      "integrity mismatch for artifacts/dataset/train.raw"), False),
    ("negative/missing-plus-mismatch", "AGENT_FAILURE", ("SCIENTIFIC_FAIL",),
     ("declared artifact missing", "integrity mismatch"), False),
    ("negative/type-garbage-manifest", "AGENT_FAILURE", ("INVALID_SUBMISSION",),
     ("manifest type validation failed",), False),
)
STRUCTURAL_POSITIVE_FIXTURE = "positive/structural-minimal"
DEFERRED_RELEASE_WORK = [
    "MLP-V4/V5/V6 hidden scientific verification",
    "expert reference run and independently reproduced lineage",
    "double-threshold calibration freeze",
    "scientific positive/alternative-valid fixture closure",
    "G0-G12 release gate closure and evidence retention",
]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Verdict:
    def __init__(self) -> None:
        self.checks: dict[str, dict[str, Any]] = {}

    def record(self, name: str, errors: list[str], detail: dict[str, Any] | None = None) -> bool:
        self.checks[name] = {
            "status": "pass" if not errors else "fail",
            "errors": errors,
            **({"detail": detail} if detail else {}),
        }
        return not errors

    @property
    def blocking_errors(self) -> list[str]:
        return [
            f"{name}: {error}"
            for name, check in self.checks.items()
            for error in check["errors"]
        ]

    @property
    def mvp_runnable(self) -> bool:
        return bool(self.checks) and all(
            check["status"] == "pass" for check in self.checks.values()
        )

    def report(self) -> dict[str, Any]:
        return {
            "mvp_runnable": self.mvp_runnable,
            "checks": self.checks,
            "blocking_errors": self.blocking_errors,
            "deferred_release_work": [] if not self.mvp_runnable else list(DEFERRED_RELEASE_WORK),
            "benchmark_valid": False,
            "runner": {
                "interpreter": sys.executable,
                "skill_root": str(SKILL_ROOT),
            },
        }


def _run_script(script: Path, *args: str) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(script), *(str(a) for a in args)],
        text=True, capture_output=True, check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def find_repo_root(case_dir: Path, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if (explicit / "dftworld_bench").is_dir() else None
    for parent in (case_dir.resolve(), *case_dir.resolve().parents):
        if (parent / "dftworld_bench").is_dir():
            return parent
    return None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_validate_case(case_dir: Path, verdict: Verdict) -> None:
    module = _load_module("mvp_validate_case", SCRIPTS_COMMON / "validate_case.py")
    result = module.validate_case(case_dir)
    verdict.record("validate_case", [] if result["valid"] else list(result["errors"]))


def check_category_semantics(case_dir: Path, design: dict, verdict: Verdict) -> None:
    if design.get("category") != "mlp":
        verdict.record("category_semantics", [
            f"no runnable gate is defined for category {design.get('category')!r}; "
            "only mlp ships one"
        ])
        return
    errors: list[str] = []
    spec_path = case_dir / "source" / "mlp-reproduction-spec.yaml"
    evidence_path = case_dir / "source" / "source-evidence-map.yaml"
    if not spec_path.is_file():
        errors.append("source/mlp-reproduction-spec.yaml missing; run extract-spec first")
    else:
        args = [spec_path]
        if evidence_path.is_file():
            args += ["--evidence", evidence_path]
        rc, out, err = _run_script(SCRIPTS_MLP / "validate_spec.py", *args)
        if rc != 0:
            errors.append(f"validate_spec.py rejected the spec: {(err or out).strip()[:400]}")
        with tempfile.TemporaryDirectory() as td:
            assess = Path(td) / "assessment.yaml"
            rc, out, err = _run_script(
                SCRIPTS_MLP / "check_readiness.py", spec_path,
                *(["--evidence", str(evidence_path)] if evidence_path.is_file() else []),
                "--output", assess,
            )
            if rc != 0:
                errors.append(f"check_readiness.py failed: {(err or out).strip()[:400]}")
            elif assess.is_file():
                import yaml
                assessment = yaml.safe_load(assess.read_text(encoding="utf-8")) or {}
                readiness = assessment.get("readiness") or {}
                if "blocked" in {str(readiness.get(key)) for key in readiness}:
                    # recoverable is allowed at MVP; a hard block is not
                    errors.append(f"check_readiness.py reports blocked: {readiness}")
    verdict.record("category_semantics", errors)


def check_verifier_plan(case_dir: Path, verdict: Verdict) -> None:
    errors: list[str] = []
    plan_path = case_dir / "verifier-plan.yaml"
    with tempfile.TemporaryDirectory() as td:
        derived = Path(td) / "verifier-plan.yaml"
        rc, out, err = _run_script(
            SCRIPTS_MLP / "derive_verifier_plan.py",
            "--design", case_dir / "case-design.yaml", "--output", derived,
        )
        if rc != 0:
            errors.append(f"derive_verifier_plan.py failed: {(err or out).strip()[:400]}")
        else:
            import yaml  # local import: only needed when PyYAML-driven steps run
            derived_plan = yaml.safe_load(derived.read_text(encoding="utf-8")) or {}
            if not plan_path.is_file():
                errors.append("verifier-plan.yaml missing; derive it into the case")
            else:
                plan = yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}
                derived_ids = [layer["id"] for layer in derived_plan.get("layers") or []]
                plan_ids = [layer["id"] for layer in plan.get("layers") or []]
                if derived_ids != plan_ids:
                    errors.append(
                        f"verifier-plan layers {plan_ids} disagree with derived {derived_ids}"
                    )
                derived_by_id = {
                    layer["id"]: layer for layer in derived_plan.get("layers") or []
                    if isinstance(layer, dict)
                }
                for layer in plan.get("layers") or []:
                    if not isinstance(layer, dict):
                        errors.append("verifier-plan layers must be objects")
                        continue
                    lid = str(layer.get("id", "?"))
                    status = layer.get("status")
                    if status not in ("selected", "deferred"):
                        errors.append(
                            f"verifier-plan {lid}: status {status!r} must be selected or "
                            "deferred — applicable layers are never silently absent"
                        )
                        continue
                    if layer.get("mandatory") is True and status == "deferred" \
                            and not str(layer.get("reason", "")).strip():
                        errors.append(
                            f"verifier-plan {lid}: mandatory layer deferred without a reason"
                        )
                    derived_layer = derived_by_id.get(lid)
                    if derived_layer is not None \
                            and derived_layer.get("status") != status:
                        errors.append(
                            f"verifier-plan {lid}: status {status!r} disagrees with "
                            f"derived {derived_layer.get('status')!r}"
                        )
                if plan.get("result_path") not in (None, STANDARD_RESULT_PATH):
                    errors.append(
                        f"verifier-plan result_path {plan.get('result_path')!r} != {STANDARD_RESULT_PATH}"
                    )
    verdict.record("verifier_plan", errors)


def check_fixture_matrix(case_dir: Path, verdict: Verdict) -> None:
    errors: list[str] = []
    matrix = _load_module("mvp_fixture_matrix", SCRIPTS_COMMON / "generate_fixture_matrix.py")
    result = matrix.build_matrix(case_dir)
    errors.extend(result["errors"])
    negative = case_dir / "tests" / "fixtures" / "negative"
    for name in REQUIRED_NEGATIVE_FIXTURES:
        if not (negative / name).is_dir():
            errors.append(
                f"tests/fixtures/negative/{name}/ missing: prose-only fixture descriptions "
                "do not close the fixture matrix"
            )
    if not (case_dir / "tests" / "fixtures" / STRUCTURAL_POSITIVE_FIXTURE).is_dir():
        errors.append(
            f"tests/fixtures/{STRUCTURAL_POSITIVE_FIXTURE}/ missing: the mount smoke needs "
            "one executable structural submission"
        )
    verdict.record("fixture_matrix", errors)


def check_cross_layer(case_dir: Path, verdict: Verdict) -> None:
    module = _load_module(
        "mvp_consistency", SCRIPTS_MLP / "check_draft_consistency.py"
    )
    result = module.check_case(case_dir)
    verdict.record("cross_layer_consistency", list(result["errors"]))


def check_real_packaging(case_dir: Path, repo_root: Path | None,
                        workdir: Path, verdict: Verdict) -> dict[str, Any] | None:
    """Load the actual CaseSpec and run the real packager. No fallback linter."""
    if repo_root is None:
        verdict.record("real_packaging", [
            "dftworld repository root not found (dftworld_bench missing on the path from "
            "the case directory); the runnable gate must use the real packager, never a "
            "substitute — pass --repo-root"
        ])
        return None
    errors: list[str] = []
    bundle: dict[str, Any] | None = None
    try:
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from dftworld_bench.contracts.case import CaseContractError, CaseSpec
        from dftworld_bench.core.packager import PackageError, package_candidate
        spec = CaseSpec.load(case_dir)
        bundle_dir = workdir / "candidate-bundle"
        manifest = package_candidate(spec, bundle_dir)
        bundle = {
            "spec_submission_root": spec.submission_root,
            "bundle_dir": str(bundle_dir),
            "paths": sorted(record.path for record in manifest.files),
            "public_digest": manifest.public_digest,
        }
    except (CaseContractError, PackageError) as exc:
        errors.append(f"real packager rejected the case: {exc}")
    except ImportError as exc:
        errors.append(f"cannot import the real packager from {repo_root}: {exc}")
    verdict.record("real_packaging", errors, bundle)
    return bundle


def check_bundle_agreement(case_dir: Path, bundle: dict[str, Any] | None,
                           design: dict, verdict: Verdict) -> None:
    if bundle is None:
        verdict.record("bundle_agreement", ["skipped: real packaging did not produce a bundle"])
        return
    consistency = _load_module("mvp_consistency_for_bundle", SCRIPTS_MLP / "check_draft_consistency.py")
    bundle_paths = set(bundle["paths"])
    errors: list[str] = []
    for token in sorted(consistency.instruction_input_tokens(case_dir)):
        if token.startswith("public/") and not consistency._path_exists_in_bundle(token, bundle_paths):
            errors.append(
                f"instruction references {token!r} but the ACTUAL packaged bundle has no such path"
            )
    input_manifest_path = case_dir / "public" / "input-manifest.json"
    if input_manifest_path.is_file():
        data = json.loads(input_manifest_path.read_text(encoding="utf-8"))
        raw_files = data.get("files") or []
        if isinstance(raw_files, dict):
            # map of {candidate_path: entry_dict}
            names = [
                path for path, entry in sorted(raw_files.items())
                if not (isinstance(entry, dict) and entry.get("candidate_generated") is True)
            ]
        else:
            names = [
                str(e.get("candidate_path", "")) for e in raw_files
                if isinstance(e, dict) and e.get("candidate_generated") is not True
            ]
        for name in names:
            if name and not consistency._path_exists_in_bundle(name, bundle_paths):
                errors.append(
                    f"public/input-manifest.json names {name!r} but the packaged bundle "
                    "does not stage it"
                )
    declared_visible = (design.get("contract") or {}).get("candidate_visible") \
        if isinstance(design.get("contract"), dict) else None
    if declared_visible is True and "CONTRACT.md" not in bundle_paths:
        errors.append("case-design declares CONTRACT.md candidate-visible but the bundle omits it")
    if declared_visible in (False, None) and "CONTRACT.md" in bundle_paths:
        errors.append("the bundle stages CONTRACT.md but case-design does not declare it candidate-visible")
    if "submission-schema.json" not in bundle_paths and "public/submission-schema.json" not in bundle_paths:
        errors.append("no submission-schema.json reached the bundle; the Candidate cannot see its output contract")
    verdict.record("bundle_agreement", errors)


def _run_mounted(tests_dir: Path, tmp: Path, submission: Path | None,
                 name: str, strip_files: tuple[str, ...] = ()) -> dict[str, Any]:
    mount = tmp / f"mount-{name}" / "tests"
    shutil.copytree(tests_dir, mount)
    for rel in strip_files:
        victim = mount / rel
        if victim.is_file():
            victim.unlink()
    result_dir = tmp / f"logs-{name}" / "verifier"
    env = dict(os.environ)
    env["PATH"] = f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/local/bin"
    if submission is not None:
        env["SUBMISSION_ROOT"] = str(submission)
    else:
        env.pop("SUBMISSION_ROOT", None)
        # make sure the in-container defaults cannot resolve either
        env["RESULT_DIR"] = str(result_dir)
    env["RESULT_DIR"] = str(result_dir)
    env["BENCH_RUN_ID"] = f"mvp-{name}"
    proc = subprocess.run(
        ["bash", str(mount / "test.sh")],
        text=True, capture_output=True, check=False, env=env, cwd=tmp,
    )
    result_path = result_dir / "result.json"
    payload: dict[str, Any] = {"_proc_returncode": proc.returncode,
                               "_stdout": proc.stdout[-500:], "_stderr": proc.stderr[-500:]}
    if result_path.is_file():
        try:
            payload["result"] = json.loads(result_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            payload["result_error"] = str(exc)
    return payload


def _result_errors(run: dict[str, Any], expect_class: str,
                   expect_codes: tuple[str, ...], expect_reason: tuple[str, ...],
                   expect_retryable: bool | None = None) -> list[str]:
    errors: list[str] = []
    result = run.get("result")
    if result is None:
        return [f"entry never wrote result.json ({run.get('result_error', 'missing')}); "
                f"stdout={run.get('_stdout', '')[-200:]}"]
    fields = set(result)
    if not fields <= RESULT_FIELDS:
        errors.append(f"result.json has fields outside the common schema: {sorted(fields - RESULT_FIELDS)}")
    if not {"run_id", "result_class", "failure_code", "reason", "retryable"} <= fields:
        errors.append("result.json is missing required common fields")
    if result.get("result_class") not in RESULT_CLASSES:
        errors.append(f"result_class {result.get('result_class')!r} not a common class")
    elif result.get("result_class") != expect_class:
        errors.append(
            f"expected {expect_class} but verifier produced {result.get('result_class')}"
            f"/{result.get('failure_code')}: {result.get('reason')!r}"
        )
    if result.get("result_class") == expect_class and expect_codes \
            and result.get("failure_code") not in expect_codes:
        errors.append(f"failure_code {result.get('failure_code')!r} not in {expect_codes}")
    reason = str(result.get("reason", ""))
    for needle in expect_reason:
        if needle not in reason:
            errors.append(f"reason {reason!r} does not attribute the failure via {needle!r}")
    if expect_retryable is not None and result.get("retryable") is not expect_retryable:
        errors.append(
            f"retryable={result.get('retryable')!r}, expected {expect_retryable!r}: "
            "this failure must not be charged to (or excused by) the harness"
        )
    return errors


def check_mount_smoke(case_dir: Path, workdir: Path, verdict: Verdict) -> None:
    errors: list[str] = []
    tests_dir = case_dir / "tests"
    if not (tests_dir / "test.sh").is_file() or not (tests_dir / "verifier.py").is_file():
        verdict.record("verifier_mount_smoke", [
            "tests/test.sh and/or tests/verifier.py missing: the graded chain does not exist; "
            "regenerate from the case template"
        ])
        return
    tmp = workdir / "smoke"
    tmp.mkdir(parents=True, exist_ok=True)
    fixtures = tests_dir / "fixtures"

    empty_root = tmp / "submission-empty"
    empty_root.mkdir(parents=True, exist_ok=True)
    errors.extend(_result_errors(
        _run_mounted(tests_dir, tmp, empty_root, "empty"),
        "AGENT_FAILURE", ("NO_SUBMISSION",), ("empty", "submission"),
    ))

    def staged_fixture(name: str) -> Path:
        dest = tmp / f"submission-{name}"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(fixtures / name, dest)
        return dest

    for name in ("negative/forged-manifest", "negative/missing-model",
                 "negative/broken-lineage"):
        errors.extend(_result_errors(
            _run_mounted(tests_dir, tmp, staged_fixture(name), name.replace("/", "-")),
            "AGENT_FAILURE", ("SCIENTIFIC_FAIL",), ("V",),
        ))

    for probe in V3_INTEGRITY_PROBES:
        probe_name, probe_class, probe_codes, probe_needles, probe_retry = probe
        errors.extend(_result_errors(
            _run_mounted(tests_dir, tmp, staged_fixture(probe_name),
                         probe_name.replace("/", "-")),
            probe_class, probe_codes, probe_needles, expect_retryable=probe_retry,
        ))

    structural = fixtures / STRUCTURAL_POSITIVE_FIXTURE
    if structural.is_dir():
        errors.extend(_result_errors(
            _run_mounted(tests_dir, tmp, staged_fixture(STRUCTURAL_POSITIVE_FIXTURE), "structural"),
            "VALID_RESULT", ("PASS",), ("deferred",),
        ))
    else:
        errors.append(f"structural positive fixture missing: {structural}")

    broken = _run_mounted(tests_dir, tmp, empty_root, "broken-entry", strip_files=("verifier.py",))
    errors.extend(_result_errors(broken, "INFRA_INVALID", ("HARNESS_FAILURE", "VERIFIER_FAILURE"), ()))

    verdict.record("verifier_mount_smoke", errors)


def check_honesty(case_dir: Path, design: dict, verdict: Verdict) -> None:
    errors: list[str] = []
    bv_path = case_dir / "benchmark_valid.json"
    if bv_path.is_file():
        bv = json.loads(bv_path.read_text(encoding="utf-8"))
        if bv.get("benchmark_valid") is True:
            errors.append("benchmark_valid=true is not derivable before release (hard fail)")
    reference_path = case_dir / "reference" / "reference.json"
    if reference_path.is_file():
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        if reference.get("state") in {"independently_verified", "reproducible"} \
                and not (reference.get("lineage") and reference.get("raw_evidence")):
            errors.append("reference.json claims a completed state without lineage/raw evidence")
    thresholds_path = case_dir / "reference" / "thresholds.json"
    if thresholds_path.is_file():
        thresholds = json.loads(thresholds_path.read_text(encoding="utf-8"))
        if thresholds.get("status") == "frozen" \
                and not (thresholds.get("produced_by") and thresholds.get("agent_results_inspected_at")):
            errors.append("thresholds.json is 'frozen' without calibration provenance")
    verdict.record("pre_discovery_honesty", errors)


# ---------------------------------------------------------------------------

def run_checks(case_dir: Path, repo_root: Path | None, workdir: Path) -> Verdict:
    verdict = Verdict()
    design: dict[str, Any] = {}
    try:
        import yaml
        design = yaml.safe_load((case_dir / "case-design.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        pass
    check_validate_case(case_dir, verdict)
    check_category_semantics(case_dir, design, verdict)
    check_verifier_plan(case_dir, verdict)
    check_fixture_matrix(case_dir, verdict)
    check_cross_layer(case_dir, verdict)
    bundle = check_real_packaging(case_dir, repo_root, workdir, verdict)
    check_bundle_agreement(case_dir, bundle, design, verdict)
    check_mount_smoke(case_dir, workdir, verdict)
    check_honesty(case_dir, design, verdict)
    return verdict


def derive_state(case_dir: Path, report: dict[str, Any], report_name: str) -> list[str]:
    validation_path = case_dir / "VALIDATION.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if validation.get("benchmark_valid") is True:
        return ["VALIDATION.json records benchmark_valid=true; refusing to downgrade"]
    validation["case_status"] = "runnable_draft"
    validation["runnable_derivation"] = {
        "derived_by": "check_discovery_runnable.py",
        "mvp_runnable": True,
        "report": report_name,
        "failing_checks": [],
    }
    validation_path.write_text(json.dumps(validation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--repo-root", type=Path,
                        help="dftworld repository root (default: walk up from the case)")
    parser.add_argument("--output", type=Path,
                        help="write the machine-readable report (MVP-READINESS.json)")
    parser.add_argument("--derive-state", action="store_true",
                        help="on a full pass, write case_status=runnable_draft into VALIDATION.json")
    parser.add_argument("--keep-workdir", type=Path,
                        help="keep packaging/smoke artifacts in this directory")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    case_dir = args.case_dir.resolve()
    if not case_dir.is_dir():
        print(f"check_discovery_runnable: no such case directory: {case_dir}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="mvp-run-") as td:
        workdir = args.keep_workdir or Path(td)
        if args.keep_workdir:
            workdir.mkdir(parents=True, exist_ok=True)
        repo_root = find_repo_root(case_dir, args.repo_root)
        verdict = run_checks(case_dir, repo_root, workdir)
        report = verdict.report()

        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            report_name = args.output.name
        else:
            report_name = "MVP-READINESS.json (not written)"

        if args.derive_state:
            if not verdict.mvp_runnable:
                print("check_discovery_runnable: refusing --derive-state; blocking errors:\n"
                      + "\n".join(f"  - {e}" for e in report["blocking_errors"]), file=sys.stderr)
                return 1
            refuse = derive_state(case_dir, report, report_name)
            if refuse:
                print(f"check_discovery_runnable: {refuse[0]}", file=sys.stderr)
                return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for error in report["blocking_errors"]:
            print(f"BLOCK: {error}")
        state = "runnable_draft" if report["mvp_runnable"] else "draft (blocked)"
        print(f"mvp_runnable={report['mvp_runnable']} state={state}")
    return 0 if report["mvp_runnable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
