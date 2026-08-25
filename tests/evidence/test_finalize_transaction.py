"""ER6: two-phase finalization recovers from a crash at every state.

A failure after any state must leave no Git-facing manifest that claims durable
evidence, and resuming the transaction must finish with exactly one primary
object, one replica object, and one v2 manifest. A sealed object is immutable:
re-running a committed transaction is refused.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

import support
from support import CASES, POLICY, ScriptableVerifierRuntime, build_workspace

from scripts.evidence.finalize_run import (
    FinalizeError,
    FinalizeTransaction,
    SealedObjectError,
    STATES,
)
from scripts.evidence.store import EvidenceStore

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_ARGS = {
    "seed": "2026081206", "run_id": "run-1",
    "git_commit": "71078a4", "git_clean": "true",
    "gpu_image": "dftworld-base-matclaw-cips:2.2.11-gpu-amd64",
    "gpu_image_digest": "sha256:" + "34" * 32,
    "cpu_verifier_image": "dftworld-base-matclaw-cips:2.2.11-cpu-amd64",
    "cpu_verifier_image_digest": "sha256:" + "f3" * 32,
    "workspace_identity": "032-2026081206",
    "started_at": "2026-08-18T11:36:27Z",
    "finished_at": "2026-08-18T12:12:34Z",
    "hardware_json": json.dumps({"job": "3567687", "node": "<site-node-gpu3>"}),
    "software_json": json.dumps({"apptainer_sif_sha256": "99" * 32}),
}


def _make_transaction(ws: Path, tmp_path: Path, run_dir: Path, *, fail_after: str | None = None,
                      ) -> FinalizeTransaction:
    primary = tmp_path / "store" / "primary"
    replica = tmp_path / "store" / "replica"
    return FinalizeTransaction(
        run_dir=run_dir, workspace=ws, case_dir=CASES["032"],
        policy=POLICY["032"], verifier_runtime=ScriptableVerifierRuntime(POLICY["032"]),
        primary_uri=f"cas+file://{primary}", replica_uri=f"cas+file://{replica}",
        manifest_args=dict(MANIFEST_ARGS), fail_after=fail_after)


def _count_objects(store_root: Path) -> int:
    return sum(1 for p in store_root.rglob("*.tar.zst") if p.is_file())


@pytest.mark.parametrize("failed_state", list(STATES[:-1]))
def test_resume_after_every_state_produces_one_of_everything(failed_state: str, tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / failed_state
    ws = build_workspace(tmp_path, "032"); tx = _make_transaction(ws, tmp_path, run_dir, fail_after=failed_state)
    with pytest.raises(FinalizeError, match="injected failure"):
        tx.run()
    assert tx.current() == failed_state
    # before MANIFEST_COMMITTED no Git-facing manifest may exist
    if failed_state != "MANIFEST_COMMITTED":
        assert not (run_dir / "manifest.json").exists()

    resumed = _make_transaction(ws, tmp_path, run_dir)
    resumed.run()
    assert resumed.current() == "MANIFEST_COMMITTED"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    schema = json.loads((ROOT / "schemas" / "evidence-manifest-v2.schema.json").read_text())
    jsonschema.validate(manifest, schema)
    store = EvidenceStore(f"cas+file://{tmp_path / 'store' / 'primary'}",
                          f"cas+file://{tmp_path / 'store' / 'replica'}")
    store.verify(manifest["bundle"]["sha256"])
    assert _count_objects(tmp_path / "store" / "primary") == 1
    assert _count_objects(tmp_path / "store" / "replica") == 1
    assert manifest["bundle"]["sha256"] == resumed.state["bundle"]["sha256"]


def test_sealed_object_is_not_overwritten(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "sealed"
    ws = build_workspace(tmp_path, "032")
    _make_transaction(ws, tmp_path, run_dir).run()
    before = (run_dir / "manifest.json").read_bytes()
    with pytest.raises(SealedObjectError):
        _make_transaction(ws, tmp_path, run_dir).run()
    assert (run_dir / "manifest.json").read_bytes() == before
    assert _count_objects(tmp_path / "store" / "primary") == 1


def test_crash_mid_primary_leaves_replica_empty(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "mid-primary"
    ws = build_workspace(tmp_path, "032"); tx = _make_transaction(ws, tmp_path, run_dir, fail_after="PRIMARY_VERIFIED")
    with pytest.raises(FinalizeError):
        tx.run()
    # primary written + verified, replica untouched until its own step
    assert _count_objects(tmp_path / "store" / "primary") == 1
    assert _count_objects(tmp_path / "store" / "replica") == 0
    _make_transaction(ws, tmp_path, run_dir).run()
    assert _count_objects(tmp_path / "store" / "replica") == 1


def test_resume_reuses_staged_curation(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "staging"
    ws = build_workspace(tmp_path, "032"); tx = _make_transaction(ws, tmp_path, run_dir, fail_after="CURATED")
    with pytest.raises(FinalizeError):
        tx.run()
    staging = Path(tx.state["staging"])
    assert staging.is_dir()
    files_before = sorted(p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file())
    _make_transaction(ws, tmp_path, run_dir).run()
    files_after = sorted(p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file())
    assert files_before == files_after
