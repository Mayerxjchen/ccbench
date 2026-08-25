"""ER8: manifest writer omits absent ``recomputed_estimate`` (case-specific).

Case 031 (active distillation) has no Tc; its verifier report carries only
``metrics``. The writer must not emit a ``null`` ``recomputed_estimate`` —
the v2 schema types it ``object``, so null fails validation. The key is
optional; omit it. Cases that do have an estimate (032) keep it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas" / "evidence-manifest-v2.schema.json"

from scripts.reference.write_evidence_manifest import main  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity() -> dict[str, str]:
    return {
        "--seed": "2026081201", "--run-id": "run-1",
        "--git-commit": "4da3d31", "--git-clean": "true",
        "--gpu-image": "/runtime/matclaw-cips-2.2.11-gpu-amd64.sif",
        "--gpu-image-digest": "sha256:" + "99" * 32,
        "--cpu-verifier-image": "/runtime/matclaw-cips-2.2.11-cpu-amd64.sif",
        "--cpu-verifier-image-digest": "sha256:" + "f1" * 32,
        "--workspace-identity": "031-2026081201",
        "--hardware-json": '{"node": "<site-node-gpu3>"}',
        "--software-json": '{"deepmd": "2.2.11"}',
    }


def _fixtures(tmp_path: Path, report: dict) -> tuple[list[str], Path]:
    """Minimal case dir, restored tree, files list, bundle descriptor, report.
    Returns argv + the dir that will receive the written manifest
    (the writer emits it at ``restored.parent / manifest.json``)."""
    # case_id is derived from the case-dir basename ("031-matclaw-…" -> "031")
    case_dir = tmp_path / "031-matclaw-cips-active-distillation"
    (case_dir / "reference").mkdir(parents=True)
    (case_dir / "reference" / "evidence-policy.json").write_text(
        json.dumps({"schema_version": "1", "case_id": "031",
                    "state": "benchmark_valid", "finalization_allowed": True}))
    (case_dir / "evaluator-manifest.json").write_text(
        json.dumps({"schema_version": "1", "case_id": "031",
                    "bundle_sha256": "ab" * 32, "files": []}))

    restored = tmp_path / "restored"
    restored.mkdir()
    (restored / "result.json").write_text(json.dumps({"final_force_mae_eV_A": 0.0968}))

    files_json = tmp_path / "files.json"
    files_json.write_text(json.dumps([{
        "path": "result.json", "role": "scoring_required",
        "size_bytes": (restored / "result.json").stat().st_size,
        "sha256": _sha256(restored / "result.json"),
    }]))

    bundle_json = tmp_path / "bundle.json"
    bundle_json.write_text(json.dumps({
        "format": "tar.zst", "sha256": "c4" * 32, "size_bytes": 123,
        "primary_uri": "cas+file:///store/primary/sha256/c4/cccc.tar.zst",
        "primary_version": "immutable",
        "replica_uri": "cas+file:///store/replica/sha256/c4/cccc.tar.zst",
        "replica_version": "immutable",
        "verified_at": "2026-08-18T00:00:00Z",
    }))

    report_json = tmp_path / "report.json"
    report_json.write_text(json.dumps(report))

    argv = [
        "--case-dir", str(case_dir), "--restored", str(restored),
        "--files-json", str(files_json), "--verifier-report", str(report_json),
        "--bundle-json", str(bundle_json),
    ]
    for key, val in _identity().items():
        argv += [key, val]
    return argv, tmp_path


def test_writer_omits_absent_recomputed_estimate(tmp_path: Path) -> None:
    """031-style report (metrics only) must produce a schema-valid manifest
    without a null recomputed_estimate key."""
    report = {"valid": True, "errors": [],
              "recomputed_final_mae_eV_A": 0.0968, "active_iterations": 2}
    argv, out_dir = _fixtures(tmp_path, report)

    assert main(argv) == 0
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert "recomputed_estimate" not in manifest["verifier_report"]
    assert manifest["verifier_report"]["metrics"]["final_force_mae_eV_A"] == 0.0968
    jsonschema.validate(manifest, json.loads(SCHEMA.read_text()))


def test_writer_keeps_recomputed_estimate_when_present(tmp_path: Path) -> None:
    """032-style report keeps its estimate."""
    report = {"valid": True, "errors": [], "recomputed_estimate": {"Tc_K": 259.44},
              "recomputed_curve": [{"temperature_K": 100}]}
    argv, out_dir = _fixtures(tmp_path, report)

    assert main(argv) == 0
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["verifier_report"]["recomputed_estimate"] == {"Tc_K": 259.44}
    jsonschema.validate(manifest, json.loads(SCHEMA.read_text()))


def test_writer_rejects_invalid_report(tmp_path: Path) -> None:
    argv, _ = _fixtures(tmp_path, {"valid": False, "errors": ["bad"]})
    assert main(argv) == 1
