#!/usr/bin/env python3
"""
034 verifier — L1..L9 outcome-based evaluation.

Contract: reference/CONTRACT.md §5. The verifier inspects the agent workspace
(submission root) and the staged hidden set, and returns a structured report.

Levels:
  L0  autonomous structure generation and provenance
  L1  initial-system identity        (the structure used for reference generation)
  L2  structure preparation/relaxation
  L3  reference-data authenticity    (real CP2K AIMD outputs, traceable labels)
  L4  reference sampling quality
  L5  DeePMD model authenticity
  L6  genuine iterative improvement loop
  L7  hidden E/F  (deepmd vs dft-validation.extxyz)
  L8  hidden 300 K NVT (verifier-run)
  L9  hidden RDF  (NVT-trajectory RDF vs rdf-reference.json)

Reward = 1 iff L1..L9 all pass. L1-L6 are structural (hard); L7-L9 bounds are
read from thresholds.json (draft; frozen after expert reruns).

The verifier never inspects anything outside the workspace except the staged
hidden files. Paths come from env with /tests/hidden defaults; dev/smoke runs
point them elsewhere.

AIMD disambiguation: a CP2K *AIMD* trajectory is a pos-*.xyz whose header carries
`i = <n>, time = <t>, E = <E>` (Hartree), and that has sibling ener/cell/frc
outputs with a matching frame count. A GEO_OPT pos-*.xyz has the same format but
no ener/cell siblings and different physics (optimization walk, not dynamics), so
it is never chosen as the reference trajectory.
"""
from __future__ import annotations

import json
import hashlib
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

ENV_HIDDEN_VAL = "AI2KIT_HIDDEN_VALIDATION"
ENV_HIDDEN_RDF = "AI2KIT_HIDDEN_RDF_REFERENCE"
ENV_THRESHOLDS = "AI2KIT_THRESHOLDS"
ENV_NVT_STEPS = "AI2KIT_NVT_STEPS"
ENV_NVT_TEMP = "AI2KIT_NVT_TEMPERATURE"
ENV_SUBMISSION = "AI2KIT_034_SUBMISSION"
ENV_REFERENCE_MODE = "AI2KIT_REFERENCE_MODE"

HARTREE_TO_EV = 27.211386245988

REQUIRED_STRUCTURE_FIELDS = frozenset({
    "initial_structure",
    "generator",
    "generator_version",
    "command",
    "seed",
    "checks",
    "initial_structure_sha256",
    "cp2k_input_structure_sha256",
})


def _default_hidden() -> Path:
    return Path(__file__).resolve().parent / "hidden"


def hidden_validation_path() -> Path:
    return Path(os.environ.get(ENV_HIDDEN_VAL, str(_default_hidden() / "dft-validation.extxyz")))


def hidden_rdf_path() -> Path:
    return Path(os.environ.get(ENV_HIDDEN_RDF, str(_default_hidden() / "rdf-reference.json")))


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


def _safe_submission_path(submission: Path, relative: str) -> Path:
    """Resolve a manifest path without allowing absolute or symlink escape."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("L0: initial structure path must be submission-relative")
    root = submission.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("L0: initial structure path escapes submission") from exc
    return candidate


def _parse_initial_extxyz(path: Path):
    """Read the first XYZ/extxyz frame and its orthorhombic Lattice header."""
    lines = path.read_text().splitlines()
    if len(lines) < 2:
        raise ValueError("L0: initial structure is not a complete XYZ frame")
    try:
        natoms = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError("L0: initial structure atom count is invalid") from exc
    if natoms <= 0 or len(lines) < natoms + 2:
        raise ValueError("L0: initial structure atom rows are incomplete")
    lattice = re.search(r'Lattice="([^"]+)"', lines[1])
    if not lattice:
        raise ValueError("L0: periodic Lattice metadata missing")
    try:
        values = [float(x) for x in lattice.group(1).split()]
    except ValueError as exc:
        raise ValueError("L0: Lattice metadata is invalid") from exc
    if len(values) != 9:
        raise ValueError("L0: Lattice must contain nine values")
    matrix = np.asarray(values, dtype=float).reshape(3, 3)
    if np.max(np.abs(matrix - np.diag(np.diag(matrix)))) > 1e-8:
        raise ValueError("L0: target requires an orthorhombic cubic cell")
    cell = np.diag(matrix).copy()
    atoms = []
    for line in lines[2:2 + natoms]:
        fields = line.split()
        if len(fields) < 4:
            raise ValueError("L0: malformed atom row")
        try:
            atoms.append((fields[0], np.array([float(x) for x in fields[1:4]], dtype=float)))
        except ValueError as exc:
            raise ValueError("L0: non-numeric coordinate") from exc
    return atoms, cell


def _minimum_image(delta: np.ndarray, cell: np.ndarray) -> np.ndarray:
    return delta - cell * np.round(delta / cell)


def _water_geometry_diagnostics(atoms, cell):
    symbols = [symbol for symbol, _ in atoms]
    positions = np.asarray([position for _, position in atoms], dtype=float)
    o_indices = [i for i, symbol in enumerate(symbols) if symbol == "O"]
    h_indices = [i for i, symbol in enumerate(symbols) if symbol == "H"]

    assigned = {oi: [] for oi in o_indices}
    molecule_of = {}
    for hi in h_indices:
        distances = []
        for oi in o_indices:
            delta = _minimum_image(positions[hi] - positions[oi], cell)
            distances.append((float(np.linalg.norm(delta)), oi))
        distance, owner = min(distances)
        assigned[owner].append((hi, distance))
        molecule_of[hi] = owner
    for oi in o_indices:
        molecule_of[oi] = oi

    oh_bonds = []
    hoh_angles = []
    for oi, hydrogens in assigned.items():
        oh_bonds.extend(distance for _, distance in hydrogens)
        if len(hydrogens) == 2:
            v1 = _minimum_image(positions[hydrogens[0][0]] - positions[oi], cell)
            v2 = _minimum_image(positions[hydrogens[1][0]] - positions[oi], cell)
            cosine = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))
            cosine = max(-1.0, min(1.0, cosine))
            hoh_angles.append(math.degrees(math.acos(cosine)))

    dmin_inter = float("inf")
    for i in range(len(atoms)):
        for j in range(i + 1, len(atoms)):
            if molecule_of.get(i) == molecule_of.get(j):
                continue
            delta = _minimum_image(positions[j] - positions[i], cell)
            dmin_inter = min(dmin_inter, float(np.linalg.norm(delta)))

    return {
        "natoms": len(atoms),
        "composition": dict(Counter(symbols)),
        "cell_A": [float(x) for x in cell],
        "molecules": sum(1 for hs in assigned.values() if len(hs) == 2),
        "hydrogens_per_oxygen": sorted(len(hs) for hs in assigned.values()),
        "OH_bond_min_A": min(oh_bonds) if oh_bonds else None,
        "OH_bond_max_A": max(oh_bonds) if oh_bonds else None,
        "HOH_angle_min_degree": min(hoh_angles) if hoh_angles else None,
        "HOH_angle_max_degree": max(hoh_angles) if hoh_angles else None,
        "min_intermolecular_distance_A": dmin_inter,
    }


def check_structure_origin(submission: Path, manifest: dict, thresholds: dict):
    """Validate autonomous structure provenance and periodic water geometry."""
    record = manifest.get("structure_generation")
    if not isinstance(record, dict):
        return False, ["L0: structure_generation record missing"], {}
    missing = sorted(REQUIRED_STRUCTURE_FIELDS - set(record))
    if missing:
        return False, [f"L0: missing structure fields: {missing}"], {}
    try:
        initial = _safe_submission_path(Path(submission), record["initial_structure"])
    except ValueError as exc:
        return False, [str(exc)], {}
    if not initial.is_file():
        return False, [f"L0: initial structure missing: {record['initial_structure']}"], {}

    actual_sha = hashlib.sha256(initial.read_bytes()).hexdigest()
    errors = []
    if record["initial_structure_sha256"] != actual_sha:
        errors.append("L0: recorded initial-structure SHA-256 does not match bytes")
    if record["cp2k_input_structure_sha256"] != actual_sha:
        errors.append("L0: CP2K input provenance does not link the generated structure SHA-256")

    thr = thresholds.get("levels", {}).get("L0", {})
    if actual_sha in set(thr.get("denied_initial_sha256", [])):
        errors.append("L0: initial structure matches a denied hidden/reference identity")

    try:
        atoms, cell = _parse_initial_extxyz(initial)
        diag = _water_geometry_diagnostics(atoms, cell)
    except Exception as exc:
        return False, errors + [str(exc)], {"sha256": actual_sha}
    diag["sha256"] = actual_sha
    coords_identity = hashlib.sha256(
        _coords_hash(np.asarray([position for _, position in atoms]), 3).encode()
    ).hexdigest()
    diag["coords_sha256"] = coords_identity
    if coords_identity in set(thr.get("denied_initial_coords_sha256", [])):
        errors.append("L0: initial structure matches a denied hidden/reference coordinate identity")
    expected = thr.get("expected_composition", {"O": 64, "H": 128})
    if diag["composition"] != expected:
        errors.append(f"L0: composition {diag['composition']} != {expected}")
    target_cell = thr.get("cell_angstrom", [12.4, 12.4, 12.4])
    cell_tol = float(thr.get("cell_tolerance_angstrom", 0.05))
    if any(abs(got - want) > cell_tol for got, want in zip(diag["cell_A"], target_cell)):
        errors.append(f"L0: cell {diag['cell_A']} outside {target_cell} ± {cell_tol} A")
    if diag["molecules"] != 64 or diag["hydrogens_per_oxygen"] != [2] * 64:
        errors.append("L0: water molecular assignment is not exactly 64 O(H)2 molecules")
    bond_lo, bond_hi = thr.get("OH_bond_range_angstrom", [0.75, 1.25])
    if (diag["OH_bond_min_A"] is None or diag["OH_bond_min_A"] < bond_lo
            or diag["OH_bond_max_A"] > bond_hi):
        errors.append("L0: O-H bond lengths outside allowed range")
    angle_lo, angle_hi = thr.get("HOH_angle_range_degree", [85.0, 125.0])
    if (diag["HOH_angle_min_degree"] is None or diag["HOH_angle_min_degree"] < angle_lo
            or diag["HOH_angle_max_degree"] > angle_hi):
        errors.append("L0: H-O-H angles outside allowed range")
    min_inter = float(thr.get("min_intermolecular_distance_angstrom", 1.2))
    if diag["min_intermolecular_distance_A"] < min_inter:
        errors.append("L0: intermolecular contact below allowed floor")
    if not isinstance(record.get("checks"), dict) or not record["checks"]:
        errors.append("L0: structure-generation checks must be a non-empty object")
    return not errors, errors, diag


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

_CP2K_HDR = re.compile(
    r"i\s*=\s*(?P<i>\d+).*?time\s*=\s*(?P<time>[-+0-9.eE]+).*?E\s*=\s*(?P<E>[-+0-9.eE]+)"
)


def parse_cp2k_xyz(path: Path):
    """Parse a raw CP2K xyz trajectory (pos or frc). Returns frames:
    {'i','time','E_Ha','atoms':[(sym,x,y,z),...], 'natoms'}."""
    frames = []
    with open(path) as fh:
        lines = fh.readlines()
    i = 0
    n = len(lines)
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
    """CP2K .ener file -> list of dicts (step,time_fs,kin,temp_K,pot_Ha)."""
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
    """CP2K .cell file -> list of (step, cell_len_a)."""
    rows = []
    with open(path) as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) < 10:
                continue
            try:
                ax, ay, az = (float(parts[2]), float(parts[3]), float(parts[4]))
                rows.append({"step": int(float(parts[0])),
                             "a": math.sqrt(ax * ax + ay * ay + az * az)})
            except ValueError:
                continue
    return rows


def _frame_energy_eV(a) -> float | None:
    """Best-effort per-frame energy (eV) from an ase Atoms read from extxyz."""
    try:
        info = getattr(a, "info", {}) or {}
        if "energy" in info:
            return float(info["energy"])
    except Exception:
        pass
    try:
        return float(a.get_potential_energy())
    except Exception:
        return None


def _frame_forces(a):
    try:
        f = a.get_forces()
        return np.asarray(f, dtype=float)
    except Exception:
        return None


def _read_extxyz(path: Path):
    """Read an extxyz file with ase; return (frames, cell). Each frame is
    {'symbols','positions','cell','energy','forces'} (units: eV, Å, eV/Å)."""
    from ase.io import read
    try:
        atoms_list = read(str(path), index=":")
    except Exception:
        return None
    frames = []
    cell = None
    for a in atoms_list:
        fr = {
            "symbols": np.array(a.get_chemical_symbols()),
            "positions": a.get_positions(),
            "cell": a.get_cell()[:].copy(),
            "energy": _frame_energy_eV(a),
            "forces": _frame_forces(a),
        }
        frames.append(fr)
        if cell is None:
            cell = a.get_cell()
    return frames, cell


def _frame_positions(frame) -> np.ndarray:
    if frame.get("atoms"):
        return np.array([a[1:4] for a in frame["atoms"]])
    return np.asarray(frame["positions"])


def _coords_hash(pos: np.ndarray, prec: float = 3) -> str:
    return ",".join(f"{float(x):.{prec}f}" for x in pos.reshape(-1))


def frame_match(a_pos: np.ndarray, b_pos: np.ndarray, cell: float, tol: float = 2e-3) -> bool:
    """True if two coordinate sets are the same structure within tol (min-image)."""
    if a_pos.shape != b_pos.shape:
        return False
    d = a_pos - b_pos
    d = d - cell * np.round(d / cell)
    return float(np.max(np.linalg.norm(d, axis=1))) < tol


# ---------------------------------------------------------------------------
# Workspace discovery
# ---------------------------------------------------------------------------

def walk_files(root: Path, exts=None):
    """Yield regular files under root. Skips symlinked directories to avoid
    cycles (final/workflow symlinks point back into the workspace)."""
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
    """Find a sibling file of `root` whose name contains needle; optionally
    matching a frame count (for frc-vs-pos consistency)."""
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


def find_all_cp2k_pos(root: Path):
    """Return a list of every CP2K-format pos trajectory in the workspace:
    [{'path','pos_frames'}] sorted by frame count descending. Only trajectories
    whose header parses with an energy and ≥1 frame are returned."""
    out = []
    for p in walk_files(root, exts={".xyz"}):
        name = p.name.lower()
        if "pos" not in name:
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
    """Pick the PRIMARY AIMD trajectory: the largest pos trajectory that has a
    matching frc sibling AND (ener or cell) siblings — the signature of a CP2K
    MD run. GEO_OPT trajectories lack these siblings."""
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
    # Fallback: no true AIMD sibling signature found — report the largest pos
    # trajectory anyway so L1/L2/L3 can produce diagnostics instead of a null.
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
    """Return DeepMD system set dirs with frame counts.

    DeepMD layout: <system>/type.raw (1 int per atom) next to <system>/set.NNN/
    coord.npy (nframes x natoms x 3). We locate every set.NNN/coord.npy and
    report the SET dir (frame pool), not the system dir."""
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


def find_labeled_extxyz(root: Path):
    """Return labeled extxyz files (per-frame energy present, forces present)."""
    out = []
    for p in walk_files(root, exts={".xyz", ".extxyz"}):
        name = p.name.lower()
        if "pos" in name or "frc" in name:
            continue
        try:
            parsed, _ = _read_extxyz(p)
        except Exception:
            continue
        if not parsed:
            continue
        if all(f["energy"] is not None for f in parsed) and any(
            f["forces"] is not None for f in parsed
        ):
            out.append({"path": str(p), "frames": len(parsed), "parsed": parsed})
    return out


def find_al_artifacts(root: Path):
    """Generic evidence of model-driven acquisition + labeling + retraining."""
    found = {"acquisition": [], "rounds": []}
    for p in walk_files(root):
        name = p.name.lower()
        rel = str(p.relative_to(root)).lower()
        # model-driven exploration / screening outputs
        if "model_devi" in name or name.endswith(".lammpstrj") or name.endswith(".dump") or "screening" in rel or "model-devi" in rel:
            found["acquisition"].append(str(p))
        # a training round is a directory holding both a deepmd input.json AND
        # its lcurve.out / checkpoint graph — the signature of a completed run
        if name in ("input.json", "lcurve.out", "checkpoint"):
            parent = p.parent
            has_graph = any(parent.glob("*.pb")) or (parent / "lcurve.out").is_file() or (parent / "checkpoint").is_file()
            if has_graph and name == "input.json":
                found["rounds"].append(str(parent))
    # A committee of N model dirs is ONE training round, not N. Dedupe rounds by
    # their parent directory (e.g. iter-001/deepmd/model-0 | model-1 collapse to
    # one round under iter-001/deepmd). Otherwise a single no-retrain committee
    # reads as N rounds and defeats L6's "at least one retraining" gate — a
    # submission that never re-trains on newly-labeled configs would pass.
    unique = {}
    for rdir in found["rounds"]:
        unique.setdefault(str(Path(rdir).parent), []).append(rdir)
    found["rounds"] = [dirs[0] for dirs in unique.values()]
    # LAMMPS dump detection by content (filenames vary)
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
    """True if any CP2K GEO_OPT output exists (RUN_TYPE GEO_OPT / converged)."""
    for p in walk_files(root, exts={".out", ".log", ".txt", ".restart"}):
        try:
            txt = p.read_text(errors="ignore")
            if "GEO_OPT" in txt and ("CONVERGED" in txt or "OPTIMIZATION COMPLETED" in txt or "BFGS" in txt):
                return True
        except Exception:
            continue
    return False


def find_model_files(root: Path, manifest: dict):
    """Resolve model files from the manifest. Returns list of absolute Paths."""
    files = []
    for rel in manifest.get("model_files", []):
        for cand in (root / rel, root / "final" / rel):
            if cand.is_file():
                files.append(cand)
                break
    return files


# ---------------------------------------------------------------------------
# DeepMD helpers (cached — a pytest run re-verifies many tampered copies of the
# same submission; the model bytes are unchanged unless a fixture tampers them)
# ---------------------------------------------------------------------------

_MODEL_CACHE = {}
_L7_CACHE = {}
_L8_CACHE = {}


def _model_cache_key(path: Path):
    try:
        st = path.stat()
        return (str(path), st.st_size, st.st_mtime_ns)
    except Exception:
        return (str(path), None, None)


def load_model(path: Path):
    try:
        import deepmd
    except ImportError:
        # local smoke without the container: no deepmd, so nothing loads
        return (None, None)
    key = _model_cache_key(path)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    try:
        dp = deepmd.DeepPotential(str(path))
        tm = list(dp.get_type_map())
        result = (dp, tm)
    except Exception:
        result = (None, None)
    _MODEL_CACHE[key] = result
    return result


def dp_infer_frame(dp, symbols: np.ndarray, pos: np.ndarray, cell: float):
    """Run deepmd inference on one frame. Returns (energy_eV, forces (n,3))."""
    tm = list(dp.get_type_map())
    atype = np.array([tm.index(s) for s in symbols], dtype=np.int32)
    coords = np.asarray(pos, dtype=np.float64).reshape(-1)
    box = np.array([cell, 0, 0, 0, cell, 0, 0, 0, cell], dtype=np.float64)
    e, f, _ = dp.eval(coords, box, atype)
    return float(e), np.asarray(f).reshape(-1, 3)


# ---------------------------------------------------------------------------
# RDF
# ---------------------------------------------------------------------------

def compute_rdf(positions_list, symbols_list, pair, cell, rmax=6.0, dr=0.02):
    bins = np.arange(0, rmax + dr, dr)
    hist = np.zeros(len(bins) - 1)
    n_valid = 0
    total_ref = 0
    total_density = 0.0
    ea, eb = pair
    for pos, sym in zip(positions_list, symbols_list):
        idx_a = np.where(sym == ea)[0]
        idx_b = np.where(sym == eb)[0]
        if len(idx_a) == 0 or len(idx_b) == 0:
            continue
        n_valid += 1
        total_ref += len(idx_a)
        total_density += len(idx_b) / cell ** 3
        dists = []
        for i in idx_a:
            for j in idx_b:
                if ea == eb and i == j:
                    continue
                rij = pos[j] - pos[i]
                rij = rij - cell * np.round(rij / cell)
                d = np.linalg.norm(rij)
                if d < rmax:
                    dists.append(d)
        hist += np.histogram(dists, bins=bins)[0]
    r = 0.5 * (bins[:-1] + bins[1:])
    shell = 4 * np.pi * r ** 2 * dr
    den = total_density / n_valid if n_valid else 1.0
    rdf = hist / (n_valid * (total_ref / n_valid) * den * shell)
    return r, rdf


def first_peak(r, g, lo=0.0, hi=6.0):
    m = (r >= lo) & (r <= hi)
    if not np.any(m):
        return None
    return float(r[m][np.argmax(g[m])])


# ---------------------------------------------------------------------------
# Level checks
# ---------------------------------------------------------------------------

def _check_L0(ctx):
    if os.environ.get(ENV_REFERENCE_MODE) == "1":
        return True, [], {"reference_mode": True,
                          "note": "benchmark reference calibration bypasses agent-only L0"}
    ok, errors, diag = check_structure_origin(
        ctx["submission"], ctx["manifest"], ctx["thresholds"])
    if not ok:
        return ok, errors, diag
    record = ctx["manifest"]["structure_generation"]
    initial = _safe_submission_path(ctx["submission"], record["initial_structure"])
    atoms, _ = _parse_initial_extxyz(initial)
    initial_hash = _coords_hash(np.asarray([position for _, position in atoms]), 3)
    linked = False
    for trajectory in ctx.get("all_pos_traj", []):
        for frame in trajectory.get("pos_frames", []):
            if _coords_hash(_frame_positions(frame), 3) == initial_hash:
                linked = True
                break
        if linked:
            break
    diag["cp2k_coordinate_link"] = linked
    if not linked:
        errors.append("L0: generated coordinates do not appear in any retained CP2K trajectory")
    return not errors, errors, diag


def verifier_level_order():
    return [f"L{i}" for i in range(10)]

def _check_L1(ctx):
    errs, diag = [], {}
    aimd = ctx["aimd"]
    if aimd is None or not aimd["pos_frames"]:
        return False, errs, {"note": "no CP2K AIMD trajectory found in workspace"}
    f0 = aimd["pos_frames"][0]
    atoms = f0["atoms"]
    natoms = len(atoms)
    comp = dict(Counter(a[0] for a in atoms))
    diag.update({"natoms": natoms, "composition": comp, "frame0_i": f0["i"],
                 "frame0_time_fs": f0["time"]})
    exp = ctx["thresholds"]["levels"]["L1"]
    ok = True
    if natoms != exp["natoms"]:
        errs.append(f"L1: natoms {natoms} != {exp['natoms']}")
        ok = False
    for el, want in exp.get("expected_composition", {}).items():
        if comp.get(el, 0) != want:
            errs.append(f"L1: composition {comp} != expected {exp['expected_composition']}")
            ok = False
    cell = ctx["cell"]
    if cell is None:
        errs.append("L1: could not determine cell")
        ok = False
    else:
        tol = exp.get("cell_tolerance_angstrom", 0.5)
        if abs(cell - ctx["system_cell"]) > tol:
            errs.append(f"L1: cell {cell:.3f} not within {tol} A of system.json {ctx['system_cell']}")
            ok = False
    return ok, errs, diag


def _check_L2(ctx):
    errs, diag = [], {}
    aimd = ctx["aimd"]
    if aimd is None or not aimd["pos_frames"]:
        return False, errs, {"note": "no AIMD"}
    f0 = aimd["pos_frames"][0]
    pos = _frame_positions(f0)
    sym = [a[0] for a in f0["atoms"]]
    cell = ctx["cell"]
    thr = ctx["thresholds"]["levels"]["L2"]
    min_floor = thr.get("min_pair_distance_angstrom", 1.2)

    # Molecular-aware min inter-atomic distance. All-pairs min-image would
    # always find an intramolecular O-H bond (~0.96 A < the 1.2 A floor) and
    # false-fail every physically sane structure. Instead we union-find each O
    # to its ≤2 nearest H within bond_cut, then take the min distance over
    # atom pairs that belong to DIFFERENT molecules. A broken/overlapping
    # starting structure shows up as inter-molecular contacts below the floor.
    dmin_inter = _min_intermolecular_pair_distance(sym, pos, cell, bond_cut=1.3)
    diag["min_intermolecular_distance_A"] = round(dmin_inter, 4)
    diag["used_geometry_optimization"] = find_geopt_evidence(ctx["submission"])
    ok = dmin_inter >= min_floor
    if not ok:
        errs.append(
            f"L2: min inter-molecular pair distance {dmin_inter:.3f} A < {min_floor} A "
            f"(overlapping / broken starting structure)"
        )
    # frame-0 forces finite and bounded
    if aimd["frc_frames"]:
        frc0 = aimd["frc_frames"][0]
        fmax = max(abs(float(a[k])) for a in frc0["atoms"] for k in (1, 2, 3)) if frc0["atoms"] else 0.0
        diag["max_force_frame0"] = round(fmax, 4)
        if not math.isfinite(fmax) or fmax > 1e6:
            errs.append("L2: frame-0 forces non-finite or absurd")
            ok = False
    return ok, errs, diag


def _min_intermolecular_pair_distance(sym, pos, cell, bond_cut=1.3):
    n = len(pos)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    o_idx = [i for i, s in enumerate(sym) if s == "O"]
    h_idx = [i for i, s in enumerate(sym) if s == "H"]
    if not o_idx or not h_idx:
        return float(np.max(np.linalg.norm(pos - pos[0], axis=1))) * 0 + 1e9
    for i in o_idx:
        dists = []
        for j in h_idx:
            d = pos[j] - pos[i]
            d = d - cell * np.round(d / cell)
            dists.append((float(np.linalg.norm(d)), j))
        dists.sort()
        for dd, j in dists[:2]:
            if dd < bond_cut:
                union(i, j)
    dmin = 1e9
    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            d = pos[j] - pos[i]
            d = d - cell * np.round(d / cell)
            dmin = min(dmin, float(np.linalg.norm(d)))
    return dmin


def _check_L3(ctx):
    errs, diag = [], {}
    aimd = ctx["aimd"]
    if aimd is None:
        return False, errs, {"note": "no CP2K AIMD trajectory -> no real reference data"}
    npos = len(aimd["pos_frames"])
    nfrc = len(aimd["frc_frames"])
    nener = len(aimd["ener_rows"])
    ncell = len(aimd["cell_rows"])
    diag.update({"pos_frames": npos, "frc_frames": nfrc, "ener_rows": nener, "cell_rows": ncell})
    ok = True
    if npos == 0:
        errs.append("L3: no reference frames")
        ok = False
    if npos != nfrc:
        errs.append(f"L3: pos frames {npos} != frc frames {nfrc} (inconsistent CP2K outputs)")
        ok = False
    # ener is written EVERY MD step while pos/frc/cell follow the trajectory
    # print cadence, so ener is always >= pos (often much larger). cell is on
    # the same cadence as pos.
    if ncell and npos != ncell:
        errs.append(f"L3: pos frames {npos} != cell rows {ncell} (inconsistent CP2K outputs)")
        ok = False
    if nener < npos:
        errs.append(f"L3: pos frames {npos} > ener rows {nener} (ener cannot be sparser than pos)")
        ok = False
    if npos < 1:
        errs.append("L3: no reference frames")
        ok = False
    exp = ctx["thresholds"]["levels"]["L3"]
    lo, hi = exp.get("energy_per_atom_bounds_eV", [-300, 0])
    max_force = exp.get("max_force_eV_A", 1000)
    natoms = len(aimd["pos_frames"][0]["atoms"]) if aimd["pos_frames"] else 1
    energies = [f["E_Ha"] for f in aimd["pos_frames"]]
    epa = [e * HARTREE_TO_EV / natoms for e in energies if e is not None]
    if not epa:
        errs.append("L3: no energies in AIMD frames")
        ok = False
    else:
        e_min, e_max = min(epa), max(epa)
        diag.update({"energy_per_atom_min_eV": round(e_min, 4), "energy_per_atom_max_eV": round(e_max, 4)})
        if e_max > hi or e_min < lo:
            errs.append(f"L3: nonphysical energies [{e_min:.3f},{e_max:.3f}] eV/atom outside [{lo},{hi}]")
            ok = False
    if aimd["frc_frames"]:
        fmax = 0.0
        for f in aimd["frc_frames"][:20]:
            for a in f["atoms"]:
                fmax = max(fmax, abs(float(a[1])), abs(float(a[2])), abs(float(a[3])))
        diag["max_force_sample"] = round(fmax, 4)
        if fmax == 0.0:
            errs.append("L3: forces are all zero (not a real calculation)")
            ok = False
        if not math.isfinite(fmax) or fmax > max_force:
            errs.append(f"L3: forces non-finite or absurd ({fmax:.3e} eV/A)")
            ok = False
    # distinct frames
    if npos >= 2:
        hs = {_coords_hash(np.round(_frame_positions(f), 3)) for f in aimd["pos_frames"]}
        diag["unique_frames"] = len(hs)
        if len(hs) < max(2, int(npos * 0.5)):
            errs.append(f"L3: {npos} frames but only {len(hs)} unique structures (duplicates)")
            ok = False
    # training frames trace to a real CP2K output (any AIMD OR label run), and
    # the labels carry physical energies/forces (no fabricated extxyz).
    trace = _trace_training_to_cp2k(ctx)
    diag.update({f"training_{k}": v for k, v in trace.items() if k in ("trace_pct", "checked")})
    if trace["checked"] > 0 and trace["trace_pct"] < 90.0:
        errs.append(
            f"L3: only {trace['trace_pct']:.0f}% of sampled training frames trace to a real CP2K "
            f"output (fabricated labels?)"
        )
        ok = False
    labels = _training_label_stats(ctx)
    diag.update(labels)
    if labels["checked"] > 0:
        if labels["bad_energy"]:
            errs.append(f"L3: {labels['bad_energy']} sampled training labels have nonphysical energies")
            ok = False
        if labels["zero_force"]:
            errs.append(f"L3: {labels['zero_force']} sampled training labels have zero forces")
            ok = False
    return ok, errs, diag


def _all_reference_positions(ctx):
    """Union of every real CP2K position set in the workspace: all pos-*.xyz
    frames plus all labeled extxyz frames (converted CP2K outputs)."""
    pos_list = []
    for cand in ctx["all_pos_traj"]:
        for f in cand["pos_frames"]:
            pos_list.append(_frame_positions(f))
    for e in ctx["labeled_extxyz"]:
        for f in e["parsed"]:
            pos_list.append(f["positions"])
    return pos_list


def _training_frames(ctx):
    """All labeled training positions."""
    out = []
    for s in ctx["training_sets"]:
        coord = np.load(str(Path(s["root"]) / "coord.npy"))
        out.append({"kind": "set", "positions": coord.reshape(len(coord), -1, 3), "source": s["path"]})
    for e in ctx["labeled_extxyz"]:
        out.append({"kind": "extxyz", "positions": [f["positions"] for f in e["parsed"]], "source": e["path"]})
    return out


def _trace_training_to_cp2k(ctx, sample: int = 40):
    ref = _all_reference_positions(ctx)
    trained = _training_frames(ctx)
    if not trained or not ref:
        return {"trace_pct": 100.0, "checked": 0}
    cell = ctx["cell"]
    candidates = []
    for t in trained:
        poslist = t["positions"] if isinstance(t["positions"], list) else list(t["positions"])
        for pos in poslist:
            candidates.append(np.asarray(pos))
    if not candidates:
        return {"trace_pct": 100.0, "checked": 0}
    rng = np.random.RandomState(0)
    idx = rng.choice(len(candidates), size=min(sample, len(candidates)), replace=False)
    # for speed, build a rounded hash set of reference frames
    ref_set = set()
    for p in ref:
        ref_set.add(_coords_hash(np.round(p, 2)))
    matched = 0
    for k in idx:
        pos = np.asarray(candidates[k])
        hkey = _coords_hash(np.round(pos, 2))
        if hkey in ref_set:
            matched += 1
            continue
        hit = any(frame_match(pos, rp, cell) for rp in ref[:400])
        matched += int(hit)
    return {"trace_pct": 100.0 * matched / len(idx), "checked": len(idx)}


def _training_label_stats(ctx, sample: int = 40):
    """Spot-check sampled training labels for physical energies / nonzero forces."""
    stats = {"checked": 0, "bad_energy": 0, "zero_force": 0, "energy_rmse_unused": 0.0}
    natoms = ctx.get("expected_natoms", 192)
    rng = np.random.RandomState(1)
    sampled = 0
    for e in ctx["labeled_extxyz"]:
        frames = e["parsed"]
        for f in frames:
            if sampled >= sample:
                return stats
            sampled += 1
            stats["checked"] += 1
            en = f["energy"]
            fr = f["forces"]
            if en is not None:
                epa = en / natoms
                if not math.isfinite(epa) or epa < -300 or epa > 0:
                    stats["bad_energy"] += 1
            if fr is not None and len(fr):
                fm = float(np.max(np.abs(fr)))
                if fm == 0.0:
                    stats["zero_force"] += 1
    return stats


def _check_L4(ctx):
    errs, diag = [], {}
    aimd = ctx["aimd"]
    if aimd is None:
        return False, errs, {"note": "no AIMD"}
    thr = ctx["thresholds"]["levels"]["L4"]
    npos = len(aimd["pos_frames"])
    diag["aimd_frames"] = npos
    ok = True
    if npos < thr.get("min_training_frames", 10):
        errs.append(f"L4: AIMD has only {npos} frames (< {thr['min_training_frames']})")
        ok = False
    temps = [r["temp_K"] for r in aimd["ener_rows"] if r.get("temp_K") is not None]
    if len(temps) >= 2:
        tmean, tstd = float(np.mean(temps)), float(np.std(temps))
        diag.update({"temp_mean_K": round(tmean, 1), "temp_std_K": round(tstd, 1)})
        band = thr.get("temperature_band_K", [200, 500])
        if not (band[0] <= tmean <= band[1]):
            errs.append(f"L4: mean temp {tmean:.0f} K outside [{band[0]},{band[1]}]")
            ok = False
        if tstd < thr.get("min_temp_std_K", 3.0):
            errs.append(f"L4: temp std {tstd:.1f} K too small (no dynamics sampled)")
            ok = False
    else:
        errs.append("L4: cannot determine temperature distribution from AIMD")
        ok = False
    if npos >= 5:
        pos0 = _frame_positions(aimd["pos_frames"][0])
        cell = ctx["cell"]
        rmsds = []
        step = max(1, npos // 10)
        for f in aimd["pos_frames"][1::step][:20]:
            pos = _frame_positions(f)
            d = pos - pos0
            d = d - cell * np.round(d / cell)
            rmsds.append(float(np.sqrt(np.mean(np.sum(d * d, axis=1)))))
        diag["rmsd_spread_A"] = round(float(np.max(rmsds)), 3) if rmsds else None
        if rmsds and max(rmsds) < 0.02:
            errs.append("L4: structures are all identical (no real sampling)")
            ok = False
    total_train = sum(s["frames"] for s in ctx["training_sets"])
    diag["total_training_frames"] = total_train
    if total_train < thr.get("min_training_frames", 10):
        errs.append(f"L4: training data has only {total_train} frames")
        ok = False
    return ok, errs, diag


def _check_L5(ctx):
    errs, diag = [], {}
    models = ctx["model_files"]
    diag["model_files"] = [str(m) for m in models]
    ok = True
    if not models:
        errs.append("L5: manifest references no model file")
        ok = False
        return ok, errs, diag
    loaded = []
    for m in models:
        dp, tm = load_model(m)
        if dp is None:
            errs.append(f"L5: model not loadable: {m.name}")
            ok = False
            continue
        diag[f"type_map_{m.name}"] = tm
        exp_tm = ctx["thresholds"]["levels"]["L5"].get("type_map", ["O", "H"])
        if set(tm) != set(exp_tm):
            errs.append(f"L5: type_map {tm} != expected {exp_tm}")
            ok = False
        loaded.append((m, dp, tm))
    if not loaded:
        return ok, errs, diag
    m0, dp0, tm0 = loaded[0]
    ctx["primary_model"] = (m0, dp0, tm0)
    f0 = ctx["aimd"]["pos_frames"][0] if ctx["aimd"] else None
    if f0 is None:
        errs.append("L5: no frame to probe inference")
        ok = False
    else:
        sym = np.array([a[0] for a in f0["atoms"]])
        pos = _frame_positions(f0)
        try:
            e, f = dp_infer_frame(dp0, sym, pos, ctx["cell"])
            diag["probe_energy_eV"] = round(e, 4)
            diag["probe_max_force"] = round(float(np.max(np.abs(f))), 4)
            if not (math.isfinite(e) and np.all(np.isfinite(f))):
                errs.append("L5: model inference returns non-finite E/F")
                ok = False
        except Exception as ex:
            errs.append(f"L5: model inference failed: {ex}")
            ok = False
    if len(models) > 1:
        blobs = set()
        for m in models:
            try:
                blobs.add(m.read_bytes()[:256])
            except Exception:
                continue
        diag["unique_model_prefixes"] = len(blobs)
        if len(blobs) < len(models):
            errs.append("L5: multiple model files are byte-identical copies")
            ok = False
    return ok, errs, diag


def _check_L6(ctx):
    errs, diag = [], {}
    art = ctx["al_artifacts"]
    aimd = ctx["aimd"]
    thr = ctx["thresholds"]["levels"]["L6"]
    min_rounds = thr.get("min_rounds", 1)
    rounds = len(art["rounds"])
    diag["training_rounds"] = rounds
    diag["acquisition_artifacts"] = len(art["acquisition"])
    ok = True
    if rounds < min_rounds + 1:
        errs.append(f"L6: only {rounds} training round(s) found (need >= {min_rounds + 1}: at least one retraining)")
        ok = False
    if not art["acquisition"]:
        errs.append("L6: no model-driven acquisition artifacts (LAMMPS explore / model_devi / screening)")
        ok = False
    # new labeled configurations: training positions not present in the primary
    # AIMD but present in some real CP2K output (i.e. the AL label sets). The
    # "already-have" set is ONLY the primary AIMD trajectory — AL label runs
    # (which live in all_pos_traj too) must count as NEW, not as duplicates.
    ref_union = _all_reference_positions(ctx)
    aimd_set = set()
    for f in ctx["aimd"]["pos_frames"]:
        aimd_set.add(_coords_hash(np.round(_frame_positions(f), 2)))
    trained = _training_frames(ctx)
    new_positions = []
    seen = set()
    total_frames = 0
    for t in trained:
        poslist = t["positions"] if isinstance(t["positions"], list) else list(t["positions"])
        for pos in poslist:
            pos = np.asarray(pos)
            total_frames += 1
            hkey = _coords_hash(np.round(pos, 2))
            if hkey in aimd_set or hkey in seen:
                continue
            # new configs must themselves trace to a real CP2K output (not forged)
            if any(frame_match(pos, rp, ctx["cell"]) for rp in ref_union[:400]):
                seen.add(hkey)
                new_positions.append(pos)
    diag["new_labeled_configs"] = len(new_positions)
    diag["total_training_frames"] = total_frames
    if len(new_positions) < 1:
        errs.append("L6: no newly-labeled configurations beyond the initial AIMD (no real active learning)")
        ok = False
    return ok, errs, diag


def _check_L7(ctx):
    errs, diag = [], {}
    prim = ctx.get("primary_model")
    if not prim:
        return False, errs, {"note": "no loadable model"}
    m0, dp, tm = prim
    hv = ctx["hidden_frames"]
    thr = ctx["thresholds"]["levels"]["L7"]
    natoms = len(hv[0]["symbols"])
    # cached inference (expensive) keyed by model bytes + hidden set bytes
    cache_key = (_model_cache_key(m0), _coords_hash(np.round(hv[0]["positions"], 2))[:64])
    if cache_key in _L7_CACHE:
        diag.update(_L7_CACHE[cache_key])
        ok = _L7_CACHE[cache_key]["ok"]
        if not ok:
            errs.append(f"L7: hidden E/F thresholds not met "
                        f"(E {_L7_CACHE[cache_key]['energy_rmse_eV_per_atom_aligned']:.5f}, "
                        f"F {_L7_CACHE[cache_key]['force_rmse_eV_A']:.4f})")
        return ok, errs, diag
    pred_e, pred_f, ref_f = [], [], []
    for f in hv:
        try:
            e, fr = dp_infer_frame(dp, f["symbols"], f["positions"], ctx["cell"])
        except Exception:
            pred_e.append(np.nan)
            pred_f.append(np.full_like(f["forces"], np.nan) if f["forces"] is not None else np.zeros((natoms, 3)))
            ref_f.append(f["forces"] if f["forces"] is not None else np.zeros((natoms, 3)))
            continue
        pred_e.append(e)
        pred_f.append(fr)
        ref_f.append(f["forces"])
    pred_e = np.array(pred_e)
    pred_f = np.array(pred_f)
    ref_f = np.array(ref_f)
    ok = True
    if np.any(~np.isfinite(pred_e)) or np.any(~np.isfinite(pred_f)):
        errs.append("L7: model inference on hidden set produced non-finite values")
        ok = False
    e_diff = (pred_e - np.array([f["energy"] for f in hv])) / natoms
    e_aligned = e_diff - np.nanmean(e_diff)
    e_rmse = float(np.sqrt(np.nanmean(e_aligned ** 2)))
    e_rmse_raw = float(np.sqrt(np.nanmean((e_diff - 0) ** 2)))
    f_rmse = float(np.sqrt(np.nanmean((pred_f - ref_f) ** 2)))
    diag.update({
        "energy_rmse_eV_per_atom_aligned": round(e_rmse, 6),
        "energy_rmse_eV_per_atom_raw": round(e_rmse_raw, 6),
        "force_rmse_eV_A": round(f_rmse, 6),
        "frames": len(hv),
    })
    if e_rmse > thr.get("energy_rmse_eV_per_atom_max", 0.01):
        errs.append(f"L7: hidden energy RMSE {e_rmse:.5f} eV/atom > {thr['energy_rmse_eV_per_atom_max']}")
        ok = False
    if f_rmse > thr.get("force_rmse_eV_A_max", 0.06):
        errs.append(f"L7: hidden force RMSE {f_rmse:.4f} eV/A > {thr['force_rmse_eV_A_max']}")
        ok = False
    diag["ok"] = ok
    _L7_CACHE[cache_key] = dict(diag)
    return ok, errs, diag


def _check_L8(ctx):
    errs, diag = [], {}
    prim = ctx.get("primary_model")
    if not prim:
        return False, errs, {"note": "no loadable model"}
    m0, dp, tm = prim
    thr = ctx["thresholds"]["levels"]["L8"]
    steps = nvt_steps()
    temp = nvt_temperature()
    cache_key = (_model_cache_key(m0), steps, temp)
    if cache_key in _L8_CACHE:
        out = _L8_CACHE[cache_key]
    else:
        out = _run_hidden_nvt(ctx, dp, tm, steps=steps, temp=temp)
        _L8_CACHE[cache_key] = out
    diag.update(out["diag"])
    ok = True
    if out.get("error"):
        errs.append(f"L8: hidden NVT failed: {out['error']}")
        ok = False
        return ok, errs, diag
    if not out["frames"]:
        errs.append("L8: hidden NVT produced no trajectory frames")
        ok = False
        return ok, errs, diag
    frames = out["frames"]
    temps = out["temps"]
    diag["nvt_frames"] = len(frames)
    diag["temp_mean_K"] = round(float(np.mean(temps)), 1) if temps else None
    diag["temp_std_K"] = round(float(np.std(temps)), 1) if len(temps) > 1 else None
    if thr.get("min_steps", 100) and len(frames) < thr["min_steps"]:
        errs.append(f"L8: only {len(frames)} NVT frames (< {thr['min_steps']})")
        ok = False
    if thr.get("no_nan") and (not temps or any(not math.isfinite(t) for t in temps)):
        errs.append("L8: non-finite temperature in NVT")
        ok = False
    band = thr.get("mean_temp_band_K", [250, 350])
    if temps and not (band[0] <= float(np.mean(temps)) <= band[1]):
        errs.append(f"L8: NVT mean temperature {np.mean(temps):.0f} K outside [{band[0]},{band[1]}]")
        ok = False
    if thr.get("no_lost_atoms"):
        for f in frames:
            if f["natoms"] != ctx["expected_natoms"]:
                errs.append(f"L8: lost atoms at step {f['step']} ({f['natoms']} != {ctx['expected_natoms']})")
                ok = False
                break
    ctx["nvt_result"] = out
    return ok, errs, diag


def _run_hidden_nvt(ctx, dp, tm, steps=5000, temp=300.0, seed=42):
    """Run LAMMPS NVT with the primary model from hidden frame 0. Returns dict
    with 'frames' (each {'step','natoms','positions','symbols'}), 'temps',
    'diag', 'error'."""
    if not ctx.get("model_files"):
        return {"frames": [], "temps": [], "diag": {}, "error": "no model files"}
    model_abs = ctx["model_files"][0].resolve()
    f0 = ctx["hidden_frames"][0]
    symbols = list(f0["symbols"])
    pos = f0["positions"]
    cell = ctx["cell"]
    # LAMMPS atom type i corresponds to model type_map[i-1]. Masses MUST follow
    # the model's type order, not a hard-coded O-first assumption.
    tm = list(tm)
    MASS = {"O": 15.999, "H": 1.008}
    if "O" not in tm or "H" not in tm:
        return {"frames": [], "temps": [], "diag": {}, "error": f"model type_map {tm} lacks O or H"}
    type_mass = {i + 1: MASS[tm[i]] for i in range(len(tm))}
    lmp_type = {sym: tm.index(sym) + 1 for sym in ("O", "H")}

    tmp = Path(tempfile.mkdtemp(prefix="ai2kit-nvt-"))
    try:
        with open(tmp / "data.water", "w") as fh:
            fh.write("water NVT\n\n")
            fh.write("2 atom types\n\n")
            fh.write(f"0.0 {cell:.6f} xlo xhi\n0.0 {cell:.6f} ylo yhi\n0.0 {cell:.6f} zlo zhi\n\n")
            fh.write("Masses\n\n")
            for t in (1, 2):
                fh.write(f"{t} {type_mass[t]}\n")
            fh.write("\nAtoms\n\n")
            for i, s in enumerate(symbols, start=1):
                t = lmp_type[s]
                fh.write(f"{i} {t} {pos[i-1,0]:.6f} {pos[i-1,1]:.6f} {pos[i-1,2]:.6f}\n")
        dt_ps = 0.0005  # 0.5 fs
        n_dump = max(1, steps // 40)
        in_text = f"""units metal
atom_style atomic
boundary p p p
read_data data.water
pair_style deepmd {model_abs}
pair_coeff * *
velocity all create {temp:.1f} {seed} mom yes rot yes dist gaussian
fix 1 all nvt temp {temp:.1f} {temp:.1f} 100.0
timestep {dt_ps}
thermo {n_dump}
thermo_style custom step temp
dump 1 all custom {n_dump} dump.nvt id type x y z
run {steps}
"""
        (tmp / "nvt.in").write_text(in_text)
        lmp_bin = shutil.which("lmp")
        if not lmp_bin:
            return {"frames": [], "temps": [], "diag": {}, "error": "lmp not found on PATH"}
        proc = subprocess.run(
            [lmp_bin, "-i", "nvt.in"],
            cwd=str(tmp), capture_output=True, text=True, timeout=3600,
        )
        log = (proc.stdout or "") + (proc.stderr or "")
        temps = []
        for line in log.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].lstrip("-").isdigit() and _is_number(parts[1]):
                temps.append(float(parts[1]))
        dump = tmp / "dump.nvt"
        frames = _parse_lammps_dump(dump) if dump.is_file() else []
        for f in frames:
            f["symbols"] = np.array(
                [tm[f["types"][i] - 1] if 1 <= f["types"][i] <= len(tm) else "X" for i in range(len(f["types"]))]
            )
        return {"frames": frames, "temps": temps,
                "diag": {"nvt_exit": proc.returncode, "lmp_found": True, "log_chars": len(log)},
                "error": None}
    except subprocess.TimeoutExpired:
        return {"frames": [], "temps": [], "diag": {}, "error": "LAMMPS NVT timed out"}
    except Exception as ex:
        return {"frames": [], "temps": [], "diag": {}, "error": f"{type(ex).__name__}: {ex}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def _parse_lammps_dump(path: Path):
    frames = []
    with open(path) as fh:
        lines = fh.readlines()
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].startswith("ITEM: TIMESTEP"):
            step = int(lines[i + 1].strip())
            i += 2
            natoms = 0
            while i < n and not lines[i].startswith("ITEM: NUMBER OF ATOMS"):
                i += 1
            if i < n:
                natoms = int(lines[i + 1].strip())
                i += 2
            while i < n and not lines[i].startswith("ITEM: BOX BOUNDS"):
                i += 1
            i += 4
            while i < n and not lines[i].startswith("ITEM: ATOMS"):
                i += 1
            i += 1
            types = np.zeros(natoms, dtype=int)
            pos = np.zeros((natoms, 3))
            for k in range(natoms):
                parts = lines[i + k].split()
                if len(parts) < 5:
                    continue
                types[k] = int(parts[1])
                pos[k, :] = [float(parts[2]), float(parts[3]), float(parts[4])]
            frames.append({"step": step, "natoms": natoms, "types": types, "positions": pos})
            i += natoms
        else:
            i += 1
    return frames


def _check_L9(ctx):
    errs, diag = [], {}
    nvt = ctx.get("nvt_result")
    if not nvt or not nvt.get("frames"):
        return False, errs, {"note": "L8 NVT unavailable"}
    thr = ctx["thresholds"]["levels"]["L9"]
    rdf_ref = ctx["rdf_reference"]
    cell = ctx["cell"]
    poslist = [f["positions"] for f in nvt["frames"]]
    symlist = [f["symbols"] for f in nvt["frames"]]
    pairs = [("O", "O"), ("O", "H"), ("H", "H")]
    labels = ["OO", "OH", "HH"]
    windows = {
        "OO": thr.get("OO_first_peak_angstrom", [2.6, 3.0]),
        "OH": thr.get("OH_first_peak_angstrom", [0.90, 1.05]),
        "HH": thr.get("HH_first_peak_angstrom", [1.45, 1.70]),
    }
    ok = True
    for pair, label in zip(pairs, labels):
        r, g = compute_rdf(poslist, symlist, pair, cell)
        peak = first_peak(r, g, 0.5, 6.0)
        diag[f"{label}_first_peak_A"] = round(peak, 3) if peak else None
        if peak is None or not (windows[label][0] <= peak <= windows[label][1]):
            errs.append(f"L9: {label} first peak {peak if peak is not None else 'None'} A outside window {windows[label]}")
            ok = False
        ref_peak = (rdf_ref.get(label) or {}).get("first_peak_angstrom")
        if ref_peak and peak:
            shift = abs(peak - ref_peak)
            diag[f"{label}_shift_A"] = round(shift, 3)
            if shift > thr.get("max_peak_shift_angstrom", 0.15):
                errs.append(f"L9: {label} peak shift {shift:.3f} A > {thr['max_peak_shift_angstrom']}")
                ok = False
        if thr.get("no_nonphysical_short_range"):
            # any significant g(r) below the physical contact floor (first nonneg bin under 0.5 A)
            m = (r > 0.05) & (r < 0.5)
            if np.any(m) and np.max(g[m]) > 0.2:
                errs.append(f"L9: {label} has nonphysical short-range peak (r<0.5 A)")
                ok = False
    return ok, errs, diag


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------

def verify(submission, profile="paper"):
    """Return {'valid', 'levels', 'errors', 'diagnostics', 'reward'}."""
    sub = Path(submission)
    report = {"valid": False, "levels": {}, "errors": [], "diagnostics": {}, "reward": 0}
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

    if profile == "paper" and manifest.get("profile") == "smoke":
        report["errors"].append("submission is a smoke-profile run; cannot satisfy formal grading")
        return report

    ctx = {
        "submission": sub,
        "manifest": manifest,
        "thresholds": thresholds,
        "system_cell": 12.4,
        "expected_natoms": 192,
    }

    hv_path = hidden_validation_path()
    if not hv_path.is_file():
        report["errors"].append(f"hidden validation missing: {hv_path}")
        return report
    from ase.io import read as ase_read
    try:
        atoms = ase_read(str(hv_path), index=":")
    except Exception as ex:
        report["errors"].append(f"hidden extxyz unreadable: {ex}")
        return report
    hidden_frames = []
    for a in atoms:
        hidden_frames.append({
            "symbols": np.array(a.get_chemical_symbols()),
            "positions": a.get_positions(),
            "cell": a.get_cell()[:].copy(),
            "energy": _frame_energy_eV(a),
            "forces": _frame_forces(a),
        })
    ctx["hidden_frames"] = hidden_frames
    if hidden_frames:
        a = float(np.linalg.norm(hidden_frames[0]["cell"][0]))
        ctx["cell"] = round(a, 6)
    else:
        ctx["cell"] = 12.4

    try:
        ctx["rdf_reference"] = json.loads(hidden_rdf_path().read_text()) if hidden_rdf_path().is_file() else {}
    except Exception:
        ctx["rdf_reference"] = {}

    ctx["all_pos_traj"] = find_all_cp2k_pos(sub)
    ctx["aimd"] = find_cp2k_aimd(sub)
    ctx["training_sets"] = find_training_sets(sub)
    ctx["labeled_extxyz"] = find_labeled_extxyz(sub)
    ctx["al_artifacts"] = find_al_artifacts(sub)
    ctx["model_files"] = find_model_files(sub, manifest)

    checks = [
        ("L0", _check_L0),
        ("L1", _check_L1),
        ("L2", _check_L2),
        ("L3", _check_L3),
        ("L4", _check_L4),
        ("L5", _check_L5),
        ("L6", _check_L6),
        ("L7", _check_L7),
        ("L8", _check_L8),
        ("L9", _check_L9),
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
    r = verify(root, os.environ.get("AI2KIT_PROFILE", "paper"))
    print(json.dumps(r, indent=2, default=str))
    sys.exit(0 if r["valid"] else 1)
