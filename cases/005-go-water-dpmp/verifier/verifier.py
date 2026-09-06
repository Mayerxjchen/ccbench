#!/usr/bin/env python3
"""
042 verifier — V0..V6 outcome-based evaluation (GO–water DPMP, deepmd-jax).

Contract: CONTRACT.md §5. The verifier inspects the agent workspace (submission
root) and the staged hidden set, and returns a structured report.

Layers:
  V0  system/submission identity  (final/ skeleton, manifest, type_map, root)
  V1  data/label provenance       (real CP2K AIMD outputs, traceable labels)
  V2  model authenticity          (deepmd-jax model loadable, infers finite)
  V3  iterative workflow integrity(active-learning explore/select/label/retrain)
  V4  hidden static accuracy      (deepmd-jax inference vs hidden held-out frames)
  V5  hidden dynamic stability    (verifier-run 300 K NVT via deepmd_jax.md)
  V6  hidden physical observable  (water number-density profile vs reference)

Reward = 1 iff V0..V6 all pass. V0-V3 are structural/authenticity gates (hard);
V4-V6 bounds are read from thresholds.json (draft; frozen after expert reruns).

The verifier never inspects anything outside the workspace except the staged
hidden files. Paths come from env with /tests/hidden defaults; dev/smoke runs
point them elsewhere.

deepmd-jax API notes (from the paper's own scripts):
  train: deepmd_jax.train.train(...)   -> saves model.pkl
  test : rmse, pred, gt = deepmd_jax.train.test(model_path, data_path, batch_size)
         pred/gt are dicts with 'energy' (total eV per frame) and 'force'.
  MD   : from deepmd_jax.md import Simulation; sim = Simulation(model_path, box,
         type_idx, mass, routine='NVT', dt, initial_position, temperature, seed,
         ...); traj = sim.run(nsteps) -> dict with 'box','position','velocity'
         (flat arrays: position (nframes, natoms*3), box (nframes, 9)).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Environment / paths
# ---------------------------------------------------------------------------

ENV_HIDDEN_FRAMES = "AI2KIT_042_HIDDEN_FRAMES"
ENV_DENSITY_REF = "AI2KIT_042_DENSITY_REFERENCE"
ENV_THRESHOLDS = "AI2KIT_042_THRESHOLDS"
ENV_NVT_STEPS = "AI2KIT_042_NVT_STEPS"
ENV_NVT_TEMP = "AI2KIT_042_NVT_TEMPERATURE"
ENV_SUBMISSION = "AI2KIT_042_SUBMISSION"
ENV_PROFILE = "AI2KIT_042_PROFILE"

HARTREE_TO_EV = 27.211386245988
KB_EV = 8.617333262145e-5  # eV/K

PUBLISHED_INTERFACES = frozenset({
    "air-water", "graphene-water", "graphene-O12", "graphene-O25", "graphene-O50",
})
# graphene-water MLMD supercell used for the hidden NVT / density reference.
DENSITY_INTERFACE = "graphene-water"


def _default_hidden() -> Path:
    return Path(__file__).resolve().parent / "hidden"


def hidden_frames_path() -> Path:
    return Path(os.environ.get(ENV_HIDDEN_FRAMES, str(_default_hidden() / "hidden-frames")))


def density_reference_path() -> Path:
    return Path(os.environ.get(ENV_DENSITY_REF, str(_default_hidden() / "density-reference.json")))


def thresholds_path() -> Path:
    return Path(os.environ.get(ENV_THRESHOLDS, str(_default_hidden() / "thresholds.json")))


def nvt_steps() -> int:
    return int(os.environ.get(ENV_NVT_STEPS, "5000"))


def nvt_temperature() -> float:
    return float(os.environ.get(ENV_NVT_TEMP, "300"))


def load_thresholds() -> dict:
    p = thresholds_path()
    if not p.is_file():
        raise FileNotFoundError(f"thresholds file missing: {p}")
    return json.loads(p.read_text())


def load_density_reference() -> dict:
    p = density_reference_path()
    if not p.is_file():
        raise FileNotFoundError(f"density reference missing: {p}")
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# Hashes / geometry helpers
# ---------------------------------------------------------------------------

def _coords_hash(pos: np.ndarray, prec: int = 3) -> str:
    return ",".join(f"{float(x):.{prec}f}" for x in np.asarray(pos).reshape(-1))


def frame_match(a_pos: np.ndarray, b_pos: np.ndarray, cell: np.ndarray, tol: float = 2e-3) -> bool:
    if a_pos.shape != b_pos.shape:
        return False
    d = a_pos - b_pos
    d = d - cell * np.round(d / cell)
    return float(np.max(np.linalg.norm(d, axis=1))) < tol


def _wrap(pos: np.ndarray, lengths: np.ndarray) -> np.ndarray:
    """Wrap positions into [0, L) per orthorhombic dimension."""
    return pos - lengths * np.floor(pos / lengths)


def _coords_hash_wrapped(pos: np.ndarray, lengths: np.ndarray, prec: int = 2) -> str:
    return _coords_hash(np.round(_wrap(np.asarray(pos), np.asarray(lengths)), prec))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# CP2K parsers (same contract as 034: pos-*.xyz carries i/time/E header)
# ---------------------------------------------------------------------------

_CP2K_HDR = re.compile(
    r"i\s*=\s*(?P<i>\d+).*?time\s*=\s*(?P<time>[-+0-9.eE]+).*?E\s*=\s*(?P<E>[-+0-9.eE]+)"
)


def parse_cp2k_xyz(path: Path):
    frames = []
    with open(path) as fh:
        lines = fh.readlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        try:
            natoms = int(line)
        except ValueError:
            i += 1
            continue
        if i + 1 >= n:
            break
        m = _CP2K_HDR.search(lines[i + 1])
        atoms = []
        for k in range(natoms):
            p = lines[i + 2 + k].split()
            if len(p) < 4:
                break
            atoms.append((p[0], float(p[1]), float(p[2]), float(p[3])))
        if len(atoms) == natoms:
            frames.append({
                "i": int(m.group("i")) if m else len(frames),
                "time": float(m.group("time")) if m else None,
                "E_Ha": float(m.group("E")) if m else None,
                "atoms": atoms,
                "natoms": natoms,
            })
        i += 2 + natoms
    return frames


def parse_ener(path: Path):
    rows = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                rows.append({
                    "step": int(float(parts[0])),
                    "time_fs": float(parts[1]),
                    "temp_K": float(parts[3]),
                    "pot_Ha": float(parts[4]),
                })
            except ValueError:
                continue
    return rows


def parse_cell(path: Path):
    rows = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) < 10:
                continue
            try:
                rows.append({
                    "step": int(float(parts[0])),
                    "a": math.sqrt(float(parts[2]) ** 2 + float(parts[3]) ** 2 + float(parts[4]) ** 2),
                })
            except ValueError:
                continue
    return rows


def _frame_positions(frame) -> np.ndarray:
    return np.array([a[1:4] for a in frame["atoms"]])


def walk_files(root: Path, exts=None):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            if p.is_symlink() and p.resolve().is_dir():
                continue
        except Exception:
            continue
        if exts and p.suffix.lower() not in exts:
            continue
        yield p


def _sibling(root: Path, needle: str, want_frames=None):
    for p in root.parent.iterdir():
        if needle.lower() in p.name.lower():
            if want_frames is not None:
                try:
                    if len(parse_cp2k_xyz(p)) != want_frames:
                        continue
                except Exception:
                    continue
            return p
    return None


# ---------------------------------------------------------------------------
# L0: structure origin validation (DeepMD raw format)
# ---------------------------------------------------------------------------

REQUIRED_STRUCTURE_FIELDS = frozenset({
    "interface", "structure_path", "generator", "generator_version",
    "command", "seed", "checks", "coord_raw_sha256",
})

ELEMENT_TYPE_MAP = {"O": 0, "H": 1, "C": 2}
_INVERSE_TYPE_MAP = {v: k for k, v in ELEMENT_TYPE_MAP.items()}


def _load_interface_specs() -> dict:
    """Single source of truth: public/system.json (the same document the
    prompt table and the expert generator read).  Duplicating numbers here
    is what let the layers drift apart in the 2026-08-25 audit."""
    data = json.loads(
        (Path(__file__).resolve().parents[1] / "public/system.json").read_text()
    )
    out = {}
    for name, iface in data["interfaces"].items():
        out[name] = {
            "composition": iface["composition"],
            "natoms": iface["natoms"],
            "cell_angstrom": iface["cell_angstrom"],
            "functional_groups": iface.get("functional_groups"),
        }
    return out


INTERFACE_SPECS = _load_interface_specs()

OH_BOND_RANGE = (0.75, 1.25)
HOH_ANGLE_RANGE = (85.0, 125.0)
MIN_INTERMOLECULAR_DIST = 1.2
CELL_TOLERANCE = 0.10


def _safe_submission_path(submission: Path, relative: str) -> Path:
    """Resolve a manifest path without allowing absolute or symlink escape."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("L0: structure path must be submission-relative")
    root = submission.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("L0: structure path escapes submission") from exc
    return candidate


def _read_deepmd_raw(directory: Path) -> tuple[np.ndarray, np.ndarray, list[int]]:
    box_flat = np.loadtxt(directory / "box.raw").flatten()
    if box_flat.size != 9:
        raise ValueError(f"L0: box.raw must contain 9 values, got {box_flat.size}")
    box = box_flat.reshape(3, 3)
    coords = np.loadtxt(directory / "coord.raw").reshape(-1, 3)
    types = [int(t) for t in (directory / "type.raw").read_text().split()]
    return box, coords, types


def _element_counts(types: list[int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in types:
        elem = _INVERSE_TYPE_MAP.get(t, f"unknown_{t}")
        counts[elem] = counts.get(elem, 0) + 1
    return counts


def _classify_functional_groups(coords: np.ndarray, types: list[int],
                                box: np.ndarray) -> dict | None:
    """Classify GO oxygen atoms as hydroxyl or epoxide.

    Heuristic: within each functional group, the O atom has 1 (hydroxyl)
    or 2 (epoxide) carbon neighbors within 1.8 Å (min-image distance).
    """
    box_diag = np.diag(box)
    c_idx = np.array([i for i, t in enumerate(types) if t == ELEMENT_TYPE_MAP["C"]])
    o_idx = np.array([i for i, t in enumerate(types) if t == ELEMENT_TYPE_MAP["O"]])
    h_idx = np.array([i for i, t in enumerate(types) if t == ELEMENT_TYPE_MAP["H"]])

    if len(c_idx) == 0 or len(o_idx) == 0:
        return None

    # Find water O atoms: O with an H within 1.3 Å AND no C within 1.8 Å
    # (hydroxyl O also has nearby H, but is bonded to C)
    water_o = set()
    if len(h_idx) > 0:
        o_pos = coords[o_idx]  # (nO, 3)
        h_pos = coords[h_idx]  # (nH, 3)
        c_pos = coords[c_idx]  # (nC, 3)
        # O-H distances
        delta_oh = o_pos[:, np.newaxis, :] - h_pos[np.newaxis, :, :]
        delta_oh = _min_image_diagonal(delta_oh, box_diag)
        oh_dists = np.linalg.norm(delta_oh, axis=2)  # (nO, nH)
        has_close_h = np.any(oh_dists < 1.3, axis=1)
        # O-C distances
        delta_oc = o_pos[:, np.newaxis, :] - c_pos[np.newaxis, :, :]
        delta_oc = _min_image_diagonal(delta_oc, box_diag)
        oc_dists = np.linalg.norm(delta_oc, axis=2)  # (nO, nC)
        has_close_c = np.any(oc_dists < 2.0, axis=1)
        # Water O: has close H but NO close C
        is_water = has_close_h & ~has_close_c
        water_o = set(o_idx[is_water].tolist())

    # Remaining O atoms are functional group O
    fg_o = [int(oi) for oi in o_idx if int(oi) not in water_o]
    if not fg_o:
        return {"hydroxyl": 0, "epoxide": 0}

    hydroxyl = 0
    epoxide = 0
    fg_pos = coords[fg_o]  # (nFgO, 3)
    c_pos = coords[c_idx]  # (nC, 3)
    # delta[i,j] = fg_pos[i] - c_pos[j], shape (nFgO, nC)
    delta = fg_pos[:, np.newaxis, :] - c_pos[np.newaxis, :, :]
    delta = _min_image_diagonal(delta, box_diag)
    fg_c_dists = np.linalg.norm(delta, axis=2)  # (nFgO, nC)
    # 2.0 Å threshold: epoxide O at 1.47 Å above C-C midpoint → C-O dist ~1.92 Å
    c_neighbor_counts = np.sum(fg_c_dists < 2.0, axis=1)
    hydroxyl = int(np.sum(c_neighbor_counts < 2))
    epoxide = int(np.sum(c_neighbor_counts >= 2))

    return {"hydroxyl": hydroxyl, "epoxide": epoxide}


def _min_image_diagonal(delta: np.ndarray, box_diag: np.ndarray) -> np.ndarray:
    """Fast minimum-image for orthorhombic (diagonal) box."""
    return delta - box_diag * np.round(delta / box_diag)


def _water_geometry_diagnostics(coords: np.ndarray, types: list[int],
                                box: np.ndarray) -> dict:
    """Compute water geometry diagnostics (O–H bonds, H–O–H angles, min distance).

    Optimized for diagonal boxes: uses vectorized operations where possible
    and avoids O(n²) full-atom loops.
    """
    box_diag = np.diag(box)
    o_idx = np.array([i for i, t in enumerate(types) if t == ELEMENT_TYPE_MAP["O"]])
    h_idx = np.array([i for i, t in enumerate(types) if t == ELEMENT_TYPE_MAP["H"]])

    if len(o_idx) == 0 or len(h_idx) == 0:
        return {
            "OH_bond_min_A": None, "OH_bond_max_A": None,
            "HOH_angle_min_degree": None, "HOH_angle_max_degree": None,
            "min_intermolecular_distance_A": float("inf"),
        }

    # Vectorized H→O assignment: compute all H-O distances at once
    # coords[h_idx] shape: (nH, 3), coords[o_idx] shape: (nO, 3)
    h_pos = coords[h_idx]  # (nH, 3)
    o_pos = coords[o_idx]  # (nO, 3)
    # delta[i,j] = h_pos[i] - o_pos[j], shape (nH, nO)
    delta = h_pos[:, np.newaxis, :] - o_pos[np.newaxis, :, :]
    delta = _min_image_diagonal(delta, box_diag)
    dists = np.linalg.norm(delta, axis=2)  # (nH, nO)
    nearest_o = np.argmin(dists, axis=1)  # (nH,)
    oh_dists = dists[np.arange(len(h_idx)), nearest_o]  # (nH,)

    # Build assigned dict: O index → list of (H global index, distance)
    assigned: dict[int, list[tuple[int, float]]] = {}
    molecule_of: dict[int, int] = {}
    for k, hi in enumerate(h_idx):
        oi = int(o_idx[nearest_o[k]])
        assigned.setdefault(oi, []).append((int(hi), float(oh_dists[k])))
        molecule_of[int(hi)] = oi

    oh_bonds = list(oh_dists)
    hoh_angles = []
    for oi, hydrogens in assigned.items():
        if len(hydrogens) >= 2:
            v1 = coords[hydrogens[0][0]] - coords[oi]
            v1 = _min_image_diagonal(v1, box_diag)
            v2 = coords[hydrogens[1][0]] - coords[oi]
            v2 = _min_image_diagonal(v2, box_diag)
            cos_a = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-12))
            cos_a = max(-1.0, min(1.0, cos_a))
            hoh_angles.append(math.degrees(math.acos(cos_a)))

    # Minimum intermolecular distance — O-O between water molecules only
    # Water O: has exactly 2 assigned H AND no C within 2.0 Å
    c_idx = np.array([i for i, t in enumerate(types) if t == ELEMENT_TYPE_MAP["C"]])
    water_o_indices = []
    for oi, hydrogens in assigned.items():
        if len(hydrogens) != 2:
            continue
        if len(c_idx) > 0:
            oc_delta = coords[oi] - coords[c_idx]
            oc_delta = _min_image_diagonal(oc_delta, box_diag)
            oc_dists = np.linalg.norm(oc_delta, axis=1)
            if np.any(oc_dists < 2.0):
                continue  # hydroxyl O, not water
        water_o_indices.append(oi)

    dmin_inter = float("inf")
    if len(water_o_indices) >= 2:
        w_pos = coords[water_o_indices]  # (nWater, 3)
        # pairwise between water O atoms
        w_delta = w_pos[:, np.newaxis, :] - w_pos[np.newaxis, :, :]
        w_delta = _min_image_diagonal(w_delta, box_diag)
        w_dists = np.linalg.norm(w_delta, axis=2)
        # mask diagonal (self-distance = 0)
        np.fill_diagonal(w_dists, float("inf"))
        dmin_inter = float(np.min(w_dists))

    return {
        "OH_bond_min_A": float(np.min(oh_bonds)) if len(oh_bonds) else None,
        "OH_bond_max_A": float(np.max(oh_bonds)) if len(oh_bonds) else None,
        "HOH_angle_min_degree": min(hoh_angles) if hoh_angles else None,
        "HOH_angle_max_degree": max(hoh_angles) if hoh_angles else None,
        "min_intermolecular_distance_A": dmin_inter,
    }


def check_structure_origin(submission: Path, manifest: dict):
    """Validate structure provenance and geometry for Case 042.

    Returns (ok, errors, diag) where ok is True iff all checks pass.
    """
    record = manifest.get("structure_generation")
    if not isinstance(record, dict):
        return False, ["L0: structure_generation record missing"], {}

    missing = sorted(REQUIRED_STRUCTURE_FIELDS - set(record))
    if missing:
        return False, [f"L0: missing structure fields: {missing}"], {}

    iface = record.get("interface", "")
    if iface not in INTERFACE_SPECS:
        return False, [f"L0: unknown interface '{iface}'"], {}

    spec = INTERFACE_SPECS[iface]
    errors = []

    # Resolve structure path safely
    try:
        struct_dir = _safe_submission_path(submission, record["structure_path"])
    except ValueError as exc:
        return False, [str(exc)], {}

    # Check required DeepMD raw files
    for fname in ("box.raw", "coord.raw", "type.raw"):
        if not (struct_dir / fname).is_file():
            errors.append(f"L0: missing {fname} in {record['structure_path']}")

    if errors:
        return False, errors, {}

    box, coords, types = _read_deepmd_raw(struct_dir)

    # SHA-256 provenance
    coord_bytes = (struct_dir / "coord.raw").read_bytes()
    actual_sha = hashlib.sha256(coord_bytes).hexdigest()
    if record["coord_raw_sha256"] != actual_sha:
        errors.append("L0: recorded coord_raw SHA-256 does not match actual bytes")

    # Atom count
    natoms = len(types)
    if natoms != spec["natoms"]:
        errors.append(f"L0: natoms {natoms} != {spec['natoms']}")

    # Composition
    ec = _element_counts(types)
    for elem, count in spec["composition"].items():
        if ec.get(elem, 0) != count:
            errors.append(
                f"L0: {elem} count {ec.get(elem, 0)} != {count}")

    # Cell dimensions
    cell_diag = [float(box[i, i]) for i in range(3)]
    for i, axis in enumerate("xyz"):
        if abs(cell_diag[i] - spec["cell_angstrom"][i]) > CELL_TOLERANCE:
            errors.append(
                f"L0: cell[{axis}] {cell_diag[i]:.3f} "
                f"!= {spec['cell_angstrom'][i]:.3f}")

    # Functional groups (GO interfaces only) — check presence, not exact counts
    # Exact FG counts are validated at V0 via manifest; L0 only checks structure sanity
    fg_expected = spec.get("functional_groups")
    fg_diag = None
    if fg_expected is not None:
        fg_diag = _classify_functional_groups(coords, types, box)
        if fg_diag is not None:
            # GO interfaces must have both hydroxyl and epoxide groups
            if fg_diag.get("hydroxyl", 0) == 0 and fg_diag.get("epoxide", 0) == 0:
                errors.append("L0: GO interface has no classified functional groups")

    # Water geometry diagnostics (for interfaces with water)
    geom = _water_geometry_diagnostics(coords, types, box)

    if geom["OH_bond_min_A"] is not None:
        lo, hi = OH_BOND_RANGE
        if geom["OH_bond_min_A"] < lo or geom["OH_bond_max_A"] > hi:
            errors.append("L0: O–H bond lengths outside allowed range")

    if geom["HOH_angle_min_degree"] is not None:
        lo, hi = HOH_ANGLE_RANGE
        if geom["HOH_angle_min_degree"] < lo or geom["HOH_angle_max_degree"] > hi:
            errors.append("L0: H–O–H angles outside allowed range")

    if geom["min_intermolecular_distance_A"] < MIN_INTERMOLECULAR_DIST:
        errors.append("L0: intermolecular contact below allowed floor")

    # checks must be non-empty dict
    if not isinstance(record.get("checks"), dict) or not record["checks"]:
        errors.append("L0: structure-generation checks must be a non-empty object")

    diag = {
        "interface": iface,
        "natoms": natoms,
        "element_counts": ec,
        "cell_angstrom": cell_diag,
        "functional_groups": fg_diag,
        "OH_bond_min_A": geom["OH_bond_min_A"],
        "OH_bond_max_A": geom["OH_bond_max_A"],
        "HOH_angle_min_degree": geom["HOH_angle_min_degree"],
        "HOH_angle_max_degree": geom["HOH_angle_max_degree"],
        "min_intermolecular_distance_A": geom["min_intermolecular_distance_A"],
        "sha256": actual_sha,
    }
    return not errors, errors, diag


def find_all_cp2k_pos(root: Path):
    out = []
    for p in walk_files(root, exts={".xyz"}):
        if "pos" not in p.name.lower():
            continue
        try:
            frames = parse_cp2k_xyz(p)
        except Exception:
            continue
        if not frames or frames[0]["E_Ha"] is None:
            continue
        out.append({"path": p, "pos_frames": frames})
    out.sort(key=lambda r: len(r["pos_frames"]), reverse=True)
    return out


def find_cp2k_aimd(root: Path, min_frames: int = 1):
    """Primary AIMD trajectory: largest pos trajectory with matching frc sibling
    AND (ener or cell) sibling — the signature of a CP2K MD run."""
    candidates = find_all_cp2k_pos(root)
    if not candidates:
        return None
    for cand in candidates:
        n = len(cand["pos_frames"])
        if n < min_frames:
            continue
        parent = cand["path"].parent
        frc = _sibling(cand["path"], "frc", want_frames=n)
        ener = next((x for x in parent.iterdir() if "ener" in x.name.lower()), None)
        cell = next((x for x in parent.iterdir() if "cell" in x.name.lower()), None)
        if frc is not None and (ener is not None or cell is not None):
            return {
                "pos_path": cand["path"],
                "pos_frames": cand["pos_frames"],
                "frc_path": frc,
                "frc_frames": parse_cp2k_xyz(frc),
                "ener_path": ener,
                "ener_rows": parse_ener(ener) if ener else [],
                "cell_path": cell,
                "cell_rows": parse_cell(cell) if cell else [],
            }
    cand = candidates[0]
    parent = cand["path"].parent
    ener = next((x for x in parent.iterdir() if "ener" in x.name.lower()), None)
    cell = next((x for x in parent.iterdir() if "cell" in x.name.lower()), None)
    return {
        "pos_path": cand["path"],
        "pos_frames": cand["pos_frames"],
        "frc_path": _sibling(cand["path"], "frc", want_frames=len(cand["pos_frames"])),
        "frc_frames": parse_cp2k_xyz(_sibling(cand["path"], "frc", want_frames=len(cand["pos_frames"])))
        if _sibling(cand["path"], "frc", want_frames=len(cand["pos_frames"])) else [],
        "ener_path": ener,
        "ener_rows": parse_ener(ener) if ener else [],
        "cell_path": cell,
        "cell_rows": parse_cell(cell) if cell else [],
    }


def find_training_sets(root: Path):
    """DeepMD system set dirs (set.NNN/coord.npy + sibling type.raw)."""
    sets = []
    for p in root.rglob("set.*"):
        coord = p / "coord.npy"
        if not coord.is_file() or not (p.parent / "type.raw").is_file():
            continue
        try:
            c = np.load(str(coord))
            nframes = int(c.shape[0])
        except Exception:
            continue
        if nframes == 0:
            continue
        sets.append({"root": str(p), "frames": nframes, "path": str(p)})
    return sets


def find_model_files(root: Path, manifest: dict):
    files = []
    for rel in manifest.get("model_files", []):
        if not isinstance(rel, str):
            continue
        for cand in (root / rel, root / "final" / rel):
            if cand.is_file():
                files.append(cand)
                break
    return files


def find_al_artifacts(root: Path):
    """Generic evidence of model-driven acquisition + training rounds.
    The final/ contract subtree (the delivered model) is NOT a training round."""
    found = {"acquisition": [], "rounds": []}
    for p in walk_files(root):
        name = p.name.lower()
        rel = str(p.relative_to(root)).lower()
        if "/final/" in "/" + rel:
            continue
        if ("model_devi" in name or name.endswith(".lammpstrj") or name.endswith(".dump")
                or "screening" in rel or "model-devi" in rel or "deviation" in rel):
            found["acquisition"].append(str(p))
        if name.endswith(".pkl") and p.stat().st_size > 1000:
            found["rounds"].append(str(p.parent))
        if name in ("input.json", "lcurve.out", "train.log", "checkpoint"):
            parent = p.parent
            has_model = any(parent.glob("*.pkl")) or (parent / "lcurve.out").is_file() \
                or (parent / "train.log").is_file() or (parent / "checkpoint").is_file()
            if has_model and name == "input.json":
                found["rounds"].append(str(parent))
    # a committee of N model dirs under one round dir is ONE training round
    unique = {}
    for rdir in found["rounds"]:
        unique.setdefault(str(Path(rdir).parent), []).append(rdir)
    found["rounds"] = [dirs[0] for dirs in unique.values()]
    for p in walk_files(root):
        if p.stat().st_size > 200:
            try:
                with open(p, errors="ignore") as fh:
                    first = fh.readline()
                if first.startswith("ITEM: TIMESTEP"):
                    found["acquisition"].append(str(p))
            except Exception:
                pass
    found["rounds"] = sorted(set(found["rounds"]))
    found["acquisition"] = sorted(set(found["acquisition"]))
    return found


def find_geopt_evidence(root: Path):
    for p in walk_files(root, exts={".out", ".log", ".txt", ".restart"}):
        try:
            txt = p.read_text(errors="ignore")
            if "GEO_OPT" in txt and ("CONVERGED" in txt or "OPTIMIZATION COMPLETED" in txt or "BFGS" in txt):
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# deepmd-jax helpers (cached)
# ---------------------------------------------------------------------------

_MODEL_CACHE = {}
_V4_CACHE = {}
_NVT_CACHE = {}


def _model_cache_key(path: Path):
    try:
        st = path.stat()
        return (str(path), st.st_size, st.st_mtime_ns)
    except Exception:
        return (str(path), None, None)


def _has_deepmd_jax() -> bool:
    try:
        import deepmd_jax  # noqa: F401
        return True
    except Exception:
        return False


def _make_small_set(src_dir: Path, out_dir: Path, n: int, seed: int = 0) -> Path:
    """Copy n frames from a hidden DeepMD raw dir into out_dir (for probes)."""
    import numpy as _np
    setdir = src_dir / "set.000"
    out = Path(out_dir)
    (out / "set.000").mkdir(parents=True, exist_ok=True)
    coord = _np.load(str(setdir / "coord.npy"))
    idx = _np.random.RandomState(seed).choice(coord.shape[0], size=min(n, coord.shape[0]), replace=False)
    idx = _np.sort(idx)
    for name in ("box", "coord", "energy", "force"):
        arr = _np.load(str(setdir / f"{name}.npy"))
        _np.save(str(out / "set.000" / f"{name}.npy"), arr[idx])
    shutil.copyfile(src_dir / "type.raw", out / "type.raw")
    shutil.copyfile(src_dir / "type_map.raw", out / "type_map.raw")
    return out


def probe_model(path: Path):
    """Load a deepmd-jax model by running deepmd_jax.train.test on a small set.
    Returns (ok, type_map_or_note). Raises on hard failure."""
    key = _model_cache_key(path)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    result = (False, "deepmd-jax unavailable")
    if _has_deepmd_jax():
        from deepmd_jax.train import test
        hf = hidden_frames_path()
        probe = None
        try:
            tmp = Path(tempfile.mkdtemp(prefix="042-probe-"))
            probe = _make_small_set(hf / DENSITY_INTERFACE, tmp, 5)
            rmse, pred, gt = test(model_path=str(path), data_path=str(probe), batch_size=4)
            e = np.asarray(pred["energy"]).reshape(-1)
            f = np.asarray(pred["force"])
            if e.size and np.all(np.isfinite(e)) and np.all(np.isfinite(f)):
                tm = (probe / "type_map.raw").read_text().split()
                result = (True, tm)
            else:
                result = (False, "non-finite probe inference")
        except Exception as ex:
            result = (False, f"{type(ex).__name__}: {ex}")
        finally:
            if probe is not None and probe != path:
                shutil.rmtree(Path(probe).parent if probe else tmp, ignore_errors=True)
    _MODEL_CACHE[key] = result
    return result


# ---------------------------------------------------------------------------
# V4 hidden static accuracy via deepmd_jax.train.test
# ---------------------------------------------------------------------------

def hidden_interface_dirs() -> list[Path]:
    hf = hidden_frames_path()
    if not hf.is_dir():
        return []
    return sorted(p for p in hf.iterdir() if p.is_dir() and (p / "set.000").is_dir())


def aggregate_v4(per_system: list[dict]) -> dict:
    """Aggregate per-system hidden E/F results into verifier diagnostics.

    Hidden systems have DIFFERENT atom counts, so force blocks are never
    concatenated across systems; metrics are computed per system (energy
    aligned per atom, then equal-weight mean across systems).
    """
    sys_metrics = []
    n_nonfinite = 0
    total_frames = 0
    for s in per_system:
        nat = s["natoms"]
        e_diff = (s["e_pred"] - s["e_gt"]) / nat
        e_aligned = e_diff - np.nanmean(e_diff)
        e_rmse = float(np.sqrt(np.nanmean(e_aligned ** 2)))
        f_rmse = float(np.sqrt(np.nanmean((s["f_pred"] - s["f_gt"]) ** 2)))
        bad = int(np.sum(~np.isfinite(s["f_pred"]) | ~np.isfinite(s["f_gt"])))
        n_nonfinite += bad
        total_frames += s["frames"]
        sys_metrics.append({
            "system": s["name"], "frames": s["frames"],
            "natoms": nat,
            "energy_rmse_eV_per_atom_aligned": round(e_rmse, 6),
            "force_rmse_eV_A": round(f_rmse, 6),
            "nonfinite_force_entries": bad,
        })
    return {
        "frames": total_frames,
        "n_systems": len(sys_metrics),
        "per_system": sys_metrics,
        "energy_rmse_eV_per_atom_aligned": round(float(np.mean(
            [m["energy_rmse_eV_per_atom_aligned"] for m in sys_metrics])), 6),
        "force_rmse_eV_A": round(float(np.mean(
            [m["force_rmse_eV_A"] for m in sys_metrics])), 6),
        "nonfinite_frames": n_nonfinite,
    }


def run_hidden_ef(model: Path):
    """Run deepmd-jax test() over every hidden interface set.

    Hidden systems have DIFFERENT atom counts, so force blocks are never
    concatenated across systems; metrics are computed per system via
    :func:`aggregate_v4` (equal system weights).  Returns
    (per_system_list, error) where each entry carries raw arrays for its own
    system only.
    """
    from deepmd_jax.train import test
    per_system = []
    for hdir in hidden_interface_dirs():
        rmse, pred, gt = test(model_path=str(model), data_path=str(hdir), batch_size=8)
        e_pred = np.asarray(pred["energy"]).reshape(-1)
        e_gt = np.asarray(gt["energy"]).reshape(-1)
        f_pred = np.asarray(pred["force"]).reshape(len(e_pred), -1)
        f_gt = np.asarray(gt["force"]).reshape(len(e_gt), -1)
        typ = np.loadtxt(hdir / "type.raw", dtype=int).reshape(-1)
        per_system.append({
            "name": hdir.name,
            "frames": int(e_pred.size),
            "natoms": int(len(typ)),
            "e_pred": e_pred,
            "e_gt": e_gt,
            "f_pred": f_pred,
            "f_gt": f_gt,
        })
    if not per_system:
        return None, "no hidden interface dirs"
    return per_system, None


def _check_V4(ctx):
    errs, diag = [], {}
    models = ctx["model_files"]
    if not models:
        return False, ["V4: no model to evaluate"], diag
    prim = models[0]
    key = _model_cache_key(prim)
    if key in _V4_CACHE:
        diag.update(_V4_CACHE[key])
    else:
        out = {"frames": 0}
        if not _has_deepmd_jax():
            out["error"] = "deepmd-jax unavailable"
        else:
            try:
                per_system, err = run_hidden_ef(prim)
                if err:
                    out["error"] = err
                elif not per_system:
                    out["error"] = "no hidden frames evaluated"
                else:
                    out.update(aggregate_v4(per_system))
            except Exception as ex:
                out["error"] = f"{type(ex).__name__}: {ex}"
            except Exception as ex:
                out["error"] = f"{type(ex).__name__}: {ex}"
        _V4_CACHE[key] = out
        diag.update(out)
    if diag.get("error"):
        errs.append(f"V4: hidden E/F evaluation failed: {diag['error']}")
        return False, errs, diag
    thr = ctx["thresholds"]["levels"]["V4"]
    nonfinite_frac = diag.get("nonfinite_frames", 0) / max(1, diag.get("frames", 1))
    if nonfinite_frac > 0.01:
        errs.append(f"V4: {diag['nonfinite_frames']}/{diag['frames']} frames non-finite")
        return False, errs, diag
    e_max = thr.get("energy_rmse_eV_per_atom_max", 0.02)
    f_max = thr.get("force_rmse_eV_A_max", 0.15)
    if diag["energy_rmse_eV_per_atom_aligned"] > e_max:
        errs.append(f"V4: hidden energy RMSE {diag['energy_rmse_eV_per_atom_aligned']:.5f} > {e_max}")
    if diag["force_rmse_eV_A"] > f_max:
        errs.append(f"V4: hidden force RMSE {diag['force_rmse_eV_A']:.4f} > {f_max}")
    ctx["v4_metrics"] = dict(diag)
    return not errs, errs, diag


# ---------------------------------------------------------------------------
# V5 hidden NVT + V6 density profile (deepmd_jax.md.Simulation)
# ---------------------------------------------------------------------------

def run_hidden_nvt(model: Path, steps: int, temp: float, seed: int = 42):
    """Run a short NVT with the primary model from hidden graphene-water frame 0.
    Returns dict with 'frames' ({step,natoms,positions,symbols}), 'temps',
    'diag', 'error'."""
    hf = hidden_frames_path() / DENSITY_INTERFACE
    if not (hf / "set.000").is_dir():
        return {"frames": [], "temps": [], "diag": {}, "error": f"hidden interface {DENSITY_INTERFACE} missing"}
    if not _has_deepmd_jax():
        return {"frames": [], "temps": [], "diag": {}, "error": "deepmd-jax unavailable"}
    try:
        import numpy as np
        from deepmd_jax.md import Simulation
    except Exception as ex:
        return {"frames": [], "temps": [], "diag": {}, "error": f"import: {ex}"}
    coord = np.load(str(hf / "set.000" / "coord.npy"))
    box = np.load(str(hf / "set.000" / "box.npy"))
    typ = np.loadtxt(hf / "type.raw", dtype=int).reshape(-1)
    natoms = int(len(typ))
    box3 = box[0].reshape(3, 3)
    pos0 = coord[0].reshape(-1, 3)
    masses = [15.999, 1.008, 12.011]
    dt_fs = 0.5
    report_interval = max(1, steps // 200)
    try:
        sim = Simulation(
            model_path=str(model),
            box=box3,
            type_idx=typ,
            mass=masses,
            routine="NVT",
            dt=dt_fs,
            initial_position=pos0,
            initial_velocity=None,
            report_interval=report_interval,
            temperature=temp,
            pressure=None,
            debug=False,
            seed=seed,
            model_deviation_paths=[],
            use_neighbor_list_when_possible=True,
            neighbor_skin=0.3,
            neighbor_buffer_ratio=1.2,
            tau_t=1000,
            tau_p=100,
        )
        traj = sim.run(int(steps))
    except Exception as ex:
        return {"frames": [], "temps": [], "diag": {}, "error": f"Simulation failed: {type(ex).__name__}: {ex}"}

    pos = np.asarray(traj.get("position"))
    vel = np.asarray(traj.get("velocity"))
    if pos.ndim == 3:
        pos = pos.reshape(pos.shape[0], -1)
    nf = pos.shape[0]
    pos3 = pos.reshape(nf, natoms, 3)
    frames = []
    for i in range(nf):
        frames.append({"step": i, "natoms": natoms, "positions": pos3[i], "symbols": None})
    temps = []
    if vel is not None and vel.size:
        if vel.ndim == 3:
            vel = vel.reshape(vel.shape[0], -1)
        vel3 = vel.reshape(nf, natoms, 3)
        m_atom = np.asarray([masses[t] for t in typ], dtype=float)
        for i in range(nf):
            ekin = 0.5 * np.sum(m_atom * np.sum(vel3[i] ** 2, axis=1))
            temps.append(float(ekin / (1.5 * natoms * KB_EV)))
    elif pos.shape[0] == steps + 1:
        # stored every step -> instantaneous T from displacement / dt
        m_atom = np.asarray([masses[t] for t in typ], dtype=float)
        for i in range(1, pos.shape[0]):
            v = (pos3[i] - pos3[i - 1]) / (dt_fs * 1e-3 / 1.0) * 0.0  # placeholder guard
            # velocity in A/fs -> m/s? compute T from v in A/fs directly is wrong;
            # guard below: only used if velocity missing AND full storage.
            # T = 2*Ekin/(3NkB); Ekin = 0.5*m[v_ms]^2; v_ms = v_afs*1e5.
            v_ms = v * 1e5
            ekin = 0.5 * np.sum(m_atom * np.sum(v_ms ** 2, axis=1))
            temps.append(float(ekin / (1.5 * natoms * KB_EV)))
    # temperature via traj key if present
    if not temps and "temperature" in traj:
        temps = [float(x) for x in np.asarray(traj["temperature"]).reshape(-1)]
    # atom types for symbol mapping (stable order from type.raw)
    symbols = np.array(["X"] * natoms)
    for t in (0, 1, 2):
        symbols[typ == t] = ["O", "H", "C"][t]
    for f in frames:
        f["symbols"] = symbols
    return {"frames": frames, "temps": temps,
            "diag": {"nvt_frames": nf, "report_interval": report_interval, "steps": steps},
            "error": None}


def compute_density_profile(positions_list, type_idx, boxes, plane_z_list, lz, bin_width=0.1):
    """Oxygen number-density (nm^-3) vs minimum-image distance from the graphite
    plane, averaged over frames. Same algorithm as the hidden generator."""
    o_mask = type_idx == 0
    hist = None
    bins = np.arange(0.0, lz / 2.0 + bin_width, bin_width)
    hist = np.zeros(len(bins) - 1)
    n_valid = 0
    area = None
    for pos, box, plane_z in zip(positions_list, boxes, plane_z_list):
        p = np.asarray(pos).reshape(-1, 3)
        o_zs = p[o_mask, 2]
        dz = o_zs - plane_z
        dz = dz - lz * np.round(dz / lz)
        dist = np.abs(dz)
        h, _ = np.histogram(dist, bins=bins)
        hist += h
        n_valid += 1
        if area is None:
            b = np.asarray(box).reshape(3, 3)
            area = float(b[0, 0] * b[1, 1])
    if n_valid == 0 or area is None:
        return None
    vol_per_bin = n_valid * area * bin_width
    density = hist / vol_per_bin * 1e3  # nm^-3
    centers = 0.5 * (bins[:-1] + bins[1:])
    sm = np.convolve(density, np.ones(5) / 5.0, mode="same")
    start = int(1.0 / bin_width)
    peak_bin = None
    for i in range(start + 1, len(sm) - 1):
        if sm[i - 1] < sm[i] >= sm[i + 1] and sm[i] > 0.0:
            peak_bin = i
            break
    first_peak = float(centers[peak_bin]) if peak_bin is not None else float("nan")
    depletion = float("nan")
    if peak_bin is not None:
        peak_val = sm[peak_bin]
        for i in range(0, peak_bin + 1):
            if sm[i] >= 0.25 * peak_val:
                depletion = float(centers[i])
                break
    near_plane_max = float(np.max(sm[: int(0.7 / bin_width)])) if int(0.7 / bin_width) < len(sm) else 0.0
    return {
        "first_peak_angstrom": first_peak,
        "depletion_width_angstrom": depletion,
        "near_plane_max_density_nm3": near_plane_max,
        "peak_density_nm3": float(sm[peak_bin]) if peak_bin is not None else float("nan"),
    }


def _check_V5(ctx):
    errs, diag = [], {}
    prim = ctx.get("primary_model_path")
    if not prim:
        return False, ["V5: no loadable model"], diag
    thr = ctx["thresholds"]["levels"]["V5"]
    steps = nvt_steps()
    temp = nvt_temperature()
    key = (_model_cache_key(prim), steps, temp)
    if key in _NVT_CACHE:
        out = _NVT_CACHE[key]
    else:
        out = run_hidden_nvt(prim, steps, temp)
        _NVT_CACHE[key] = out
    diag.update(out["diag"])
    ok = True
    if out.get("error"):
        errs.append(f"V5: hidden NVT failed: {out['error']}")
        return False, errs, diag
    if not out["frames"]:
        errs.append("V5: hidden NVT produced no trajectory frames")
        return False, errs, diag
    frames = out["frames"]
    temps = out["temps"]
    diag["nvt_frames"] = len(frames)
    diag["temp_mean_K"] = round(float(np.mean(temps)), 1) if temps else None
    diag["temp_std_K"] = round(float(np.std(temps)), 1) if len(temps) > 1 else None
    min_frames = thr.get("min_steps", 20)
    if len(frames) < min_frames:
        errs.append(f"V5: only {len(frames)} NVT frames (< {min_frames})")
        ok = False
    if thr.get("no_nan"):
        for f in frames:
            if not np.all(np.isfinite(f["positions"])):
                errs.append(f"V5: non-finite positions in NVT frame {f['step']}")
                ok = False
                break
    if thr.get("no_lost_atoms"):
        for f in frames:
            if f["natoms"] != ctx.get("expected_natoms", f["natoms"]):
                errs.append(f"V5: lost atoms at frame {f['step']}")
                ok = False
                break
    if temps:
        band = thr.get("mean_temp_band_K", [250, 350])
        if not (band[0] <= float(np.mean(temps)) <= band[1]):
            errs.append(f"V5: NVT mean temperature {np.mean(temps):.0f} K outside {band}")
            ok = False
    elif thr.get("require_temperature", True):
        errs.append("V5: could not compute temperature from NVT")
        ok = False
    ctx["nvt_result"] = out
    return ok, errs, diag


def _check_V6(ctx):
    errs, diag = [], {}
    nvt = ctx.get("nvt_result")
    if not nvt or not nvt.get("frames"):
        return False, errs, {"note": "V5 NVT unavailable"}
    thr = ctx["thresholds"]["levels"]["V6"]
    ref = ctx["density_reference"]
    hf = hidden_frames_path() / DENSITY_INTERFACE
    typ = np.loadtxt(hf / "type.raw", dtype=int).reshape(-1)
    boxes = np.load(str(hf / "set.000" / "box.npy"))
    lz = float(boxes[0].reshape(3, 3)[2, 2])
    positions_list, plane_list = [], []
    c_mask = typ == 2
    for f in nvt["frames"]:
        p = f["positions"]
        positions_list.append(p)
        plane_list.append(float(np.mean(p[c_mask, 2] % lz)))
    prof = compute_density_profile(positions_list, typ, [boxes[0]] * len(positions_list), plane_list, lz)
    if prof is None:
        errs.append("V6: could not compute density profile")
        return False, errs, diag
    diag.update({f"density_{k}": v for k, v in prof.items()})
    ok = True
    first_peak = prof["first_peak_angstrom"]
    lo, hi = thr.get("first_peak_window_angstrom", [1.0, 2.2])
    if not (math.isfinite(first_peak) and lo <= first_peak <= hi):
        errs.append(f"V6: first hydration-layer peak {first_peak:.2f} A outside [{lo}, {hi}]")
        ok = False
    depletion = prof["depletion_width_angstrom"]
    if ref.get("depletion_width_angstrom") and math.isfinite(depletion):
        r_lo, r_hi = thr.get("depletion_width_window_angstrom", [0.8, 2.0])
        if not (r_lo <= depletion <= r_hi):
            errs.append(f"V6: depletion width {depletion:.2f} A outside [{r_lo}, {r_hi}]")
            ok = False
    if thr.get("no_unphysical_near_plane"):
        peak_d = prof.get("peak_density_nm3") or 1.0
        frac = prof["near_plane_max_density_nm3"] / max(peak_d, 1e-9)
        diag["near_plane_density_frac_of_peak"] = round(frac, 3)
        if frac > thr.get("near_plane_max_frac_of_peak", 0.10):
            errs.append(f"V6: water density spikes near the graphite plane "
                        f"({prof['near_plane_max_density_nm3']:.1f} nm^-3, "
                        f"{frac:.2f} of peak) — unphysical layering")
            ok = False
    return ok, errs, diag


# ---------------------------------------------------------------------------
# V0..V3 structural gates
# ---------------------------------------------------------------------------

def _check_V0(ctx):
    errs, diag = [], {}
    manifest = ctx["manifest"]
    required = ("model_family", "framework", "model_files", "structure_origin",
                "reference_labeling", "status")
    missing = [k for k in required if not isinstance(manifest.get(k), (str, list, dict))]
    if missing:
        errs.append(f"V0: manifest missing required fields: {missing}")
    mf = str(manifest.get("model_family", "")).upper()
    if "DPMP" not in mf:
        errs.append(f"V0: model_family must contain DPMP (got {manifest.get('model_family')})")
    fw = str(manifest.get("framework", "")).lower()
    if "jax" not in fw:
        errs.append(f"V0: framework must contain jax (got {manifest.get('framework')})")
    mfiles = manifest.get("model_files", [])
    if not isinstance(mfiles, list) or not mfiles:
        errs.append("V0: model_files must be a non-empty list")
    so = manifest.get("structure_origin")
    if isinstance(so, dict):
        used = so.get("used_interfaces", [])
        if not isinstance(used, list) or not used:
            errs.append("V0: structure_origin.used_interfaces must be non-empty")
        else:
            bad = [u for u in used if u not in PUBLISHED_INTERFACES]
            if bad:
                errs.append(f"V0: used_interfaces not in published set: {bad}")
        if "public/structures" not in str(so.get("source", "")):
            errs.append("V0: structure_origin.source must cite public/structures (published initial supercell)")
    rl = manifest.get("reference_labeling")
    if isinstance(rl, dict):
        engine = str(rl.get("engine", "")).upper()
        if "CP2K" not in engine:
            errs.append(f"V0: reference_labeling.engine must be CP2K (got {engine})")
    diag["model_files_resolved"] = [str(m) for m in ctx["model_files"]]
    if not ctx["model_files"]:
        errs.append("V0: none of manifest model_files resolve to an existing file")
    return not errs, errs, diag


def _check_V1(ctx):
    errs, diag = [], {}
    aimd = ctx["aimd"]
    if aimd is None:
        return False, ["V1: no CP2K AIMD trajectory in workspace"], diag
    npos, nfrc = len(aimd["pos_frames"]), len(aimd["frc_frames"])
    nener, ncell = len(aimd["ener_rows"]), len(aimd["cell_rows"])
    diag.update({"pos_frames": npos, "frc_frames": nfrc, "ener_rows": nener, "cell_rows": ncell})
    ok = True
    if npos == 0:
        errs.append("V1: no reference frames")
        ok = False
    if npos != nfrc:
        errs.append(f"V1: pos frames {npos} != frc frames {nfrc} (inconsistent CP2K outputs)")
        ok = False
    if ncell and npos != ncell:
        errs.append(f"V1: pos frames {npos} != cell rows {ncell}")
        ok = False
    if nener < npos:
        errs.append(f"V1: pos frames {npos} > ener rows {nener}")
        ok = False
    natoms = len(aimd["pos_frames"][0]["atoms"]) if aimd["pos_frames"] else 1
    energies = [f["E_Ha"] for f in aimd["pos_frames"]]
    epa = [e * HARTREE_TO_EV / natoms for e in energies if e is not None]
    if not epa:
        errs.append("V1: no energies in AIMD frames")
        ok = False
    else:
        lo, hi = ctx["thresholds"]["levels"]["V1"].get("energy_per_atom_bounds_eV", [-300, 0])
        e_min, e_max = min(epa), max(epa)
        diag.update({"energy_per_atom_min_eV": round(e_min, 4), "energy_per_atom_max_eV": round(e_max, 4)})
        if e_max > hi or e_min < lo:
            errs.append(f"V1: nonphysical energies [{e_min:.3f},{e_max:.3f}] eV/atom")
            ok = False
    if aimd["frc_frames"]:
        fmax = max(abs(float(a[k])) for f in aimd["frc_frames"][:20] for a in f["atoms"] for k in (1, 2, 3))
        diag["max_force_sample"] = round(fmax, 4)
        if fmax == 0.0:
            errs.append("V1: forces are all zero (not a real calculation)")
            ok = False
        if not math.isfinite(fmax) or fmax > ctx["thresholds"]["levels"]["V1"].get("max_force_eV_A", 1000):
            errs.append(f"V1: forces non-finite or absurd ({fmax:.3e})")
            ok = False
    if npos >= 2:
        hs = {_coords_hash(np.round(_frame_positions(f), 3)) for f in aimd["pos_frames"]}
        diag["unique_frames"] = len(hs)
        if len(hs) < max(2, int(npos * 0.5)):
            errs.append(f"V1: {npos} frames but only {len(hs)} unique structures")
            ok = False
    # every labeled training frame must trace to a real CP2K output
    trace = _trace_training_to_cp2k(ctx)
    diag.update({f"training_{k}": v for k, v in trace.items() if k in ("trace_pct", "checked")})
    if trace["checked"] > 0 and trace["trace_pct"] < 90.0:
        errs.append(f"V1: only {trace['trace_pct']:.0f}% of sampled training frames trace "
                    f"to a real CP2K output (fabricated labels?)")
        ok = False
    labels = _training_label_stats(ctx)
    diag.update(labels)
    if labels["checked"] > 0:
        if labels["bad_energy"]:
            errs.append(f"V1: {labels['bad_energy']} sampled training labels have nonphysical energies")
            ok = False
        if labels["zero_force"]:
            errs.append(f"V1: {labels['zero_force']} sampled training labels have zero forces")
            ok = False
    return ok, errs, diag


def _all_cp2k_reference_positions(ctx):
    """Every real CP2K position frame in the workspace (pos trajectories)."""
    out = []
    for cand in ctx["all_pos_traj"]:
        for f in cand["pos_frames"]:
            out.append(np.asarray(_frame_positions(f)))
    return out


def _trace_training_to_cp2k(ctx, sample: int = 40):
    """For each training set, wrap its frames and all CP2K frames by the set's
    own cell, then require rounded-hash matches (fabricated frames fail)."""
    ref_frames = _all_cp2k_reference_positions(ctx)
    if not ref_frames:
        return {"trace_pct": 100.0, "checked": 0}
    rng = np.random.RandomState(0)
    matched = checked = 0
    for s in ctx["training_sets"]:
        try:
            coord = np.load(str(Path(s["root"]) / "coord.npy"))
            box = np.load(str(Path(s["root"]) / "box.npy"))
        except Exception:
            continue
        lengths = np.diag(np.asarray(box[0]).reshape(3, 3))
        if np.any(lengths <= 0):
            continue
        n = coord.shape[0]
        idx = np.sort(rng.choice(n, size=min(sample, n), replace=False))
        ref_set = {_coords_hash_wrapped(p, lengths, 2) for p in ref_frames}
        for j in idx:
            checked += 1
            hkey = _coords_hash_wrapped(coord[j].reshape(-1, 3), lengths, 2)
            if hkey in ref_set:
                matched += 1
    return {"trace_pct": 100.0 * matched / max(1, checked), "checked": checked}


def _training_label_stats(ctx, sample: int = 40):
    stats = {"checked": 0, "bad_energy": 0, "zero_force": 0}
    rng = np.random.RandomState(1)
    sampled = 0
    for s in ctx["training_sets"]:
        try:
            ener = np.load(str(Path(s["root"]) / "energy.npy"))
            force = np.load(str(Path(s["root"]) / "force.npy"))
            typ = np.loadtxt(Path(s["root"]).parent / "type.raw", dtype=int).reshape(-1)
        except Exception:
            continue
        natoms = int(len(typ))
        idx = np.sort(rng.choice(ener.shape[0], size=min(sample, ener.shape[0]), replace=False))
        for j in idx:
            sampled += 1
            stats["checked"] += 1
            epa = float(ener[j]) / natoms
            if not math.isfinite(epa) or epa < -300 or epa > 0:
                stats["bad_energy"] += 1
            fm = float(np.max(np.abs(force[j])))
            if not math.isfinite(fm) or fm == 0.0:
                stats["zero_force"] += 1
        if sampled >= sample:
            break
    return stats


def _check_V2(ctx):
    errs, diag = [], {}
    models = ctx["model_files"]
    diag["model_files"] = [str(m) for m in models]
    ok = True
    if not models:
        errs.append("V2: manifest references no model file")
        return False, errs, diag
    loaded = []
    for m in models:
        if m.stat().st_size < 1000:
            errs.append(f"V2: model too small to be real: {m.name}")
            ok = False
            continue
        good, note = probe_model(m)
        if not good:
            errs.append(f"V2: model not loadable/inferable by deepmd-jax: {m.name} ({note})")
            ok = False
            continue
        loaded.append(m)
        diag[f"probe_{m.name}"] = note
    if not loaded:
        return ok, errs, diag
    ctx["primary_model_path"] = loaded[0]
    if len(models) > 1:
        blobs = set()
        for m in models:
            try:
                blobs.add(_sha256(m.read_bytes()[:4096]))
            except Exception:
                continue
        diag["unique_model_prefixes"] = len(blobs)
        if len(blobs) < len(models):
            errs.append("V2: multiple model files are byte-identical copies")
            ok = False
    return ok, errs, diag


def _qhash(pos: np.ndarray, cell: np.ndarray, prec: int = 2) -> bytes:
    """Cell-wrapped, quantized position hash (cheap, byte-sized)."""
    pos = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
    b = np.asarray(cell, dtype=np.float64).reshape(3, 3)
    L = np.abs(np.diag(b))
    if np.any(L <= 0):
        L = np.array([1.0, 1.0, 1.0])
    w = pos - L * np.floor(pos / L)
    q = np.round(w * (10 ** prec)).astype(np.int64)
    return q.tobytes()


def _cell_key(cell: np.ndarray, prec: int = 4) -> tuple:
    return tuple(round(float(x), prec) for x in np.diag(np.asarray(cell).reshape(3, 3)))


def _run_cell(traj: dict, default: np.ndarray) -> np.ndarray:
    parent = traj["path"].parent
    cell = next((x for x in parent.iterdir() if "cell" in x.name.lower()), None)
    if cell is not None:
        # CP2K cell file: "step | a[bc] | b[bc] | c[bc] | alpha beta gamma"
        with open(cell) as fh:
            for line in fh:
                if line.startswith("#") or not line.strip():
                    continue
                p = line.split()
                if len(p) >= 11:
                    try:
                        a = math.sqrt(float(p[2]) ** 2 + float(p[3]) ** 2 + float(p[4]) ** 2)
                        b = math.sqrt(float(p[5]) ** 2 + float(p[6]) ** 2 + float(p[7]) ** 2)
                        c = math.sqrt(float(p[8]) ** 2 + float(p[9]) ** 2 + float(p[10]) ** 2)
                        if min(a, b, c) > 0:
                            return np.diag([a, b, c])
                    except (ValueError, IndexError):
                        break
    return np.asarray(default).reshape(3, 3)


def _iterative_loop_evidence(ctx, sample_new: int = 300):
    """Per-cell hashes: training frames, CP2K reference frames, growth traces."""
    aimd = ctx["aimd"]
    primary_path = aimd["pos_path"] if aimd else None
    # training frame hashes, per cell
    train_by_cell = {}
    total_train = 0
    for s in ctx["training_sets"]:
        try:
            coord = np.load(str(Path(s["root"]) / "coord.npy"))
            box = np.load(str(Path(s["root"]) / "box.npy"))
        except Exception:
            continue
        cell = box[0].reshape(3, 3)
        ck = _cell_key(cell)
        total_train += coord.shape[0]
        st = train_by_cell.setdefault(ck, set())
        for pos in coord.reshape(coord.shape[0], -1, 3):
            st.add(_qhash(pos, cell))
    # CP2K reference frame hashes, per cell
    cp2k_by_cell = {}
    run_frames = []  # (path, cell, frames)
    for traj in ctx["all_pos_traj"]:
        if not traj["pos_frames"]:
            continue
        cell = _run_cell(traj, np.eye(3) * 20.0)
        ck = _cell_key(cell)
        st = cp2k_by_cell.setdefault(ck, set())
        for f in traj["pos_frames"]:
            st.add(_qhash(_frame_positions(f), cell))
        run_frames.append((str(traj["path"]), ck, traj["pos_frames"]))
    # primary set
    primary = set()
    if aimd and primary_path:
        cell = _run_cell({"path": aimd["pos_path"]}, np.eye(3) * 20.0)
        ck = _cell_key(cell)
        for f in aimd["pos_frames"]:
            primary.add((ck, _qhash(_frame_positions(f), cell)))
    # classify new training frames (sampled)
    rng = np.random.RandomState(3)
    new_positions = 0
    untraced_new = 0
    seen_new = set()
    checked = 0
    for s in ctx["training_sets"]:
        try:
            coord = np.load(str(Path(s["root"]) / "coord.npy"))
            box = np.load(str(Path(s["root"]) / "box.npy"))
        except Exception:
            continue
        cell = box[0].reshape(3, 3)
        ck = _cell_key(cell)
        n = coord.shape[0]
        idx = np.sort(rng.choice(n, size=min(sample_new, n), replace=False))
        for j in idx:
            h = (ck, _qhash(coord[j].reshape(-1, 3), cell))
            if h in primary or h in seen_new:
                continue
            checked += 1
            seen_new.add(h)
            new_positions += 1
            if h[0] not in cp2k_by_cell or h[1] not in cp2k_by_cell[h[0]]:
                untraced_new += 1
    # every extra CP2K run must contribute to training
    used_runs = []
    unused_runs = []
    if primary_path:
        train_union = set()
        for st in train_by_cell.values():
            train_union |= st
        for path, ck, frames in run_frames:
            if path == str(primary_path):
                continue
            cell = _run_cell(next(t for t in ctx["all_pos_traj"] if str(t["path"]) == path), np.eye(3) * 20.0)
            run_set = {_qhash(_frame_positions(f), cell) for f in frames[:200]}
            if run_set & train_by_cell.get(_cell_key(cell), set()):
                used_runs.append(path)
            else:
                unused_runs.append(path)
    return {
        "total_training_frames": total_train,
        "new_labeled_configs_sampled": new_positions,
        "untraced_new_labeled_configs": untraced_new,
        "checked_new": checked,
        "extra_cp2k_runs": len([r for r in run_frames if str(r[0]) != str(primary_path)]),
        "extra_cp2k_runs_unused": unused_runs,
        "primary_cp2k_path": str(primary_path) if primary_path else None,
    }


def _check_V3(ctx):
    errs, diag = [], {}
    art = ctx["al_artifacts"]
    thr = ctx["thresholds"]["levels"]["V3"]
    min_rounds = thr.get("min_rounds", 1)
    rounds = len(art["rounds"])
    diag["training_rounds"] = rounds
    diag["acquisition_artifacts"] = len(art["acquisition"])
    diag["round_dirs"] = art["rounds"][:20]
    ev = _iterative_loop_evidence(ctx)
    diag.update({f"al_{k}": v for k, v in ev.items()})
    ok = True
    if rounds < min_rounds + 1:
        errs.append(f"V3: only {rounds} training round(s) found (need >= {min_rounds + 1}: at least one retraining)")
        ok = False
    if not art["acquisition"]:
        errs.append("V3: no model-driven acquisition artifacts (jax-md/LAMMPS explore / deviation screen)")
        ok = False
    if ev["new_labeled_configs_sampled"] < 1:
        errs.append("V3: no newly-labeled configurations beyond the initial AIMD (no real active learning)")
        ok = False
    if ev["untraced_new_labeled_configs"] > 0:
        errs.append(f"V3: {ev['untraced_new_labeled_configs']}/{ev['checked_new']} newly-labeled "
                    f"configurations trace to no CP2K output (fabricated acquisition)")
        ok = False
    if ev["extra_cp2k_runs_unused"]:
        errs.append(f"V3: {len(ev['extra_cp2k_runs_unused'])} additional CP2K output(s) never used "
                    f"in retraining: {[Path(u).name for u in ev['extra_cp2k_runs_unused'][:5]]}")
        ok = False
    if not ctx["aimd"]:
        errs.append("V3: no AIMD baseline to measure dataset growth against")
        ok = False
    return ok, errs, diag


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------

def verify(submission, profile="formal"):
    report = {"valid": False, "levels": {}, "errors": [], "diagnostics": {}, "reward": 0}
    sub = Path(submission)
    if not sub.is_dir():
        report["errors"].append(f"submission dir missing: {sub}")
        return report
    try:
        thresholds = load_thresholds()
    except Exception as ex:
        report["errors"].append(f"thresholds: {ex}")
        return report

    manifest_path = sub / "final" / "manifest.json"
    if not manifest_path.is_file():
        report["errors"].append("final/manifest.json missing")
        return report
    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception:
        report["errors"].append("final/manifest.json is not valid JSON")
        return report

    if profile == "formal" and str(manifest.get("profile", "")).lower() == "smoke":
        report["errors"].append("submission is a smoke-profile run; cannot satisfy formal grading")
        return report

    if not hidden_frames_path().is_dir():
        report["errors"].append(f"hidden frames missing: {hidden_frames_path()}")
        return report
    if not density_reference_path().is_file():
        report["errors"].append(f"density reference missing: {density_reference_path()}")
        return report

    ctx = {
        "submission": sub,
        "manifest": manifest,
        "thresholds": thresholds,
        "expected_natoms": 489,
        "model_files": find_model_files(sub, manifest),
    }
    try:
        ctx["density_reference"] = load_density_reference()
    except Exception as ex:
        report["errors"].append(f"density reference: {ex}")
        return report

    ctx["all_pos_traj"] = find_all_cp2k_pos(sub)
    ctx["aimd"] = find_cp2k_aimd(sub)
    ctx["training_sets"] = find_training_sets(sub)
    ctx["al_artifacts"] = find_al_artifacts(sub)

    checks = [
        ("V0", _check_V0),
        ("V1", _check_V1),
        ("V2", _check_V2),
        ("V3", _check_V3),
        ("V4", _check_V4),
        ("V5", _check_V5),
        ("V6", _check_V6),
    ]
    for name, fn in checks:
        try:
            ok, errs, diag = fn(ctx)
        except Exception as ex:
            ok, errs, diag = False, [f"{name}: verifier error: {ex}"], {}
        report["levels"][name] = {"ok": ok, "errors": errs, "diagnostics": diag}
        report["diagnostics"][name] = diag
        report["errors"].extend(errs)

    report["valid"] = all(v["ok"] for v in report["levels"].values())
    report["reward"] = 1 if report["valid"] else 0
    return report


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(ENV_SUBMISSION, "/app")
    r = verify(root, os.environ.get(ENV_PROFILE, "formal"))
    print(json.dumps(r, indent=2, default=str))
    sys.exit(0 if r["valid"] else 1)
