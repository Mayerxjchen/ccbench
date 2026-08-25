"""ER9: retention audits, recovery drills, and safe garbage collection (TDD).

Operational guarantees this suite pins:

- ``audit_store`` checks every Git-tracked v2 manifest against both stores.
  Both present + digest/size correct = ``complete``. Exactly one present is
  ``degraded``, never "valid redundancy". Neither present blocks benchmark
  validity. A corrupt object is reported, never silently trusted.
- ``gc_plan`` is dry-run by default: a referenced object never appears in a
  deletion plan, and unreferenced uploads stay protected for a 30-day grace
  period before they even become candidates. Actual deletion needs an explicit
  ``--apply`` and does not happen through the planning API.
- ``recovery_drill`` restores a selected run from the store and re-runs its
  Verifier over the restored tree, producing a JSON report fit for Run Record
  attachment.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from scripts.evidence.store import EvidenceStore, StoredObject

# Modules under test — imported lazily so the RED phase reports cleanly that
# the operational tools do not exist yet.


def _import(name: str):
    return __import__(f"scripts.evidence.{name}", fromlist=["*"])


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _store(tmp_path: Path) -> tuple[Path, Path, EvidenceStore]:
    primary = tmp_path / "store-primary"
    replica = tmp_path / "store-replica"
    store = EvidenceStore(f"cas+file://{primary}", f"cas+file://{replica}")
    return primary, replica, store


def _put(store: EvidenceStore, payload: bytes) -> StoredObject:
    return store.put(payload, _sha256(payload))


def _write_manifest(root: Path, case_id: str, run_id: str, obj: StoredObject,
                    artifacts: list[dict] | None = None) -> Path:
    run = root / case_id / run_id
    run.mkdir(parents=True)
    manifest = {
        "schema_version": "2.0",
        "evidence_class": "formal",
        "case": case_id,
        "case_version": "1",
        "profile": "paper",
        "seed": 1,
        "run_id": run_id,
        "started_at": "2026-08-18T00:00:00Z",
        "finished_at": "2026-08-18T01:00:00Z",
        "exit_status": 0,
        "git_commit": "a" * 40,
        "git_clean": True,
        "gpu_image": "img:gpu",
        "gpu_image_digest": "sha256:" + "b" * 64,
        "cpu_verifier_image": "img:cpu",
        "cpu_verifier_image_digest": "sha256:" + "c" * 64,
        "hardware": {},
        "software": {},
        "command": "formal",
        "workspace_identity": "ws",
        "evaluator_bundle_sha256": "ab" * 32,
        "artifact_policy_sha256": "cd" * 32,
        "artifacts": artifacts or [],
        "bundle": {
            "format": "tar.zst",
            "sha256": obj.sha256,
            "size_bytes": obj.size_bytes,
            "primary_uri": obj.primary_uri,
            "primary_version": str(obj.primary_version),
            "replica_uri": obj.replica_uri,
            "replica_version": str(obj.replica_version),
            "verified_at": "2026-08-18T00:00:00Z",
        },
        "verifier_report": {"valid": True, "metrics": {"final_force_mae_eV_A": 0.09}},
    }
    path = run / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


@pytest.fixture
def complete_evidence(tmp_path: Path) -> tuple[Path, Path, EvidenceStore, str]:
    """evidence_root with one formal run whose bundle is in both stores."""
    primary, replica, store = _store(tmp_path)
    obj = _put(store, b"bundle-bytes-for-audit" * 10)
    evidence_root = tmp_path / "evidence"
    _write_manifest(evidence_root, "031", "run-1", obj)
    return evidence_root, primary, replica, obj.sha256


def test_operational_tools_exist() -> None:
    """Step 2 RED marker: the three operational tools must exist."""
    audit_store = _import("audit_store")
    recovery_drill = _import("recovery_drill")
    gc_plan = _import("gc_plan")
    assert hasattr(audit_store, "audit_store")
    assert hasattr(recovery_drill, "recover_and_verify")
    assert hasattr(gc_plan, "plan")


def test_audit_complete_when_both_stores_ok(complete_evidence: tuple) -> None:
    evidence_root, primary, replica, digest = complete_evidence
    audit_store = _import("audit_store")
    report = audit_store.audit_store(evidence_root)
    run = report["cases"]["031"]["runs"]["run-1"]
    assert run["status"] == "complete"
    assert run["primary_ok"] is True
    assert run["replica_ok"] is True
    assert run["sha256_ok"] is True
    assert report["any_degraded"] is False
    assert report["any_unavailable"] is False


def test_audit_reports_degraded_when_replica_missing(complete_evidence: tuple) -> None:
    evidence_root, primary, replica, digest = complete_evidence
    (replica / "sha256" / digest[:2] / f"{digest}.tar.zst").unlink()
    audit_store = _import("audit_store")
    report = audit_store.audit_store(evidence_root)
    run = report["cases"]["031"]["runs"]["run-1"]
    assert run["status"] == "degraded"
    assert run["primary_ok"] is True
    assert run["replica_ok"] is False
    assert report["any_degraded"] is True


def test_audit_unavailable_blocks_benchmark_validity(complete_evidence: tuple) -> None:
    """Missing in both stores must block benchmark validity — never promote a
    manifest whose bundle bytes no longer exist anywhere."""
    evidence_root, primary, replica, digest = complete_evidence
    for store in (primary, replica):
        (store / "sha256" / digest[:2] / f"{digest}.tar.zst").unlink()
    audit_store = _import("audit_store")
    report = audit_store.audit_store(evidence_root)
    run = report["cases"]["031"]["runs"]["run-1"]
    assert run["status"] == "unavailable"
    assert report["any_unavailable"] is True
    assert report["blocks_benchmark_valid"] is True


def test_audit_detects_corrupt_object(complete_evidence: tuple) -> None:
    evidence_root, primary, replica, digest = complete_evidence
    target = primary / "sha256" / digest[:2] / f"{digest}.tar.zst"
    target.write_bytes(b"tampered-bytes")
    audit_store = _import("audit_store")
    report = audit_store.audit_store(evidence_root)
    run = report["cases"]["031"]["runs"]["run-1"]
    assert run["sha256_ok"] is False
    assert run["status"] == "corrupt"
    assert run["errors"]


def _unreferenced_object(replica: Path, payload: bytes) -> str:
    """Write an object straight into the store, referenced by no manifest."""
    digest = _sha256(payload)
    target = replica / "sha256" / digest[:2] / f"{digest}.tar.zst"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return digest


def test_gc_plan_never_lists_referenced_objects(
    complete_evidence: tuple, tmp_path: Path
) -> None:
    evidence_root, primary, replica, digest = complete_evidence
    # Make the referenced object OLD so only the reference protects it.
    target = primary / "sha256" / digest[:2] / f"{digest}.tar.zst"
    old = time.time() - 400 * 24 * 3600
    os.utime(target, (old, old))
    gc_plan = _import("gc_plan")
    plan = gc_plan.plan(f"cas+file://{primary}", f"cas+file://{replica}", evidence_root)
    candidates = {c["digest"] for c in plan["candidates"]}
    assert digest not in candidates
    assert digest in plan["referenced"]


def test_gc_plan_grace_protects_recent_unreferenced(
    complete_evidence: tuple, tmp_path: Path
) -> None:
    evidence_root, primary, replica, digest = complete_evidence
    young = _unreferenced_object(replica, b"brand-new-upload-no-manifest")
    gc_plan = _import("gc_plan")
    plan = gc_plan.plan(f"cas+file://{primary}", f"cas+file://{replica}", evidence_root,
                       grace_days=30)
    assert young not in {c["digest"] for c in plan["candidates"]}
    assert young in plan["protected_unreferenced"]


def test_gc_plan_lists_old_unreferenced_with_reason(
    complete_evidence: tuple, tmp_path: Path
) -> None:
    evidence_root, primary, replica, digest = complete_evidence
    old = _unreferenced_object(replica, b"orphan-from-abandoned-run")
    target = replica / "sha256" / old[:2] / f"{old}.tar.zst"
    stamp = time.time() - 40 * 24 * 3600
    os.utime(target, (stamp, stamp))
    gc_plan = _import("gc_plan")
    plan = gc_plan.plan(f"cas+file://{primary}", f"cas+file://{replica}", evidence_root,
                       grace_days=30)
    matches = [c for c in plan["candidates"] if c["digest"] == old]
    assert len(matches) == 1
    assert matches[0]["age_days"] >= 39
    assert matches[0]["size_bytes"] == len(b"orphan-from-abandoned-run")
    assert matches[0]["reason"]


def test_gc_plan_is_dry_run_by_default(
    complete_evidence: tuple, tmp_path: Path, monkeypatch
) -> None:
    """plan() must never delete; only main() with --apply may, and that path
    stays non-interactive-explicit."""
    evidence_root, primary, replica, digest = complete_evidence
    obj_path = primary / "sha256" / digest[:2] / f"{digest}.tar.zst"
    gc_plan = _import("gc_plan")
    gc_plan.plan(f"cas+file://{primary}", f"cas+file://{replica}", evidence_root)
    assert obj_path.is_file(), "plan() deleted a referenced object — dry-run violated"

    # Apply path: with only a referenced object there is nothing to delete, and
    # main() refuses --apply with a nonzero rc rather than deleting anything.
    argv = ["--primary", f"cas+file://{primary}", "--replica", f"cas+file://{replica}",
            "--evidence-root", str(evidence_root), "--apply"]
    monkeypatch.setattr("sys.argv", ["gc_plan.py", *argv])
    assert gc_plan.main() == 1  # referenced object is never deletable


def _real_bundle(tmp_path: Path) -> tuple[StoredObject, dict]:
    """A real deterministic bundle with one file, stored in both stores."""
    from scripts.evidence.bundle import build_bundle

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result_bytes = json.dumps({"value": 42}).encode()
    (workspace / "result.json").write_bytes(result_bytes)
    bundle_path = tmp_path / "run.tar.zst"
    desc = build_bundle(
        [{"path": "result.json", "role": "scoring_required",
          "size_bytes": len(result_bytes), "sha256": _sha256(result_bytes)}],
        destination=bundle_path, base_dir=workspace)
    _, _, store = _store(tmp_path / "drill-store")
    obj = _put(store, bundle_path.read_bytes())
    assert obj.sha256 == desc.sha256, "deterministic bundle digest must match store digest"
    artifact = {"path": "restored/result.json", "role": "scoring_required",
                "size_bytes": len(result_bytes), "sha256": _sha256(result_bytes)}
    return obj, artifact


def test_recovery_drill_restores_and_verifies(tmp_path: Path) -> None:
    """A drill restores the sealed bundle and re-runs the Verifier over the
    restored tree, reporting a JSON result with the restored digest check."""
    evidence_root = tmp_path / "evidence"
    obj, artifact = _real_bundle(tmp_path)
    _write_manifest(evidence_root, "031", "run-1", obj, artifacts=[artifact])
    recovery_drill = _import("recovery_drill")

    class FakeRuntime:
        calls: list[Path] = []

        def run(self, submission: Path, profile: str) -> dict:
            self.calls.append(submission)
            assert (submission / "result.json").read_bytes() == json.dumps(
                {"value": 42}).encode()
            return {"valid": True, "metrics": {"final_force_mae_eV_A": 0.09},
                    "profile": profile}

    runtime = FakeRuntime()
    report = recovery_drill.recover_and_verify(
        case_id="031", run_id="run-1", evidence_root=evidence_root,
        case_dir=tmp_path / "case", policy={"case_id": "031"},
        verifier_runtime=runtime,  # type: ignore[arg-type]
    )
    assert report["restored"]["sha256_ok"] is True
    assert report["restored"]["artifacts_ok"] is True
    assert report["verifier"]["valid"] is True
    assert report["valid"] is True
    assert runtime.calls, "drill never invoked the verifier"
