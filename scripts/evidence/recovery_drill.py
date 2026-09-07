#!/usr/bin/env python3
"""Recovery drill: restore a sealed run from the CAS store and re-run its Verifier
over the restored tree (plan Task 9).

Proves the durable claim end to end: bundle bytes -> exact restore -> independent
Verifier re-run, with a JSON report fit for Run Record attachment. The drill never
touches the sealed manifest or the store; it reads both.

Stores are normally cluster-side (``cas+file://``). For HPC cases run this on the
cluster login node where the store and the frozen verifier SIF live; for a local
store any host works.

Usage::

    PYTHONPATH=$PAYLOAD python3 scripts/evidence/recovery_drill.py \\
        --case 001 --run run-1 \\
        --evidence-root …/matclaw-001/evidence \\
        --case-dir $PAYLOAD/cases/001-matclaw-cips-active-distillation \\
        --verifier-slurm \\
        --verifier-sif …/matclaw-cips-2.2.11-cpu-amd64.sif \\
        --verifier-tests $PAYLOAD/cases/001-matclaw-cips-active-distillation/verifier \\
        --verifier-apptainer /public/software/apptainer/bin/apptainer \\
        --verifier-scratch …/verify-scratch \\
        --verifier-partition cpu --verifier-gres '' --verifier-account acct-blocked

Exit 0 iff the bundle restored with matching digests and the Verifier returned
``valid=true``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from scripts.evidence.store import EvidenceStore
from scripts.evidence.finalize_run import SifVerifierRuntime, SlurmVerifierRuntime

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_ROOT = ROOT / "evidence" / "matclaw" / "formal"
PROFILE = "paper"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _artifact_check(manifest: dict, restored: Path) -> tuple[bool, list[str]]:
    """Every manifest artifact (role scoring_required etc.) must match by digest."""
    errors = []
    for artifact in manifest.get("artifacts", []):
        rel = artifact.get("path", "").removeprefix("restored/")
        candidate = restored / rel
        if not candidate.is_file():
            errors.append(f"restored artifact missing: {rel}")
            continue
        if _sha256_file(candidate) != artifact.get("sha256"):
            errors.append(f"restored artifact digest mismatch: {rel}")
    return not errors, errors


def recover_and_verify(case_id: str, run_id: str, evidence_root: Path,
                       case_dir: Path, policy: dict, verifier_runtime,
                       profile: str = PROFILE, work_dir: Path | None = None) -> dict:
    """Restore the run's bundle, byte-check it, and re-run the verifier over it.

    ``work_dir`` must be on a filesystem shared with the verifier's compute node
    (Slurm binds the restored tree read-only into the SIF); per-node ``/tmp``
    from the login node is not visible there.
    """
    run_dir = evidence_root / case_id / run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no manifest for {case_id}/{run_id}: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "2.0" or not manifest.get("bundle"):
        raise ValueError(f"{case_id}/{run_id} is not a sealed v2 run (no bundle)")
    bundle = manifest["bundle"]
    digest, size = bundle["sha256"], int(bundle["size_bytes"])
    store = EvidenceStore(bundle["primary_uri"], bundle["replica_uri"])

    work = work_dir or Path(tempfile.mkdtemp(prefix=f"evidence-drill-{case_id}-{run_id}-"))
    restore_dir = work / "restored"
    try:
        try:
            extracted = store.get(digest, restore_dir)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"{case_id}/{run_id} bundle unavailable in both stores: {digest[:16]}…") from exc
        # extract_bundle already recomputed per-file digests; re-check the curated set.
        artifacts_ok, errors = _artifact_check(manifest, restore_dir)
        restored_report = {
            "sha256_ok": True,
            "bundle_sha256": digest,
            "size_bytes": size,
            "files": extracted,
            "artifacts_ok": artifacts_ok,
            "errors": errors,
        }
        if not artifacts_ok:
            return {"case_id": case_id, "run_id": run_id, "valid": False,
                    "restored": restored_report,
                    "verifier": {"valid": False, "errors": ["artifact digest check failed"]},
                    "errors": errors}
        verifier_report = verifier_runtime.run(restore_dir, profile)
        valid = bool(verifier_report.get("valid"))
        return {
            "case_id": case_id, "run_id": run_id, "profile": profile,
            "valid": valid,
            "restored": restored_report,
            "verifier": verifier_report,
            "errors": [] if valid else verifier_report.get("errors", []),
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", required=True, choices=("001", "002", "003", "004", "005"))
    ap.add_argument("--run", required=True, choices=("run-1", "run-2"))
    ap.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    ap.add_argument("--case-dir", type=Path, required=True)
    ap.add_argument("--policy", type=Path, required=True)
    ap.add_argument("--profile", default=PROFILE)
    ap.add_argument("--verifier-slurm", action="store_true",
                    help="run the verifier via sbatch (cluster compute node)")
    ap.add_argument("--verifier-sif", default=None)
    ap.add_argument("--verifier-tests", default=None)
    ap.add_argument("--verifier-apptainer", default=None)
    ap.add_argument("--verifier-scratch", default=None)
    ap.add_argument("--verifier-partition", default="gpu")
    ap.add_argument("--verifier-gres", default="gpu:1")
    ap.add_argument("--verifier-account", default=None)
    ap.add_argument("--verifier-host", default=None,
                    help="Slurm host for SifVerifierRuntime (non-slurm mode)")
    ap.add_argument("--verifier-cpus", type=int, default=8)
    ap.add_argument("--verifier-mem", default="64G")
    ap.add_argument("--verifier-time", default="02:00:00")
    args = ap.parse_args(argv)

    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    if args.verifier_slurm:
        if not (args.verifier_sif and args.verifier_tests and args.verifier_apptainer
                and args.verifier_scratch):
            ap.error("--verifier-slurm requires --verifier-sif/--verifier-tests/"
                     "--verifier-apptainer/--verifier-scratch")
        runtime = SlurmVerifierRuntime(
            sif=args.verifier_sif, tests_src=Path(args.verifier_tests),
            apptainer=args.verifier_apptainer, scratch_base=args.verifier_scratch,
            partition=args.verifier_partition, gres=args.verifier_gres,
            account=args.verifier_account, cpus=args.verifier_cpus,
            mem=args.verifier_mem, time=args.verifier_time)
    else:
        if not (args.verifier_sif and args.verifier_host):
            ap.error("non-slurm drill requires --verifier-sif and --verifier-host "
                     "(SifVerifierRuntime: ssh + apptainer exec on a Slurm host)")
        runtime = SifVerifierRuntime(
            host=args.verifier_host, sif=args.verifier_sif,
            tests_src=Path(args.verifier_tests or "."),
            apptainer=args.verifier_apptainer or "/bin/sh",
            scratch_base=args.verifier_scratch or str(Path(tempfile.gettempdir())))

    report = recover_and_verify(
        case_id=args.case, run_id=args.run, evidence_root=args.evidence_root,
        case_dir=args.case_dir, policy=policy,
        verifier_runtime=runtime, profile=args.profile,
        work_dir=Path(args.verifier_scratch) if args.verifier_scratch else None)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
