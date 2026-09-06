"""Tests for the CP2K single-point input builder (03-active-learning).

Guards the coord.inc element-symbol bug: CP2K &COORD rows need
``<Element> x y z``; writing only ``x y z`` makes CP2K fail to assign
Kinds.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
CASE = TESTS.parent
sys.path.insert(0, str(CASE / "solution" / "expert" / "03-active-learning"))
import cp2k_input  # noqa: E402


def _write_xyz(path: Path, symbols: list[str], coords: list[tuple]) -> None:
    nat = len(symbols)
    lines = [f"{nat}\nframe\n"]
    for s, (x, y, z) in zip(symbols, coords):
        lines.append(f"{s} {x:.8f} {y:.8f} {z:.8f}\n")
    path.write_text("".join(lines))


class TestCoordInc:
    def test_coord_inc_keeps_element_symbol(self, tmp_path):
        xyz = tmp_path / "config-000.xyz"
        _write_xyz(xyz, ["O", "H", "C"],
                   [(0.0, 0.0, 0.0), (0.96, 0.0, 0.0), (0.0, 1.42, 0.0)])
        inc = tmp_path / "coord.inc"
        n = cp2k_input.xyz_to_coord_inc(xyz, inc)
        assert n == 3
        rows = inc.read_text().splitlines()
        assert len(rows) == 3
        for row, sym in zip(rows, ["O", "H", "C"]):
            parts = row.split()
            assert parts[0] == sym, f"coord.inc row missing element: {row!r}"
            assert len(parts) == 4  # element + 3 coords
            # the three coords must be floats
            for c in parts[1:]:
                float(c)

    def test_coord_inc_not_xyz_only(self, tmp_path):
        """Regression: the old writer dropped the element column."""
        xyz = tmp_path / "config-000.xyz"
        _write_xyz(xyz, ["O", "H"], [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
        inc = tmp_path / "coord.inc"
        cp2k_input.xyz_to_coord_inc(xyz, inc)
        first = inc.read_text().splitlines()[0].split()
        assert first[0] == "O", "element symbol must be the first token"
        assert first[1:] == ["1.00000000", "2.00000000", "3.00000000"]

    def test_coord_inc_respects_natoms(self, tmp_path):
        """Only the frame's natoms rows must be written (trailing junk ignored)."""
        xyz = tmp_path / "config-000.xyz"
        xyz.write_text("2\nframe\nO 0 0 0\nH 1 0 0\nstray extra line\n")
        inc = tmp_path / "coord.inc"
        n = cp2k_input.xyz_to_coord_inc(xyz, inc)
        assert n == 2
        assert len(inc.read_text().splitlines()) == 2


class TestCoordIncCp2kSmoke:
    """A minimal CP2K &COORD + coord.inc parse check (no CP2K binary needed)."""

    def test_coord_inc_parses_as_cp2k_coord_block(self, tmp_path):
        """A coord.inc produced here is valid as a @INCLUDE under &COORD:
        each line is <Element> x y z with a known element symbol."""
        xyz = tmp_path / "config-000.xyz"
        _write_xyz(xyz, ["O", "H", "H", "C"],
                   [(0, 0, 0), (0.96, 0, 0), (-0.24, 0.93, 0), (0, 0, 3.35)])
        inc = tmp_path / "coord.inc"
        cp2k_input.xyz_to_coord_inc(xyz, inc)
        known = {"O", "H", "C", "N", "S", "P", "F", "Cl", "Br", "I", "Si"}
        for line in inc.read_text().splitlines():
            parts = line.split()
            assert parts[0] in known, f"unknown element symbol: {parts[0]!r}"
            assert len(parts) == 4
