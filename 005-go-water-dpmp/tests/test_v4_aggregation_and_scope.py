"""Regression tests from the 042 REFINE audit (2026-08-25).

Covers the two structural P0s that unit tests previously missed:
  1. V4 heterogeneous aggregation — hidden systems with different atom counts
     must be scored per system and equal-weight averaged, never concatenated.
  2. Hidden scope — regenerated hidden artifacts must cover exactly the five
     publicly specified systems, in BOTH on-disk locations the verifier reads.
"""
from __future__ import annotations

import json
import hashlib
import importlib.util
from pathlib import Path

import numpy as np
import pytest


def _load_verifier():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verifier_042", CASE / "tests" / "verifier.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_generator():
    path = (CASE / "solution/expert/00-structure-generation"
                 / "build_all_interfaces.py")
    spec = importlib.util.spec_from_file_location("generator_042", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CASE = Path(__file__).resolve().parents[1]
PUBLIC_SYSTEMS = {
    "air-water", "graphene-water", "graphene-O12",
    "graphene-O25", "graphene-O50",
}
aggregate_v4 = _load_verifier().aggregate_v4


def _sys(name: str, natoms: int, frames: int, *, e_bias: float = 0.0,
         f_noise: float = 0.0) -> dict:
    rng = np.random.default_rng(abs(hash(name)) % (2**32))
    return {
        "name": name,
        "frames": frames,
        "natoms": natoms,
        "e_pred": np.full(frames, e_bias + 10.0) + rng.normal(0, 0.01, frames),
        "e_gt": np.full(frames, 10.0),
        "f_pred": rng.normal(f_noise, 0.05, (frames, natoms * 3)),
        "f_gt": np.zeros((frames, natoms * 3)),
    }


def test_v4_heterogeneous_systems_aggregate_without_concat() -> None:
    """Different natoms across systems must not raise and must produce a
    per-system breakdown with an equal-weight mean."""
    per = [
        _sys("small", 192, 5),
        _sys("large", 816, 7, f_noise=0.2),
    ]
    out = aggregate_v4(per)
    assert out["n_systems"] == 2
    assert {m["system"] for m in out["per_system"]} == {"small", "large"}
    assert out["frames"] == 12
    assert np.isfinite(out["force_rmse_eV_A"])
    # equal weights: the noisy large system cannot dominate via atom count
    small_only = aggregate_v4([per[0]])["force_rmse_eV_A"]
    large_only = aggregate_v4([per[1]])["force_rmse_eV_A"]
    assert abs(out["force_rmse_eV_A"]
               - (small_only + large_only) / 2) < 1e-6


def test_v4_energy_alignment_removes_constant_offset() -> None:
    per = [_sys("aligned", 144, 9, e_bias=3.3)]
    out = aggregate_v4(per)
    assert out["energy_rmse_eV_per_atom_aligned"] < 0.05


def test_hidden_artifacts_cover_exactly_public_systems() -> None:
    """Both regenerated hidden locations must contain exactly the five public
    systems — no author-only extras (water / *-long)."""
    for base in (CASE / "reference/hidden-validation",
                 CASE / "tests/hidden"):
        manifest = json.loads((base / "manifest.json").read_text())
        dirs = {p.name for p in (base / "hidden-frames").iterdir()
                if p.is_dir()}
        assert dirs == PUBLIC_SYSTEMS, (base, dirs)
        assert len(manifest["frames"]) == manifest["total_held_out_frames"]
        assert manifest["total_held_out_frames"] > 0


def test_verifier_scans_same_five_dirs(tmp_path: Path) -> None:
    """verifier.hidden_interface_dirs() must see exactly five systems when
    pointed at the regenerated tree."""
    V = _load_verifier()

    orig = V.hidden_frames_path
    V.hidden_frames_path = lambda: CASE / "tests/hidden/hidden-frames"
    try:
        dirs = V.hidden_interface_dirs()
    finally:
        V.hidden_frames_path = orig
    assert {d.name for d in dirs} == PUBLIC_SYSTEMS


def test_instruction_and_system_json_agree() -> None:
    """The rewritten prompt table and system.json must carry identical totals,
    cells and functional-group counts for all five systems."""
    text = (CASE / "instruction.md").read_text()
    spec = json.loads((CASE / "public/system.json").read_text())
    assert set(spec["interfaces"]) == PUBLIC_SYSTEMS
    forbidden = ("4 stacked monolayers", "design freedom")
    for frag in forbidden:
        assert frag not in text
    for name, iface in spec["interfaces"].items():
        fg = iface.get("functional_groups") or {}
        # table rows use en-dash display names; match on the numeric columns
        row = (f"| {iface['composition'].get('C', 0)} | "
               f"{iface['water_molecules']} | {fg.get('hydroxyl', 0)} | "
               f"{fg.get('epoxide', 0)} | {iface['natoms']} |")
        assert row in text, (name, row)


def test_system_contract_has_one_unambiguous_dual_plane_topology() -> None:
    spec = json.loads((CASE / "public/system.json").read_text())
    assert "initial_structures" not in spec
    topology = spec["construction_model"]
    assert topology["topology"] == "dual_basal_plane_water_sandwich"
    assert topology["planes_per_interface"] == 2
    assert topology["carbon_atoms_per_plane"] == 288
    assert topology["functional_groups"]["split_between_planes"] == "equal"
    assert topology["functional_groups"]["orientation"] == "toward_water"


def test_prompt_requires_labels_from_all_five_systems_without_loophole() -> None:
    text = (CASE / "instruction.md").read_text().lower()
    assert "one or more systems" not in text
    assert "every target system" in text


def test_production_generator_places_author_scale_water_sandwich() -> None:
    G = _load_generator()
    rng = np.random.RandomState(42)
    spec = G.INTERFACE_SPECS["graphene-water"]
    coord, types = G.build_graphene_water(spec, rng)
    coord = np.asarray(coord)
    types = np.asarray(types)
    carbon_z = coord[types == G.ELEMENT_TYPE_MAP["C"], 2]
    oxygen_z = coord[types == G.ELEMENT_TYPE_MAP["O"], 2]
    split = float(spec["cell_angstrom"][2]) / 2
    lower = carbon_z[carbon_z < split]
    upper = carbon_z[carbon_z > split]
    assert len(lower) == len(upper) == 288
    assert 15.0 < float(np.mean(lower)) < 19.0
    assert 51.0 < float(np.mean(upper)) < 55.0
    assert float(oxygen_z.min()) > float(np.mean(lower)) + 2.0
    assert float(oxygen_z.max()) < float(np.mean(upper)) - 2.0


def test_air_water_generator_has_vacuum_and_unit_density_slab() -> None:
    G = _load_generator()
    spec = G.INTERFACE_SPECS["air-water"]
    coord, types = G.build_air_water(spec, np.random.RandomState(7))
    coord = np.asarray(coord)
    types = np.asarray(types)
    oxygen_z = coord[types == G.ELEMENT_TYPE_MAP["O"], 2]
    lz = float(spec["cell_angstrom"][2])
    assert float(oxygen_z.min()) > 15.0
    assert float(oxygen_z.max()) < lz - 15.0
    occupied = float(oxygen_z.max() - oxygen_z.min())
    area = float(spec["cell_angstrom"][0] * spec["cell_angstrom"][1])
    mass_g = spec["water_molecules"] * 18.01528 * 1.66053906660e-24
    density = mass_g / (area * occupied * 1e-24)
    assert 0.85 <= density <= 1.15


@pytest.mark.parametrize("iface", ["graphene-O12", "graphene-O25", "graphene-O50"])
def test_go_functional_groups_are_balanced_and_face_water(iface: str) -> None:
    G = _load_generator()
    spec = G.INTERFACE_SPECS[iface]
    coord, types = G.build_go_interface(spec, np.random.RandomState(11))
    coord = np.asarray(coord)
    n_carbon = spec["composition"]["C"]
    n_fg = sum(spec["functional_groups"].values())
    fg_z = coord[n_carbon:n_carbon + n_fg, 2]
    lower_plane, upper_plane = G._plane_zs(spec)
    lower = fg_z[fg_z < spec["cell_angstrom"][2] / 2]
    upper = fg_z[fg_z > spec["cell_angstrom"][2] / 2]
    assert len(lower) == len(upper) == n_fg // 2
    assert np.all(lower > lower_plane)
    assert np.all(upper < upper_plane)


def test_source_lock_matches_current_public_bytes() -> None:
    lock = json.loads((CASE / "reference/source.lock.json").read_text())
    for rel, record in lock["public_input"]["files"].items():
        actual = hashlib.sha256((CASE / rel).read_bytes()).hexdigest()
        assert actual == record["sha256"], rel


def test_aimd_submission_is_parsable_and_preflight_reports_stderr() -> None:
    runner = (CASE / "solution/expert/01-aimd/run.sh").read_text()
    preflight = (CASE / "tools/aimd_preflight.py").read_text()
    assert "sbatch --parsable" in runner
    assert "if not ok and proc.stderr.strip()" in preflight
