#!/usr/bin/env python3
"""Prove the curated (policy-resolved) file set reproduces verifier outcomes (plan Task 5).

``curate_and_verify`` stages only the files the hidden verifier reads plus declared
reproduction records, runs the verifier against both the original workspace and the
staged directory, and refuses to bundle on *any* scientific difference. Fail-closed:
a removed required trajectory/model/dataset surfaces as a metric mismatch (or a
verifier validity flip), not as a silent acceptance.

The verifier runtime is injected (``LocalVerifierRuntime`` for an in-process run when
numpy/ase are present; a SIF/container runtime for real HPC finalization). The host
unit tests use a scriptable runtime that reads the same files the frozen verifier reads;
the genuine independent-verifier equality proof runs on the cluster during finalization
(ER6/ER8), where the frozen verifier container is mounted over the curated tree.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from scripts.evidence.resolve_required_artifacts import EvidenceFile, resolve_required_artifacts


class CurateError(ValueError):
    """The curated tree does not reproduce the original verifier outcome."""


class VerifierRuntime(Protocol):
    def run(self, submission: Path, profile: str) -> dict[str, Any]:
        """Run the frozen case verifier over ``submission``; return its report dict."""


class LocalVerifierRuntime:
    """Import the case verifier module from ``case_dir/tests`` and run in-process.

    Requires numpy and ase in the interpreter. Real finalization should use the
    container runtime instead; this exists for hosts that can run the verifier.
    """

    def __init__(self, case_dir: Path | str) -> None:
        self.case_dir = Path(case_dir)
        verifier_path = self.case_dir / "tests" / "verifier.py"
        if not verifier_path.is_file():
            raise CurateError(f"no verifier.py at {verifier_path}")
        spec = importlib.util.spec_from_file_location("_case_verifier", verifier_path)
        if spec is None or spec.loader is None:
            raise CurateError(f"cannot load verifier module {verifier_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._verify = module.verify

    def run(self, submission: Path, profile: str) -> dict[str, Any]:
        return self._verify(submission, profile)


# Per-case scientific metrics that must be identical between original and curated
# runs. Keyed by the metric names the science gate (matclaw_validation) consumes.

_CASE_METRIC_EXTRACTORS: dict[str, Any] = {}


def _result(workspace: Path) -> dict[str, Any]:
    result_path = workspace / "result.json"
    if not result_path.is_file():
        raise CurateError(f"missing result.json in {workspace}")
    return json.loads(result_path.read_text(encoding="utf-8"))


def _metrics_001(workspace: Path, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "valid": report.get("valid"),
        "final_force_mae_eV_A": report.get("recomputed_final_mae_eV_A"),
        "active_iterations": report.get("active_iterations"),
        "file_sha256": report.get("file_sha256"),
    }


def _metrics_002(workspace: Path, report: dict[str, Any]) -> dict[str, Any]:
    return {
        "valid": report.get("valid"),
        "Tc_K": (report.get("recomputed_estimate") or {}).get("Tc_K"),
        "temperatures_K": [row.get("temperature_K") for row in report.get("recomputed_curve", [])],
        "atom_count": _result(workspace).get("atom_count"),
        "file_sha256": report.get("file_sha256"),
    }


def _metrics_003(workspace: Path, report: dict[str, Any]) -> dict[str, Any]:
    result = _result(workspace)
    best = result.get("best") or {}
    history = result.get("history", [])
    per_iteration: dict[int, int] = {}
    for row in history:
        iteration = int(row.get("iteration", 0))
        per_iteration[iteration] = per_iteration.get(iteration, 0) + 1
    return {
        "valid": report.get("valid"),
        "best_Ez_V_A": best.get("Ez_V_A"),
        "best_temperature_K": best.get("temperature_K"),
        "slope_ps_per_site": best.get("slope_ps_per_site"),
        "rounds": len(per_iteration),
        "jobs": len(history),
        "max_jobs_per_round": max(per_iteration.values()) if per_iteration else 0,
        "file_sha256": report.get("file_sha256"),
    }


_CASE_METRIC_EXTRACTORS = {
    "001": _metrics_001,
    "002": _metrics_002,
    "003": _metrics_003,
}


def _metrics_for(case_id: str, workspace: Path, report: dict[str, Any]) -> dict[str, Any]:
    extractor = _CASE_METRIC_EXTRACTORS.get(case_id)
    if extractor is None:
        raise CurateError(f"no metric extractor for case {case_id!r}")
    return extractor(workspace, report)


def stage_files(workspace: Path, files: list[EvidenceFile], staging_dir: Path | str | None = None) -> Path:
    """Copy resolved files into a staging dir; no links are followed.

    With ``staging_dir`` the copy is (re)created there so a finalization
    transaction can resume from a deterministic path; otherwise a fresh temp
    dir is used.
    """
    staging = Path(staging_dir) if staging_dir is not None \
        else Path(tempfile.mkdtemp(prefix=f"curated-{workspace.name}-"))
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for entry in files:
        target = staging / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        source = workspace / entry.path
        if not source.is_file():
            raise CurateError(f"resolved artifact vanished: {entry.path}")
        shutil.copyfile(source, target)  # follows source, never materializes links
        target.chmod(0o644)
    return staging


@dataclass
class CuratedEvidence:
    case_id: str
    files: list[EvidenceFile]
    staging_dir: Path
    original_report: dict[str, Any]
    curated_report: dict[str, Any]
    metrics: dict[str, Any]

    @property
    def valid(self) -> bool:
        return bool(self.curated_report.get("valid"))


def verify_curated(
    case_id: str,
    workspace: Path,
    staging: Path,
    verifier_runtime: VerifierRuntime,
    profile: str = "paper",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run the verifier over workspace and staging; refuse on divergence/invalidity.

    Returns ``(original_report, curated_report, curated_metrics)``. Raises
    ``CurateError`` on any metric difference or a non-valid verdict.
    """
    try:
        original_report = verifier_runtime.run(workspace, profile)
        curated_report = verifier_runtime.run(staging, profile)
    except CurateError:
        raise
    except Exception as exc:
        raise CurateError(f"verifier failed during curation: {exc}") from exc

    original_metrics = _metrics_for(case_id, workspace, original_report)
    curated_metrics = _metrics_for(case_id, staging, curated_report)
    differences = {
        key: (original_metrics.get(key), curated_metrics.get(key))
        for key in sorted(set(original_metrics) | set(curated_metrics))
        if original_metrics.get(key) != curated_metrics.get(key)
    }
    if differences:
        raise CurateError(f"curated tree diverges from original: {differences}")
    if not curated_report.get("valid"):
        raise CurateError(
            f"verifier rejects curated tree: {curated_report.get('errors')}")
    return original_report, curated_report, curated_metrics


def curate_and_verify(
    case_dir: Path | str,
    workspace: Path | str,
    policy: dict[str, Any],
    verifier_runtime: VerifierRuntime,
    profile: str = "paper",
    staging_dir: Path | str | None = None,
) -> CuratedEvidence:
    """Resolve the minimal policy set, verify it, and refuse on any difference."""
    workspace = Path(workspace).resolve()
    files = resolve_required_artifacts(workspace, policy)
    if not files:
        raise CurateError("policy resolved no artifacts — nothing to curate")

    staging = stage_files(workspace, files, staging_dir)
    try:
        original_report, curated_report, curated_metrics = verify_curated(
            _case_id(policy), workspace, staging, verifier_runtime, profile)
    except Exception:
        if staging_dir is None:
            shutil.rmtree(staging, ignore_errors=True)
        raise

    return CuratedEvidence(
        case_id=_case_id(policy),
        files=files,
        staging_dir=staging,
        original_report=original_report,
        curated_report=curated_report,
        metrics=curated_metrics,
    )


def _case_id(policy: dict[str, Any]) -> str:
    case_id = policy.get("case_id")
    if not case_id:
        raise CurateError("policy has no case_id")
    return str(case_id)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("workspace", type=Path)
    ap.add_argument("--policy", required=True, type=Path)
    ap.add_argument("--profile", default="paper")
    args = ap.parse_args(argv)

    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    evidence = curate_and_verify(args.case_dir, args.workspace, policy,
                                 LocalVerifierRuntime(args.case_dir), args.profile)
    print(f"curated {len(evidence.files)} files; verifier valid={evidence.valid}")
    for key, value in evidence.metrics.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
