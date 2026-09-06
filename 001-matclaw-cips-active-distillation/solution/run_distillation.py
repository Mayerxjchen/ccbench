#!/opt/matclaw/bin/python
"""Run an auditable teacher-to-student active-distillation workflow for CIPS.

The per-iteration exploration plan (which temperatures to try, in which order,
and how many attempts are allowed before the run must stop) lives in the hidden
``solution/run_profiles.json`` — it is never staged into ``/app``, so an agent
that only sees the graded workspace cannot replay a precomputed selection path.

The run is resumable: ``checkpoint.json`` records the run identity (profile,
canonical profile hash, resolved seed, structure hash, teacher hash, and the
``run_identity_sha256`` digest over that canonical JSON) plus a hash of every
committed artifact. An explicit ``--seed`` is required in paper mode (smoke mode
may fall back to the profile seed), and ``--resume`` is the only way to continue
an existing output directory. A resume only proceeds when that identity and
every recorded artifact still match; otherwise it fails closed. Paper runs must
complete at least one full selection/relabel/retrain cycle before a low MAE may
stop the loop, so an iteration-0 convergence without any active step is not a
valid formal result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from ase import Atoms, units
from ase.io import read
from ase.io.trajectory import Trajectory
from ase.md.langevin import Langevin
from ase.md.velocitydistribution import MaxwellBoltzmannDistribution, Stationary, ZeroRotation
from deepmd.calculator import DP

# Pure, host-testable helpers (numpy + stdlib only).
from active_utils import configuration_hash, sha256

# Deterministic selection/batch rules stay in the pure contract module: use its
# ``select_informative`` (hash de-duplicated, band-inclusive, no fallback) and
# ``next_batch`` here rather than re-implementing a second copy.
import active_contract

# Public inputs (structure, teacher model, profiles) are staged into the graded
# workspace (/app by default); never read them from this script's own directory,
# which lives in /solution in the container.
WORKSPACE = Path(os.environ.get("MATCLAW_OUTPUT", "/app")).resolve()
SOLUTION = Path(__file__).resolve().parent
TYPE_MAP = ["Cu", "In", "P", "S"]
SELECTION_BAND_EV_A = (0.05, 0.15)
MAE_CONVERGENCE_THRESHOLD_EV_A = 0.10

# The persisted, hash-locked run state. Every key is load-bearing:
# ``run_identity`` names the exact run, ``completed_stage`` the last committed
# stage, ``artifact_hashes`` the sha256 of every committed artifact (relative to
# the output root), ``history`` the ordered iteration records, and
# ``selected_hashes`` the cumulative teacher-selected configuration hashes.
CHECKPOINT_SCHEMA_VERSION = "1"


def write_checkpoint(path: Path, state: dict) -> None:
    """Atomically persist a checkpoint (tmp + flush + fsync + Path.replace).

    The state is written to ``<path>.tmp``, flushed and fsynced to disk, then
    atomically moved over ``path`` with :meth:`Path.replace`. A crash mid-write
    can therefore never leave a torn checkpoint: a reader sees either the
    previous complete state or the new one, never a partial file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def load_checkpoint(path: Path, expected_identity: dict) -> dict | None:
    """Return a persisted checkpoint only when identity and hashes still match.

    Returns ``None`` when no checkpoint exists. Raises ``RuntimeError`` when the
    checkpoint schema, the run identity, or any recorded artifact hash no longer
    matches the workspace — a tampered or incompatible state is **never** reused
    (fail closed) and the first invalid artifact is named.
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"unreadable checkpoint.json: {exc}") from exc
    if state.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise RuntimeError(
            "checkpoint schema_version mismatch: "
            f"{state.get('schema_version')!r}"
        )
    identity = state.get("run_identity")
    if not isinstance(identity, dict):
        raise RuntimeError("checkpoint has no run_identity")
    for key, value in expected_identity.items():
        if identity.get(key) != value:
            raise RuntimeError(f"checkpoint identity mismatch for {key}")
    for rel, digest in state.get("artifact_hashes", {}).items():
        artifact = path.parent / rel
        if not artifact.is_file():
            raise RuntimeError(f"checkpoint artifact missing: {rel}")
        if sha256(artifact) != digest:
            raise RuntimeError(f"checkpoint artifact mismatch: {rel}")
    return state


def record_artifact(state: dict, output: Path, path: Path) -> None:
    """Record one artifact's sha256 under ``state["artifact_hashes"]``."""
    rel = str(path.resolve().relative_to(output.resolve()))
    state.setdefault("artifact_hashes", {})[rel] = sha256(path)


def record_dir(state: dict, output: Path, directory: Path) -> None:
    """Record every file under ``directory`` as a checkpoint artifact."""
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            record_artifact(state, output, path)


def _recorded_unchanged(state: dict, output: Path, path: Path) -> bool:
    rel = str(path.resolve().relative_to(output.resolve()))
    recorded = state.get("artifact_hashes", {}).get(rel)
    return recorded is not None and path.is_file() and sha256(path) == recorded


def stage_complete(state: dict, output: Path, paths: list[Path]) -> bool:
    """True when every path is recorded and still byte-identical on disk.

    A directory is complete when every file under it is recorded with a matching
    hash. Used on resume to skip stages whose artifacts are already committed
    and verified; any missing or modified artifact returns False so the stage is
    rebuilt deterministically — never silently reusing a mismatched artifact.
    """
    if not state:
        return False
    for path in paths:
        if path.is_dir():
            files = [p for p in path.rglob("*") if p.is_file()]
            if not files or any(not _recorded_unchanged(state, output, p) for p in files):
                return False
        elif not _recorded_unchanged(state, output, path):
            return False
    return True


def _output_has_run_artifacts(output: Path) -> bool:
    """True when the output root already carries run-produced artifacts.

    A fresh workspace holds only staged public inputs (structure, teacher model,
    profile), so it is never mistaken for a prior run: the distinctive markers
    of a previous run are ``checkpoint.json``, ``result.json``, or any content
    under the run output subdirectories.
    """
    if not output.exists():
        return False
    if (output / "checkpoint.json").is_file() or (output / "result.json").is_file():
        return True
    for name in ("teacher_md", "student_md", "models", "data", "active_learning"):
        sub = output / name
        if sub.exists() and any(sub.iterdir()):
            return True
    return False


def resolve_run_seed(profile_name: str, seed: int | None, profile: dict) -> int:
    """Return the effective root seed for a run.

    An explicit ``seed`` always wins. Without one, only ``smoke`` mode may fall
    back to the profile's own ``seed``; ``paper`` mode must name a seed so two
    formal runs are provably distinct (and a repeat is provably identical).
    Raises ``ValueError`` for a paper run with no explicit seed.
    """
    if seed is not None:
        return int(seed)
    if profile_name == "paper":
        raise ValueError(
            "paper mode requires an explicit --seed; the profile seed is not "
            "reproducible evidence for a formal run"
        )
    return int(profile["seed"])


def resolve_protocol_seed(profile_name: str, profile: dict, run_seed: int) -> int:
    """Resolve the MD random state independently of formal evidence identity.

    The recovered source execution supplied no MD seed at its ForceFieldMDMaker
    calls; the reconstructed paper protocol therefore locks one deterministic
    random state instead of allowing an arbitrary evidence-run id to alter the
    physical trajectory. Smoke fixtures remain run-seed sensitive unless they
    explicitly declare the same contract.
    """
    if profile_name == "paper":
        if "protocol_seed" not in profile:
            raise ValueError("paper profile must declare protocol_seed")
        return int(profile["protocol_seed"])
    return int(profile.get("protocol_seed", run_seed))


def training_steps_for_iteration(profile: dict, iteration: int) -> int:
    """Resolve the source-demonstrated 2000/2000/3000 training schedule."""
    raw = profile.get("training_steps_by_iteration", [profile.get("training_steps")])
    schedule = [int(value) for value in raw]
    if not schedule or any(value <= 0 for value in schedule):
        raise ValueError("training_steps_by_iteration must contain positive integers")
    return schedule[min(int(iteration), len(schedule) - 1)]


def committee_seed(profile: dict, iteration: int, member: int) -> int:
    """Return the source committee seed; it is intentionally iteration-stable."""
    raw = profile.get("committee_seeds")
    if raw is None:
        root = int(profile["seed"]) + int(iteration) * 10
        raw = [root, root + 1]
    seeds = [int(value) for value in raw]
    if len(seeds) != 2 or len(set(seeds)) != 2:
        raise ValueError("committee_seeds must contain two distinct seeds")
    return seeds[int(member)]


def resolve_run_identity(
    profile_name: str,
    profile: dict,
    seed: int,
    structure_sha256: str,
    teacher_sha256: str,
) -> dict:
    """Build the auditable run identity and its canonical ``run_identity_sha256``.

    The identity payload is canonicalized with ``json.dumps(sort_keys=True,
    separators=(",", ":"))`` and then hashed with sha256, so identical inputs
    always reproduce the same digest and any change to the profile content, seed,
    structure, or teacher changes the digest. The returned dict carries the
    payload fields plus the ``run_identity_sha256`` digest over the canonical
    serialization of exactly those payload fields.
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


def load_dataset(path: Path) -> tuple[list, np.ndarray, np.ndarray, list[str]]:
    """Reconstruct (frames, energies, forces, hashes) from a written dataset."""
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


def train_config(train_path: Path, test_path: Path, steps: int, train_seed: int,
                 model_seeds: list | None = None,
                 fitting_net: list | None = None,
                 descriptor_neuron: list | None = None,
                 axis_neuron: int | None = None,
                 decay_steps: int | None = None) -> dict:
    # Defaults preserve the original calibration baseline ([32,32] fitting net,
    # descriptor [8,16,32], decay over steps//10). A profile may override these
    # via the optional public ``student_training`` block to cure student
    # under-fitting (the force val RMSE floor that decay_steps=steps//10 hits
    # before the small net has converged). Overrides are agent-visible public
    # parameters, never the hidden exploration schedule.
    net = fitting_net or [32, 32]
    desc = descriptor_neuron or [8, 16, 32]
    seeds = [int(value) for value in (model_seeds or [train_seed, train_seed + 1])]
    if len(seeds) != 2:
        raise ValueError("model_seeds must contain descriptor and fitting seeds")
    decay = decay_steps if decay_steps is not None else max(1, steps // 10)
    return {
        "model": {"type_map": TYPE_MAP,
                  "descriptor": {"type": "se_e2_a", "sel": "auto", "rcut_smth": 0.5, "rcut": 6.0,
                                 "neuron": desc, "axis_neuron": int(axis_neuron or 4),
                                 "seed": seeds[0]},
                  "fitting_net": {"neuron": net, "resnet_dt": False, "seed": seeds[1]}},
        "learning_rate": {"type": "exp", "start_lr": 0.001, "stop_lr": 1e-6,
                          "decay_steps": decay},
        "loss": {"type": "ener", "start_pref_e": 0.02, "limit_pref_e": 1.0,
                 "start_pref_f": 1000.0, "limit_pref_f": 1.0},
        "training": {"seed": train_seed, "disp_file": "lcurve.out", "disp_freq": max(1, steps // 5),
                     "save_freq": steps, "save_ckpt": "model.ckpt", "numb_steps": steps,
                     "training_data": {"systems": [str(train_path)], "batch_size": 1},
                     "validation_data": {"systems": [str(test_path)], "batch_size": 1, "numb_btch": 1}}
    }


def train_student(train_path: Path, test_path: Path, steps: int, train_seed: int, output: Path,
                  model_seeds: list | None = None,
                  fitting_net: list | None = None,
                  descriptor_neuron: list | None = None,
                  axis_neuron: int | None = None,
                  decay_steps: int | None = None) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    config = output / "input.json"
    config.write_text(json.dumps(train_config(train_path, test_path, steps, train_seed,
                                              model_seeds=model_seeds,
                                              fitting_net=fitting_net,
                                              descriptor_neuron=descriptor_neuron,
                                              axis_neuron=axis_neuron,
                                              decay_steps=decay_steps), indent=2) + "\n")
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
    """Committee disagreement: std over members per component, max over atoms."""
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


# Terminal condition for a run that attempted every declared exploration batch
# without finding any in-band configuration. It is never a formal success.
EXHAUSTED_STOP_REASON = "no_informative_configurations_after_all_declared_batches"


def formal_result_for(stop_reason: str | None, declared_formal: bool) -> bool:
    """The ``formal_result`` a run must write for a terminal condition.

    A run that exhausted every declared exploration batch must never be
    reported as a formal success — even when the profile claims ``formal_result``
    — so it cannot masquerade as a valid paper run.
    """
    if stop_reason == EXHAUSTED_STOP_REASON:
        return False
    return bool(declared_formal)


def may_accept_convergence(
    mae: float, completed_active_iterations: int, minimum: int
) -> bool:
    """Whether a run may stop early on held-out force MAE.

    Held-out MAE alone is not active distillation: the iteration-zero committee
    is ordinary supervised training on the teacher-labelled initial set. A low
    MAE may stop the loop only after at least ``minimum`` real
    selection -> label -> retrain cycles have completed *and* the current MAE is
    strictly below the convergence threshold. This is the single gate that keeps
    ``heldout_force_mae_below_0.10`` off any iteration-zero report.
    """
    return (
        float(mae) < MAE_CONVERGENCE_THRESHOLD_EV_A
        and int(completed_active_iterations) >= int(minimum)
    )


def teacher_label_sha256(
    configuration_hashes: list[str],
    energies: np.ndarray,
    forces: np.ndarray,
) -> str:
    """Canonical audit digest of one pinned-teacher labelling event.

    Covers the selected configuration hashes plus the teacher energy and
    per-atom force labels, serialized as sorted compact JSON. The hidden
    verifier can recompute this digest from the delivered teacher model and the
    selected frames to prove the labels were assigned by the pinned teacher to
    exactly these configurations.
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


def commit_active_retrain(
    before_hashes: list[str], selected_hashes: list[str]
) -> list[str]:
    """The exact next-iteration training hashes after one active cycle.

    The dataset grows by exactly the unique, teacher-labelled selection:
    ``after == before + selected`` in order. The growth rule is delegated to
    ``active_contract.assert_exact_growth`` so the orchestrator and the hidden
    verifier share one audit check, and a selection that would duplicate a frame
    (a repeated hash, or a hash already in the training set) is rejected instead
    of being appended twice.
    """
    before_hashes = list(before_hashes)
    selected_hashes = list(selected_hashes)
    if len(set(selected_hashes)) != len(selected_hashes):
        raise ValueError("selected hashes must be unique: append each frame once")
    overlap = set(before_hashes).intersection(selected_hashes)
    if overlap:
        raise ValueError(
            "selected hashes must be disjoint from the training set: "
            f"{sorted(overlap)[:5]}"
        )
    after_hashes = before_hashes + selected_hashes
    active_contract.assert_exact_growth(before_hashes, selected_hashes, after_hashes)
    return after_hashes


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


def run_exploration(
    batches: list[dict],
    *,
    band: tuple[float, float],
    cap: int,
    excluded: set[str],
    md_callback,
    deviation_callback,
) -> dict:
    """Cumulative, bounded exploration across every declared batch.

    Batches are attempted strictly in declaration order. After *each* batch the
    committee deviation is recomputed over every candidate accumulated so far
    (``deviation_callback``) and :func:`active_contract.select_informative` is
    called with the *fixed* band. The next batch is attempted only while that
    selection is empty, so a single low-yield batch can never abort the run
    while a later declared physical condition might still produce an in-band
    configuration. When every declared batch has been attempted and the
    selection is still empty the run fails closed with
    ``EXHAUSTED_STOP_REASON``.

    ``md_callback(batch)`` runs one declared batch and returns
    ``(frames, record)``; ``record`` is appended to ``exploration_records`` with
    the batch's cumulative ``deviation_summary`` added.

    Returns a dict with ``candidates``, ``sigma``, ``selected``,
    ``exploration_records`` and ``stop_reason`` (``None`` on success). The band
    is never modified and no out-of-band or nearest candidate is ever selected:
    ``active_contract.select_informative`` only ever returns in-band hashes.
    """
    candidates: list = []
    batch_stops: list[int] = []
    sigma = np.asarray([], dtype=float)
    selected: list[int] = []
    records: list[dict] = []
    completed = 0
    stop_reason: str | None = None
    while True:
        batch = active_contract.next_batch(batches, completed)
        if batch is None:
            if not selected:
                stop_reason = EXHAUSTED_STOP_REASON
            break
        completed += 1
        frames, record = md_callback(batch)
        candidates.extend(frames)
        batch_stops.append(len(candidates))
        sigma = np.asarray(deviation_callback(candidates), dtype=float).reshape(-1)
        record["deviation_summary"] = deviation_summary(sigma)
        records.append(record)
        selected = active_contract.select_informative_by_batch(
            sigma,
            [configuration_hash(frame) for frame in candidates],
            batch_stops,
            excluded,
            band[0],
            band[1],
            cap,
        )
    return {
        "candidates": candidates,
        "sigma": sigma,
        "selected": selected,
        "exploration_records": records,
        "stop_reason": stop_reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("smoke", "paper"), default="smoke")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", type=Path, default=Path("/app"))
    parser.add_argument("--resume", action="store_true",
                        help="continue an existing run from checkpoint.json")
    args = parser.parse_args()
    output = args.output.resolve()
    checkpoint_path = output / "checkpoint.json"
    # Resume is the only way to continue an existing output root. Failing closed
    # here keeps two formal runs from silently sharing artifacts through a stale
    # checkpoint, and forces an explicit --resume to reuse any prior work. A
    # fresh run likewise refuses an output root that already carries run
    # artifacts (a staged public workspace alone is not a prior run).
    if args.resume:
        if not checkpoint_path.is_file():
            parser.error("--resume requested but no checkpoint.json exists")
    elif _output_has_run_artifacts(output):
        parser.error(
            f"output directory {output} contains prior run artifacts; pass "
            "--resume to continue this run or use a fresh output directory"
        )
    output.mkdir(parents=True, exist_ok=True)
    for name in ("teacher_md", "student_md", "models", "data", "active_learning"):
        (output / name).mkdir(exist_ok=True)
    profile = json.loads((WORKSPACE / "run_profiles.json").read_text())[args.profile]
    hidden = json.loads((SOLUTION / "run_profiles.json").read_text())[args.profile]
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
        base_seed = resolve_run_seed(args.profile, args.seed, profile)
    except ValueError as exc:
        parser.error(str(exc))
    protocol_seed = resolve_protocol_seed(args.profile, profile, base_seed)

    structure_path, teacher_path = WORKSPACE / "CuInP2S6.cif", WORKSPACE / "teacher_model.pb"
    base = read(structure_path).repeat(profile["supercell"])
    teacher = DP(model=str(teacher_path))
    max_iterations = profile["max_iterations"]

    identity = resolve_run_identity(args.profile, profile, base_seed,
                                    sha256(structure_path), sha256(teacher_path))
    state = load_checkpoint(checkpoint_path, identity) if args.resume else None
    if state is not None and state.get("completed_stage") == "result":
        (output / "result.json").write_text(json.dumps(state["result"], indent=2) + "\n")
        (output / "active_learning" / "history.json").write_text(
            json.dumps(state["result"]["history"], indent=2) + "\n")
        print("resume: result already complete; nothing to do")
        return
    history = list(state.get("history", [])) if state else []
    start_iteration = len(history)
    if state is None:
        state = {"schema_version": CHECKPOINT_SCHEMA_VERSION,
                 "run_identity": dict(identity),
                 "completed_stage": None,
                 "artifact_hashes": {},
                 "history": [],
                 "selected_hashes": []}

    # ---- Teacher-labelled initial set. On resume each committed sub-stage is
    # skipped only when every recorded artifact still matches on disk; nothing
    # is deleted or silently regenerated under --resume.
    dataset_zero = output / "data" / "iteration_0"
    heldout_dir = output / "data" / "heldout"
    if stage_complete(state, output, [dataset_zero]):
        train_frames, train_energy, train_forces, _ = load_dataset(dataset_zero)
        print(f"resume: reloaded teacher initial set ({len(train_frames)} frames)")
    else:
        train_frames, train_energy, train_forces = [], [], []
        for index, temperature in enumerate(profile["teacher_temperatures_K"]):
            path = output / "teacher_md" / f"initial_{temperature}K.traj"
            frames = md_frames(base, teacher, temperature, profile["teacher_md_steps"],
                               protocol_seed + index, path)
            dynamic = frames[1:]
            pick = np.linspace(0, len(dynamic) - 1,
                               profile["initial_frames_per_temperature"], dtype=int)
            train_frames.extend(dynamic[int(frame_index)] for frame_index in pick)
            record_artifact(state, output, path)
        train_energy, train_forces = label(train_frames, teacher)
        write_dataset(dataset_zero, train_frames, train_energy, train_forces)
        record_dir(state, output, dataset_zero)
        state.update({"completed_stage": "teacher_md", "history": [],
                      "iterations_completed": 0})
        write_checkpoint(checkpoint_path, state)
        print(f"teacher MD complete: {len(train_frames)} initial frames")
    if stage_complete(state, output, [heldout_dir]):
        test_frames, test_energy, test_forces, test_hashes = load_dataset(heldout_dir)
        print("resume: reloaded held-out set")
    else:
        test_trajectory = output / "teacher_md" / "heldout_400K.traj"
        test_frames = md_frames(
            base, teacher, profile["heldout_temperature_K"],
            profile["heldout_md_steps"], protocol_seed + 100, test_trajectory,
            interval=int(profile.get("heldout_trajectory_interval", 20)),
        )
        record_artifact(state, output, test_trajectory)
        test_energy, test_forces = label(test_frames, teacher)
        write_dataset(heldout_dir, test_frames, test_energy, test_forces)
        record_dir(state, output, heldout_dir)
        state["completed_stage"] = "heldout_label"
        write_checkpoint(checkpoint_path, state)
    test_frames, test_energy, test_forces, test_hashes = load_dataset(heldout_dir)
    test_meta = {"path": str(heldout_dir), "frames": len(test_frames),
                 "configuration_hashes": test_hashes}
    if start_iteration > 0:
        train_frames, train_energy, train_forces, _ = load_dataset(
            output / "data" / f"iteration_{start_iteration}")

    # ---- Active-distillation loop.
    for iteration in range(start_iteration, max_iterations + 1):
        if iteration == 0:
            train_path = dataset_zero
            train_meta = {"path": str(train_path), "frames": len(train_frames),
                          "configuration_hashes": [configuration_hash(frame) for frame in train_frames]}
        else:
            train_path = output / "data" / f"iteration_{iteration}"
            if stage_complete(state, output, [train_path]):
                train_meta = {"path": str(train_path), "frames": len(train_frames),
                              "configuration_hashes": [configuration_hash(frame) for frame in train_frames]}
            else:
                train_meta = write_dataset(train_path, train_frames, train_energy, train_forces)
                record_dir(state, output, train_path)
                state["completed_stage"] = f"iteration_{iteration}_dataset"
                write_checkpoint(checkpoint_path, state)

        # Each committee member is committed separately, so a crash between
        # members never retrains a committed model under --resume.
        model_paths = []
        for committee in range(2):
            member_dir = output / "models" / f"iteration_{iteration}" / f"member_{committee}"
            if stage_complete(state, output, [member_dir]):
                model_paths.append(member_dir / "student.pb")
                print(f"resume: reused committed committee member {committee}")
            else:
                student_cfg = profile.get("student_training", {})
                model_path = train_student(train_path, output / "data" / "heldout",
                                           training_steps_for_iteration(profile, iteration),
                                           committee_seed(profile, iteration, committee),
                                           member_dir,
                                           model_seeds=student_cfg.get("model_seeds"),
                                           fitting_net=student_cfg.get("fitting_net"),
                                           descriptor_neuron=student_cfg.get("descriptor_neuron"),
                                           axis_neuron=student_cfg.get("axis_neuron"),
                                           decay_steps=student_cfg.get("decay_steps"))
                record_dir(state, output, member_dir)
                model_paths.append(model_path)
                state["completed_stage"] = f"iteration_{iteration}_committee_{committee}"
                write_checkpoint(checkpoint_path, state)
        mae = evaluate_model(model_paths[0], test_frames, test_forces)
        record = {"iteration": iteration, "training_frames": len(train_frames),
                  "heldout_force_mae_eV_A": mae,
                  "train_configuration_hashes": train_meta["configuration_hashes"],
                  "test_configuration_hashes": test_meta["configuration_hashes"],
                  "models": [{"path": str(path.relative_to(output)), "sha256": sha256(path)}
                             for path in model_paths],
                  "selected_frames": 0, "selection_band_eV_A": list(band)}
        history.append(record)
        # Only a retrained iteration may claim convergence: iteration-zero MAE
        # is ordinary supervised training, reported but never a formal stop.
        # may_accept_convergence keeps heldout_force_mae_below_0.10 strictly on
        # the retrained path (completed_active_iterations >= min_active_iterations).
        if iteration == max_iterations:
            record["stop_reason"] = "maximum_active_iterations"
            break
        if may_accept_convergence(mae, iteration, min_active_iterations):
            record["stop_reason"] = "heldout_force_mae_below_0.10"
            break

        # ---- Bounded cumulative exploration across every declared batch. Each
        # trajectory is committed (and, on resume, reused) only when its hash
        # still matches; a committed trajectory is never regenerated.
        exploration_traj_index = [0]

        def _run_batch(batch: dict) -> tuple[list, dict]:
            temperature = int(batch["temperature_K"])
            md_steps = int(batch["md_steps"])
            member = int(batch["driver_member"])
            seed = protocol_seed + int(batch["seed_offset"])
            trajectory = output / "student_md" / (
                f"iteration_{iteration + 1}_{temperature}K_m{member}.traj")
            batch_index = exploration_traj_index[0]
            exploration_traj_index[0] += 1
            if stage_complete(state, output, [trajectory]):
                generated = list(Trajectory(str(trajectory)))
            else:
                generated = md_frames(base, DP(model=str(model_paths[member])), temperature,
                                      md_steps, seed, trajectory,
                                      interval=int(batch["trajectory_interval"]))
                record_artifact(state, output, trajectory)
                state["completed_stage"] = (
                    f"iteration_{iteration}_exploration_traj_{batch_index}")
                write_checkpoint(checkpoint_path, state)
            meta = {"temperature_K": temperature, "md_steps": md_steps,
                    "driver_member": member, "seed_offset": int(batch["seed_offset"]),
                    "path": str(trajectory.relative_to(output)),
                    "sha256": sha256(trajectory),
                    "candidate_frames": len(generated)}
            return generated, meta

        rounds = hidden.get("exploration_rounds")
        declared_batches = (
            rounds[min(iteration, len(rounds) - 1)]
            if rounds else hidden.get("exploration_batches", [])
        )
        exploration = run_exploration(
            declared_batches,
            band=band,
            cap=int(profile["selection_cap"]),
            excluded=set(train_meta["configuration_hashes"]),
            md_callback=_run_batch,
            deviation_callback=lambda frames: deviations(model_paths, frames),
        )
        candidates = exploration["candidates"]
        sigma = exploration["sigma"]
        selected = exploration["selected"]
        record["exploration_attempts"] = len(exploration["exploration_records"])
        record["exploration_frames"] = len(candidates)
        record["exploration_trajectories"] = exploration["exploration_records"]
        record["committee_deviation_eV_A"] = sigma.tolist()
        record["deviation_summary"] = deviation_summary(sigma)
        record["selected_indices"] = selected
        record["selected_frames"] = int(len(selected))
        if exploration["stop_reason"] is not None:
            # Every declared batch was attempted and none produced an in-band
            # frame: fail closed, never fall back to an out-of-band frame.
            record["stop_reason"] = exploration["stop_reason"]
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
        # hashes are exactly the previous hashes plus the selected hashes. This
        # locks exact growth and rejects any duplicate or overlapping selection.
        commit_active_retrain(before_hashes, selected_hashes)
        train_frames.extend(selected_frames)
        train_energy = np.concatenate((train_energy, new_energy))
        train_forces = np.concatenate((train_forces, new_forces))
        record["training_frames_after"] = len(train_frames)
        # Write the retrained dataset now so a crash between cycles is resumable.
        next_path = output / "data" / f"iteration_{iteration + 1}"
        write_dataset(next_path, train_frames, train_energy, train_forces)
        record_dir(state, output, next_path)
        state["selected_hashes"] = list(state.get("selected_hashes", [])) + selected_hashes
        state.update({"history": history, "iterations_completed": iteration + 1,
                      "completed_stage": f"iteration_{iteration}_cycle"})
        write_checkpoint(checkpoint_path, state)

    terminal = history[-1]
    stop_reason = terminal.get("stop_reason")
    exhausted = stop_reason == EXHAUSTED_STOP_REASON
    all_train_hashes = set(terminal["train_configuration_hashes"])
    if all_train_hashes.intersection(test_hashes):
        raise RuntimeError("train/test configuration leakage")
    # Iteration-zero MAE is reporting only — it is ordinary supervised training
    # and never a formal convergence. The formal fields below always reflect the
    # last (retrained) iteration.
    reporting = {
        "iteration_zero_force_mae_eV_A": (
            history[0]["heldout_force_mae_eV_A"] if history else None
        ),
    }
    result = {"schema_version": "1.0", "case": "031", "profile": args.profile,
              "seed": base_seed, "protocol_seed": protocol_seed,
              "run_identity_sha256": identity["run_identity_sha256"],
              "formal_result": formal_result_for(stop_reason, bool(profile["formal_result"])),
              "supercell": profile["supercell"],
              "atom_count": len(base), "teacher_model_sha256": sha256(teacher_path),
              "structure_sha256": sha256(structure_path), "type_map": TYPE_MAP,
              "heldout": test_meta, "reporting": reporting, "history": history,
              "iterations_completed": terminal["iteration"],
              "final_force_mae_eV_A": terminal["heldout_force_mae_eV_A"],
              "stopping_condition": stop_reason}
    if exhausted:
        # Fail closed: every declared batch was attempted with no in-band frame,
        # so this is not a valid formal result. The profile stays "paper" but
        # formal_result is forced to false, the run is explicitly marked invalid
        # and the process exits non-zero.
        result["valid"] = False
        result["invalid_reason"] = EXHAUSTED_STOP_REASON
    (output / "active_learning" / "history.json").write_text(json.dumps(history, indent=2) + "\n")
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    state.update({"result": result, "history": history, "stage": "result"})
    write_checkpoint(checkpoint_path, state)
    if exhausted:
        print(f"run exhausted every declared exploration batch: {stop_reason}", file=sys.stderr)
        print("result.json written with formal_result=false and valid=false", file=sys.stderr)
        sys.exit(2)
    print(f"run complete: {len(history)} iterations, "
          f"stopping_condition={stop_reason}, "
          f"final_force_mae_eV_A={terminal['heldout_force_mae_eV_A']:.4f}")


if __name__ == "__main__":
    main()
