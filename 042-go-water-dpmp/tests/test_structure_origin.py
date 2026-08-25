"""L0 structure-origin tests for Case 042 (GO–water DPMP).

Validates that the agent independently constructed all five interfaces
from the composition/cell specs in public/system.json.  Each interface is
checked for correct composition, cell dimensions, functional-group counts,
geometric sanity (O–H bonds, H–O–H angles, intermolecular distance), and
SHA-256 provenance.

Format: DeepMD raw (box.raw / coord.raw / type.raw per interface directory).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

TESTS = Path(__file__).resolve().parent
CASE = TESTS.parent
sys.path.insert(0, str(TESTS))
import verifier  # noqa: E402

# ── Per-interface specs (from public/system.json) ──────────────────────

INTERFACE_SPECS = {
    "air-water": {
        "composition": {"O": 752, "H": 1504},
        "natoms": 2256,
        "cell_angstrom": [29.520, 25.566, 70.000],
        "functional_groups": None,
    },
    "graphene-water": {
        "composition": {"C": 576, "O": 752, "H": 1504},
        "natoms": 2832,
        "cell_angstrom": [29.520, 25.566, 70.000],
        "functional_groups": None,
    },
    "graphene-O12": {
        "composition": {"C": 576, "O": 824, "H": 1536},
        "natoms": 2936,
        "cell_angstrom": [29.712, 25.756, 70.000],
        "functional_groups": {"hydroxyl": 32, "epoxide": 40},
    },
    "graphene-O25": {
        "composition": {"C": 576, "O": 896, "H": 1576},
        "natoms": 3048,
        "cell_angstrom": [29.794, 26.014, 70.000],
        "functional_groups": {"hydroxyl": 72, "epoxide": 72},
    },
    "graphene-O50": {
        "composition": {"C": 576, "O": 1040, "H": 1648},
        "natoms": 3264,
        "cell_angstrom": [29.998, 25.850, 70.000],
        "functional_groups": {"hydroxyl": 144, "epoxide": 144},
    },
}

ELEMENT_TYPE_MAP = {"O": 0, "H": 1, "C": 2}

# Geometric thresholds (from system.json constraints + physical sanity)
OH_BOND_RANGE_ANGSTROM = (0.75, 1.25)
HOH_ANGLE_RANGE_DEGREE = (85.0, 125.0)
MIN_INTERMOLECULAR_DIST_ANGSTROM = 1.2
CELL_TOLERANCE_ANGSTROM = 0.10

# ── DeepMD raw helpers ─────────────────────────────────────────────────


def _minimum_image(delta: np.ndarray, cell: np.ndarray) -> np.ndarray:
    return delta - cell * np.round(delta / cell)


def _write_deepmd_raw(directory: Path, box_diag: list[float], coords: np.ndarray,
                      types: list[int]) -> None:
    """Write box.raw / coord.raw / type.raw into *directory*.

    DeepMD raw format: box.raw = 9 floats (one per line, row-major 3x3 diagonal),
    coord.raw = one atom per line (x y z space-separated), type.raw = one int per line.
    """
    directory.mkdir(parents=True, exist_ok=True)
    # box.raw: 9 values, one per line (row-major 3x3 matrix of diagonal)
    box_flat = np.diag(box_diag).flatten()
    lines = "\n".join(f"{v:.8f}" for v in box_flat) + "\n"
    (directory / "box.raw").write_text(lines)
    # coord.raw: one line per atom "x y z"
    np.savetxt(directory / "coord.raw", coords, fmt="%.8f")
    # type.raw: one int per line
    (directory / "type.raw").write_text(
        "\n".join(str(t) for t in types) + "\n"
    )


def _read_deepmd_raw(directory: Path) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Read box / coords / types from DeepMD raw files."""
    box = np.loadtxt(directory / "box.raw").reshape(3, 3)
    coords = np.loadtxt(directory / "coord.raw").reshape(-1, 3)
    types = [int(t) for t in (directory / "type.raw").read_text().split()]
    return box, coords, types


def _element_counts(types: list[int]) -> dict[str, int]:
    inv = {v: k for k, v in ELEMENT_TYPE_MAP.items()}
    counts: dict[str, int] = {}
    for t in types:
        elem = inv.get(t, f"unknown_{t}")
        counts[elem] = counts.get(elem, 0) + 1
    return counts


# ── Synthetic structure builders (for tests) ──────────────────────────


def _make_water_molecule(o_pos: np.ndarray, rng) -> tuple[np.ndarray, list[int]]:
    """Create a single water molecule with realistic geometry at o_pos."""
    oh_len = 0.96  # Å
    angle = math.radians(104.5)
    # Random orientation
    phi = rng.uniform(0, 2 * math.pi)
    theta1 = rng.uniform(0, math.pi)
    theta2 = theta1 + angle
    h1 = o_pos + oh_len * np.array([
        math.sin(theta1) * math.cos(phi),
        math.sin(theta1) * math.sin(phi),
        math.cos(theta1)])
    h2 = o_pos + oh_len * np.array([
        math.sin(theta2) * math.cos(phi + 0.3),
        math.sin(theta2) * math.sin(phi + 0.3),
        math.cos(theta2)])
    coords = np.array([o_pos, h1, h2])
    types = [ELEMENT_TYPE_MAP["O"], ELEMENT_TYPE_MAP["H"], ELEMENT_TYPE_MAP["H"]]
    return coords, types


def _build_water_slab(n_molecules: int, cell: list[float],
                      seed: int = 42) -> tuple[np.ndarray, list[int]]:
    """Place n_molecules water molecules in a grid with realistic geometry."""
    rng = np.random.RandomState(seed)
    box = np.diag(cell)
    # Grid layout: find cubic root
    n_per_side = max(1, round(n_molecules ** (1 / 3)))
    while n_per_side ** 3 < n_molecules:
        n_per_side += 1
    spacing = np.array(cell) / n_per_side

    all_coords = []
    all_types = []
    count = 0
    for ix in range(n_per_side):
        for iy in range(n_per_side):
            for iz in range(n_per_side):
                if count >= n_molecules:
                    break
                o_pos = np.array([
                    (ix + 0.5) * spacing[0],
                    (iy + 0.5) * spacing[1],
                    (iz + 0.5) * spacing[2]])
                mol_coords, mol_types = _make_water_molecule(o_pos, rng)
                all_coords.append(mol_coords)
                all_types.extend(mol_types)
                count += 1
            if count >= n_molecules:
                break
        if count >= n_molecules:
            break

    return np.vstack(all_coords), all_types


def _build_graphene_sheet(n_carbon: int, cell: list[float],
                          seed: int = 42) -> np.ndarray:
    """Place n_carbon atoms in a graphene-like layered arrangement."""
    rng = np.random.RandomState(seed)
    # 4 layers of graphene, each with n_carbon/4 atoms
    n_per_layer = n_carbon // 4
    layer_heights = [cell[2] * 0.05, cell[2] * 0.10,
                     cell[2] * 0.15, cell[2] * 0.20]
    all_c = []
    for z in layer_heights:
        coords = np.zeros((n_per_layer, 3))
        coords[:, 0] = rng.uniform(0, cell[0], n_per_layer)
        coords[:, 1] = rng.uniform(0, cell[1], n_per_layer)
        coords[:, 2] = z
        all_c.append(coords)
    # Handle remainder
    remainder = n_carbon - n_per_layer * 4
    if remainder > 0:
        coords = np.zeros((remainder, 3))
        coords[:, 0] = rng.uniform(0, cell[0], remainder)
        coords[:, 1] = rng.uniform(0, cell[1], remainder)
        coords[:, 2] = layer_heights[-1]
        all_c.append(coords)
    return np.vstack(all_c)


def _build_graphene_water(spec: dict, seed: int = 42) -> tuple[np.ndarray, list[int]]:
    """Build graphene + water slab."""
    n_water_mol = spec["composition"]["O"]  # one O per water mol
    n_carbon = spec["composition"]["C"]
    rng = np.random.RandomState(seed)
    cell = spec["cell_angstrom"]

    # Graphene carbons in bottom layers (z < 25% of cell)
    c_coords = _build_graphene_sheet(n_carbon, cell, seed)
    # Water in top 65% of cell, using grid placement
    water_all = []
    water_types = []
    n_per_side = max(1, round(n_water_mol ** (1 / 3)))
    while n_per_side ** 3 < n_water_mol:
        n_per_side += 1
    z_min = cell[2] * 0.35
    z_range = cell[2] - z_min
    spacing = [cell[0] / n_per_side, cell[1] / n_per_side, z_range / n_per_side]
    count = 0
    for ix in range(n_per_side):
        for iy in range(n_per_side):
            for iz in range(n_per_side):
                if count >= n_water_mol:
                    break
                o_pos = np.array([
                    (ix + 0.5) * spacing[0],
                    (iy + 0.5) * spacing[1],
                    z_min + (iz + 0.5) * spacing[2]])
                mol_c, mol_t = _make_water_molecule(o_pos, rng)
                water_all.append(mol_c)
                water_types.extend(mol_t)
                count += 1
            if count >= n_water_mol:
                break
        if count >= n_water_mol:
            break

    coords = np.vstack([c_coords] + water_all)
    types = [ELEMENT_TYPE_MAP["C"]] * n_carbon + water_types
    return coords, types


def _build_go_interface(spec: dict, seed: int = 42) -> tuple[np.ndarray, list[int]]:
    """Build GO–water interface with functional groups.

    Functional groups are placed on specific carbon atoms so the classifier
    can correctly identify them:
    - Hydroxyl: O placed 1.43 Å above a C, H placed 0.96 Å above that O
    - Epoxide: O placed 1.47 Å above the midpoint of two adjacent C atoms
    """
    n_carbon = spec["composition"]["C"]
    n_oxygen_total = spec["composition"]["O"]
    fg = spec.get("functional_groups", {})
    n_hydroxyl = fg.get("hydroxyl", 0)
    n_epoxide = fg.get("epoxide", 0)
    n_oxygen_fg = n_hydroxyl + n_epoxide
    n_water_mol = (n_oxygen_total - n_oxygen_fg)  # remaining O are water
    rng = np.random.RandomState(seed)
    cell = spec["cell_angstrom"]

    # Graphene carbons
    c_coords = _build_graphene_sheet(n_carbon, cell, seed)

    # Place epoxide oxygens: bridge two adjacent carbons
    epoxide_o_coords = []
    used_c = set()
    c_idx = 0
    for i in range(n_epoxide):
        # Find two unused carbons
        while c_idx in used_c:
            c_idx += 1
        ci = c_idx
        used_c.add(ci)
        c_idx += 1
        while c_idx in used_c:
            c_idx += 1
        cj = c_idx
        used_c.add(cj)
        c_idx += 1
        midpoint = (c_coords[ci] + c_coords[cj]) / 2
        midpoint[2] += 1.47  # O above the C-C bridge
        epoxide_o_coords.append(midpoint)

    # Place hydroxyl oxygens: one O bonded to one C
    hydroxyl_o_coords = []
    hydroxyl_h_coords = []
    for i in range(n_hydroxyl):
        while c_idx in used_c:
            c_idx += 1
        ci = c_idx
        used_c.add(ci)
        c_idx += 1
        o_pos = c_coords[ci].copy()
        o_pos[2] += 1.43  # O above C
        hydroxyl_o_coords.append(o_pos)
        h_pos = o_pos.copy()
        h_pos[2] += 0.96  # H above O
        hydroxyl_h_coords.append(h_pos)

    o_fg_coords = np.array(epoxide_o_coords + hydroxyl_o_coords)
    h_hydroxyl_coords = np.array(hydroxyl_h_coords) if hydroxyl_h_coords else np.zeros((0, 3))

    # Water above graphene (grid placement in top 60% of cell)
    z_min = cell[2] * 0.40
    water_all = []
    water_types = []
    n_per_side = max(1, round(n_water_mol ** (1 / 3)))
    while n_per_side ** 3 < n_water_mol:
        n_per_side += 1
    z_range = cell[2] - z_min
    sp = [cell[0] / n_per_side, cell[1] / n_per_side, z_range / n_per_side]
    count = 0
    for ix in range(n_per_side):
        for iy in range(n_per_side):
            for iz in range(n_per_side):
                if count >= n_water_mol:
                    break
                o_pos = np.array([
                    (ix + 0.5) * sp[0],
                    (iy + 0.5) * sp[1],
                    z_min + (iz + 0.5) * sp[2]])
                mol_c, mol_t = _make_water_molecule(o_pos, rng)
                water_all.append(mol_c)
                water_types.extend(mol_t)
                count += 1
            if count >= n_water_mol:
                break
        if count >= n_water_mol:
            break

    coords = np.vstack([c_coords, o_fg_coords] + water_all + [h_hydroxyl_coords])
    types = (
        [ELEMENT_TYPE_MAP["C"]] * n_carbon
        + [ELEMENT_TYPE_MAP["O"]] * n_oxygen_fg
        + water_types
        + [ELEMENT_TYPE_MAP["H"]] * n_hydroxyl
    )
    return coords, types


# ── Submission builder ─────────────────────────────────────────────────


def make_submission(tmp_path: Path, iface: str, *,
                    coords: np.ndarray | None = None,
                    types: list[int] | None = None,
                    box_override: list[float] | None = None,
                    mutate=None) -> tuple[Path, dict]:
    """Create a minimal agent submission workspace for one interface."""
    spec = INTERFACE_SPECS[iface]
    cell = box_override or spec["cell_angstrom"]
    if coords is None or types is None:
        if "graphene-O" in iface:
            coords, types = _build_go_interface(spec)
        elif "graphene" in iface:
            coords, types = _build_graphene_water(spec)
        else:
            coords, types = _build_water_slab(
                spec["composition"]["O"], cell)

    sub = tmp_path / "submission"
    iface_dir = sub / f"structures/{iface}"
    _write_deepmd_raw(iface_dir, cell, coords, types)

    # Compute SHA-256 of coord.raw (the primary provenance input)
    coord_bytes = (iface_dir / "coord.raw").read_bytes()
    coord_sha = hashlib.sha256(coord_bytes).hexdigest()

    record = {
        "interface": iface,
        "structure_path": f"structures/{iface}",
        "generator": "independent-structure-builder",
        "generator_version": "1.0",
        "command": f"python build_structure.py --interface {iface} --seed 42",
        "seed": 42,
        "checks": {"composition": "PASS", "periodic_geometry": "PASS"},
        "coord_raw_sha256": coord_sha,
    }
    if mutate:
        mutate(record, sub)

    manifest = {"structure_generation": record}
    (sub / "final").mkdir(parents=True, exist_ok=True)
    (sub / "final/manifest.json").write_text(json.dumps(manifest))
    return sub, manifest


def check(sub: Path, manifest: dict) -> tuple[bool, list[str], dict]:
    return verifier.check_structure_origin(sub, manifest)


# ── Tests ──────────────────────────────────────────────────────────────

ALL_IFACES = list(INTERFACE_SPECS.keys())


class TestComposition:
    """Per-interface element counts must match system.json specs."""

    @pytest.mark.parametrize("iface", ALL_IFACES)
    def test_composition_matches_spec(self, tmp_path, iface):
        sub, manifest = make_submission(tmp_path, iface)
        ok, errors, diag = check(sub, manifest)
        assert ok, errors
        spec = INTERFACE_SPECS[iface]
        for elem, count in spec["composition"].items():
            assert diag["element_counts"].get(elem, 0) == count, (
                f"{iface}: {elem} count {diag['element_counts'].get(elem)} "
                f"!= {count}"
            )

    def test_air_water_no_carbon(self, tmp_path):
        sub, manifest = make_submission(tmp_path, "air-water")
        ok, errors, diag = check(sub, manifest)
        assert ok, errors
        assert diag["element_counts"].get("C", 0) == 0

    def test_graphene_interfaces_have_carbon(self, tmp_path):
        for iface in ["graphene-water", "graphene-O12",
                      "graphene-O25", "graphene-O50"]:
            sub, manifest = make_submission(tmp_path, iface)
            ok, errors, diag = check(sub, manifest)
            assert ok, errors
            assert diag["element_counts"].get("C", 0) == 576


class TestCellDimensions:
    """Cell vectors must match system.json within tolerance."""

    @pytest.mark.parametrize("iface", ALL_IFACES)
    def test_cell_matches_spec(self, tmp_path, iface):
        sub, manifest = make_submission(tmp_path, iface)
        ok, errors, diag = check(sub, manifest)
        assert ok, errors
        spec = INTERFACE_SPECS[iface]
        for i, axis in enumerate("xyz"):
            got = diag["cell_angstrom"][i]
            want = spec["cell_angstrom"][i]
            assert abs(got - want) < CELL_TOLERANCE_ANGSTROM, (
                f"{iface}: cell[{axis}] {got} != {want}"
            )

    def test_wrong_cell_fails(self, tmp_path):
        sub, manifest = make_submission(
            tmp_path, "air-water", box_override=[10.0, 10.0, 10.0])
        ok, errors, _ = check(sub, manifest)
        assert not ok
        assert any("cell" in e for e in errors)


class TestFunctionalGroups:
    """GO interfaces must have functional groups; pristine graphene must not."""

    @pytest.mark.parametrize("iface", ["graphene-O12", "graphene-O25",
                                       "graphene-O50"])
    def test_go_interfaces_have_functional_groups(self, tmp_path, iface):
        sub, manifest = make_submission(tmp_path, iface)
        ok, errors, diag = check(sub, manifest)
        assert ok, errors
        fg = diag["functional_groups"]
        assert fg is not None, "GO interface should have functional groups"
        assert fg["hydroxyl"] > 0, "Expected hydroxyl groups"
        assert fg["epoxide"] > 0, "Expected epoxide groups"

    def test_pristene_graphene_has_no_functional_groups(self, tmp_path):
        sub, manifest = make_submission(tmp_path, "graphene-water")
        ok, errors, diag = check(sub, manifest)
        assert ok, errors
        assert diag["functional_groups"] is None


class TestGeometry:
    """Water geometry sanity checks (O–H bonds, H–O–H angles, min distance)."""

    @pytest.mark.parametrize("iface", ALL_IFACES)
    def test_min_intermolecular_distance(self, tmp_path, iface):
        sub, manifest = make_submission(tmp_path, iface)
        ok, errors, diag = check(sub, manifest)
        assert ok, errors
        assert diag["min_intermolecular_distance_A"] >= MIN_INTERMOLECULAR_DIST_ANGSTROM

    def test_water_o_h_bond_lengths_in_range(self, tmp_path):
        """Water O–H bonds should be ~0.96 Å (within 0.75–1.25)."""
        sub, manifest = make_submission(tmp_path, "air-water")
        ok, errors, diag = check(sub, manifest)
        assert ok, errors
        if diag.get("OH_bond_min_A") is not None:
            lo, hi = OH_BOND_RANGE_ANGSTROM
            assert diag["OH_bond_min_A"] >= lo
            assert diag["OH_bond_max_A"] <= hi

    def test_water_hoh_angle_in_range(self, tmp_path):
        """Water H–O–H angle should be ~104.5° (within 85–125)."""
        sub, manifest = make_submission(tmp_path, "air-water")
        ok, errors, diag = check(sub, manifest)
        assert ok, errors
        if diag.get("HOH_angle_min_degree") is not None:
            lo, hi = HOH_ANGLE_RANGE_DEGREE
            assert diag["HOH_angle_min_degree"] >= lo
            assert diag["HOH_angle_max_degree"] <= hi


class TestAtomCount:
    """Total atom count must match spec exactly."""

    @pytest.mark.parametrize("iface", ALL_IFACES)
    def test_natoms_matches_spec(self, tmp_path, iface):
        sub, manifest = make_submission(tmp_path, iface)
        ok, errors, diag = check(sub, manifest)
        assert ok, errors
        spec = INTERFACE_SPECS[iface]
        assert diag["natoms"] == spec["natoms"]


class TestProvenance:
    """SHA-256 provenance and manifest field completeness."""

    @pytest.mark.parametrize("field", [
        "interface", "structure_path", "generator", "generator_version",
        "command", "seed", "checks", "coord_raw_sha256",
    ])
    def test_required_provenance_fields(self, tmp_path, field):
        sub, manifest = make_submission(
            tmp_path, "air-water",
            mutate=lambda r, _: r.pop(field, None))
        ok, errors, _ = check(sub, manifest)
        assert not ok
        assert any("missing" in e.lower() or "required" in e.lower()
                    for e in errors)

    def test_sha256_must_match_actual_bytes(self, tmp_path):
        sub, manifest = make_submission(
            tmp_path, "air-water",
            mutate=lambda r, _: r.__setitem__(
                "coord_raw_sha256", "0" * 64))
        ok, errors, _ = check(sub, manifest)
        assert not ok
        assert any("SHA-256" in e or "sha256" in e.lower() for e in errors)

    def test_path_escape_fails(self, tmp_path):
        def escape(record, sub):
            outside = sub.parent / "outside.xyz"
            outside.write_text("0\n\n")
            record["structure_path"] = "../outside"

        sub, manifest = make_submission(
            tmp_path, "air-water", mutate=escape)
        ok, errors, _ = check(sub, manifest)
        assert not ok
        assert any("escapes" in e.lower() or "outside" in e.lower()
                    for e in errors)

    def test_manifest_path_escape_fails(self, tmp_path):
        def escape(record, sub):
            record["structure_path"] = "../../etc/passwd"

        sub, manifest = make_submission(
            tmp_path, "air-water", mutate=escape)
        ok, errors, _ = check(sub, manifest)
        assert not ok
        assert any("escapes" in e.lower() or "escape" in e.lower()
                    for e in errors)


class TestChecksObject:
    """structure_generation.checks must be a non-empty dict."""

    def test_missing_checks_fails(self, tmp_path):
        sub, manifest = make_submission(
            tmp_path, "air-water",
            mutate=lambda r, _: r.pop("checks", None))
        ok, errors, _ = check(sub, manifest)
        assert not ok
        assert any("checks" in e.lower() for e in errors)

    def test_empty_checks_fails(self, tmp_path):
        sub, manifest = make_submission(
            tmp_path, "air-water",
            mutate=lambda r, _: r.__setitem__("checks", {}))
        ok, errors, _ = check(sub, manifest)
        assert not ok
        assert any("checks" in e.lower() for e in errors)


class TestTotalAtomCount:
    """Cross-interface: total atoms across all 5 must sum correctly."""

    def test_all_interfaces_sum(self, tmp_path):
        """Sum of all interface natoms should be 2256+2832+2936+3048+3264 = 14336."""
        total = sum(s["natoms"] for s in INTERFACE_SPECS.values())
        assert total == 14336


# ── Production generator integration ───────────────────────────────────

GENERATOR_SCRIPT = (
    CASE / "solution" / "expert" / "00-structure-generation"
         / "build_all_interfaces.py"
)


def _run_production_generator(tmp_path: Path, seed: int = 42) -> Path:
    """Execute build_all_interfaces.py in an isolated output directory.

    Returns the path to the generated structures/ directory.
    """
    out_structures = tmp_path / "structures"
    out_structures.mkdir(parents=True, exist_ok=True)
    env = {
        "AI2KIT_042_SEED": str(seed),
        "AI2KIT_042_STRUCTURES_DIR": str(out_structures),
        "PATH": os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        [sys.executable, str(GENERATOR_SCRIPT)],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert result.returncode == 0, (
        f"build_all_interfaces.py failed:\n{result.stderr}\n{result.stdout}")
    return out_structures


class TestProductionGenerator:
    """Integration tests that execute the real build_all_interfaces.py."""

    @pytest.mark.parametrize("iface", ALL_IFACES)
    def test_generated_interface_passes_verifier(self, tmp_path, iface):
        """Each interface produced by the generator must pass L0 checks."""
        struct_dir = _run_production_generator(tmp_path)
        manifest_path = struct_dir / "structure_generation.json"
        assert manifest_path.is_file(), "generator did not write manifest"
        manifest = json.loads(manifest_path.read_text())
        record = manifest["structure_generation"][iface]

        # Build a submission root that the verifier can resolve.  Copy (not
        # symlink) the generated interface into the tree — the verifier's
        # path-escape check resolves symlinks and would reject an outside
        # target.
        sub = tmp_path / "submission"
        (sub / "structures").mkdir(parents=True)
        shutil.copytree(struct_dir / iface, sub / "structures" / iface)
        # Also write the manifest into final/
        (sub / "final").mkdir(parents=True)
        (sub / "final" / "manifest.json").write_text(
            json.dumps({"structure_generation": record}))

        ok, errors, diag = verifier.check_structure_origin(sub, {"structure_generation": record})
        assert ok, f"{iface} failed L0: {errors}"

    def test_graphene_cc_distances_are_physical(self, tmp_path):
        """Production graphene must have C-C >= 1.3 Å (no random overlaps)."""
        struct_dir = _run_production_generator(tmp_path)
        for iface in ["graphene-water", "graphene-O12", "graphene-O25", "graphene-O50"]:
            box, coords, types = verifier._read_deepmd_raw(struct_dir / iface)
            c_mask = np.array([t == 2 for t in types])
            c_pos = coords[c_mask]
            # minimum-image pairwise distances
            box_diag = np.diag(box)
            delta = c_pos[:, None, :] - c_pos[None, :, :]
            delta = delta - box_diag * np.round(delta / box_diag)
            dist = np.sqrt((delta ** 2).sum(-1))
            n = len(c_pos)
            iu = np.triu_indices(n, 1)
            min_dist = float(dist[iu].min())
            assert min_dist >= 1.3, (
                f"{iface}: min C-C = {min_dist:.4f} Å < 1.3 Å — "
                "generator is scattering atoms, not building a lattice")

    def test_water_geometry_in_generated_structures(self, tmp_path):
        """Generated water must have O-H ~0.96 Å and H-O-H ~104.5°."""
        struct_dir = _run_production_generator(tmp_path)
        for iface in ALL_IFACES:
            box, coords, types = verifier._read_deepmd_raw(struct_dir / iface)
            geom = verifier._water_geometry_diagnostics(coords, types, box)
            if geom["OH_bond_min_A"] is not None:
                lo, hi = OH_BOND_RANGE_ANGSTROM
                assert geom["OH_bond_min_A"] >= lo, f"{iface}: O-H too short"
                assert geom["OH_bond_max_A"] <= hi, f"{iface}: O-H too long"
            if geom["HOH_angle_min_degree"] is not None:
                lo, hi = HOH_ANGLE_RANGE_DEGREE
                assert geom["HOH_angle_min_degree"] >= lo, f"{iface}: angle too small"
                assert geom["HOH_angle_max_degree"] <= hi, f"{iface}: angle too large"

    def test_deterministic_output(self, tmp_path):
        """Same seed must produce identical coord.raw files."""
        d1 = _run_production_generator(tmp_path / "run1", seed=42)
        d2 = _run_production_generator(tmp_path / "run2", seed=42)
        for iface in ALL_IFACES:
            sha1 = hashlib.sha256((d1 / iface / "coord.raw").read_bytes()).hexdigest()
            sha2 = hashlib.sha256((d2 / iface / "coord.raw").read_bytes()).hexdigest()
            assert sha1 == sha2, f"{iface}: non-deterministic output"


# ── Stage 00 → Stage 01 conversion integration ──────────────────────────

CONVERT_SCRIPT = (
    CASE / "solution" / "expert" / "01-aimd" / "convert.py"
)


def _write_cp2k_aimd_fixture(work_iface: Path, spec: dict, nframes: int = 2) -> None:
    """Write minimal CP2K AIMD outputs (pos/frc xyz + ener) into work/<iface>.

    Atom order mirrors stage-00's type.raw layout: all O, then all H, then
    all C, sized from the interface's composition so the round-trip through
    convert.py preserves element counts.
    """
    work_iface.mkdir(parents=True, exist_ok=True)
    nat = spec["natoms"]
    symbols = []
    for elem, count in spec["composition"].items():
        symbols.extend([elem] * count)
    assert len(symbols) == nat

    def _xyz(fact: float) -> str:
        lines = []
        for f in range(nframes):
            lines.append(f"{nat}\nframe{f}\n")
            for s in symbols:
                lines.append(f"{s} {0.1 * f * fact:.6f} 0.0 0.0\n")
        return "".join(lines)

    (work_iface / "go-water-pos-1.xyz").write_text(_xyz(1.0))
    (work_iface / "go-water-frc-1.xyz").write_text(_xyz(2.0))
    ener = "".join(
        f"{i + 1} col col col {-76.0 - 0.1 * i:.6f}\n" for i in range(nframes)
    )
    (work_iface / "go-water-1.ener").write_text(ener)


class TestConvertIntegration:
    """End-to-end: production convert.py over real CP2K-shaped outputs.

    These run convert.py as a subprocess against a synthetic stage-01 work/
    dir, guarding the stage-00 -> stage-01 handoff (which the V0-V6 verifier
    tests never exercise because they use pre-baked fixtures).
    """

    @pytest.mark.parametrize("iface", ALL_IFACES)
    def test_convert_produces_valid_labeled_set(self, tmp_path, monkeypatch, iface):
        """convert.py must read stage-00 structures and emit a DeepMD labeled set."""
        struct_root = _run_production_generator(tmp_path / "gen")
        struct_dir = struct_root / iface
        nat = INTERFACE_SPECS[iface]["natoms"]

        # Lay out a fake stage-01 work dir alongside the structure
        work_iface = tmp_path / "work" / iface
        _write_cp2k_aimd_fixture(work_iface, INTERFACE_SPECS[iface], nframes=2)

        result = subprocess.run(
            [sys.executable, str(CONVERT_SCRIPT), iface, str(struct_dir)],
            capture_output=True, text=True, cwd=str(tmp_path), timeout=120,
        )
        assert result.returncode == 0, (
            f"{iface}: convert.py failed:\n{result.stderr}\n{result.stdout}")

        labeled = tmp_path / "work" / iface / "labeled"
        assert (labeled / "type.raw").is_file(), "convert did not write type.raw"
        typ = [int(t) for t in (labeled / "type.raw").read_text().split()]
        assert len(typ) == nat, f"type.raw has {len(typ)} atoms, expected {nat}"
        # Composition must round-trip through the conversion
        counts = _element_counts(typ)
        for elem, want in INTERFACE_SPECS[iface]["composition"].items():
            assert counts.get(elem, 0) == want, (
                f"{iface}: converted {elem}={counts.get(elem)} != {want}")

        setd = labeled / "set.000"
        for f in ("box.npy", "coord.npy", "energy.npy", "force.npy"):
            assert (setd / f).is_file(), f"missing {f}"
        coord = np.load(setd / "coord.npy")
        assert coord.shape == (2, nat * 3), f"coord shape {coord.shape}"
        energy = np.load(setd / "energy.npy")
        assert energy.shape == (2,)
        # Hartree -> eV conversion applied
        assert energy.min() < -76.0 * 27.2 * 0.99
        # Forces must be converted hartree/bohr -> eV/Å (~×51.422067).
        # The fixture writes force = 0.2 * frame_idx (hartree/bohr) on the x
        # axis, 0 elsewhere; converted max |f| on frame 1 = 0.2*51.422067.
        force = np.load(setd / "force.npy")
        assert force.shape == (2, nat * 3)
        HA_PER_BOHR_TO_EV_PER_A = 27.211386245988 / 0.529177210903
        assert force.max() == pytest.approx(0.2 * HA_PER_BOHR_TO_EV_PER_A,
                                             rel=1e-4)
        assert force.min() == pytest.approx(0.0)

    def test_convert_missing_structure_fails(self, tmp_path):
        """convert.py must fail cleanly when stage-00 structure is absent."""
        work_iface = tmp_path / "work" / "graphene-water"
        _write_cp2k_aimd_fixture(work_iface, INTERFACE_SPECS["graphene-water"], nframes=2)
        result = subprocess.run(
            [sys.executable, str(CONVERT_SCRIPT), "graphene-water",
             str(tmp_path / "nonexistent")],
            capture_output=True, text=True, cwd=str(tmp_path), timeout=120,
        )
        assert result.returncode != 0, "convert should fail when structure missing"
        assert "box.raw" in result.stderr or "not found" in result.stderr
