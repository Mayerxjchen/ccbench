"""Shared, standard-library primitives for the Packmol Skill scripts."""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

AVOGADRO = 6.02214076e23
ATOMIC_MASSES = {
    "H": 1.008,
    "He": 4.002602,
    "Li": 6.94,
    "B": 10.81,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "F": 18.998403163,
    "Na": 22.98976928,
    "Mg": 24.305,
    "Si": 28.085,
    "P": 30.973761998,
    "S": 32.06,
    "Cl": 35.45,
    "K": 39.0983,
    "Ca": 40.078,
    "Br": 79.904,
    "I": 126.90447,
}
PACKMOL_VERSION_RE = re.compile(r"\bVersion\s+([0-9][0-9A-Za-z.+_-]*)", re.IGNORECASE)


def parse_packmol_version(text: str) -> str | None:
    """Extract a version from Packmol's banner without treating errors as versions."""
    if "PACKMOL" not in text.upper():
        return None
    match = PACKMOL_VERSION_RE.search(text)
    return match.group(1) if match else None


def emit_result(
    *,
    check: str,
    status: str,
    source_paths: list[str] | None = None,
    preparation_stage: str = "",
    **fields: object,
) -> None:
    """Print one ordinary JSON result object (no Evidence prefix).

    *preparation_stage* tracks the workflow phase:
    ``"manifested"`` → ``"preflighted"`` → ``"validated"`` →
    ``"built"`` → ``"executed"`` → ``"qc"`` → ``"packed"``.
    """
    payload: dict[str, object] = {
        "check": check,
        "status": status.lower(),
        "source_paths": source_paths or [],
        **fields,
    }
    if preparation_stage:
        payload["preparation_stage"] = preparation_stage
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def fail(
    *,
    check: str,
    name: str,
    error: Exception | str,
    source_paths: list[str] | None = None,
    **fields: object,
) -> int:
    message = str(error)
    print(message, file=sys.stderr)
    emit_result(
        check=check,
        status="failed",
        source_paths=source_paths,
        name=name,
        error=message,
        **fields,
    )
    return 1


def load_json(path: str | Path) -> dict:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {source}")
    return payload


def write_json(path: str | Path, payload: dict) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def normalize_element(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise ValueError("empty element symbol")
    element = text[0].upper() + text[1:].lower()
    if element not in ATOMIC_MASSES:
        raise ValueError(f"unrecognized element symbol: {raw}")
    return element


def read_xyz(path: str | Path) -> list[tuple[str, float, float, float]]:
    source = Path(path)
    lines = source.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        raise ValueError(f"XYZ requires atom-count and comment lines: {source}")
    try:
        declared_count = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError(f"invalid XYZ atom count: {source}") from exc
    if declared_count <= 0:
        raise ValueError(f"XYZ atom count must be positive: {source}")

    rows = [line for line in lines[2:] if line.strip()]
    if len(rows) != declared_count:
        raise ValueError(
            f"XYZ row count mismatch in {source}: expected {declared_count}, got {len(rows)}"
        )

    atoms: list[tuple[str, float, float, float]] = []
    for index, row in enumerate(rows, start=1):
        parts = row.split()
        if len(parts) < 4:
            raise ValueError(f"XYZ row {index} has fewer than four columns: {source}")
        element = normalize_element(parts[0])
        try:
            coordinates = tuple(float(value) for value in parts[1:4])
        except ValueError as exc:
            raise ValueError(
                f"XYZ row {index} has invalid coordinates: {source}"
            ) from exc
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError(f"XYZ row {index} has non-finite coordinates: {source}")
        atoms.append((element, *coordinates))
    return atoms


def composition(atoms: list[tuple[str, float, float, float]]) -> dict[str, int]:
    return dict(sorted(Counter(atom[0] for atom in atoms).items()))


def molecular_mass(atoms: list[tuple[str, float, float, float]]) -> float:
    return sum(ATOMIC_MASSES[atom[0]] for atom in atoms)


def atom_distance(
    first: tuple[str, float, float, float],
    second: tuple[str, float, float, float],
) -> float:
    """Return the Cartesian distance between two XYZ atom rows."""
    return math.sqrt(sum((first[index] - second[index]) ** 2 for index in range(1, 4)))


def atom_angle_degrees(
    first: tuple[str, float, float, float],
    vertex: tuple[str, float, float, float],
    third: tuple[str, float, float, float],
) -> float:
    """Return the first-vertex-third angle in degrees."""
    left = tuple(first[index] - vertex[index] for index in range(1, 4))
    right = tuple(third[index] - vertex[index] for index in range(1, 4))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise ValueError("cannot compute an angle from duplicate coordinates")
    cosine = sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def positive_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{label} must be a positive finite number")
    return result


def expected_composition(manifest: dict) -> dict[str, int]:
    totals: Counter[str] = Counter()
    derived = manifest.get("derived") or {}
    component_compositions = derived.get("component_compositions") or {}
    for component in manifest.get("components", []):
        name = component["name"]
        count = component["count"]
        for element, atoms_per_molecule in component_compositions[name].items():
            totals[element] += count * atoms_per_molecule
    return dict(sorted(totals.items()))
