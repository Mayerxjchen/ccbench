#!/usr/bin/env python3
"""Perform exact intermolecular-distance QC for a packed XYZ structure."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from packmol_common import composition, emit_result, fail, load_json, read_xyz

MAX_EXACT_ATOMS = 5_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    args = parser.parse_args()
    source = Path(args.manifest)
    try:
        manifest = load_json(source)
        output_path = Path(manifest["packmol"]["output_path"])
        atoms = read_xyz(output_path)
        if len(atoms) > MAX_EXACT_ATOMS:
            raise ValueError(
                f"exact_qc_size_limit: {len(atoms)} atoms exceeds {MAX_EXACT_ATOMS}"
            )
        expected_count = int(manifest["derived"]["expected_atom_count"])
        if len(atoms) != expected_count:
            raise ValueError(
                f"packed atom count mismatch: expected {expected_count}, got {len(atoms)}"
            )

        molecule_ids: list[int] = []
        atom_labels: list[dict[str, object]] = []
        molecule_id = 0
        atom_index = 0
        component_atom_counts = manifest["derived"]["component_atom_counts"]
        for component in manifest["components"]:
            atoms_per_molecule = int(component_atom_counts[component["name"]])
            for component_molecule_id in range(int(component["count"])):
                molecule_id += 1
                for _ in range(atoms_per_molecule):
                    molecule_ids.append(molecule_id)
                    atom_labels.append(
                        {
                            "component": component["name"],
                            "component_molecule_id": component_molecule_id + 1,
                            "atom_index": atom_index + 1,
                        }
                    )
                    atom_index += 1
        if len(molecule_ids) != len(atoms):
            raise ValueError("molecule grouping does not match packed atom order")

        periodic = bool(manifest["box"]["periodic"])
        dimensions = [float(value) for value in manifest["derived"]["box_dimensions_A"]]
        minimum = math.inf
        closest: tuple[int, int] | None = None
        for first in range(len(atoms)):
            for second in range(first + 1, len(atoms)):
                if molecule_ids[first] == molecule_ids[second]:
                    continue
                deltas = [
                    atoms[first][axis] - atoms[second][axis] for axis in range(1, 4)
                ]
                if periodic:
                    deltas = [
                        delta - length * round(delta / length)
                        for delta, length in zip(deltas, dimensions)
                    ]
                distance = math.sqrt(sum(delta * delta for delta in deltas))
                if distance < minimum:
                    minimum = distance
                    closest = (first, second)
        if closest is None or not math.isfinite(minimum):
            raise ValueError("structure contains fewer than two distinct molecules")

        if minimum < 1.2:
            gross = "FAILED"
        elif minimum < 1.8:
            gross = "WARNING"
        else:
            gross = "PASS"
        tolerance = float(manifest["packmol"]["tolerance_A"])
        tolerance_verdict = "PASS" if minimum >= tolerance - 0.01 else "FAILED"
        if gross == "FAILED" or tolerance_verdict == "FAILED":
            verdict = "FAILED"
        elif gross == "WARNING":
            verdict = "WARNING"
        else:
            verdict = "PASS"

        first, second = closest
        record = {
            "periodic": periodic,
            "box_A": dimensions,
            "density_g_cm3": manifest["derived"]["implied_density_g_cm3"],
            "composition": composition(atoms),
            "component_counts": {
                component["name"]: component["count"]
                for component in manifest["components"]
            },
            "atom_count": len(atoms),
            "net_charge_e": manifest["derived"]["net_charge_e"],
            "min_distance_A": minimum,
            "closest_pair": [atom_labels[first], atom_labels[second]],
            "gross_overlap_verdict": gross,
            "tolerance_A": tolerance,
            "tolerance_compliance": tolerance_verdict,
        }
    except Exception as exc:
        paths = [str(source)]
        if "output_path" in locals():
            paths.append(str(output_path))
        return fail(
            check="structure_qc",
            name="packmol_structure_qc",
            error=exc,
            source_paths=paths,
        )

    emit_result(
        check="structure_qc",
        name="packmol_structure_qc",
        status=verdict,
        preparation_stage="packed",
        source_paths=[str(source), str(output_path)],
        **record,
    )
    return 1 if verdict == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
