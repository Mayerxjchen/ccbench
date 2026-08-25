"""Shared ER5/ER6 test fixtures: a policy-faithful verifier runtime and small
positive workspaces for 031/032/033. Placed in the same directory as the
tests so ``import support`` works under pytest's prepend import mode.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.evidence.resolve_required_artifacts import resolve_required_artifacts

ROOT = Path(__file__).resolve().parents[2]
CASES = {
    "031": ROOT / "031-matclaw-cips-active-distillation",
    "032": ROOT / "032-matclaw-cips-curie-temperature",
    "033": ROOT / "033-matclaw-cips-domain-wall-search",
}
POLICY = {
    case: json.loads((CASES[case] / "reference" / "evidence-policy.json").read_text())
    for case in CASES
}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ScriptableVerifierRuntime:
    """Re-resolves the real case policy; validates declared hashes; aggregates bytes."""

    def __init__(self, policy: dict) -> None:
        self.policy = policy

    def run(self, submission: Path, profile: str) -> dict:
        files = resolve_required_artifacts(submission, self.policy)  # fail-closed
        result = json.loads((submission / "result.json").read_text(encoding="utf-8"))
        declared = result.get("declared_sha256") or {}
        errors = []
        for f in files:
            if f.path in declared and declared[f.path] != f.sha256:
                errors.append(f"file {f.path} does not match declared sha256")
        aggregate = sha(b"\0".join(b"%s:%s" % (f.path.encode(), f.sha256.encode()) for f in files))
        return {
            "valid": not errors,
            "errors": errors,
            "file_sha256": aggregate,
            "recomputed_estimate": {"Tc_K": result.get("Tc_K")},
            "recomputed_curve": result.get("trajectories", []),
            "recomputed_final_mae_eV_A": result.get("final_force_mae_eV_A"),
            "active_iterations": result.get("active_iterations")
            or len(result.get("history", [])),
        }


def _write_tree(root: Path, tree: dict[str, bytes | str]) -> None:
    for rel, content in tree.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)


def build_workspace(tmp_path: Path, case_id: str) -> Path:
    """Small positive fixture workspace for the case's evidence policy."""
    ws = tmp_path / case_id / "workspace"
    ws.mkdir(parents=True)
    declared: dict[str, str] = {}
    if case_id == "032":
        files: dict[str, bytes | str] = {
            "result.json": json.dumps({
                "profile": "paper", "atom_count": 360, "Tc_K": 259.44,
                "trajectories": [{"path": "md/0K.traj", "temperature_K": 100},
                                 {"path": "md/600K.traj", "temperature_K": 600}],
            }),
            "md/pilot_350K.traj": "pilot-trajectory",
            "md/0K.traj": "traj-100K",
            "md/600K.traj": "traj-600K",
            "order_parameter.csv": "T,K,order\n100,0.9\n600,0.1\n",
            "curie_temperature.png": b"\x89PNG\r\n\x1a\nfixture",
            "report.md": "# 032 fixture report",
            "run_profiles.json": json.dumps({"paper": {"supercell": [6, 6, 1]}}),
        }
        for rel, content in files.items():
            if rel != "result.json":
                declared[rel] = sha(content if isinstance(content, bytes) else content.encode())
        result = json.loads(files["result.json"])
        result["declared_sha256"] = declared
        files["result.json"] = json.dumps(result)
        _write_tree(ws, files)
    elif case_id == "031":
        files = {
            "result.json": json.dumps({
                "profile": "paper", "final_force_mae_eV_A": 0.07,
                "history": [
                    {"iteration": 0, "models": [{"path": "models/model_0.pb"}],
                     "exploration_trajectories": [{"path": "exploration/e_0.traj"}]},
                    {"iteration": 1, "models": [{"path": "models/model_1.pb"}],
                     "exploration_trajectories": [{"path": "exploration/e_1.traj"}]},
                ],
            }),
            "checkpoint.json": json.dumps({"artifact_hashes": {"teacher_model.pb": "x"}}),
            "active_learning/history.json": json.dumps({"history": []}),
            "teacher_model.pb": "teacher-bytes",
            "models/model_0.pb": "model-0",
            "models/model_1.pb": "model-1",
            "exploration/e_0.traj": "explore-0",
            "exploration/e_1.traj": "explore-1",
            "data/heldout/a.xyz": "heldout-a",
            "data/iteration_0/train.xyz": "train-0",
            "data/iteration_1/train.xyz": "train-1",
            "run_profiles.json": json.dumps({"paper": {"max_iterations": 5}}),
        }
        for rel, content in files.items():
            if rel != "result.json":
                declared[rel] = sha(content if isinstance(content, bytes) else content.encode())
        result = json.loads(files["result.json"])
        result["declared_sha256"] = declared
        files["result.json"] = json.dumps(result)
        _write_tree(ws, files)
    else:  # 033
        files = {
            "result.json": json.dumps({
                "profile": "paper",
                "best": {"Ez_V_A": 1.05, "temperature_K": 800,
                         "slope_ps_per_site": 0.6, "sequential_propagation": True},
                "history": [
                    {"iteration": 1, "trajectory": "traj_1.traj", "trajectory_sha256": "x"},
                    {"iteration": 1, "trajectory": "traj_2.traj", "trajectory_sha256": "y"},
                ],
            }),
            "teacher_model.pb": "teacher-bytes",
            "traj_1.traj": "traj-1",
            "traj_2.traj": "traj-2",
            "best_trajectory.traj": "best-trajectory",
            "search_history.csv": "ez,temperature,slope\n1.0,800,0.6\n",
            "domino_analysis.csv": "domino,count\nA,3\n",
            "search_results.png": b"\x89PNG\r\n\x1a\nsearch",
            "run_profiles.json": json.dumps({"paper": {"max_rounds": 7}}),
        }
        for rel, content in files.items():
            if rel != "result.json":
                declared[rel] = sha(content if isinstance(content, bytes) else content.encode())
        result = json.loads(files["result.json"])
        result["declared_sha256"] = declared
        files["result.json"] = json.dumps(result)
        _write_tree(ws, files)
    return ws
