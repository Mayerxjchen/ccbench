"""Unit tests for the CP2K single-point output parser (03-active-learning).

Coverage, driven by four P0 defects:
  * units  — CP2K forces are hartree/bohr; conversion to eV/Å is verified.
  * ABORT — a run that printed ENERGY but then ABORTed must be rejected.
  * block — only a scoped force block is parsed (last complete one); stray
            5-column numeric tables elsewhere in the output must NOT be
            absorbed.
  * real  — the parser must read a genuine CP2K stdout fragment.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

TESTS = Path(__file__).resolve().parent
CASE = TESTS.parent
sys.path.insert(0, str(CASE / "solution" / "expert" / "03-active-learning"))
import parse_cp2k_label as pcpl  # noqa: E402

# A genuine single-point fragment, shaped like CP2K's real printout
# (see 034-.../reference/runtime-evidence/e-geopt/geopt-output.log).
_REAL_FRAGMENT = """\
 ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]    -1101.2703454561

 FORCES| Atomic forces [hartree/bohr]
 FORCES|   Atom     x               y               z               |f|
 FORCES|      1  1.11494962E-02  1.59816054E-03  2.12988782E-02   2.40937252E-02
 FORCES|      2 -1.63766898E-02 -5.60855684E-03 -8.47386276E-03   1.92732517E-02
 FORCES|      3  1.26716613E-02 -1.75206507E-03 -7.73807320E-03   1.49505354E-02

 *******************************************************************************
 ***                    PROGRAM ENDED AT 2026-08-21 13:00:00                  ***
 *******************************************************************************
"""

# Same structure but with TWO force blocks (two SCF/geometry steps).
# The parser must take the LAST complete block, not concatenate both.
_TWO_BLOCK_FRAGMENT = """\
 ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]    -1101.2703454561

 FORCES| Atomic forces [hartree/bohr]
 FORCES|   Atom     x               y               z               |f|
 FORCES|      1  1.00000000E-02  0.00000000E+00  0.00000000E+00   1.00000000E-02
 FORCES|      2  2.00000000E-02  0.00000000E+00  0.00000000E+00   2.00000000E-02

 FORCES| Atomic forces [hartree/bohr]
 FORCES|   Atom     x               y               z               |f|
 FORCES|      1  3.00000000E-02  0.00000000E+00  0.00000000E+00   3.00000000E-02
 FORCES|      2  4.00000000E-02  0.00000000E+00  0.00000000E+00   4.00000000E-02

 *******************************************************************************
 ***                    PROGRAM ENDED AT 2026-08-21 13:00:00                  ***
 *******************************************************************************
"""

# An output with an ENERGY line but then an ABORT (SCF did not converge).
# This MUST be rejected — a partial label must never enter the training set.
_ABORTED_FRAGMENT = """\
 ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]    -1101.2703454561

 FORCES| Atomic forces [hartree/bohr]
 FORCES|      1  1.00000000E-02  0.00000000E+00  0.00000000E+00   1.00000000E-02

 *** ABORTED in scf by request ***
"""

# A fragment where a stray 5-column numeric table (Mulliken-style) sits
# BEFORE the real force block. The legacy 5-col matcher would wrongly grab
# it; the scoped parser must ignore it and take only the FORCES| block.
_STRAY_TABLE_FRAGMENT = """\
 ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]    -1101.2703454561

 # Mulliken charges per element
   1   O  0.1234  -0.5678   0.9999
   2   H  0.2345   0.6789   0.8888

 FORCES| Atomic forces [hartree/bohr]
 FORCES|   Atom     x               y               z               |f|
 FORCES|      1  1.11494962E-02  1.59816054E-03  2.12988782E-02   2.40937252E-02
 FORCES|      2 -1.63766898E-02 -5.60855684E-03 -8.47386276E-03   1.92732517E-02

 *******************************************************************************
 ***                    PROGRAM ENDED                                          ***
 *******************************************************************************
"""

# Legacy format some older CP2K builds emit.
_LEGACY_FRAGMENT = """\
 ENERGY| Total FORCE_EVAL ( QS ) energy [a.u.]:             -47.60123456789123

 ATOMIC FORCES in [a.u.]

 # Atom   Element          X              Y              Z
      1   O             0.0123456789    -0.0000000001     0.1000000000
      2   H             0.0000000000     0.2000000000     0.0000000000
      3   C            -0.1000000000     0.0000000000     0.0000000000

 *******************************************************************************
 ***                    PROGRAM ENDED                                          ***
 *******************************************************************************
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


class TestParseRealFormat:
    def test_real_fragment_parses_energy(self, tmp_path):
        r = pcpl.parse_cp2k_out(_write(tmp_path, "mod.out", _REAL_FRAGMENT))
        assert r is not None
        assert r["energy_Ha"] == pytest.approx(-1101.2703454561)

    def test_real_fragment_parses_forces(self, tmp_path):
        r = pcpl.parse_cp2k_out(_write(tmp_path, "mod.out", _REAL_FRAGMENT))
        assert r["forces"].shape == (3, 3)
        assert r["forces"][0, 0] == pytest.approx(1.11494962E-02)

    def test_force_modulus_column_is_dropped(self, tmp_path):
        """The parser must take fx,fy,fz (cols 2,3,4), not the |f| column."""
        r = pcpl.parse_cp2k_out(_write(tmp_path, "mod.out", _REAL_FRAGMENT))
        # |f| for atom 1 is 2.40937252E-02; it must not appear as a component
        assert abs(r["forces"][0].sum() - 2.40937252E-02) > 1e-6


class TestForceBlockScoping:
    def test_takes_last_complete_block(self, tmp_path):
        """Two force blocks -> the LAST one wins (not their concatenation)."""
        r = pcpl.parse_cp2k_out(_write(tmp_path, "two.out", _TWO_BLOCK_FRAGMENT))
        assert r is not None
        assert r["forces"].shape == (2, 3)
        # second block: atom1 fx=3e-2, atom2 fx=4e-2
        assert r["forces"][0, 0] == pytest.approx(3.0E-02)
        assert r["forces"][1, 0] == pytest.approx(4.0E-02)

    def test_stray_numeric_table_is_not_absorbed(self, tmp_path):
        """A 5-col table outside any force block must not pollute forces."""
        r = pcpl.parse_cp2k_out(_write(tmp_path, "stray.out", _STRAY_TABLE_FRAGMENT))
        assert r is not None
        assert r["forces"].shape == (2, 3)
        # only the two FORCES| rows; the Mulliken row (fx=0.1234) must be absent
        assert not np.any(np.isclose(r["forces"][:, 0], 0.1234))
        assert r["forces"][0, 0] == pytest.approx(1.11494962E-02)

    def test_legacy_block_scoped(self, tmp_path):
        r = pcpl.parse_cp2k_out(_write(tmp_path, "leg.out", _LEGACY_FRAGMENT))
        assert r is not None
        assert r["energy_Ha"] == pytest.approx(-47.60123456789123)
        assert r["forces"].shape == (3, 3)
        assert r["forces"][2, 0] == pytest.approx(-0.1000000000)  # carbon


class TestAbortRejection:
    def test_aborted_run_rejected(self, tmp_path):
        """Energy printed, then ABORT -> must return None (no partial labels)."""
        r = pcpl.parse_cp2k_out(_write(tmp_path, "abort.out", _ABORTED_FRAGMENT))
        assert r is None

    def test_no_program_ended_rejected(self, tmp_path):
        text = (" ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]  -1.0\n"
                " FORCES|      1  0.1  0.2  0.3  0.4\n")
        assert pcpl.parse_cp2k_out(_write(tmp_path, "noend.out", text)) is None

    def test_empty_file_rejected(self, tmp_path):
        assert pcpl.parse_cp2k_out(_write(tmp_path, "empty.out", "")) is None

    def test_energy_without_forces_rejected(self, tmp_path):
        text = (" ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]  -1.0\n"
                " *** PROGRAM ENDED ***\n")
        assert pcpl.parse_cp2k_out(_write(tmp_path, "nof.out", text)) is None

    def test_no_energy_line_rejected(self, tmp_path):
        text = " FORCES|      1  0.1  0.2  0.3  0.4\n *** PROGRAM ENDED ***\n"
        assert pcpl.parse_cp2k_out(_write(tmp_path, "noe.out", text)) is None


class TestUnitConversion:
    def test_factor_value(self):
        assert pcpl.HARTREE_BOHR_TO_EV_ANGSTROM == pytest.approx(51.422067, rel=1e-5)

    def test_to_deepmd_units_energy(self, tmp_path):
        r = pcpl.parse_cp2k_out(_write(tmp_path, "mod.out", _REAL_FRAGMENT))
        u = pcpl.to_deepmd_units(r)
        assert u["energy_eV"] == pytest.approx(-1101.2703454561 * 27.211386245988)

    def test_to_deepmd_units_forces(self, tmp_path):
        r = pcpl.parse_cp2k_out(_write(tmp_path, "mod.out", _REAL_FRAGMENT))
        u = pcpl.to_deepmd_units(r)
        assert u["forces_eV_per_A"][0, 0] == pytest.approx(
            1.11494962E-02 * pcpl.HARTREE_BOHR_TO_EV_ANGSTROM)

    def test_forces_are_not_raw_when_converted(self, tmp_path):
        """Sanity: converted forces differ from raw by exactly the factor."""
        r = pcpl.parse_cp2k_out(_write(tmp_path, "mod.out", _REAL_FRAGMENT))
        u = pcpl.to_deepmd_units(r)
        ratio = u["forces_eV_per_A"][0, 0] / r["forces"][0, 0]
        assert ratio == pytest.approx(pcpl.HARTREE_BOHR_TO_EV_ANGSTROM)


class TestRealCP2KLog:
    REAL_LOG = (CASE.parent /
                "034-ai2kit-water64-end-to-end-potential" /
                "reference" / "runtime-evidence" / "e-geopt" /
                "geopt-output.log")

    @pytest.mark.skipif(not REAL_LOG.is_file(),
                        reason="034 geopt log not present in this checkout")
    def test_real_geopt_log_parses(self):
        r = pcpl.parse_cp2k_out(self.REAL_LOG)
        assert r is not None, "parser must handle a real CP2K stdout"
        assert r["energy_Ha"] == pytest.approx(-1101.270345456108771, rel=1e-10)
        assert r["forces"].ndim == 2 and r["forces"].shape[1] == 3
        assert r["forces"].shape[0] > 0
        u = pcpl.to_deepmd_units(r)
        assert u["energy_eV"] == pytest.approx(-29967.0, rel=1e-3)
