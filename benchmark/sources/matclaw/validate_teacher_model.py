"""Load and evaluate the recovered legacy DeePMD teacher model."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(model: Path, structure: Path, type_map_path: Path) -> dict[str, object]:
    import ase
    import deepmd
    import numpy as np
    import tensorflow as tf
    from ase.io import read
    from deepmd.infer import DeepPot

    atoms = read(structure)
    type_map = type_map_path.read_text(encoding="utf-8").split()
    atom_types = np.asarray(
        [type_map.index(symbol) for symbol in atoms.get_chemical_symbols()],
        dtype=np.int32,
    )
    coordinates = np.asarray(atoms.positions, dtype=float).reshape(1, -1)
    cell = np.asarray(atoms.cell, dtype=float).reshape(1, 9)
    potential = DeepPot(str(model))
    model_type_map = list(potential.get_type_map())
    if model_type_map != type_map:
        raise RuntimeError(
            f"model type map {model_type_map} does not match archive map {type_map}"
        )
    energy, force, virial = potential.eval(coordinates, cell, atom_types)
    arrays = [np.asarray(energy), np.asarray(force), np.asarray(virial)]
    passed = all(np.isfinite(array).all() for array in arrays)
    return {
        "passed": bool(passed),
        "engine": "DeePMD-kit DeepPot",
        "deepmd_version": deepmd.__version__,
        "tensorflow_version": tf.__version__,
        "ase_version": ase.__version__,
        "numpy_version": np.__version__,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "model_path": model.name,
        "model_sha256": _sha256(model),
        "structure_path": structure.name,
        "structure_sha256": _sha256(structure),
        "type_map_path": type_map_path.name,
        "type_map_sha256": _sha256(type_map_path),
        "num_atoms": len(atoms),
        "type_map": type_map,
        "model_type_map": model_type_map,
        "outputs": {
            "energy_shape": list(arrays[0].shape),
            "force_shape": list(arrays[1].shape),
            "virial_shape": list(arrays[2].shape),
            "all_finite": bool(passed),
            "energy_min": float(arrays[0].min()),
            "energy_max": float(arrays[0].max()),
            "force_min": float(arrays[1].min()),
            "force_max": float(arrays[1].max()),
            "virial_min": float(arrays[2].min()),
            "virial_max": float(arrays[2].max()),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--structure", type=Path, required=True)
    parser.add_argument("--type-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate(
        args.model.resolve(),
        args.structure.resolve(),
        args.type_map.resolve(),
    )
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not result["passed"]:
        raise SystemExit("teacher-model inference returned non-finite values")


if __name__ == "__main__":
    main()
