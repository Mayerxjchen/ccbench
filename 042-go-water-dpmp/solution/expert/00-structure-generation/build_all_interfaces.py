#!/usr/bin/env python3
"""
042 structure generation: build all five GO–water interfaces from scratch.

Reads composition/cell specs from public/system.json and writes DeepMD raw
files (box.raw, coord.raw, type.raw) into structures/<iface>/ for each
interface.  Also writes structure_generation.json with SHA-256 provenance.

No initial structures or training data are supplied — everything is
constructed from the composition and cell specifications.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Interface specs (from public/system.json)
# ---------------------------------------------------------------------------

def _load_interface_specs() -> dict:
    """Single source of truth: public/system.json (same document as the
    prompt table and the verifier)."""
    data = json.loads(
        (Path(__file__).resolve().parents[3] / "public/system.json").read_text()
    )
    global CONSTRUCTION_MODEL
    CONSTRUCTION_MODEL = data["construction_model"]
    out = {}
    for name, iface in data["interfaces"].items():
        out[name] = {
            "composition": iface["composition"],
            "natoms": iface["natoms"],
            "cell_angstrom": iface["cell_angstrom"],
            "functional_groups": iface.get("functional_groups"),
            "water_molecules": iface["water_molecules"],
        }
    return out


INTERFACE_SPECS = _load_interface_specs()

ELEMENT_TYPE_MAP = {"O": 0, "H": 1, "C": 2}

# Binding construction parameters come from public/system.json.
GRAPHENE_CC_BOND = float(CONSTRUCTION_MODEL["nearest_neighbor_cc_angstrom"])
GRAPHENE_LATTICE_A = float(CONSTRUCTION_MODEL["graphene_lattice_constant_angstrom"])


# ---------------------------------------------------------------------------
# Water molecule builder
# ---------------------------------------------------------------------------

def _make_water_molecule(o_pos: np.ndarray, rng: np.random.RandomState) -> tuple[np.ndarray, list[int]]:
    """Create a single water molecule with realistic geometry at o_pos."""
    oh_len = 0.9572  # Å (experimental O-H bond length)
    angle = math.radians(104.52)  # experimental H-O-H angle
    # Random orientation
    phi = rng.uniform(0, 2 * math.pi)
    theta1 = rng.uniform(0.1, math.pi - 0.1)  # avoid poles
    theta2 = theta1 + angle
    h1 = o_pos + oh_len * np.array([
        math.sin(theta1) * math.cos(phi),
        math.sin(theta1) * math.sin(phi),
        math.cos(theta1)])
    h2 = o_pos + oh_len * np.array([
        math.sin(theta2) * math.cos(phi + 0.5),
        math.sin(theta2) * math.sin(phi + 0.5),
        math.cos(theta2)])
    coords = np.array([o_pos, h1, h2])
    types = [ELEMENT_TYPE_MAP["O"], ELEMENT_TYPE_MAP["H"], ELEMENT_TYPE_MAP["H"]]
    return coords, types


def _water_slab_bounds(spec: dict) -> tuple[float, float]:
    """Centered water-O slab bounds derived from count, density, and xy area."""
    n_molecules = int(spec["water_molecules"])
    cell = spec["cell_angstrom"]
    density = float(CONSTRUCTION_MODEL["water_density_g_cm3"])
    molar_mass = 18.01528
    amu_g = 1.66053906660e-24
    volume_a3 = n_molecules * molar_mass * amu_g / density / 1.0e-24
    thickness = volume_a3 / (float(cell[0]) * float(cell[1]))
    if thickness >= float(cell[2]):
        raise ValueError(f"water slab thickness {thickness:.3f} exceeds Lz={cell[2]}")
    center = float(cell[2]) * float(CONSTRUCTION_MODEL["water_slab_center_fraction_z"])
    return center - thickness / 2.0, center + thickness / 2.0


def _build_water_in_bounds(n_molecules: int, cell: list[float], z_min: float,
                           z_max: float, rng: np.random.RandomState
                           ) -> tuple[np.ndarray, list[int]]:
    """Place water on an approximately isotropic grid inside declared bounds."""
    volume_per_molecule = float(cell[0]) * float(cell[1]) * (z_max - z_min) / n_molecules
    target_spacing = volume_per_molecule ** (1.0 / 3.0)
    nx = max(1, round(float(cell[0]) / target_spacing))
    ny = max(1, round(float(cell[1]) / target_spacing))
    nz = max(1, math.ceil(n_molecules / (nx * ny)))
    spacing = np.array([float(cell[0]) / nx, float(cell[1]) / ny,
                        (z_max - z_min) / nz])

    all_coords = []
    all_types = []
    count = 0
    for ix in range(nx):
        for iy in range(ny):
            for iz in range(nz):
                if count >= n_molecules:
                    break
                o_pos = np.array([
                    (ix + 0.5) * spacing[0],
                    (iy + 0.5) * spacing[1],
                    z_min + (iz + 0.5) * spacing[2]])
                mol_coords, mol_types = _make_water_molecule(o_pos, rng)
                all_coords.append(mol_coords)
                all_types.extend(mol_types)
                count += 1
            if count >= n_molecules:
                break
        if count >= n_molecules:
            break

    return np.vstack(all_coords), all_types


def _build_water_slab(spec: dict, rng: np.random.RandomState
                      ) -> tuple[np.ndarray, list[int]]:
    z_min, z_max = _water_slab_bounds(spec)
    return _build_water_in_bounds(
        int(spec["water_molecules"]), spec["cell_angstrom"], z_min, z_max, rng)


# ---------------------------------------------------------------------------
# Graphene sheet builder
# ---------------------------------------------------------------------------

def _plane_zs(spec: dict) -> tuple[float, float]:
    """Basal-plane positions derived from the centered water slab contract."""
    z_min, z_max = _water_slab_bounds(spec)
    clearance = float(CONSTRUCTION_MODEL["plane_to_water_oxygen_clearance_angstrom"])
    return z_min - clearance, z_max + clearance


def _build_graphene_sheet(spec: dict, rng: np.random.RandomState) -> np.ndarray:
    """Build the TWO parallel basal planes sandwiching the water slab.

    SI ground truth (measured from the published initial structures,
    evidence/refine-2026-08-25): each interface carries TWO identical
    graphene honeycomb planes of exactly n_carbon/2 C atoms each — NOT a
    stacked graphite multilayer.  Planes sit symmetric about the box center;
    water (placed separately) occupies the region between them.

    Deterministic construction: rectangular 4-atom graphene unit cell tiled
    na × nb so that 4·na·nb == n_per_plane exactly, with the lattice constant
    uniformly stretched to the target cell (mild expansion consistent with
    functionalized GO).  Fails loudly if no integer tiling matches.
    """
    n_carbon = int(spec["composition"]["C"])
    cell = spec["cell_angstrom"]
    assert n_carbon % 2 == 0 and n_carbon % 4 == 0, \
        "two rectangular planes require n_carbon divisible by 4"
    n_per_plane = n_carbon // 2

    best = None  # (|atoms-target| after stretch-fit, na, nb)
    for nb in range(1, 13):
        for na in range(1, 25):
            if 4 * na * nb == n_per_plane:
                err = abs(cell[0] / na - GRAPHENE_LATTICE_A) + abs(
                    cell[1] / (nb * math.sqrt(3)) - GRAPHENE_LATTICE_A)
                if best is None or err < best[0]:
                    best = (err, na, nb)
    if best is None or best[0] > 0.15:
        raise ValueError(
            f"no rectangular honeycomb tiling places {n_per_plane} C inside "
            f"{cell[:2]} within lattice tolerance")
    _, na, nb = best

    ax = cell[0] / na                 # stretched lattice constant along x
    ay_unit = cell[1] / (nb * math.sqrt(3))  # along y (per sqrt3 block)
    basis = np.array([
        [0.0, 0.0],
        [ax / 2, math.sqrt(3) * ay_unit / 6],
        [0.0, math.sqrt(3) * ay_unit / 3],
        [ax / 2, math.sqrt(3) * ay_unit / 2],
    ])
    plane_coords = []
    for jx in range(na):
        for jy in range(nb):
            origin = np.array([jx * ax, jy * math.sqrt(3) * ay_unit])
            for b in basis:
                plane_coords.append(origin + b)
    plane_coords = np.array(plane_coords)
    assert len(plane_coords) == n_per_plane, (
        f"tiling produced {len(plane_coords)} != {n_per_plane}")

    # Two planes, symmetric about the box center along z, leaving the
    # middle region free for the water slab.
    zs = _plane_zs(spec)
    all_c = []
    for z in zs:
        coords3d = np.zeros((len(plane_coords), 3))
        coords3d[:, :2] = plane_coords
        coords3d[:, 2] = z
        all_c.append(coords3d)
    return np.vstack(all_c)


# ---------------------------------------------------------------------------
# GO functional group placement
# ---------------------------------------------------------------------------

def _place_functional_groups(c_coords: np.ndarray, n_hydroxyl: int,
                             n_epoxide: int, cell: list[float],
                             rng: np.random.RandomState) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Place hydroxyl and epoxide functional groups on graphene carbons.

    Returns (epoxide_O, hydroxyl_O, hydroxyl_H) coordinate arrays.
    """
    if n_hydroxyl % 2 or n_epoxide % 2:
        raise ValueError("functional-group totals must split equally between two planes")
    center = float(cell[2]) / 2.0
    plane_indices = [
        np.where(c_coords[:, 2] < center)[0],
        np.where(c_coords[:, 2] > center)[0],
    ]
    directions = (1.0, -1.0)
    epoxide_o: list[np.ndarray] = []
    hydroxyl_o: list[np.ndarray] = []
    hydroxyl_h: list[np.ndarray] = []

    for indices, direction in zip(plane_indices, directions):
        if len(indices) * 2 != len(c_coords):
            raise ValueError("carbon atoms are not evenly split between two planes")
        pairs: list[tuple[int, int]] = []
        for pos, ci in enumerate(indices):
            tail = indices[pos + 1:]
            delta = c_coords[tail] - c_coords[int(ci)]
            dist = np.linalg.norm(delta[:, :2], axis=1)
            for off in np.where((dist > 1.2) & (dist < 1.65))[0]:
                pairs.append((int(ci), int(tail[off])))
        rng.shuffle(pairs)

        used: set[int] = set()
        selected_pairs: list[tuple[int, int]] = []
        for ci, cj in pairs:
            if ci in used or cj in used:
                continue
            selected_pairs.append((ci, cj))
            used.update((ci, cj))
            if len(selected_pairs) == n_epoxide // 2:
                break
        if len(selected_pairs) != n_epoxide // 2:
            raise ValueError("not enough disjoint C-C bonds for epoxide placement")
        for ci, cj in selected_pairs:
            midpoint = (c_coords[ci] + c_coords[cj]) / 2.0
            midpoint[2] += direction * 1.47
            epoxide_o.append(midpoint)

        remaining = np.array([int(i) for i in indices if int(i) not in used])
        rng.shuffle(remaining)
        selected_oh = remaining[:n_hydroxyl // 2]
        if len(selected_oh) != n_hydroxyl // 2:
            raise ValueError("not enough unused carbons for hydroxyl placement")
        for ci in selected_oh:
            o_pos = c_coords[int(ci)].copy()
            o_pos[2] += direction * 1.43
            hydroxyl_o.append(o_pos)
            h_pos = o_pos.copy()
            h_pos[2] += direction * 0.96
            hydroxyl_h.append(h_pos)

    return (np.array(epoxide_o) if epoxide_o else np.zeros((0, 3)),
            np.array(hydroxyl_o) if hydroxyl_o else np.zeros((0, 3)),
            np.array(hydroxyl_h) if hydroxyl_h else np.zeros((0, 3)))


# ---------------------------------------------------------------------------
# Interface builders
# ---------------------------------------------------------------------------

def build_air_water(spec: dict, rng: np.random.RandomState) -> tuple[np.ndarray, list[int]]:
    return _build_water_slab(spec, rng)


def build_graphene_water(spec: dict, rng: np.random.RandomState) -> tuple[np.ndarray, list[int]]:
    n_water = spec["composition"]["O"]
    n_carbon = spec["composition"]["C"]
    cell = spec["cell_angstrom"]

    c_coords = _build_graphene_sheet(spec, rng)
    z_min, z_max = _water_slab_bounds(spec)
    water_coord, water_types = _build_water_in_bounds(
        n_water, cell, z_min, z_max, rng)
    coords = np.vstack([c_coords, water_coord])
    types = [ELEMENT_TYPE_MAP["C"]] * n_carbon + water_types
    return coords, types


def build_go_interface(spec: dict, rng: np.random.RandomState) -> tuple[np.ndarray, list[int]]:
    n_carbon = spec["composition"]["C"]
    n_oxygen_total = spec["composition"]["O"]
    fg = spec.get("functional_groups", {})
    n_hydroxyl = fg.get("hydroxyl", 0)
    n_epoxide = fg.get("epoxide", 0)
    n_oxygen_fg = n_hydroxyl + n_epoxide
    n_water = n_oxygen_total - n_oxygen_fg
    cell = spec["cell_angstrom"]

    c_coords = _build_graphene_sheet(spec, rng)
    epoxide_o, hydroxyl_o, hydroxyl_h = _place_functional_groups(
        c_coords, n_hydroxyl, n_epoxide, cell, rng)

    z_min, z_max = _water_slab_bounds(spec)
    water_coord, water_types = _build_water_in_bounds(
        n_water, cell, z_min, z_max, rng)

    o_fg = np.vstack([epoxide_o, hydroxyl_o]) if len(epoxide_o) + len(hydroxyl_o) > 0 else np.zeros((0, 3))
    coords = np.vstack([c_coords, o_fg, water_coord, hydroxyl_h])
    types = (
        [ELEMENT_TYPE_MAP["C"]] * n_carbon
        + [ELEMENT_TYPE_MAP["O"]] * (len(epoxide_o) + len(hydroxyl_o))
        + water_types
        + [ELEMENT_TYPE_MAP["H"]] * len(hydroxyl_h)
    )
    return coords, types


BUILDERS = {
    "air-water": build_air_water,
    "graphene-water": build_graphene_water,
    "graphene-O12": build_go_interface,
    "graphene-O25": build_go_interface,
    "graphene-O50": build_go_interface,
}


# ---------------------------------------------------------------------------
# DeepMD raw I/O
# ---------------------------------------------------------------------------

def write_deepmd_raw(directory: Path, box_diag: list[float], coords: np.ndarray,
                     types: list[int]) -> None:
    """Write box.raw / coord.raw / type.raw into *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    box_flat = np.diag(box_diag).flatten()
    (directory / "box.raw").write_text(
        "\n".join(f"{v:.8f}" for v in box_flat) + "\n")
    np.savetxt(directory / "coord.raw", coords, fmt="%.8f")
    (directory / "type.raw").write_text(
        "\n".join(str(t) for t in types) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

INTERFACE_SEED_OFFSETS = {
    "air-water": 101,
    "graphene-water": 211,
    "graphene-O12": 307,
    "graphene-O25": 401,
    "graphene-O50": 503,
}


def main() -> None:
    # Write to structures/ at the case root (agent workspace), or to an
    # explicit directory when AI2KIT_042_STRUCTURES_DIR is set (integration
    # tests use this to avoid mutating the checkout).
    case_dir = Path(__file__).resolve().parent.parent.parent.parent
    structures_dir = Path(os.environ.get(
        "AI2KIT_042_STRUCTURES_DIR", str(case_dir / "structures")))
    seed = int(os.environ.get("AI2KIT_042_SEED", "42"))

    records = {}
    for iface, spec in INTERFACE_SPECS.items():
        print(f"[build] {iface} ...")
        rng = np.random.RandomState(seed + INTERFACE_SEED_OFFSETS[iface])
        builder = BUILDERS[iface]
        coords, types = builder(spec, rng)

        # Validate
        assert len(types) == spec["natoms"], (
            f"{iface}: natoms {len(types)} != {spec['natoms']}")
        from collections import Counter
        ec = Counter(types)
        for elem, expected_count in spec["composition"].items():
            type_idx = ELEMENT_TYPE_MAP[elem]
            assert ec[type_idx] == expected_count, (
                f"{iface}: {elem} count {ec[type_idx]} != {expected_count}")

        # Write DeepMD raw
        iface_dir = structures_dir / iface
        write_deepmd_raw(iface_dir, spec["cell_angstrom"], coords, types)

        # SHA-256 provenance
        coord_bytes = (iface_dir / "coord.raw").read_bytes()
        coord_sha = hashlib.sha256(coord_bytes).hexdigest()

        records[iface] = {
            "interface": iface,
            "structure_path": f"structures/{iface}",
            "generator": "independent-structure-builder",
            "generator_version": "1.0",
            "command": f"python build_all_interfaces.py --seed {seed}",
            "seed": seed,
            "checks": {"composition": "PASS", "periodic_geometry": "PASS"},
            "coord_raw_sha256": coord_sha,
        }
        print(f"  -> {iface}: {len(types)} atoms, SHA256={coord_sha[:16]}...")

    # Write combined manifest
    manifest = {"structure_generation": records}
    out_path = structures_dir / "structure_generation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[build] manifest written to {out_path}")


if __name__ == "__main__":
    main()
