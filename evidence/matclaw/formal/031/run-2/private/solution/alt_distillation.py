#!/opt/matclaw/bin/python
"""Independent active-distillation implementation for Case 031 (alternative path).

This is a *second, independently-written* solver that produces the same artifact
contract as :mod:`run_distillation` but is organizationally separate: it never
imports the primary workflow or shares its orchestration functions. It differs
in the scientific choices:

* exploration plan — a distinct temperature/batch schedule read from the hidden
  ``solution/run_profiles.json`` ``*_alt`` sections instead of the primary
  ``exploration_batches``;
* seed offsets for student training and exploration MD;
* initial teacher-set sampling — reversed evenly-spaced picks instead of
  forward ones.

It records the same ``result.json`` schema, emits a hash-locked
``checkpoint.json``, and is graded by the same hidden verifier
(``tests/verifier.py``). An alternative solver accepted by the same evaluator
that rejects the negatives is the G11 evidence.

The committee-deviation selection band ``[0.05, 0.15] eV/A`` is a hard global
contract and is never relaxed. A low held-out MAE may stop the loop only after
at least one real selection -> label -> retrain cycle has completed, so an
iteration-zero convergence is reporting only and never a formal stop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np
from ase import units
from ase.io import read
from ase.io.trajectory import Trajectory
from ase.md.langevin import Langevin
from ase.md.velocitydistribution import MaxwellBoltzmannDistribution, Stationary, ZeroRotation
from deepmd.calculator import DP

# Pure, host-testable helpers shared across the case (audit hashes + the atomic
# checkpoint writer) and the pure selection contract locked in Task 1. These are
# contract modules, not primary-workflow orchestration code.
from active_utils import checkpoint, configuration_hash, sha256
import active_contract

WORKSPACE = Path(os.environ.get("MATCLAW_OUTPUT", "/app")).resolve()
SOLUTION = Path(__file__).resolve().parent
TYPE_MAP = ["Cu", "In", "P", "S"]
SELECTION_BAND_EV_A = (0.05, 0.15)
MAE_CONVERGENCE_THRESHOLD_EV_A = 0.10
CHECKPOINT_SCHEMA_VERSION = "1"
EXHAUSTED_STOP_REASON = "no_informative_configurations_after_all_declared_batches"


def md_frames(atoms, calculator, temperature: int, steps: int, seed: int, trajectory_path: Path,
              interval: int = 20) -> list:
    atoms = atoms.copy()
    atoms.calc = calculator
    rng = np.random.default_rng(seed)
    MaxwellBoltzmannDistribution(atoms, temperature_K=temperature, rng=rng)
    Stationary(atoms)
    ZeroRotation(atoms)
    dynamics = Langevin(atoms, 2.0 * units.fs, temperature_K=temperature,
                        friction=0.01 / units.fs, rng=rng)
    trajectory = Trajectory(str(trajectory_path), "w", atoms)
    dynamics.attach(trajectory.write, interval=interval)
    dynamics.run(steps)
    trajectory.close()
    return list(Trajectory(str(trajectory_path)))


def label(frames: list, teacher: DP) -> tuple[np.ndarray, np.ndarray]:
    energies, forces = [], []
    for frame in frames:
        frame.calc = teacher
        energies.append(frame.get_potential_energy())
        forces.append(frame.get_forces())
    return np.asarray(energies), np.asarray(forces)


def write_dataset(path: Path, frames: list, energies: np.ndarray, forces: np.ndarray) -> dict:
    """Persist one DeepMD system directory (set.000 + type.raw + hashes)."""
    set_dir = path / "set.000"
    set_dir.mkdir(parents=True, exist_ok=True)
    symbols = frames[0].get_chemical_symbols()
    types = np.asarray([TYPE_MAP.index(symbol) for symbol in symbols], dtype=int)
    (path / "type.raw").write_text("\n".join(str(value) for value in types) + "\n")
    (path / "type_map.raw").write_text("\n".join(TYPE_MAP) + "\n")
    np.save(set_dir / "box.npy", np.asarray([frame.cell.array.reshape(-1) for frame in frames]))
    np.save(set_dir / "coord.npy", np.asarray([frame.positions.reshape(-1) for frame in frames]))
    np.save(set_dir / "energy.npy", np.asarray(energies))
    np.save(set_dir / "force.npy", np.asarray(forces).reshape(len(frames), -1))
    hashes = [configuration_hash(frame) for frame in frames]
    (path / "configuration_hashes.json").write_text(json.dumps(hashes, indent=2) + "\n")
    return {"path": str(path), "frames": len(frames), "configuration_hashes": hashes}


def train_config(train_path: Path, test_path: Path, steps: int, train_seed: int,
                 model_seeds: list | None = None,
                 fitting_net: list | None = None,
                 descriptor_neuron: list | None = None,
                 axis_neuron: int | None = None) -> dict:
    net = fitting_net or [32, 32]
    desc = descriptor_neuron or [8, 16, 32]
    seeds = [int(value) for value in (model_seeds or [train_seed, train_seed + 1])]
    return {
        "model": {"type_map": TYPE_MAP,
                  "descriptor": {"type": "se_e2_a", "sel": "auto", "rcut_smth": 0.5, "rcut": 6.0,
                                 "neuron": desc, "axis_neuron": int(axis_neuron or 4), "seed": seeds[0]},
                  "fitting_net": {"neuron": net, "resnet_dt": False, "seed": seeds[1]}},
        "learning_rate": {"type": "exp", "start_lr": 0.001, "stop_lr": 1e-6,
                          "decay_steps": max(1, steps // 10)},
        "loss": {"type": "ener", "start_pref_e": 0.02, "limit_pref_e": 1.0,
                 "start_pref_f": 1000.0, "limit_pref_f": 1.0},
        "training": {"seed": train_seed, "disp_file": "lcurve.out", "disp_freq": max(1, steps // 5),
                     "save_freq": steps, "save_ckpt": "model.ckpt", "numb_steps": steps,
                     "training_data": {"systems": [str(train_path)], "batch_size": 1},
                     "validation_data": {"systems": [str(test_path)], "batch_size": 1, "numb_btch": 1}}
    }


def train_student(train_path: Path, test_path: Path, steps: int, train_seed: int, output: Path,
                  model_seeds: list | None = None, fitting_net: list | None = None,
                  descriptor_neuron: list | None = None,
                  axis_neuron: int | None = None) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    config = output / "input.json"
    config.write_text(json.dumps(train_config(
        train_path, test_path, steps, train_seed, model_seeds=model_seeds,
        fitting_net=fitting_net, descriptor_neuron=descriptor_neuron,
        axis_neuron=axis_neuron,
    ), indent=2) + "\n")
    environment = dict(os.environ, OMP_NUM_THREADS="1", TF_INTRA_OP_PARALLELISM_THREADS="1",
                       TF_INTER_OP_PARALLELISM_THREADS="1")
    subprocess.run(["/opt/matclaw/bin/dp", "train", "input.json"], cwd=output,
                   env=environment, check=True)
    model = output / "student.pb"
    subprocess.run(["/opt/matclaw/bin/dp", "freeze", "-o", model.name], cwd=output,
                   env=environment, check=True)
    return model


def evaluate_model(model: Path, frames: list, teacher_forces: np.ndarray) -> float:
    calculator = DP(model=str(model))
    errors = []
    for frame, expected in zip(frames, teacher_forces):
        frame.calc = calculator
        errors.append(np.abs(frame.get_forces() - expected))
    return float(np.mean(errors))


def deviations(models: list[Path], frames: list) -> np.ndarray:
    """Committee disagreement: std over members per component, max over atoms.

    This is the exact quantity the hidden verifier recomputes from the two
    delivered student models and the raw candidate frames, so the alt must use
    the same metric for its recorded ``committee_deviation_eV_A`` to be
    model-derived.
    """
    calculators = [DP(model=str(model)) for model in models]
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


def deviation_summary(deviations: np.ndarray) -> dict:
    """min/p25/median/p75/max quantiles of the committee deviations."""
    dev = np.asarray(deviations, dtype=float).reshape(-1)
    if dev.size == 0:
        return {"min": None, "p25": None, "median": None, "p75": None, "max": None}
    return {
        "min": float(np.min(dev)),
        "p25": float(np.percentile(dev, 25)),
        "median": float(np.percentile(dev, 50)),
        "p75": float(np.percentile(dev, 75)),
        "max": float(np.max(dev)),
    }


def teacher_label_sha256(
    configuration_hashes: list[str],
    energies: np.ndarray,
    forces: np.ndarray,
) -> str:
    """Canonical audit digest of one pinned-teacher labelling event.

    Covers the selected configuration hashes plus the teacher energy and
    per-atom force labels, serialized as sorted compact JSON. The hidden
    verifier recomputes this digest from the delivered teacher model and the
    selected frames to prove the labels came from the pinned teacher.
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


def resolve_seed(profile_name: str, seed: int | None, profile: dict) -> int:
    """The effective root seed: an explicit seed always wins; smoke may fall back
    to the profile seed, but paper must name a seed so two formal runs are
    provably distinct."""
    if seed is not None:
        return int(seed)
    if profile_name == "paper":
        raise ValueError("paper mode requires an explicit --seed")
    return int(profile["seed"])


def resolve_protocol_seed(profile_name: str, profile: dict, run_seed: int) -> int:
    if profile_name == "paper":
        if "protocol_seed" not in profile:
            raise ValueError("paper profile must declare protocol_seed")
        return int(profile["protocol_seed"])
    return int(profile.get("protocol_seed", run_seed))


def resolve_identity(
    profile_name: str,
    profile: dict,
    seed: int,
    structure_sha256: str,
    teacher_sha256: str,
) -> dict:
    """The auditable run identity and its canonical ``run_identity_sha256``.

    The identity payload is canonicalized with ``json.dumps(sort_keys=True,
    separators=(",", ":"))`` and hashed with sha256, so identical inputs always
    reproduce the same digest and any change to the profile content, seed,
    structure, or teacher changes the digest.
    """
    profile_sha256 = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    payload = {
        "profile": profile_name,
        "seed": int(seed),
        "structure_sha256": structure_sha256,
        "teacher_model_sha256": teacher_sha256,
        "profile_sha256": profile_sha256,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        **payload,
        "run_identity_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def record_artifact(state: dict, output: Path, path: Path) -> None:
    rel = str(path.resolve().relative_to(output.resolve()))
    state.setdefault("artifact_hashes", {})[rel] = sha256(path)


def record_dir(state: dict, output: Path, directory: Path) -> None:
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            record_artifact(state, output, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("smoke", "paper"), default="smoke")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", type=Path, default=Path("/app"))
    parser.add_argument("--resume", action="store_true",
                        help="continue a completed run from checkpoint.json")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ("teacher_md", "student_md", "models", "data", "active_learning"):
        (output / name).mkdir(exist_ok=True)
    profile = json.loads((WORKSPACE / "run_profiles.json").read_text())[args.profile]
    hidden = json.loads((SOLUTION / "run_profiles.json").read_text())[f"{args.profile}_alt"]
    band = tuple(float(value) for value in hidden.get(
        "selection_band_eV_A", SELECTION_BAND_EV_A))
    # The committee-deviation selection band is a hard global contract: the
    # hidden profile may restate it, but never relax or tighten it.
    if band != tuple(float(value) for value in SELECTION_BAND_EV_A):
        raise RuntimeError(
            f"selection band must stay {list(SELECTION_BAND_EV_A)} eV/A; "
            f"profile declares {list(band)}"
        )
    min_active_iterations = int(hidden.get("min_active_iterations", 1))
    try:
        base_seed = resolve_seed(args.profile, args.seed, profile)
    except ValueError as exc:
        parser.error(str(exc))
    protocol_seed = resolve_protocol_seed(args.profile, profile, base_seed)

    structure_path = WORKSPACE / "CuInP2S6.cif"
    teacher_path = WORKSPACE / "teacher_model.pb"
    base = read(structure_path).repeat(profile["supercell"])
    teacher = DP(model=str(teacher_path))
    max_iterations = profile["max_iterations"]

    identity = resolve_identity(args.profile, profile, base_seed,
                                sha256(structure_path), sha256(teacher_path))

    # Resume only when the persisted run identity and every recorded artifact
    # still match (fail closed). The alt keeps a single final checkpoint, so a
    # completed run is re-emitted and anything else is refused.
    if args.resume:
        checkpoint_path = output / "checkpoint.json"
        if not checkpoint_path.is_file():
            parser.error("--resume requested but no checkpoint.json exists")
        state = json.loads(checkpoint_path.read_text())
        if state.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            raise RuntimeError("checkpoint schema_version mismatch")
        if state.get("run_identity", {}).get("run_identity_sha256") != identity["run_identity_sha256"]:
            raise RuntimeError("checkpoint run identity mismatch")
        for rel, digest in state.get("artifact_hashes", {}).items():
            artifact = output / rel
            if not artifact.is_file() or sha256(artifact) != digest:
                raise RuntimeError(f"checkpoint artifact mismatch: {rel}")
        if state.get("completed_stage") == "result":
            (output / "result.json").write_text(json.dumps(state["result"], indent=2) + "\n")
            (output / "active_learning" / "history.json").write_text(
                json.dumps(state["result"]["history"], indent=2) + "\n")
            print("resume: result already complete; nothing to do")
            return
        raise RuntimeError("alt resume of an incomplete run is not supported; use a fresh workspace")

    # Independent seed offsets: the alternative never reuses the primary's RNG
    # stream for student training or exploration MD.
    student_seed_offset = 9000
    exploration_seed_offset = 5000

    # ---- Teacher-labelled initial set (reversed evenly-spaced picks).
    train_frames = []
    for index, temperature in enumerate(profile["teacher_temperatures_K"]):
        path = output / "teacher_md" / f"initial_{temperature}K.traj"
        frames = md_frames(base, teacher, temperature, profile["teacher_md_steps"],
                           protocol_seed + index, path)
        dynamic = frames[1:]
        pick = np.linspace(0, len(dynamic) - 1,
                           profile["initial_frames_per_temperature"], dtype=int)[::-1]
        train_frames.extend(dynamic[int(frame_index)] for frame_index in pick)
    test_trajectory = output / "teacher_md" / "heldout_400K.traj"
    # The trajectory writer includes the identical, unevolved base structure
    # at frame zero for every MD run.  A held-out set must not contain that
    # shared pre-dynamics frame: exploration legitimately scores its own frame
    # zero, and selecting it would otherwise create train/test leakage.
    test_frames = md_frames(
        base, teacher, profile["heldout_temperature_K"],
        profile["heldout_md_steps"], protocol_seed + 100, test_trajectory,
        interval=int(profile.get("heldout_trajectory_interval", 20)),
    )[1:]
    train_energy, train_forces = label(train_frames, teacher)
    test_energy, test_forces = label(test_frames, teacher)
    test_meta = write_dataset(output / "data" / "heldout", test_frames, test_energy, test_forces)
    history = []

    # ---- Active-distillation loop.
    for iteration in range(max_iterations + 1):
        train_path = output / "data" / f"iteration_{iteration}"
        train_meta = write_dataset(train_path, train_frames, train_energy, train_forces)
        student_cfg = profile.get("student_training", {})
        schedule = profile.get("training_steps_by_iteration", [profile.get("training_steps")])
        steps = int(schedule[min(iteration, len(schedule) - 1)])
        model_paths = [train_student(train_path, output / "data" / "heldout", steps,
                                     base_seed + student_seed_offset + iteration * 10 + committee,
                                     output / "models" / f"iteration_{iteration}" / f"member_{committee}",
                                     model_seeds=student_cfg.get("model_seeds"),
                                     fitting_net=student_cfg.get("fitting_net"),
                                     descriptor_neuron=student_cfg.get("descriptor_neuron"),
                                     axis_neuron=student_cfg.get("axis_neuron"))
                       for committee in range(2)]
        mae = evaluate_model(model_paths[0], test_frames, test_forces)
        record = {"iteration": iteration, "training_frames": len(train_frames),
                  "heldout_force_mae_eV_A": mae,
                  "train_configuration_hashes": train_meta["configuration_hashes"],
                  "test_configuration_hashes": test_meta["configuration_hashes"],
                  "models": [{"path": str(path.relative_to(output)), "sha256": sha256(path)}
                             for path in model_paths],
                  "selected_frames": 0, "selection_band_eV_A": list(band)}
        history.append(record)
        if iteration == max_iterations:
            record["stop_reason"] = "maximum_active_iterations"
            break
        # Iteration-zero MAE is ordinary supervised training: reported but never
        # a formal stop. A low MAE may stop the loop only after at least
        # min_active_iterations real selection -> label -> retrain cycles.
        if mae < MAE_CONVERGENCE_THRESHOLD_EV_A and iteration >= min_active_iterations:
            record["stop_reason"] = "heldout_force_mae_below_0.10"
            break

        # ---- Bounded cumulative exploration across the independent _alt
        # schedule. Attempts run strictly in declaration order; after every
        # attempt the committee deviation is recomputed over every candidate
        # accumulated so far and active_contract.select_informative is applied
        # with the fixed band. The next declared condition is attempted only
        # while the selection is empty; exhaustion fails closed.
        attempts_plan = hidden.get("exploration_attempts", {}).get(str(iteration), [])
        max_attempts = hidden.get("max_exploration_attempts", len(attempts_plan))
        candidates: list = []
        exploration_records: list = []
        sigma = np.asarray([], dtype=float)
        selected: list[int] = []
        attempted = 0
        exhausted = True
        for attempt_index, batch in enumerate(attempts_plan):
            if attempt_index >= int(max_attempts):
                break
            attempted = attempt_index + 1
            for j, temperature in enumerate(batch):
                trajectory = output / "student_md" / (
                    f"iteration_{iteration + 1}_a{attempt_index}_{temperature}K.traj")
                generated = md_frames(base, DP(model=str(model_paths[0])), temperature,
                                      profile["exploration_md_steps"],
                                      protocol_seed + exploration_seed_offset + iteration * 100
                                      + attempt_index * 10 + j, trajectory)
                candidates.extend(generated)
                exploration_records.append({"temperature_K": temperature,
                                            "md_steps": profile["exploration_md_steps"],
                                            "driver_member": 0,
                                            "seed_offset": attempt_index * 10 + j,
                                            "path": str(trajectory.relative_to(output)),
                                            "sha256": sha256(trajectory),
                                            "candidate_frames": len(generated)})
            sigma = deviations(model_paths, candidates)
            exploration_records[-1]["deviation_summary"] = deviation_summary(sigma)
            selected = active_contract.select_informative(
                sigma,
                [configuration_hash(frame) for frame in candidates],
                excluded=set(train_meta["configuration_hashes"]),
                low=band[0],
                high=band[1],
                cap=int(profile["selection_cap"]),
            )
            if selected:
                exhausted = False
                break
        record["exploration_attempts"] = attempted
        record["exploration_frames"] = len(candidates)
        record["exploration_trajectories"] = exploration_records
        record["committee_deviation_eV_A"] = sigma.tolist()
        record["deviation_summary"] = deviation_summary(sigma)
        record["selected_indices"] = selected
        record["selected_frames"] = int(len(selected))
        if exhausted:
            # Every declared attempt produced no in-band frame: fail closed and
            # never fall back to an out-of-band or nearest candidate.
            record["stop_reason"] = EXHAUSTED_STOP_REASON
            break
        selected_frames = [candidates[int(index)] for index in selected]
        before_hashes = [configuration_hash(frame) for frame in train_frames]
        selected_hashes = [configuration_hash(frame) for frame in selected_frames]
        record["training_frames_before"] = len(train_frames)
        record["selected_configuration_hashes"] = selected_hashes
        new_energy, new_forces = label(selected_frames, teacher)
        record["teacher_label_sha256"] = teacher_label_sha256(
            selected_hashes, new_energy, new_forces
        )
        # Append each selected frame exactly once; the next iteration's training
        # hashes are exactly the previous hashes plus the selected hashes.
        active_contract.assert_exact_growth(
            before_hashes, selected_hashes, before_hashes + selected_hashes
        )
        train_frames.extend(selected_frames)
        train_energy = np.concatenate((train_energy, new_energy))
        train_forces = np.concatenate((train_forces, new_forces))
        record["training_frames_after"] = len(train_frames)

    terminal = history[-1]
    stop_reason = terminal.get("stop_reason")
    exhausted = stop_reason == EXHAUSTED_STOP_REASON
    all_train_hashes = set(terminal["train_configuration_hashes"])
    if all_train_hashes.intersection(test_meta["configuration_hashes"]):
        raise RuntimeError("train/test configuration leakage")
    result = {"schema_version": "1.0", "case": "031", "profile": args.profile,
              "seed": base_seed, "protocol_seed": protocol_seed,
              "run_identity_sha256": identity["run_identity_sha256"],
              "formal_result": (False if exhausted else bool(profile["formal_result"])),
              "supercell": profile["supercell"], "atom_count": len(base),
              "teacher_model_sha256": sha256(teacher_path),
              "structure_sha256": sha256(structure_path), "type_map": TYPE_MAP,
              "heldout": test_meta,
              "reporting": {"iteration_zero_force_mae_eV_A":
                            history[0]["heldout_force_mae_eV_A"]},
              "history": history,
              "iterations_completed": terminal["iteration"],
              "final_force_mae_eV_A": terminal["heldout_force_mae_eV_A"],
              "stopping_condition": stop_reason}
    if exhausted:
        # Fail closed: every declared attempt produced no in-band frame, so this
        # is not a valid formal result. The profile stays "paper" but
        # formal_result is forced to false and the run is marked invalid.
        result["valid"] = False
        result["invalid_reason"] = EXHAUSTED_STOP_REASON
    (output / "active_learning" / "history.json").write_text(json.dumps(history, indent=2) + "\n")
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    # Hash-locked checkpoint over every committed artifact. The verifier reads
    # this file to prove every delivered artifact is byte-identical on disk.
    state = {"schema_version": CHECKPOINT_SCHEMA_VERSION,
             "run_identity": dict(identity),
             "completed_stage": "result",
             "artifact_hashes": {},
             "history": history,
             "selected_hashes": [h for row in history
                                 for h in row.get("selected_configuration_hashes", [])],
             "result": result}
    for name in ("teacher_md", "student_md", "models", "data", "active_learning"):
        record_dir(state, output, output / name)
    record_artifact(state, output, output / "result.json")
    checkpoint(output, state)
    print(f"alt run complete: {len(history)} iterations, "
          f"stopping_condition={stop_reason}, "
          f"final_force_mae_eV_A={terminal['heldout_force_mae_eV_A']:.4f}")


if __name__ == "__main__":
    main()
