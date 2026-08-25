#!/usr/bin/env python3
"""Author a v2 formal-run manifest.json for the evidence tree (plan Task 5).

Replaces the v1 full-workspace scan with the curated (policy-resolved) file list:
artifacts carry ``role`` and ``size_bytes``, the manifest references the case-level
evaluator bundle and artifact policy by digest, and the content-addressed bundle is
recorded with its two store locations. Identity, hardware/software, seed, timestamps,
and verifier metrics from v1 are preserved.

Usage::

    python scripts/reference/write_evidence_manifest.py \\
        --case-dir 032-matclaw-cips-curie-temperature \\
        --restored evidence/matclaw/formal/032/run-1/restored \\
        --files-json curated_files.json \\
        --verifier-report artifacts/verifier_report.json \\
        --seed 2026081206 --run-id run-1 \\
        --git-commit 71078a4 --git-clean true \\
        --gpu-image ... --gpu-image-digest sha256:... \\
        --cpu-verifier-image ... --cpu-verifier-image-digest sha256:... \\
        --workspace-identity 032-2026081206 \\
        --started-at ... --finished-at ... \\
        --hardware-json '{...}' --software-json '{...}' \\
        --bundle-json '{"format":"tar.zst","sha256":"...","size_bytes":123,"primary_uri":"cas+file://...","primary_version":"123","replica_uri":"cas+file://...","replica_version":"123","verified_at":"..."}'

Writes ``manifest.json`` beside ``--restored`` (i.e. ``run-N/manifest.json``).
Stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case-dir", required=True, type=Path)
    ap.add_argument("--restored", required=True, type=Path)
    ap.add_argument("--files-json", required=True, type=Path,
                    help="curated list: [{path, role, size_bytes, sha256}] workspace-relative")
    ap.add_argument("--verifier-report", required=True, type=Path)
    ap.add_argument("--seed", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--git-commit", required=True)
    ap.add_argument("--git-clean", default="true")
    ap.add_argument("--gpu-image", required=True)
    ap.add_argument("--gpu-image-digest", required=True)
    ap.add_argument("--cpu-verifier-image", required=True)
    ap.add_argument("--cpu-verifier-image-digest", required=True)
    ap.add_argument("--workspace-identity", default=None)
    ap.add_argument("--evaluator-bundle-sha256", default=None,
                    help="digest of the case-level evaluator manifest; default computes it")
    ap.add_argument("--artifact-policy-sha256", default=None,
                    help="digest of case reference/evidence-policy.json; default computes it")
    ap.add_argument("--started-at", default=None)
    ap.add_argument("--finished-at", default=None)
    ap.add_argument("--hardware-json", default="{}")
    ap.add_argument("--software-json", default="{}")
    ap.add_argument("--bundle-json", required=True,
                    help="stored bundle descriptor from the CAS store (both locations verified)")
    args = ap.parse_args(argv)

    case_dir = args.case_dir.resolve()
    restored = args.restored.resolve()
    if not case_dir.is_dir():
        print(f"case dir missing: {case_dir}", file=sys.stderr)
        return 1
    if not restored.is_dir():
        print(f"restored dir missing: {restored}", file=sys.stderr)
        return 1

    report = json.loads(args.verifier_report.read_text(encoding="utf-8"))
    if report.get("valid") is not True:
        print(f"verifier report NOT valid: {report.get('errors')}", file=sys.stderr)
        return 1

    curated = json.loads(args.files_json.read_text(encoding="utf-8"))
    if not isinstance(curated, list) or not curated:
        print("files-json must be a non-empty list", file=sys.stderr)
        return 1

    # Artifacts reference the restored tree relative to the manifest dir (run-N).
    artifacts = []
    for entry in curated:
        rel = str(entry["path"])
        if rel.startswith("/") or ".." in rel.split("/"):
            print(f"unsafe curated path: {rel!r}", file=sys.stderr)
            return 1
        size = int(entry["size_bytes"])
        digest = entry["sha256"]
        restored_path = restored / rel
        if not restored_path.is_file():
            print(f"restored artifact missing: {rel}", file=sys.stderr)
            return 1
        if restored_path.stat().st_size != size:
            print(f"restored size mismatch: {rel}", file=sys.stderr)
            return 1
        if _sha256_file(restored_path) != digest:
            print(f"restored digest mismatch: {rel}", file=sys.stderr)
            return 1
        artifacts.append({"path": f"restored/{rel}", "role": entry["role"],
                          "size_bytes": size, "sha256": digest})

    # Case-level digests: evaluator bundle (case manifest) and artifact policy.
    evaluator_sha = args.evaluator_bundle_sha256
    if not evaluator_sha:
        evaluator_manifest = case_dir / "evaluator-manifest.json"
        if not evaluator_manifest.is_file():
            print("no evaluator-manifest.json in case dir", file=sys.stderr)
            return 1
        evaluator_sha = json.loads(evaluator_manifest.read_text(encoding="utf-8")).get("bundle_sha256")
    policy_path = case_dir / "reference" / "evidence-policy.json"
    if not policy_path.is_file():
        print("no evidence-policy.json in case reference/", file=sys.stderr)
        return 1
    policy_sha = args.artifact_policy_sha256 or hashlib.sha256(
        json.dumps(json.loads(policy_path.read_text(encoding="utf-8")),
                   sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    bundle = json.loads(Path(args.bundle_json).read_text(encoding="utf-8"))
    for key in ("format", "sha256", "size_bytes", "primary_uri", "primary_version",
                "replica_uri", "replica_version", "verified_at"):
        if key not in bundle:
            print(f"bundle descriptor missing key: {key}", file=sys.stderr)
            return 1

    case_id = args.case_dir.name.split("-matclaw-")[0] if "-matclaw-" in args.case_dir.name else args.case_dir.name
    metrics = _metrics_for_case(case_id, restored, report)
    started_at = args.started_at or _now_utc()
    finished_at = args.finished_at or _now_utc()
    identity = args.workspace_identity or f"{case_id}-{args.seed}"

    manifest = {
        "schema_version": "2.0",
        "evidence_class": "formal",
        "case": case_id,
        "case_version": json.loads(policy_path.read_text(encoding="utf-8")).get("schema_version", "1"),
        "profile": "paper",
        "seed": int(args.seed),
        "run_id": args.run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_status": 0,
        "git_commit": args.git_commit,
        "git_clean": args.git_clean == "true",
        "gpu_image": args.gpu_image,
        "gpu_image_digest": args.gpu_image_digest,
        "cpu_verifier_image": args.cpu_verifier_image,
        "cpu_verifier_image_digest": args.cpu_verifier_image_digest,
        "hardware": json.loads(args.hardware_json),
        "software": json.loads(args.software_json),
        "command": f"formal round {args.run_id.split('-')[-1]} seed {args.seed} (HPC paper run + hidden verifier)",
        "workspace_identity": identity,
        "evaluator_bundle_sha256": evaluator_sha,
        "artifact_policy_sha256": policy_sha,
        "artifacts": artifacts,
        "bundle": bundle,
        "verifier_report": {
            "valid": report.get("valid"),
            "errors": report.get("errors", []),
            "metrics": metrics,
        },
    }
    # recomputed_estimate is case-specific (e.g. Tc_K); omit it when the
    # verifier produced none rather than writing a schema-rejected null.
    if report.get("recomputed_estimate"):
        manifest["verifier_report"]["recomputed_estimate"] = report["recomputed_estimate"]

    out = restored.parent / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(artifacts)} curated artifacts, bundle={bundle['sha256'][:12]}…)")
    return 0


def _metrics_for_case(case_id: str, restored: Path, report: dict) -> dict:
    """Scientific metrics consumed by the gate; mirrors the v1 writer for 032."""
    if case_id == "031":
        return {
            "final_force_mae_eV_A": report.get("recomputed_final_mae_eV_A"),
            "active_iterations": report.get("active_iterations"),
        }
    if case_id == "033":
        result_path = restored / "result.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            best = result.get("best") or {}
            history = result.get("history", [])
            per_iteration: dict[int, int] = {}
            for row in history:
                iteration = int(row.get("iteration", 0))
                per_iteration[iteration] = per_iteration.get(iteration, 0) + 1
            return {
                "best_Ez_V_A": best.get("Ez_V_A"),
                "best_temperature_K": best.get("temperature_K"),
                "slope_ps_per_site": best.get("slope_ps_per_site"),
                "rounds": len(per_iteration),
                "jobs": len(history),
                "max_jobs_per_round": max(per_iteration.values()) if per_iteration else 0,
            }
        return {}
    curve = report.get("recomputed_curve", [])
    atom_count = None
    result_path = restored / "result.json"
    if result_path.is_file():
        atom_count = json.loads(result_path.read_text(encoding="utf-8")).get("atom_count")
    return {
        "Tc_K": (report.get("recomputed_estimate") or {}).get("Tc_K"),
        "temperatures_K": [row.get("temperature_K") for row in curve],
        "atom_count": atom_count,
    }


if __name__ == "__main__":
    raise SystemExit(main())
