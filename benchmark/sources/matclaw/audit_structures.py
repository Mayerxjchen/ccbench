"""Copy and cross-audit the pinned MatClaw CIPS structures."""

from __future__ import annotations

import argparse
import json
import shutil
from functools import reduce
from math import gcd
from pathlib import Path

from benchmark.sources.matclaw.recovery import sha256_file


STRUCTURE_FILES = ("CuInP2S6.cif", "cips_monolayer.cif")
COMMON_FILES = (*STRUCTURE_FILES, "cips_monolayer.data")


def _rounded(values: list[float] | tuple[float, ...]) -> list[float]:
    return [round(float(value), 12) for value in values]


def _cips_reduced_formula(counts: dict[str, int]) -> str:
    expected = ("Cu", "In", "P", "S")
    if set(counts) != set(expected):
        raise RuntimeError(f"expected only CIPS elements, found {sorted(counts)}")
    divisor = reduce(gcd, counts.values())
    return "".join(
        element + (str(counts[element] // divisor) if counts[element] // divisor != 1 else "")
        for element in expected
    )


def copy_common_files(root: Path) -> Path:
    """Copy pinned inputs byte-for-byte into the normalized common view."""
    source = root / "repository" / "release" / ".ref"
    common = root / "common"
    common.mkdir(parents=True, exist_ok=True)
    for filename in COMMON_FILES:
        shutil.copyfile(source / filename, common / filename)
    return common


def audit_structure(path: Path, source_path: str) -> dict[str, object]:
    """Parse one CIF independently with pymatgen and ASE."""
    try:
        from ase.io import read as ase_read
        from pymatgen.core import Structure
    except ImportError as exc:  # pragma: no cover - exercised by generation command
        raise RuntimeError("structure audit requires pymatgen and ASE") from exc

    structure = Structure.from_file(path)
    atoms = ase_read(path)
    pymatgen_counts = {
        str(element): int(amount)
        for element, amount in structure.composition.get_el_amt_dict().items()
    }
    ase_counts: dict[str, int] = {}
    for symbol in atoms.get_chemical_symbols():
        ase_counts[symbol] = ase_counts.get(symbol, 0) + 1

    return {
        "filename": path.name,
        "canonical_reduced_formula": _cips_reduced_formula(pymatgen_counts),
        "provenance": "upstream_repository",
        "source_path": source_path,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "pymatgen": {
            "formula": structure.composition.formula,
            "reduced_formula": structure.composition.reduced_formula,
            "element_counts": pymatgen_counts,
            "num_sites": len(structure),
            "lattice_abc_angstrom": _rounded(structure.lattice.abc),
            "lattice_angles_degree": _rounded(structure.lattice.angles),
            "volume_angstrom3": round(float(structure.volume), 12),
            "periodic_boundary_conditions": list(structure.lattice.pbc),
        },
        "ase": {
            "formula": atoms.get_chemical_formula(),
            "element_counts": ase_counts,
            "num_atoms": len(atoms),
            "cell_parameters": _rounded(atoms.cell.cellpar()),
            "periodic_boundary_conditions": [bool(value) for value in atoms.pbc],
        },
        "parser_agreement": {
            "atom_count": len(structure) == len(atoms),
            "formula": pymatgen_counts == ase_counts,
        },
    }


def generate_audit(root: Path) -> Path:
    common = copy_common_files(root)
    structures = [
        audit_structure(
            common / filename,
            f"repository/release/.ref/{filename}",
        )
        for filename in STRUCTURE_FILES
    ]
    if not all(
        all(entry["parser_agreement"].values())  # type: ignore[union-attr]
        for entry in structures
    ):
        raise RuntimeError("pymatgen and ASE disagree on a recovered structure")

    audit = {
        "schema_version": 1,
        "method": "byte-preserving copy cross-parsed with pymatgen and ASE",
        "structures": structures,
    }
    output = common / "structure-audit.json"
    output.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(generate_audit(args.root.resolve()))


if __name__ == "__main__":
    main()
