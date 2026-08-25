"""ER7: validation derives from restorable bundles and fails closed.

A v2 run is sealed once (bundle stored in primary + replica CAS). Local
workspace bytes are caches: deleting them must not break validation that
restores from the store. Corruption of primary must fall back to the replica;
corruption of both must force the run invalid; a changed evaluator digest must
be caught as an identity mismatch before scientific validation. v1 manifests
keep validating adjacent bytes and report ``migration_required=true``.
"""

from __future__ import annotations

import json
from pathlib import Path

from support import CASES, POLICY, ScriptableVerifierRuntime, build_workspace, sha

from scripts.ablation.verify_evidence import (
    EVIDENCE_HASH_MISMATCH,
    EVIDENCE_UNAVAILABLE,
    EVALUATOR_IDENTITY_MISMATCH,
    _validate_run,
    main,
)
from scripts.evidence.finalize_run import FinalizeTransaction
from scripts.evidence.store import EvidenceStore
from scripts.matclaw_validation import load_policy

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = ROOT / "benchmark" / "sources" / "matclaw" / "acceptance.json"
CASE_DIR = CASES["032"]
POLICY032 = POLICY["032"]
GRID = [100, 150, 200, 250, 275, 300, 325, 350, 375, 400, 450, 500, 600]


class GateValidRuntime(ScriptableVerifierRuntime):
    """Scriptable runtime that returns the locked 13-value grid so the science
    gate (temperatures_K == acceptance grid, Tc within tolerance) passes."""

    def run(self, submission: Path, profile: str) -> dict:
        report = super().run(submission, profile)
        report["recomputed_curve"] = [{"temperature_K": t} for t in GRID]
        report["recomputed_estimate"] = {"Tc_K": 259.44}
        return report


def _store_roots(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "store" / "primary", tmp_path / "store" / "replica"


def _seal_run(tmp_path: Path, run_dir: Path, *, runtime=None,
              seed: str = "2026081206") -> Path:
    """Build a full workspace and run the finalize transaction to MANIFEST_COMMITTED."""
    primary, replica = _store_roots(tmp_path)
    ws = build_workspace(tmp_path / "ws", "032")
    tx = FinalizeTransaction(
        run_dir=run_dir, workspace=ws, case_dir=CASE_DIR, policy=POLICY032,
        verifier_runtime=runtime or GateValidRuntime(POLICY032),
        primary_uri=f"cas+file://{primary}", replica_uri=f"cas+file://{replica}",
        manifest_args=_manifest_args(seed))
    tx.run()
    assert tx.current() == "MANIFEST_COMMITTED"
    return run_dir


def _manifest_args(seed: str) -> dict:
    return {
        "seed": seed, "run_id": "run-1",
        "git_commit": "71078a4", "git_clean": "true",
        "gpu_image": "dftworld-base-matclaw-cips:2.2.11-gpu-amd64",
        "gpu_image_digest": "sha256:" + "34" * 32,
        "cpu_verifier_image": "dftworld-base-matclaw-cips:2.2.11-cpu-amd64",
        "cpu_verifier_image_digest": "sha256:" + "f3" * 32,
        "workspace_identity": f"032-{seed}",
        "started_at": "2026-08-18T11:36:27Z",
        "finished_at": "2026-08-18T12:12:34Z",
        "hardware_json": json.dumps({"job": "3567687", "node": "<site-node-gpu3>"}),
        "software_json": json.dumps({"apptainer_sif_sha256": "99" * 32}),
    }


def _corrupt(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[0] ^= 0xFF
    path.write_bytes(bytes(data))


def test_restore_always_recovers_after_local_bytes_deleted(tmp_path: Path) -> None:
    """Local restored/ + bundle are caches; deleting them must not invalidate a run."""
    run_dir = tmp_path / "evidence" / "032" / "run-1"
    _seal_run(tmp_path, run_dir)
    # wipe the local curated/restored bytes and the local bundle copy
    for child in run_dir.iterdir():
        if child.name in ("restored", "bundle.tar.zst", "bundle-staging"):
            (run_dir / child.name).unlink() if child.is_file() else __import__("shutil").rmtree(child)

    result = _validate_run("032", run_dir, load_policy(ACCEPTANCE), CASE_DIR, "always")
    assert result["valid"] is True, result["errors"]
    assert result["restored"] is True
    assert result["failure_code"] is None


def test_replica_recovery_when_primary_corrupt(tmp_path: Path) -> None:
    """Corrupt primary must fall back to the independent replica."""
    run_dir = tmp_path / "evidence" / "032" / "run-1"
    _seal_run(tmp_path, run_dir)
    primary, _ = _store_roots(tmp_path)
    obj = next(primary.rglob("*.tar.zst"))
    _corrupt(obj)

    result = _validate_run("032", run_dir, load_policy(ACCEPTANCE), CASE_DIR, "always")
    assert result["valid"] is True, result["errors"]
    assert result["restored"] is True


def test_both_stores_corrupt_forces_invalid(tmp_path: Path) -> None:
    """Both stores corrupt -> the run cannot be proven restorable -> invalid."""
    run_dir = tmp_path / "evidence" / "032" / "run-1"
    _seal_run(tmp_path, run_dir)
    primary, replica = _store_roots(tmp_path)
    _corrupt(next(primary.rglob("*.tar.zst")))
    _corrupt(next(replica.rglob("*.tar.zst")))

    result = _validate_run("032", run_dir, load_policy(ACCEPTANCE), CASE_DIR, "always")
    assert result["valid"] is False
    assert result["failure_code"] == EVIDENCE_HASH_MISMATCH
    assert result["restored"] is False


def test_both_stores_missing_fails_closed_when_always(tmp_path: Path) -> None:
    """--restore always must not silently accept adjacent bytes when the store is gone."""
    run_dir = tmp_path / "evidence" / "032" / "run-1"
    _seal_run(tmp_path, run_dir)
    primary, replica = _store_roots(tmp_path)
    for obj in list(primary.rglob("*.tar.zst")) + list(replica.rglob("*.tar.zst")):
        obj.unlink()

    result = _validate_run("032", run_dir, load_policy(ACCEPTANCE), CASE_DIR, "always")
    assert result["valid"] is False
    assert result["failure_code"] == EVIDENCE_UNAVAILABLE
    assert result["restored"] is False


def test_auto_falls_back_to_adjacent_bytes_when_store_missing(tmp_path: Path) -> None:
    """auto keeps the pre-migration contract: local bytes still validate."""
    run_dir = tmp_path / "evidence" / "032" / "run-1"
    _seal_run(tmp_path, run_dir)
    primary, replica = _store_roots(tmp_path)
    for obj in list(primary.rglob("*.tar.zst")) + list(replica.rglob("*.tar.zst")):
        obj.unlink()

    result = _validate_run("032", run_dir, load_policy(ACCEPTANCE), CASE_DIR, "auto")
    assert result["valid"] is True  # adjacent restored/ bytes still validate
    assert result["restored"] is False
    assert result["failure_code"] == EVIDENCE_UNAVAILABLE


def test_evaluator_identity_mismatch_before_science(tmp_path: Path) -> None:
    """A changed evaluator digest must be an identity failure, not a science pass."""
    run_dir = tmp_path / "evidence" / "032" / "run-1"
    _seal_run(tmp_path, run_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evaluator_bundle_sha256"] = "5" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    result = _validate_run("032", run_dir, load_policy(ACCEPTANCE), CASE_DIR, "always")
    assert result["valid"] is False
    assert result["failure_code"] == EVALUATOR_IDENTITY_MISMATCH


def test_policy_digest_mismatch_is_identity_failure(tmp_path: Path) -> None:
    run_dir = tmp_path / "evidence" / "032" / "run-1"
    _seal_run(tmp_path, run_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_policy_sha256"] = "6" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    result = _validate_run("032", run_dir, load_policy(ACCEPTANCE), CASE_DIR, "always")
    assert result["valid"] is False
    assert result["failure_code"] == EVALUATOR_IDENTITY_MISMATCH


def _v1_manifest(run_dir: Path, case_id: str = "032") -> Path:
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    payload = json.dumps({"case": case_id, "run_id": "run-1"}, sort_keys=True).encode()
    (artifacts / "result.json").write_bytes(payload)
    manifest = {
        "schema_version": "1.0", "evidence_class": "formal", "case": case_id,
        "profile": "paper", "seed": 2026081206, "run_id": "run-1",
        "started_at": "2026-08-18T11:36:27Z", "finished_at": "2026-08-18T12:12:34Z",
        "exit_status": 0, "git_commit": "71078a4", "git_clean": True,
        "gpu_image": "dftworld-base-matclaw-cips:2.2.11-gpu-amd64",
        "gpu_image_digest": "sha256:" + "34" * 32,
        "cpu_verifier_image": "dftworld-base-matclaw-cips:2.2.11-cpu-amd64",
        "cpu_verifier_image_digest": "sha256:" + "f3" * 32,
        "hardware": {"job": "3567687"}, "software": {"apptainer_sif_sha256": "99" * 32},
        "command": ["bash", "/solution/solve.sh"],
        "workspace_identity": "032-2026081206",
        "artifacts": [{"path": "artifacts/result.json", "sha256": sha(payload)}],
        "verifier_report": {"valid": True, "metrics": {
            "Tc_K": 259.44, "temperatures_K": GRID, "atom_count": 360}},
    }
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def test_v1_manifest_reports_migration_required(tmp_path: Path) -> None:
    """v1 keeps validating adjacent bytes but never claims durable storage."""
    run_dir = tmp_path / "evidence" / "032" / "run-1"
    _v1_manifest(run_dir)

    result = _validate_run("032", run_dir, load_policy(ACCEPTANCE), CASE_DIR, "always")
    assert result["valid"] is True, result["errors"]
    assert result["migration_required"] is True
    assert result["restored"] is False


def test_main_end_to_end_evidence_complete(tmp_path: Path, capsys) -> None:
    """Two sealed runs + --restore always -> evidence_complete true."""
    import shutil

    ev_root = tmp_path / "evidence" / "032"
    _seal_run(tmp_path, ev_root / "run-1", seed="2026081206")
    # second run with a fresh workspace so its bundle digest differs
    ws2 = build_workspace(tmp_path / "ws2", "032")
    primary, replica = _store_roots(tmp_path)
    run_dir2 = ev_root / "run-2"
    ws2.mkdir(parents=True, exist_ok=True)
    tx = FinalizeTransaction(
        run_dir=run_dir2, workspace=ws2, case_dir=CASE_DIR, policy=POLICY032,
        verifier_runtime=GateValidRuntime(POLICY032),
        primary_uri=f"cas+file://{primary}", replica_uri=f"cas+file://{replica}",
        manifest_args=_manifest_args("2026081213"))
    tx.run()

    capsys.readouterr()  # drain finalize/manifest-writer chatter
    rc = main(["--case", "032", "--evidence-root",
               str(tmp_path / "evidence"), "--restore", "always"])
    assert rc == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["evidence_complete"] is True, verdict["runs"]
    for run_id, result in verdict["runs"].items():
        assert result["valid"] is True, (run_id, result["errors"])
        assert result["restored"] is True

    # and the case-level gate on the evidence side is provable via the same path
    store = EvidenceStore(f"cas+file://{primary}", f"cas+file://{replica}")
    for run in ("run-1", "run-2"):
        manifest = json.loads((ev_root / run / "manifest.json").read_text())
        store.verify(manifest["bundle"]["sha256"])
