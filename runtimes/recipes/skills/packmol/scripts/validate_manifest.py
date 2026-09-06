#!/usr/bin/env python3
"""Validate and normalize a Packmol task manifest."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from packmol_common import (
    AVOGADRO,
    atom_angle_degrees,
    atom_distance,
    composition,
    emit_result,
    fail,
    load_json,
    molecular_mass,
    positive_number,
    read_xyz,
    write_json,
)

REQUIRED_CONFIRMED_FIELDS = {"components", "box.periodic", "packmol.tolerance_A"}
TEMPLATE_ORIGINS = {"provided", "existing", "generated"}


def validate_component_geometry(
    name: str,
    atoms: list[tuple[str, float, float, float]],
    template_origin: str,
) -> dict[str, str]:
    for first_index, first in enumerate(atoms):
        for second_index in range(first_index + 1, len(atoms)):
            if atom_distance(first, atoms[second_index]) < 0.20:
                raise ValueError(
                    f"component {name} has duplicate or near-duplicate coordinates "
                    f"at atoms {first_index + 1} and {second_index + 1}"
                )

    if template_origin != "generated":
        mode = "generic_template"
    elif len(atoms) == 1:
        mode = "generated_monatomic"
    elif composition(atoms) == {"H": 2, "O": 1} and len(atoms) == 3:
        oxygen = next(atom for atom in atoms if atom[0] == "O")
        hydrogens = [atom for atom in atoms if atom[0] == "H"]
        distances = [atom_distance(oxygen, hydrogen) for hydrogen in hydrogens]
        if not all(0.80 <= value <= 1.20 for value in distances):
            raise ValueError(
                "generated H2O O-H distances must each be within 0.80-1.20 A; "
                f"got {distances[0]:.6f}, {distances[1]:.6f}"
            )
        angle = atom_angle_degrees(hydrogens[0], oxygen, hydrogens[1])
        if not 90.0 <= angle <= 120.0:
            raise ValueError(
                "generated H2O H-O-H angle must be within 90-120 degrees; "
                f"got {angle:.6f}"
            )
        mode = "generated_water"
    else:
        raise ValueError(
            f"generated multi-atom component {name} is unsupported; provide an "
            "existing or user-provided template"
        )

    return {
        "template_origin": template_origin,
        "validation_mode": mode,
        "verdict": "PASS",
    }


def normalize_manifest(payload: dict) -> dict:
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if (
        not isinstance(payload.get("system_name"), str)
        or not payload["system_name"].strip()
    ):
        raise ValueError("system_name must be a non-empty string")

    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("provenance must be an object")
    confirmed = provenance.get("confirmed_fields", [])
    defaulted = provenance.get("defaulted_fields", [])
    if not isinstance(confirmed, list) or not isinstance(defaulted, list):
        raise ValueError("confirmed_fields and defaulted_fields must be lists")
    supplied = {str(field) for field in confirmed + defaulted}
    missing_core = sorted(REQUIRED_CONFIRMED_FIELDS - supplied)
    if missing_core:
        raise ValueError(f"unconfirmed scientific fields: {', '.join(missing_core)}")

    components = payload.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("components must be a non-empty list")
    seen_names: set[str] = set()
    atom_counts: dict[str, int] = {}
    component_compositions: dict[str, dict[str, int]] = {}
    component_geometry_checks: dict[str, dict[str, str]] = {}
    masses: dict[str, float] = {}
    expected_atom_count = 0
    total_mass = 0.0
    net_charge = 0.0

    for component in components:
        if not isinstance(component, dict):
            raise ValueError("each component must be an object")
        name = str(component.get("name", "")).strip()
        if not name or name in seen_names:
            raise ValueError(f"component names must be non-empty and unique: {name!r}")
        seen_names.add(name)
        count = component.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError(f"component {name} count must be a positive integer")
        template_path = Path(str(component.get("template_path", "")))
        if not template_path.is_file():
            raise ValueError(f"component template does not exist: {template_path}")
        template_origin = component.get("template_origin")
        if template_origin not in TEMPLATE_ORIGINS:
            raise ValueError(
                f"component {name} template_origin must be one of: "
                "provided, existing, generated"
            )
        atoms = read_xyz(template_path)
        component_geometry_checks[name] = validate_component_geometry(
            name, atoms, str(template_origin)
        )
        atom_counts[name] = len(atoms)
        component_compositions[name] = composition(atoms)
        declared_mass = component.get("molar_mass_g_mol")
        mass = (
            molecular_mass(atoms)
            if declared_mass is None
            else positive_number(declared_mass, f"component {name} molar_mass_g_mol")
        )
        masses[name] = mass
        formal_charge = component.get("formal_charge_e")
        if isinstance(formal_charge, bool) or not isinstance(
            formal_charge, (int, float)
        ):
            raise ValueError(f"component {name} formal_charge_e must be numeric")
        if not math.isfinite(float(formal_charge)):
            raise ValueError(f"component {name} formal_charge_e must be finite")
        expected_atom_count += count * len(atoms)
        total_mass += count * mass
        net_charge += count * float(formal_charge)

    if abs(net_charge) > 1e-12 and "net_charge_e" not in supplied:
        raise ValueError("non-neutral system requires confirmed field net_charge_e")

    box = payload.get("box")
    if not isinstance(box, dict) or not isinstance(box.get("periodic"), bool):
        raise ValueError("box.periodic must be true or false")
    dimensions = box.get("dimensions_A")
    density = box.get("target_density_g_cm3")
    if (dimensions is None) == (density is None):
        raise ValueError(
            "exactly one of box.dimensions_A or box.target_density_g_cm3 is required"
        )
    if dimensions is not None:
        if "box.dimensions_A" not in supplied:
            raise ValueError("unconfirmed scientific field: box.dimensions_A")
        if not isinstance(dimensions, list) or len(dimensions) != 3:
            raise ValueError("box.dimensions_A must contain three values")
        box_dimensions = [
            positive_number(value, "box dimension") for value in dimensions
        ]
        volume_a3 = math.prod(box_dimensions)
        implied_density = total_mass / AVOGADRO / volume_a3 * 1e24
    else:
        if "box.target_density_g_cm3" not in supplied:
            raise ValueError("unconfirmed scientific field: box.target_density_g_cm3")
        target_density = positive_number(density, "box.target_density_g_cm3")
        volume_a3 = total_mass / AVOGADRO / target_density * 1e24
        length = volume_a3 ** (1.0 / 3.0)
        box_dimensions = [length, length, length]
        implied_density = target_density

    packmol = payload.get("packmol")
    if not isinstance(packmol, dict):
        raise ValueError("packmol must be an object")
    positive_number(packmol.get("tolerance_A"), "packmol.tolerance_A")
    for field in ("output_path", "input_path", "log_path"):
        if not isinstance(packmol.get(field), str) or not packmol[field].strip():
            raise ValueError(f"packmol.{field} must be a non-empty path")
    seed = packmol.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError("packmol.seed must be an integer or null")

    normalized = dict(payload)
    normalized["derived"] = {
        "component_atom_counts": atom_counts,
        "component_compositions": component_compositions,
        "component_masses_g_mol": masses,
        "component_geometry_checks": component_geometry_checks,
        "expected_atom_count": expected_atom_count,
        "total_mass_g_mol": total_mass,
        "net_charge_e": net_charge,
        "volume_A3": volume_a3,
        "box_dimensions_A": box_dimensions,
        "implied_density_g_cm3": implied_density,
    }
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--output")
    args = parser.parse_args()
    source = Path(args.manifest)
    target = (
        Path(args.output)
        if args.output
        else source.with_name("packmol-task.normalized.json")
    )
    try:
        normalized = normalize_manifest(load_json(source))
        write_json(target, normalized)
    except Exception as exc:
        return fail(
            check="validation",
            name="packmol_inputs",
            error=exc,
            source_paths=[str(source)],
        )
    derived = normalized["derived"]
    emit_result(
        check="validation",
        name="packmol_inputs",
        status="PASS",
        preparation_stage="validated",
        source_paths=[str(source), str(target)],
        atom_count=derived["expected_atom_count"],
        net_charge_e=derived["net_charge_e"],
        box_A=derived["box_dimensions_A"],
        density_g_cm3=derived["implied_density_g_cm3"],
        component_geometry_checks=derived["component_geometry_checks"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
