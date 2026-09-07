"""ER5: the curated (policy-resolved) file set reproduces verifier outcomes.

The host cannot run the frozen verifier (numpy/ase live in the container), so
tests use ``ScriptableVerifierRuntime`` (see support.py): it re-resolves the
real case policy against the submission (failing closed on a missing file) and
recomputes the science metrics from the file bytes. This is the same dependency
the frozen verifier has — the exact file set — so equal metrics on the staged
tree prove sufficiency. The genuine independent-verifier equality proof runs on
the cluster during finalization (ER6/ER8).
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from scripts.evidence.curate_and_verify import (
    CurateError,
    LocalVerifierRuntime,
    curate_and_verify,
)
from scripts.evidence.resolve_required_artifacts import EvidencePolicyError

import support
from support import CASES, POLICY, ScriptableVerifierRuntime, build_workspace, sha

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("case_id", ["001", "002", "003"])
def test_curated_tree_reproduces_verifier_metrics(case_id: str, tmp_path: Path) -> None:
    ws = build_workspace(tmp_path, case_id)
    evidence = curate_and_verify(CASES[case_id], ws, POLICY[case_id],
                                 ScriptableVerifierRuntime(POLICY[case_id]))
    assert evidence.valid
    for key, value in evidence.metrics.items():
        assert value is not None, f"metric {key} missing for {case_id}"
    # staging holds exactly the resolved files — no full-workspace copy
    staged = {p.relative_to(evidence.staging_dir).as_posix() for p in evidence.staging_dir.rglob("*") if p.is_file()}
    resolved = {f.path for f in evidence.files}
    assert staged == resolved
    assert len(resolved) >= 6  # non-trivial minimal set
    assert evidence.original_report == evidence.curated_report


@pytest.mark.parametrize("case_id", ["001", "002", "003"])
def test_missing_required_file_fails_closed(case_id: str, tmp_path: Path) -> None:
    ws = build_workspace(tmp_path, case_id)
    victim = {
        "001": "models/model_1.pb",
        "002": "md/pilot_350K.traj",
        "003": "traj_1.traj",
    }[case_id]
    (ws / victim).unlink()
    with pytest.raises(EvidencePolicyError):
        curate_and_verify(CASES[case_id], ws, POLICY[case_id],
                          ScriptableVerifierRuntime(POLICY[case_id]))


@pytest.mark.parametrize("case_id", ["001", "002", "003"])
def test_tampered_required_file_refuses_bundle(case_id: str, tmp_path: Path) -> None:
    ws = build_workspace(tmp_path, case_id)
    victim = {
        "001": "exploration/e_1.traj",
        "002": "md/0K.traj",
        "003": "best_trajectory.traj",
    }[case_id]
    (ws / victim).write_text("tampered-bytes")
    with pytest.raises(CurateError, match="rejects|diverges"):
        curate_and_verify(CASES[case_id], ws, POLICY[case_id],
                          ScriptableVerifierRuntime(POLICY[case_id]))


def test_local_runtime_requires_case_verifier(tmp_path: Path) -> None:
    with pytest.raises(CurateError):
        LocalVerifierRuntime(tmp_path / "no-verifier-here")


def test_v2_manifest_writer_emits_curated_artifacts_and_bundle(tmp_path: Path) -> None:
    """ER5 Step 4: manifest references the curated list + stored bundle, not the
    full workspace scan; output satisfies the v2 schema."""
    ws = build_workspace(tmp_path, "002")
    evidence = curate_and_verify(CASES["002"], ws, POLICY["002"],
                                 ScriptableVerifierRuntime(POLICY["002"]))
    files_json = [{"path": f.path, "role": f.role,
                   "size_bytes": f.size_bytes, "sha256": f.sha256} for f in evidence.files]
    files_listing = tmp_path / "curated_files.json"
    files_listing.write_text(json.dumps(files_json))

    restored = tmp_path / "002" / "restored"
    restored.mkdir(parents=True)
    for entry in evidence.files:
        target = restored / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ws / entry.path).read_bytes())

    bundle = {"format": "tar.zst", "sha256": "ab" * 32, "size_bytes": 12345,
              "primary_uri": f"cas+file://{tmp_path}/store/p/sha256/ab/{'ab' * 32}.tar.zst",
              "primary_version": "12345",
              "replica_uri": f"cas+file://{tmp_path}/store/r/sha256/ab/{'ab' * 32}.tar.zst",
              "replica_version": "12345",
              "verified_at": "2026-08-18T12:00:00Z"}
    bundle_listing = tmp_path / "bundle.json"
    bundle_listing.write_text(json.dumps(bundle))

    report = evidence.curated_report
    report_path = tmp_path / "verifier_report.json"
    report_path.write_text(json.dumps(report))

    from scripts.evidence.write_evidence_manifest import main as write_manifest
    rc = write_manifest([
        "--case-dir", str(CASES["002"]),
        "--restored", str(restored),
        "--files-json", str(files_listing),
        "--verifier-report", str(report_path),
        "--seed", "2026081206", "--run-id", "run-1",
        "--git-commit", "71078a4", "--git-clean", "true",
        "--gpu-image", "img", "--gpu-image-digest", "sha256:" + "11" * 32,
        "--cpu-verifier-image", "img", "--cpu-verifier-image-digest", "sha256:" + "22" * 32,
        "--workspace-identity", "002-2026081206",
        "--started-at", "2026-08-18T11:36:27Z", "--finished-at", "2026-08-18T12:12:34Z",
        "--bundle-json", str(bundle_listing),
    ])
    assert rc == 0, "v2 manifest writer failed"

    manifest = json.loads((restored.parent / "manifest.json").read_text())
    schema = json.loads((ROOT / "schemas" / "evidence-manifest-v2.schema.json").read_text())
    jsonschema.validate(manifest, schema)
    assert manifest["schema_version"] == "2.0"
    assert manifest["artifacts"]
    assert all(a["path"].startswith("restored/") for a in manifest["artifacts"])
    for artifact in manifest["artifacts"]:
        assert artifact["role"] in ("scoring_required", "reproduction_required")
        target = restored / artifact["path"][len("restored/"):]
        assert target.is_file()
        assert sha(target.read_bytes()) == artifact["sha256"]
    assert manifest["bundle"]["format"] == "tar.zst"
    assert manifest["evaluator_bundle_sha256"] and manifest["artifact_policy_sha256"]
    assert len(manifest["evaluator_bundle_sha256"]) == 64


def test_v2_manifest_writer_rejects_nonvalid_report(tmp_path: Path) -> None:
    ws = build_workspace(tmp_path, "002")
    restored = tmp_path / "002" / "restored"
    restored.mkdir(parents=True)
    files_listing = tmp_path / "curated.json"
    files_listing.write_text("[]")
    bad_report = tmp_path / "bad.json"
    bad_report.write_text(json.dumps({"valid": False, "errors": ["tampered"]}))
    bundle_listing = tmp_path / "bundle.json"
    bundle_listing.write_text(json.dumps({
        "format": "tar.zst", "sha256": "ab" * 32, "size_bytes": 1,
        "primary_uri": "cas+file:///s/p/x.tar.zst", "primary_version": "1",
        "replica_uri": "cas+file:///s/r/x.tar.zst", "replica_version": "1",
        "verified_at": "2026-08-18T12:00:00Z"}))

    from scripts.evidence.write_evidence_manifest import main as write_manifest
    rc = write_manifest([
        "--case-dir", str(CASES["002"]), "--restored", str(restored),
        "--files-json", str(files_listing),
        "--verifier-report", str(bad_report),
        "--seed", "1", "--run-id", "run-1", "--git-commit", "x", "--git-clean", "true",
        "--gpu-image", "i", "--gpu-image-digest", "sha256:" + "11" * 32,
        "--cpu-verifier-image", "i", "--cpu-verifier-image-digest", "sha256:" + "22" * 32,
        "--bundle-json", str(bundle_listing)])
    assert rc != 0
    assert not (restored.parent / "manifest.json").exists()
