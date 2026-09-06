"""Self-contained hidden evaluator for Case 031 active distillation.

Security model: inspection is limited to the graded submission directory
(default /app). This module embeds the locked profile and identity constants and
recomputes every scientific claim from the delivered raw artifacts (datasets,
student models, trajectories); it never reads public/ or reference/ at runtime,
so the same file grades both in-container (/tests) and via the reference wrapper.

The active step is verified by independent recomputation, not by trusting the
submission's own bookkeeping: for every transition that feeds a later training
round the raw exploration trajectories are re-read, the committee deviation is
recomputed from the two delivered student models, the selection is reconstructed
with the fixed ``[0.05, 0.15] eV/A`` band, teacher labels on the selected frames
are re-derived with the pinned teacher and cross-checked through the canonical
digest, and the exact dataset growth is compared. A paper (formal) run is
accepted only when it completed at least one genuine active iteration, its
recomputed final held-out force MAE is below ``0.10 eV/A``, and the reproduced
MAE is within 25% of the source paper's ``0.098 eV/A``.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
from ase import Atoms
from ase.io.trajectory import Trajectory
from deepmd.calculator import DP

TYPE_MAP = ["Cu", "In", "P", "S"]
EXPECTED_TEACHER = "a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d"
EXPECTED_STRUCTURE = "b9e3b0c4470274d5e3e1ce19e7c8834bda323e50483eef9791b1e744bbd629de"
SELECTION_BAND_EV_A = (0.05, 0.15)
PAPER_MAX_ACTIVE_ITERATIONS = 5  # locked paper profile["max_iterations"]
MAE_CONVERGENCE_THRESHOLD_EV_A = 0.10
SOURCE_MAE_EV_A = 0.098  # upstream reference: force_mae_eV_A in original_reference.json
SOURCE_RELATIVE_ERROR_MAX = 0.25
CHECKPOINT_SCHEMA_VERSION = "1"
PAPER_PROTOCOL_SEED = 2026081208


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_CALCULATOR_CACHE_MAX = 8
_CALCULATOR_CACHE: OrderedDict[str, DP] = OrderedDict()


def _dp_calculator(model: Path) -> DP:
    """Content-addressed, bounded LRU pool of deepmd DP calculators.

    A deepmd ``DP`` calculator owns a TensorFlow graph that is *not* released
    when the Python object is dropped (no ``__del__``/``close``), so building a
    fresh calculator per inference call leaks memory across many ``verify``
    runs. Every negative fixture copies the same submission workspace, so the
    teacher and student model files are byte-identical across tests: keying the
    pool by file content makes those copies share a single calculator. The pool
    is bounded so a pathological number of genuinely distinct models still
    cannot grow memory without limit.
    """
    key = sha256(model)
    calculator = _CALCULATOR_CACHE.pop(key, None)
    if calculator is None:
        calculator = DP(model=str(model))
        if len(_CALCULATOR_CACHE) >= _CALCULATOR_CACHE_MAX:
            _CALCULATOR_CACHE.popitem(last=False)
    _CALCULATOR_CACHE[key] = calculator
    return calculator


def configuration_hash(atoms: Atoms) -> str:
    digest = hashlib.sha256()
    digest.update(np.asarray(atoms.cell.array, dtype="<f8").tobytes())
    digest.update(np.asarray(atoms.positions, dtype="<f8").tobytes())
    digest.update(" ".join(atoms.get_chemical_symbols()).encode())
    return digest.hexdigest()


def load_dataset(path: Path) -> tuple[list[Atoms], np.ndarray, np.ndarray, list[str]]:
    atom_types = np.loadtxt(path / "type.raw", dtype=int, ndmin=1)
    symbols = [TYPE_MAP[int(index)] for index in atom_types]
    box = np.load(path / "set.000/box.npy")
    coord = np.load(path / "set.000/coord.npy")
    energy = np.load(path / "set.000/energy.npy")
    force = np.load(path / "set.000/force.npy").reshape(len(energy), len(symbols), 3)
    frames = [Atoms(symbols, positions=xyz.reshape(-1, 3), cell=cell.reshape(3, 3), pbc=True)
              for xyz, cell in zip(coord, box)]
    hashes = [configuration_hash(frame) for frame in frames]
    return frames, energy, force, hashes


def evaluate(model: Path, frames: list[Atoms], expected_forces: np.ndarray) -> float:
    calculator = _dp_calculator(model)
    errors = []
    for frame, expected in zip(frames, expected_forces):
        frame.calc = calculator
        errors.append(np.abs(frame.get_forces() - expected))
    return float(np.mean(errors))


def committee_deviations(models: list[Path], frames: list[Atoms]) -> np.ndarray:
    """Committee disagreement: std over members per component, max over atoms.

    This is the exact deviation metric the reference workflow uses for
    selection, so the verifier recomputes the same quantity from the delivered
    models and candidate frames instead of trusting the recorded values.
    """
    calculators = [_dp_calculator(model) for model in models]
    output = []
    for frame in frames:
        predictions = []
        for calculator in calculators:
            frame.calc = calculator
            predictions.append(frame.get_forces())
        array = np.asarray(predictions)
        per_atom = np.sqrt(np.mean(np.sum((array - np.mean(array, axis=0)) ** 2, axis=2), axis=0))
        output.append(float(np.max(per_atom)))
    return np.asarray(output)


def teacher_label_sha256(
    configuration_hashes: list[str],
    energies: np.ndarray,
    forces: np.ndarray,
) -> str:
    """Canonical audit digest of one pinned-teacher labelling event.

    Mirrors ``solution.run_distillation.teacher_label_sha256`` so the verifier
    can prove the delivered teacher labels were assigned by the pinned teacher
    to exactly the selected configurations.
    """
    payload = {
        "configuration_hashes": list(configuration_hashes),
        "energies": [float(value) for value in np.asarray(energies).reshape(-1)],
        "forces": [
            np.asarray(value, dtype=float).reshape(-1).tolist() for value in forces
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def teacher_label_evidence_valid(
    recorded_digest: str,
    configuration_hashes: list[str],
    stored_energies: np.ndarray,
    stored_forces: np.ndarray,
    recomputed_energies: np.ndarray,
    recomputed_forces: np.ndarray,
) -> bool:
    """Independently verify exact stored labels and tolerant teacher outputs."""
    return (
        recorded_digest
        == teacher_label_sha256(configuration_hashes, stored_energies, stored_forces)
        and np.allclose(
            np.asarray(stored_energies), np.asarray(recomputed_energies),
            rtol=1e-8, atol=1e-7,
        )
        and np.allclose(
            np.asarray(stored_forces), np.asarray(recomputed_forces),
            rtol=1e-8, atol=1e-7,
        )
    )


def select_informative(
    deviations: np.ndarray,
    hashes: list[str],
    excluded: set[str],
    low: float,
    high: float,
    cap: int,
) -> list[int]:
    """Inclusive band selection: de-duplicated, deterministic, never out-of-band.

    Mirrors ``solution.active_contract.select_informative``. The verifier applies
    it to the *recomputed* committee deviations to reconstruct the selection a
    genuine run had to make, independent of any claimed bookkeeping.
    """
    dev = np.asarray(deviations, dtype=float).reshape(-1)
    excluded_hashes = set(excluded)
    candidates: list[tuple[float, str, int]] = []
    for index, (deviation, frame_hash) in enumerate(zip(dev, hashes)):
        if low <= deviation <= high and frame_hash not in excluded_hashes:
            candidates.append((-float(deviation), frame_hash, index))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    seen: set[str] = set()
    selected: list[int] = []
    for _neg_deviation, frame_hash, index in candidates:
        if frame_hash in seen:
            continue
        seen.add(frame_hash)
        selected.append(index)
        if len(selected) >= int(cap):
            break
    return selected


def select_informative_by_trajectory(
    deviations: np.ndarray,
    hashes: list[str],
    trajectory_stops: list[int],
    excluded: set[str],
    cap: int,
) -> list[int]:
    """Recompute the source protocol's per-trajectory selection cap."""
    selected: list[int] = []
    unavailable = set(excluded)
    start = 0
    for stop in trajectory_stops:
        local = select_informative(
            deviations[start:stop], hashes[start:stop], unavailable,
            low=SELECTION_BAND_EV_A[0], high=SELECTION_BAND_EV_A[1], cap=cap,
        )
        global_indices = [start + index for index in local]
        selected.extend(global_indices)
        unavailable.update(hashes[index] for index in global_indices)
        start = stop
    return selected


def _label_with(teacher: DP, frames: list[Atoms]) -> tuple[np.ndarray, np.ndarray]:
    energies, forces = [], []
    for frame in frames:
        frame.calc = teacher
        energies.append(frame.get_potential_energy())
        forces.append(frame.get_forces())
    return np.asarray(energies), np.asarray(forces)


def verify(
    submission: Path,
    expected_profile: str | None = None,
    teacher_model: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    result_path = submission / "result.json"
    if not result_path.is_file():
        return {"valid": False, "errors": ["missing result.json"]}
    try:
        claimed = json.loads(result_path.read_text())
    except (OSError, ValueError) as exc:
        return {"valid": False, "errors": [f"invalid result.json: {exc}"]}

    profile = claimed.get("profile")
    if profile not in ("smoke", "paper") or (expected_profile and profile != expected_profile):
        errors.append("profile mismatch")
    if claimed.get("formal_result") is not (profile == "paper"):
        errors.append("formal_result does not match profile")
    if profile == "paper" and claimed.get("protocol_seed") != PAPER_PROTOCOL_SEED:
        errors.append("paper protocol seed mismatch")

    teacher_path = Path(teacher_model) if teacher_model is not None else submission / "teacher_model.pb"
    if not teacher_path.is_file() or sha256(teacher_path) != EXPECTED_TEACHER:
        errors.append("teacher model identity mismatch")
    elif claimed.get("teacher_model_sha256") != EXPECTED_TEACHER:
        errors.append("teacher model identity mismatch in result.json")
    if claimed.get("structure_sha256") != EXPECTED_STRUCTURE or claimed.get("type_map") != TYPE_MAP:
        errors.append("structure or type-map identity mismatch")

    heldout_path = submission / "data/heldout"
    try:
        test_frames, test_energy, test_forces, test_hashes = load_dataset(heldout_path)
    except (OSError, ValueError) as exc:
        return {"valid": False, "errors": errors + [f"invalid held-out dataset: {exc}"]}
    if claimed.get("heldout", {}).get("configuration_hashes") != test_hashes:
        errors.append("held-out configuration hashes mismatch")
    teacher = _dp_calculator(teacher_path)
    for index in sorted(set((0, len(test_frames) // 2, len(test_frames) - 1))):
        frame = test_frames[index]
        frame.calc = teacher
        if not np.isclose(frame.get_potential_energy(), test_energy[index], rtol=1e-8, atol=1e-7):
            errors.append("held-out energy is not teacher-labelled")
        if not np.allclose(frame.get_forces(), test_forces[index], rtol=1e-8, atol=1e-7):
            errors.append("held-out forces are not teacher-labelled")

    history = claimed.get("history", [])
    if profile == "paper" and len(history) < 2:
        errors.append("paper run must complete iteration 0 plus at least one later active iteration")
    previous_count = 0
    recomputed_mae: list[float] = []
    active_iterations = 0
    for expected_iteration, row in enumerate(history):
        if row.get("iteration") != expected_iteration:
            errors.append("active-learning iterations are not contiguous")
        train_path = submission / "data" / f"iteration_{expected_iteration}"
        try:
            train_frames, train_energy, train_forces, train_hashes = load_dataset(train_path)
        except (OSError, ValueError) as exc:
            errors.append(f"invalid training dataset at iteration {expected_iteration}: {exc}")
            continue
        if row.get("training_frames") != len(train_frames) or row.get("train_configuration_hashes") != train_hashes:
            errors.append(f"training dataset manifest mismatch at iteration {expected_iteration}")
        if set(train_hashes).intersection(test_hashes):
            errors.append(f"train/test configuration leakage at iteration {expected_iteration}")
        if len(train_frames) < previous_count:
            errors.append("training dataset shrank across iterations")
        if expected_iteration and len(train_frames) != previous_count + history[expected_iteration - 1].get("selected_frames", -1):
            errors.append("dataset growth does not equal teacher-selected additions")
        previous_count = len(train_frames)
        for index in sorted(set((0, len(train_frames) - 1))):
            frame = train_frames[index]
            frame.calc = teacher
            if not np.allclose(frame.get_forces(), train_forces[index], rtol=1e-8, atol=1e-7):
                errors.append(f"training force is not teacher-labelled at iteration {expected_iteration}")

        models = row.get("models", [])
        if len(models) != 2 or len({item.get("sha256") for item in models}) != 2:
            errors.append(f"iteration {expected_iteration} lacks two independent committee models")
            continue
        model_paths: list[Path] = []
        for item in models:
            relative = Path(str(item.get("path", "")))
            path = submission / relative
            if relative.is_absolute() or ".." in relative.parts or not path.is_file() or sha256(path) != item.get("sha256"):
                errors.append(f"student model evidence mismatch at iteration {expected_iteration}")
            else:
                model_paths.append(path)
        if model_paths:
            mae = evaluate(model_paths[0], test_frames, test_forces)
            recomputed_mae.append(mae)
            if not np.isclose(mae, row.get("heldout_force_mae_eV_A", np.nan), rtol=1e-7, atol=1e-8):
                errors.append(f"held-out MAE is not model-derived at iteration {expected_iteration}")

        if expected_iteration < len(history) - 1:
            deviations = row.get("committee_deviation_eV_A", [])
            selected = row.get("selected_indices", [])
            if len(deviations) != row.get("exploration_frames"):
                errors.append(f"committee deviation coverage mismatch at iteration {expected_iteration}")
            # Re-read the raw exploration trajectories the student actually ran.
            candidates: list[Atoms] = []
            trajectory_stops: list[int] = []
            for trajectory in row.get("exploration_trajectories", []):
                path = submission / trajectory.get("path", "")
                if path.is_file():
                    # ``md_frames`` returns (and the solver scores) every frame
                    # written by ASE, including the initial frame.  Dropping
                    # frame zero here would shift indices, turn a genuine 501
                    # candidate source-protocol trajectory into 500 frames,
                    # and reject otherwise model-derived selections.
                    candidates.extend(list(Trajectory(str(path))))
                    trajectory_stops.append(len(candidates))
            if len(candidates) != row.get("exploration_frames"):
                errors.append(f"exploration candidate count mismatch at iteration {expected_iteration}")
            if any(not SELECTION_BAND_EV_A[0] <= deviations[int(index)] <= SELECTION_BAND_EV_A[1] for index in selected):
                errors.append(f"selected frame outside committee-deviation band at iteration {expected_iteration}")
            if row.get("selected_frames") != len(selected):
                errors.append(f"selected-frame count mismatch at iteration {expected_iteration}")
            if row.get("exploration_attempts", 0) < 1:
                errors.append(f"exploration-extension bookkeeping missing at iteration {expected_iteration}")
            before = row.get("training_frames_before")
            after = row.get("training_frames_after")
            if not (isinstance(before, int) and isinstance(after, int)
                    and after == before + len(selected)):
                errors.append(f"training-frame audit mismatch at iteration {expected_iteration}")
            for trajectory in row.get("exploration_trajectories", []):
                path = submission / trajectory.get("path", "")
                if not path.is_file() or sha256(path) != trajectory.get("sha256"):
                    errors.append(f"student exploration evidence mismatch at iteration {expected_iteration}")

            # Independent recomputation of the active step. A genuine transition
            # must reproduce the claimed selection from the models and raw
            # trajectory frames alone; the fixed band is never relaxed.
            candidate_hashes = [configuration_hash(frame) for frame in candidates]
            recomputed_selected: list[int] = []
            if len(model_paths) == 2 and candidates:
                try:
                    recomputed_dev = committee_deviations(model_paths, candidates)
                except Exception:
                    recomputed_dev = None
                if recomputed_dev is not None:
                    if not np.allclose(np.asarray(deviations, dtype=float), recomputed_dev,
                                       rtol=1e-5, atol=1e-6):
                        errors.append(f"recorded committee deviation is not model-derived at iteration {expected_iteration}")
                    recomputed_selected = select_informative_by_trajectory(
                        recomputed_dev, candidate_hashes,
                        trajectory_stops,
                        excluded=set(train_hashes),
                        cap=200)
            recomputed_selected_hashes = [candidate_hashes[i] for i in recomputed_selected]
            claimed_hashes = row.get("selected_configuration_hashes") or []
            if claimed_hashes != recomputed_selected_hashes[:len(claimed_hashes)]:
                errors.append(f"recomputed selection does not match claimed selected hashes at iteration {expected_iteration}")
            if set(claimed_hashes) - set(candidate_hashes):
                errors.append(f"selected configuration not among recomputed candidates at iteration {expected_iteration}")
            if len(claimed_hashes) != len(set(claimed_hashes)):
                errors.append(f"selected configuration hashes are duplicated at iteration {expected_iteration}")
            # Exact (order-preserving) dataset growth: next == before + selected.
            if expected_iteration + 1 < len(history):
                next_train = history[expected_iteration + 1].get("train_configuration_hashes", [])
                if next_train != list(train_hashes) + list(claimed_hashes):
                    errors.append(f"dataset growth is not exactly before + selected at iteration {expected_iteration}")
            # Bind the labels actually committed to the next training dataset,
            # then independently recompute all of them with the pinned teacher.
            # CPU and CUDA inference need numerical equivalence, not bit identity.
            if claimed_hashes:
                selected_frames = [candidates[i] for i in recomputed_selected]
                if (len(selected_frames) == len(claimed_hashes)
                        and expected_iteration + 1 < len(history)):
                    next_path = submission / "data" / f"iteration_{expected_iteration + 1}"
                    try:
                        _, next_energy, next_forces, next_hashes = load_dataset(next_path)
                    except (OSError, ValueError):
                        next_energy = next_forces = np.asarray([])
                        next_hashes = []
                    stored_hashes = next_hashes[len(train_hashes):]
                    stored_energy = next_energy[len(train_hashes):]
                    stored_forces = next_forces[len(train_hashes):]
                    recomputed_energy, recomputed_forces = _label_with(teacher, selected_frames)
                    if (stored_hashes != claimed_hashes or not teacher_label_evidence_valid(
                        str(row.get("teacher_label_sha256", "")),
                        claimed_hashes,
                        stored_energy,
                        stored_forces,
                        recomputed_energy,
                        recomputed_forces,
                    )):
                        errors.append(f"teacher-label digest is not model-derived at iteration {expected_iteration}")
            if recomputed_selected:
                active_iterations += 1

    if history:
        last = history[-1]
        reason = claimed.get("stopping_condition")
        if reason == "heldout_force_mae_below_0.10" and last.get("heldout_force_mae_eV_A", 1) >= MAE_CONVERGENCE_THRESHOLD_EV_A:
            errors.append("invalid MAE stopping condition")
        if reason == "maximum_active_iterations" and claimed.get("iterations_completed") not in (1, PAPER_MAX_ACTIVE_ITERATIONS):
            errors.append("invalid iteration-limit stopping condition")
        if profile == "paper" and reason not in ("heldout_force_mae_below_0.10", "maximum_active_iterations"):
            errors.append("formal run stopped without an allowed terminal condition")
    if not (submission / "active_learning" / "history.json").is_file():
        errors.append("missing active-learning history")

    # The hash-locked atomic checkpoint is part of the audit trail: every
    # committed artifact must still match on disk, so a tampered or stale
    # checkpoint fails closed even when result.json was left untouched.
    checkpoint_path = submission / "checkpoint.json"
    if not checkpoint_path.is_file():
        errors.append("missing checkpoint.json")
    else:
        try:
            ckpt = json.loads(checkpoint_path.read_text())
        except (OSError, ValueError) as exc:
            errors.append(f"invalid checkpoint.json: {exc}")
        else:
            if ckpt.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                errors.append("checkpoint schema_version mismatch")
            if ckpt.get("run_identity", {}).get("run_identity_sha256") != claimed.get("run_identity_sha256"):
                errors.append("checkpoint run identity mismatch")
            for rel, digest in ckpt.get("artifact_hashes", {}).items():
                artifact = submission / rel
                if not artifact.is_file() or sha256(artifact) != digest:
                    errors.append(f"checkpoint artifact mismatch: {rel}")

    recomputed_final_mae = recomputed_mae[-1] if recomputed_mae else None
    source_relative_error = None
    if recomputed_final_mae is not None:
        source_relative_error = abs(recomputed_final_mae - SOURCE_MAE_EV_A) / SOURCE_MAE_EV_A
    if profile == "paper":
        if active_iterations < 1:
            errors.append("paper run completed no active distillation iteration (no selected frames)")
        if recomputed_final_mae is not None and recomputed_final_mae >= MAE_CONVERGENCE_THRESHOLD_EV_A:
            errors.append(
                f"final MAE {recomputed_final_mae:.4f} eV/A exceeds acceptance "
                f"threshold {MAE_CONVERGENCE_THRESHOLD_EV_A:.2f} eV/A")
        if source_relative_error is not None and source_relative_error > SOURCE_RELATIVE_ERROR_MAX:
            errors.append(
                f"source-relative MAE error {source_relative_error:.3f} exceeds "
                f"{SOURCE_RELATIVE_ERROR_MAX:.2f}")

    artifact_hashes = {}
    for path in sorted(submission.rglob("*")):
        if path.is_file():
            artifact_hashes[str(path.relative_to(submission))] = sha256(path)

    return {
        "valid": not errors,
        "errors": errors,
        "profile": profile,
        "active_iterations": active_iterations,
        "recomputed_heldout_mae_eV_A": recomputed_mae,
        "recomputed_final_mae_eV_A": recomputed_final_mae,
        "source_relative_error": source_relative_error,
        "artifact_hashes": artifact_hashes,
    }
