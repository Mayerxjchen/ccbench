"""Hidden L0 contracts for autonomous water-structure generation."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import pytest


TESTS = Path(__file__).resolve().parent
CASE = TESTS.parent
sys.path.insert(0, str(TESTS))
import verifier  # noqa: E402


THRESHOLDS = {
    "levels": {
        "L0": {
            "expected_composition": {"O": 64, "H": 128},
            "cell_angstrom": [12.4, 12.4, 12.4],
            "cell_tolerance_angstrom": 0.05,
            "min_intermolecular_distance_angstrom": 1.2,
            "OH_bond_range_angstrom": [0.75, 1.25],
            "HOH_angle_range_degree": [85.0, 125.0],
            "denied_initial_sha256": [
                "b1d5ed709d0128ea83662f9387033bdf478078c0338b51ff58dcc863b24ad0e3"
            ],
            "denied_initial_coords_sha256": [
                "85ed03dea6f283614b2ddaea806be326250cb94e6fbf302f4b15e7854c0d6425"
            ],
        }
    }
}


def water_positions(nwater=64, cell=12.4, *, broken=False, overlap=False, phase=0.0):
    atoms = []
    spacing = cell / 4.0
    theta = math.radians(104.5 + phase)
    for index in range(nwater):
        ix = index % 4
        iy = (index // 4) % 4
        iz = index // 16
        o = [0.25 + ix * spacing, 0.25 + iy * spacing, 0.25 + iz * spacing]
        if overlap and index == 1:
            o = [0.30, 0.25, 0.25]
        bond = 1.55 if broken and index == 0 else 0.96
        h1 = [o[0] + bond, o[1], o[2]]
        h2 = [o[0] + bond * math.cos(theta), o[1] + bond * math.sin(theta), o[2]]
        atoms.extend([("O", *o), ("H", *h1), ("H", *h2)])
    return atoms


def write_extxyz(path: Path, atoms, cell=12.4):
    lines = [
        str(len(atoms)),
        f'Lattice="{cell} 0 0 0 {cell} 0 0 0 {cell}" Properties=species:S:1:pos:R:3 pbc="T T T"',
    ]
    lines.extend(f"{s} {x:.8f} {y:.8f} {z:.8f}" for s, x, y, z in atoms)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def make_submission(tmp_path: Path, *, atoms=None, cell=12.4, mutate_record=None):
    sub = tmp_path / "submission"
    initial = sub / "work/initial/generated.extxyz"
    write_extxyz(initial, atoms or water_positions(), cell)
    digest = hashlib.sha256(initial.read_bytes()).hexdigest()
    record = {
        "initial_structure": "work/initial/generated.extxyz",
        "generator": "independent-grid-water-builder",
        "generator_version": "1.0",
        "command": "python generate_water.py --seed 41",
        "seed": 41,
        "checks": {"composition": "PASS", "periodic_geometry": "PASS"},
        "initial_structure_sha256": digest,
        "cp2k_input_structure_sha256": digest,
    }
    if mutate_record:
        mutate_record(record, sub)
    manifest = {"structure_generation": record}
    (sub / "final").mkdir(parents=True)
    (sub / "final/manifest.json").write_text(json.dumps(manifest))
    return sub, manifest


def check(sub, manifest):
    return verifier.check_structure_origin(sub, manifest, THRESHOLDS)


def test_valid_independently_generated_structure_passes(tmp_path):
    sub, manifest = make_submission(tmp_path)
    ok, errors, diag = check(sub, manifest)
    assert ok, errors
    assert diag["composition"] == {"O": 64, "H": 128}
    assert diag["molecules"] == 64


def test_alternative_valid_coordinates_also_pass(tmp_path):
    sub, manifest = make_submission(tmp_path, atoms=water_positions(phase=3.0))
    ok, errors, _ = check(sub, manifest)
    assert ok, errors


@pytest.mark.parametrize("field", [
    "initial_structure", "generator", "generator_version", "command", "seed",
    "checks", "initial_structure_sha256", "cp2k_input_structure_sha256",
])
def test_every_structure_provenance_field_is_required(tmp_path, field):
    sub, manifest = make_submission(tmp_path, mutate_record=lambda r, _: r.pop(field))
    ok, errors, _ = check(sub, manifest)
    assert not ok
    assert any("missing structure fields" in e for e in errors)


def test_63_waters_fail(tmp_path):
    sub, manifest = make_submission(tmp_path, atoms=water_positions(63))
    ok, errors, _ = check(sub, manifest)
    assert not ok
    assert any("composition" in e for e in errors)


def test_wrong_cell_fails(tmp_path):
    sub, manifest = make_submission(tmp_path, cell=11.0)
    ok, errors, _ = check(sub, manifest)
    assert not ok
    assert any("cell" in e for e in errors)


def test_close_intermolecular_contact_fails(tmp_path):
    sub, manifest = make_submission(tmp_path, atoms=water_positions(overlap=True))
    ok, errors, _ = check(sub, manifest)
    assert not ok
    assert any("intermolecular" in e for e in errors)


def test_broken_water_geometry_fails(tmp_path):
    sub, manifest = make_submission(tmp_path, atoms=water_positions(broken=True))
    ok, errors, _ = check(sub, manifest)
    assert not ok
    assert any("O-H" in e for e in errors)


def test_manifest_path_escape_fails(tmp_path):
    def escape(record, sub):
        outside = sub.parent / "outside.xyz"
        outside.write_text("0\n\n")
        record["initial_structure"] = "../outside.xyz"

    sub, manifest = make_submission(tmp_path, mutate_record=escape)
    ok, errors, _ = check(sub, manifest)
    assert not ok
    assert any("escapes submission" in e for e in errors)


def test_recorded_sha_must_match_generated_bytes(tmp_path):
    sub, manifest = make_submission(
        tmp_path, mutate_record=lambda r, _: r.__setitem__("initial_structure_sha256", "0" * 64))
    ok, errors, _ = check(sub, manifest)
    assert not ok
    assert any("SHA-256" in e for e in errors)


def test_cp2k_provenance_must_link_same_structure(tmp_path):
    sub, manifest = make_submission(
        tmp_path, mutate_record=lambda r, _: r.__setitem__("cp2k_input_structure_sha256", "1" * 64))
    ok, errors, _ = check(sub, manifest)
    assert not ok
    assert any("CP2K" in e for e in errors)


def test_hidden_expert_coordinate_identity_is_denied(tmp_path):
    hidden = CASE / "reference/expert-trajectory/initial/water64.xyz"

    def copied(record, sub):
        initial = sub / record["initial_structure"]
        initial.write_bytes(hidden.read_bytes())
        digest = hashlib.sha256(initial.read_bytes()).hexdigest()
        record["initial_structure_sha256"] = digest
        record["cp2k_input_structure_sha256"] = digest

    sub, manifest = make_submission(tmp_path, mutate_record=copied)
    ok, errors, _ = check(sub, manifest)
    assert not ok
    assert any("hidden/reference identity" in e for e in errors)


def test_reformatted_hidden_coordinates_are_also_denied(tmp_path):
    hidden = CASE / "reference/expert-trajectory/initial/water64.xyz"
    rows = hidden.read_text().splitlines()[2:]
    atoms = [(parts[0], *(float(x) for x in parts[1:4]))
             for parts in (row.split() for row in rows)]
    sub, manifest = make_submission(tmp_path, atoms=atoms)
    ok, errors, _ = check(sub, manifest)
    assert not ok
    assert any("hidden/reference coordinate identity" in e for e in errors)


def test_verifier_level_order_starts_with_structure_generation():
    assert verifier.verifier_level_order() == [f"L{i}" for i in range(10)]


def test_reference_calibration_mode_explicitly_bypasses_only_l0(monkeypatch):
    monkeypatch.setenv("AI2KIT_REFERENCE_MODE", "1")
    ok, errors, diag = verifier._check_L0({
        "submission": Path("/does/not/exist"),
        "manifest": {},
        "thresholds": THRESHOLDS,
    })
    assert ok
    assert errors == []
    assert diag["reference_mode"] is True


def _cp2k_frame(atoms):
    return {"atoms": [(s, x, y, z) for s, x, y, z in atoms]}


def test_l0_requires_generated_coordinates_to_appear_in_real_cp2k_trajectory(tmp_path, monkeypatch):
    monkeypatch.delenv("AI2KIT_REFERENCE_MODE", raising=False)
    atoms = water_positions()
    sub, manifest = make_submission(tmp_path, atoms=atoms)
    ctx = {
        "submission": sub,
        "manifest": manifest,
        "thresholds": THRESHOLDS,
        "all_pos_traj": [{"pos_frames": [_cp2k_frame(atoms)]}],
    }
    ok, errors, diag = verifier._check_L0(ctx)
    assert ok, errors
    assert diag["cp2k_coordinate_link"] is True


def test_l0_rejects_unlinked_generated_coordinates(tmp_path, monkeypatch):
    monkeypatch.delenv("AI2KIT_REFERENCE_MODE", raising=False)
    sub, manifest = make_submission(tmp_path)
    unrelated = water_positions(phase=3.0)
    ctx = {
        "submission": sub,
        "manifest": manifest,
        "thresholds": THRESHOLDS,
        "all_pos_traj": [{"pos_frames": [_cp2k_frame(unrelated)]}],
    }
    ok, errors, diag = verifier._check_L0(ctx)
    assert not ok
    assert diag["cp2k_coordinate_link"] is False
    assert any("CP2K trajectory" in e for e in errors)
