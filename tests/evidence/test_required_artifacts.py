"""Verifier-driven artifact resolution (plan Task 3).

Positive fixtures prove every file the hidden verifier reads resolves; a
removed referenced artifact raises EvidencePolicyError; unreferenced scratch is
excluded; 004 refuses finalization.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evidence.resolve_required_artifacts import (
    EvidencePolicyError,
    resolve_required_artifacts,
)

ROOT = Path(__file__).resolve().parents[2]
CASE_POLICIES = {
    "001": ROOT / "001-matclaw-cips-active-distillation" / "reference" / "evidence-policy.json",
    "002": ROOT / "002-matclaw-cips-curie-temperature" / "reference" / "evidence-policy.json",
    "003": ROOT / "003-matclaw-cips-domain-wall-search" / "reference" / "evidence-policy.json",
    "004": ROOT / "004-ai2kit-water64-end-to-end-potential" / "reference" / "evidence-policy.json",
}


def _policy(case: str) -> dict:
    cands = [
        ROOT / "maintainer" / "cases" / case / "reference" / "evidence-policy.json",
        ROOT / "maintainer" / "cases" / case / "baseline" / "evidence-policy.json",
        CASE_POLICIES[case],
    ]
    p = next((c for c in cands if c.is_file()), cands[-1])
    return json.loads(p.read_text(encoding="utf-8"))


def _mk(workspace: Path, rel: str, data: str = "x") -> None:
    path = workspace / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


@pytest.fixture
def ws_002(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    temps = [100, 150, 200, 250, 275, 300, 325, 350, 375, 400, 450, 500, 600]
    records = []
    for t in temps:
        p = f"md/production_{t}K.traj"
        _mk(ws, p)
        records.append({"temperature_K": t, "path": p, "sha256": "a" * 64, "steps": 30000})
    _mk(ws, "md/pilot_350K.traj")
    _mk(ws, "order_parameter.csv")
    _mk(ws, "curie_temperature.png")
    _mk(ws, "report.md")
    _mk(ws, "run_profiles.json")
    _mk(ws, "scratch.bin", "not-scored")
    result = {
        "profile": "paper",
        "formal_result": True,
        "trajectories": records,
        "curve": [{"temperature_K": t, "mean_abs_eta_A": 1.0} for t in temps],
        "pilot": {"converged": True},
    }
    (ws / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return ws


@pytest.fixture
def ws_003(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    history = []
    for i in range(1, 5):
        traj = f"md/round_{i}_job1.traj"
        _mk(ws, traj)
        history.append({"iteration": i, "trajectory": traj, "trajectory_sha256": "a" * 64})
    for name in ("teacher_model.pb", "best_trajectory.traj",
                 "search_history.csv", "domino_analysis.csv", "search_results.png"):
        _mk(ws, name)
    _mk(ws, "run_profiles.json")
    _mk(ws, "scratch.bin", "not-scored")
    (ws / "result.json").write_text(
        json.dumps({"profile": "paper", "history": history,
                    "best_trajectory_sha256": "a" * 64}),
        encoding="utf-8")
    return ws


@pytest.fixture
def ws_001(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    _mk(ws, "result.json")
    _mk(ws, "checkpoint.json")
    _mk(ws, "active_learning/history.json")
    _mk(ws, "teacher_model.pb", "pb")
    _mk(ws, "models/iteration_0/member_0/student.pb", "m0")
    _mk(ws, "models/iteration_0/member_1/student.pb", "m1")
    _mk(ws, "student_md/iteration_1_200K_m0.traj", "traj")
    _mk(ws, "data/heldout/set.000/coord.npy", "coord")
    _mk(ws, "data/heldout/type_map.raw", "type")
    _mk(ws, "data/iteration_0/set.000/coord.npy", "coord0")
    _mk(ws, "data/iteration_0/configuration_hashes.json", "{}")
    _mk(ws, "run_profiles.json")
    (ws / "result.json").write_text(json.dumps({
        "history": [
            {"models": [{"path": "models/iteration_0/member_0/student.pb"},
                        {"path": "models/iteration_0/member_1/student.pb"}],
             "exploration_trajectories": [
                 {"path": "student_md/iteration_1_200K_m0.traj"}]},
        ],
    }), encoding="utf-8")
    (ws / "checkpoint.json").write_text(json.dumps({
        "artifact_hashes": {
            "data/heldout/set.000/coord.npy": "c" * 64,
            "data/heldout/type_map.raw": "d" * 64,
            "data/iteration_0/set.000/coord.npy": "e" * 64,
            "data/iteration_0/configuration_hashes.json": "f" * 64,
        },
    }), encoding="utf-8")
    return ws


def test_002_resolves_verifier_read_set(ws_002: Path) -> None:
    files = resolve_required_artifacts(ws_002, _policy("002"))
    paths = {f.path for f in files}
    assert "result.json" in paths
    assert "md/pilot_350K.traj" in paths
    assert "order_parameter.csv" in paths
    assert "curie_temperature.png" in paths
    assert "report.md" in paths
    assert len(paths & {f"md/production_{t}K.traj" for t in
                        (100, 150, 200, 250, 275, 300, 325, 350, 375, 400, 450, 500, 600)}) == 13
    assert "scratch.bin" not in paths


def test_002_missing_trajectory_fails_closed(ws_002: Path) -> None:
    (ws_002 / "md/production_100K.traj").unlink()
    with pytest.raises(EvidencePolicyError, match="missing referenced artifact"):
        resolve_required_artifacts(ws_002, _policy("002"))


def test_003_resolves_verifier_read_set(ws_003: Path) -> None:
    files = resolve_required_artifacts(ws_003, _policy("003"))
    paths = {f.path for f in files}
    assert "result.json" in paths
    assert "teacher_model.pb" in paths
    assert "best_trajectory.traj" in paths
    assert "search_history.csv" in paths
    assert "domino_analysis.csv" in paths
    assert "search_results.png" in paths
    assert len(paths & {f"md/round_{i}_job1.traj" for i in range(1, 5)}) == 4
    assert "scratch.bin" not in paths


def test_003_missing_best_trajectory_fails_closed(ws_003: Path) -> None:
    (ws_003 / "best_trajectory.traj").unlink()
    with pytest.raises(EvidencePolicyError, match="missing referenced artifact"):
        resolve_required_artifacts(ws_003, _policy("003"))


def test_001_resolves_refs_globs_and_artifact_hashes(ws_001: Path) -> None:
    files = resolve_required_artifacts(ws_001, _policy("001"))
    paths = {f.path for f in files}
    assert "models/iteration_0/member_0/student.pb" in paths
    assert "models/iteration_0/member_1/student.pb" in paths
    assert "student_md/iteration_1_200K_m0.traj" in paths
    assert "data/heldout/set.000/coord.npy" in paths
    assert "data/iteration_0/configuration_hashes.json" in paths
    assert "teacher_model.pb" in paths
    assert "active_learning/history.json" in paths


def test_001_missing_referenced_model_fails_closed(ws_001: Path) -> None:
    (ws_001 / "models/iteration_0/member_0/student.pb").unlink()
    with pytest.raises(EvidencePolicyError, match="missing referenced artifact"):
        resolve_required_artifacts(ws_001, _policy("001"))


def test_004_refuses_finalization(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(EvidencePolicyError, match="may not finalize"):
        resolve_required_artifacts(ws, _policy("004"))


def test_double_role_is_rejected(ws_002: Path) -> None:
    policy = _policy("002")
    policy["reproduction_required"] = [
        {"path": "result.json", "role": "reproduction_required"}]
    with pytest.raises(EvidencePolicyError, match="two roles"):
        resolve_required_artifacts(ws_002, policy)


def test_escaping_ref_is_rejected(ws_002: Path) -> None:
    policy = _policy("002")
    policy["scoring_required"].append(
        {"path": "../outside.bin", "role": "scoring_required"})
    with pytest.raises(EvidencePolicyError, match="unsafe reference path"):
        resolve_required_artifacts(ws_002, policy)


def test_real_001_workspace_resolves(ws_001: Path) -> None:
    """The positive fixture mirrors the real 001 shape; the real workspace must
    also resolve completely (it is the migration source in ER8)."""
    # Historical evidence tree still uses legacy directory name 031.
    real = ROOT / "evidence/matclaw/formal/031/run-1/workspace"
    if not (real / "result.json").is_file():
        pytest.skip("001 evidence not present locally")
    files = resolve_required_artifacts(real, _policy("001"))
    assert files
    assert all(f.role in ("scoring_required", "reproduction_required") for f in files)
