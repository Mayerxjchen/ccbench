from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.matclaw_validation import (
    compare_formal_runs,
    derive_case,
    load_policy,
    main,
    validate_run_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "benchmark/sources/matclaw/acceptance.json"
CASE_NAMES = {
    "031": "031-matclaw-cips-active-distillation",
    "032": "032-matclaw-cips-curie-temperature",
    "033": "033-matclaw-cips-domain-wall-search",
}
TEMPERATURES = [100, 150, 200, 250, 275, 300, 325, 350, 375, 400, 450, 500, 600]


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def metrics_for(case_id: str) -> dict:
    if case_id == "031":
        return {"final_force_mae_eV_A": 0.099, "active_iterations": 1}
    if case_id == "032":
        return {"Tc_K": 261.3, "temperatures_K": TEMPERATURES, "atom_count": 360}
    return {
        "best_Ez_V_A": -0.16,
        "best_temperature_K": 50,
        "slope_ps_per_site": 0.35,
        "rounds": 7,
        "jobs": 14,
        "max_jobs_per_round": 2,
    }


def write_manifest(
    root: Path,
    case_id: str = "031",
    run_id: str = "run-1",
    seed: int = 101,
    evidence_class: str = "formal",
    verifier_valid: bool = True,
    metrics: dict | None = None,
    git_commit: str = "a" * 40,
    gpu_image_digest: str = "sha256:" + "b" * 64,
    workspace_identity: str | None = None,
) -> Path:
    run = root / case_id / run_id
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True)
    payload = json.dumps({"case": case_id, "run_id": run_id}, sort_keys=True).encode()
    result_path = artifacts / "result.json"
    result_path.write_bytes(payload)
    manifest = {
        "schema_version": "1.0",
        "evidence_class": evidence_class,
        "case": case_id,
        "profile": "paper",
        "seed": seed,
        "run_id": run_id,
        "started_at": "2026-08-11T00:00:00Z",
        "finished_at": "2026-08-11T01:00:00Z",
        "exit_status": 0,
        "git_commit": git_commit,
        "git_clean": True,
        "gpu_image": "registry.example/matclaw:2.2.11-gpu",
        "gpu_image_digest": gpu_image_digest,
        "cpu_verifier_image": "registry.example/matclaw:2.2.11-cpu",
        "cpu_verifier_image_digest": "sha256:" + "c" * 64,
        "hardware": {"gpu": "fixture"},
        "software": {"deepmd": "2.2.11"},
        "command": ["run-reference", "--case", case_id],
        "workspace_identity": workspace_identity or f"workspace-{case_id}-{run_id}",
        "artifacts": [{"path": "artifacts/result.json", "sha256": sha256_bytes(payload)}],
        "verifier_report": {"valid": verifier_valid, "metrics": metrics or metrics_for(case_id)},
    }
    path = run / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def case_fixture(tmp_path: Path, case_id: str) -> Path:
    case = tmp_path / CASE_NAMES[case_id]
    case.mkdir(parents=True)
    return case


def test_missing_formal_bundle_forces_false(tmp_path: Path) -> None:
    report = derive_case(
        case_dir=case_fixture(tmp_path, "031"),
        evidence_root=tmp_path / "formal",
        policy_path=POLICY,
    )
    assert report["benchmark_valid"] is False
    assert report["state"] == "constructed"
    assert "two formal runs required" in report["reasons"]
    assert set(report["gates"]) == {f"G{i}" for i in range(13)}


def test_diagnostic_manifest_is_never_formal(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path, evidence_class="diagnostic")
    result = validate_run_manifest(manifest, "031", load_policy(POLICY))
    assert result["eligible"] is False
    assert "evidence_class" in result["errors"]


def test_digest_tampering_forces_false(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path)
    (manifest.parent / "artifacts/result.json").write_text("{}", encoding="utf-8")
    result = validate_run_manifest(manifest, "031", load_policy(POLICY))
    assert result["eligible"] is False
    assert any("sha256" in error for error in result["errors"])


def test_manifest_rejects_unsafe_artifact_path(tmp_path: Path) -> None:
    manifest = write_manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data["artifacts"][0]["path"] = "../outside.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    result = validate_run_manifest(manifest, "031", load_policy(POLICY))
    assert result["eligible"] is False
    assert "unsafe artifact path" in result["errors"]


def write_v2_manifest(
    root: Path,
    case_id: str = "031",
    run_id: str = "run-1",
    seed: int = 101,
    metrics: dict | None = None,
    bundle: bool = True,
) -> Path:
    """A schema-2.0 manifest whose artifacts reference restored/ paths (which
    live in the content-addressed store, not adjacent to the manifest)."""
    run = root / case_id / run_id
    run.mkdir(parents=True)
    manifest = {
        "schema_version": "2.0",
        "evidence_class": "formal",
        "case": case_id,
        "case_version": "2.2.11",
        "profile": "paper",
        "seed": seed,
        "run_id": run_id,
        "started_at": "2026-08-18T00:00:00Z",
        "finished_at": "2026-08-18T01:00:00Z",
        "exit_status": 0,
        "git_commit": "a" * 40,
        "git_clean": True,
        "gpu_image": "dftworld-base-matclaw-cips:2.2.11-gpu-amd64",
        "gpu_image_digest": "sha256:" + "b" * 64,
        "cpu_verifier_image": "dftworld-base-matclaw-cips:2.2.11-cpu-amd64",
        "cpu_verifier_image_digest": "sha256:" + "c" * 64,
        "hardware": {"gpu": "fixture"},
        "software": {"deepmd": "2.2.11"},
        "command": "formal round 1",
        "workspace_identity": f"workspace-{case_id}-{run_id}",
        "evaluator_bundle_sha256": "ab" * 32,
        "artifact_policy_sha256": "cd" * 32,
        "artifacts": [
            {"path": "restored/workspace/result.json", "role": "scoring_required",
             "size_bytes": 10, "sha256": "ef" * 32}
        ],
        "bundle": {
            "format": "tar.zst", "sha256": "11" * 32, "size_bytes": 100,
            "primary_uri": "cas+file:///s/p1.tar.zst", "primary_version": "immutable",
            "replica_uri": "cas+file:///s/p2.tar.zst", "replica_version": "immutable",
            "verified_at": "2026-08-18T00:00:00Z",
        },
        "verifier_report": {"valid": True, "metrics": metrics or metrics_for(case_id)},
    }
    if not bundle:
        del manifest["bundle"]
    path = run / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def test_v2_manifest_validates_on_record_path_without_adjacent_bytes(tmp_path: Path) -> None:
    """v2 restored/ bytes live in the store; the record path must not demand them."""
    manifest = write_v2_manifest(tmp_path)
    result = validate_run_manifest(manifest, "031", load_policy(POLICY))
    assert result["eligible"] is True, result["errors"]


def test_v2_manifest_requires_bundle_descriptor_on_record_path(tmp_path: Path) -> None:
    manifest = write_v2_manifest(tmp_path, bundle=False)
    result = validate_run_manifest(manifest, "031", load_policy(POLICY))
    assert result["eligible"] is False
    assert "bundle descriptor" in result["errors"]


def test_v2_manifest_byte_checks_on_restore_path(tmp_path: Path) -> None:
    """With artifact_base set (a restored tree), v2 artifacts are still byte-checked."""
    manifest = write_v2_manifest(tmp_path)
    restored = tmp_path / "restored"
    (restored / "workspace").mkdir(parents=True)
    (restored / "workspace" / "result.json").write_bytes(b"wrong-bytes")
    result = validate_run_manifest(manifest, "031", load_policy(POLICY),
                                   artifact_base=tmp_path)
    assert result["eligible"] is False
    assert any("sha256" in error for error in result["errors"])


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("git_clean", False, "git_clean"),
        ("profile", "smoke", "profile"),
        ("exit_status", 1, "exit_status"),
        ("gpu_image_digest", "tag-only", "gpu_image_digest"),
        ("cpu_verifier_image_digest", "tag-only", "cpu_verifier_image_digest"),
    ],
)
def test_manifest_rejects_nonformal_identity(
    tmp_path: Path, field: str, value: object, error: str
) -> None:
    manifest = write_manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data[field] = value
    manifest.write_text(json.dumps(data), encoding="utf-8")
    result = validate_run_manifest(manifest, "031", load_policy(POLICY))
    assert result["eligible"] is False
    assert error in result["errors"]


@pytest.mark.parametrize(
    ("case_id", "bad_metrics", "error_fragment"),
    [
        ("031", {"final_force_mae_eV_A": 0.101, "active_iterations": 1}, "MAE"),
        ("031", {"final_force_mae_eV_A": 0.099, "active_iterations": 0}, "active"),
        ("032", {"Tc_K": 272.0, "temperatures_K": TEMPERATURES, "atom_count": 360}, "Tc"),
        ("032", {"Tc_K": 261.3, "temperatures_K": TEMPERATURES[:-1], "atom_count": 360}, "temperature grid"),
        ("033", {**metrics_for("033"), "best_Ez_V_A": -0.10}, "field"),
        ("033", {**metrics_for("033"), "slope_ps_per_site": 0.3}, "slope"),
    ],
)
def test_case_science_thresholds_fail_closed(
    tmp_path: Path, case_id: str, bad_metrics: dict, error_fragment: str
) -> None:
    manifest = write_manifest(tmp_path, case_id=case_id, metrics=bad_metrics)
    result = validate_run_manifest(manifest, case_id, load_policy(POLICY))
    assert result["eligible"] is False
    assert any(error_fragment in error for error in result["errors"])


@pytest.mark.parametrize(
    ("rounds", "jobs"),
    [
        (7, 14),  # full paper trajectory (paper_rounds/paper_jobs in acceptance.json)
        (5, 10),  # early-stop after the band is reached (as the 033 GPU diagnostic did)
        (3, 6),   # earliest plausible stop
    ],
)
def test_033_early_stop_paths_are_valid(tmp_path: Path, rounds: int, jobs: int) -> None:
    """The paper profile's early-stop rule makes short adaptive paths valid."""
    manifest = write_manifest(
        tmp_path, case_id="033",
        metrics={**metrics_for("033"), "rounds": rounds, "jobs": jobs},
    )
    result = validate_run_manifest(manifest, "033", load_policy(POLICY))
    assert result["eligible"] is True


def valid_pair(tmp_path: Path, case_id: str) -> tuple[dict, dict]:
    policy = load_policy(POLICY)
    left = validate_run_manifest(
        write_manifest(tmp_path, case_id, "run-1", 101), case_id, policy
    )
    right = validate_run_manifest(
        write_manifest(tmp_path, case_id, "run-2", 102), case_id, policy
    )
    assert left["eligible"] and right["eligible"]
    return left, right


@pytest.mark.parametrize("case_id", ["031", "032", "033"])
def test_valid_independent_pairs_pass(case_id: str, tmp_path: Path) -> None:
    left, right = valid_pair(tmp_path, case_id)
    comparison = compare_formal_runs(case_id, left, right, load_policy(POLICY))
    assert comparison["valid"] is True
    assert comparison["errors"] == []


@pytest.mark.parametrize(
    ("change", "error_fragment"),
    [
        ({"seed": 101}, "distinct seeds"),
        ({"git_commit": "d" * 40}, "git commit"),
        ({"gpu_image_digest": "sha256:" + "e" * 64}, "GPU image"),
        ({"workspace_identity": "workspace-031-run-1"}, "workspace"),
    ],
)
def test_pair_identity_must_be_independent_and_frozen(
    tmp_path: Path, change: dict, error_fragment: str
) -> None:
    policy = load_policy(POLICY)
    left = validate_run_manifest(write_manifest(tmp_path, "031", "run-1", 101), "031", policy)
    kwargs = {"seed": 102, **change}
    right_path = write_manifest(tmp_path, "031", "run-2", **kwargs)
    right = validate_run_manifest(right_path, "031", policy)
    comparison = compare_formal_runs("031", left, right, policy)
    assert comparison["valid"] is False
    assert any(error_fragment in error for error in comparison["errors"])


@pytest.mark.parametrize(
    ("case_id", "right_metrics", "error_fragment"),
    [
        ("031", {"final_force_mae_eV_A": 0.088, "active_iterations": 1}, "MAE difference"),
        ("032", {"Tc_K": 250.0, "temperatures_K": TEMPERATURES, "atom_count": 360}, "Tc difference"),
        ("033", {**metrics_for("033"), "best_Ez_V_A": -0.12}, "Ez difference"),
    ],
)
def test_cross_run_science_disagreement_fails(
    tmp_path: Path, case_id: str, right_metrics: dict, error_fragment: str
) -> None:
    policy = load_policy(POLICY)
    left = validate_run_manifest(write_manifest(tmp_path, case_id, "run-1", 101), case_id, policy)
    right = validate_run_manifest(
        write_manifest(tmp_path, case_id, "run-2", 102, metrics=right_metrics),
        case_id,
        policy,
    )
    comparison = compare_formal_runs(case_id, left, right, policy)
    assert comparison["valid"] is False
    assert any(error_fragment in error for error in comparison["errors"])


def test_derive_writes_matching_fail_closed_outputs(tmp_path: Path, monkeypatch) -> None:
    case = case_fixture(tmp_path, "031")
    monkeypatch.setattr(
        "sys.argv",
        [
            "matclaw_validation.py",
            "derive",
            "--case",
            "031",
            "--repo-root",
            str(tmp_path),
            "--policy",
            str(POLICY),
            "--evidence-root",
            str(tmp_path / "formal"),
            "--write",
        ],
    )
    assert main() == 0
    validation = json.loads((case / "VALIDATION.json").read_text())
    summary = json.loads((case / "benchmark_valid.json").read_text())
    assert validation["benchmark_valid"] is False
    assert summary == {
        "benchmark_id": CASE_NAMES["031"],
        "benchmark_valid": False,
        "state": "constructed",
        "reasons": ["two formal runs required"],
    }
