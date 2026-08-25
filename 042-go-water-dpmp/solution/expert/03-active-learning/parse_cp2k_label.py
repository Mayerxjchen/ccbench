#!/usr/bin/env python3
"""Parse CP2K single-point outputs into labeled DeepMD frames.

Extracted from 03-active-learning/run.sh so the parsing logic (which must
match CP2K's real stdout format) can be unit-tested against genuine
fragments rather than only exercised inside the bash heredoc.

CP2K's actual force-eval printout (confirmed against
034-.../reference/runtime-evidence/e-geopt/geopt-output.log):

    ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]    -1101.2703454561
    FORCES| Atomic forces [hartree/bohr]
    FORCES|   Atom     x               y               z               |f|
    FORCES|      1  1.11494962E-02  1.59816054E-03  2.12988782E-02   2.40937252E-02

The energy is in hartree; the forces are in hartree/bohr.  DeepMD expects
eV and eV/Å, so :func:`to_deepmd_units` applies the conversion (the parser
itself returns raw CP2K units so the round-trip is auditable).
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

# ── Physical constants (match 034's reference converter) ───────────────
HARTREE_TO_EV = 27.211386245988
BOHR_TO_ANGSTROM = 0.529177210903
HARTREE_BOHR_TO_EV_ANGSTROM = HARTREE_TO_EV / BOHR_TO_ANGSTROM  # ≈ 51.422067

# Match "ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]  -123.45"
# or the legacy "[a.u.]" spelling, with or without a trailing colon.
_ENERGY_RE = re.compile(
    r"ENERGY\|\s+Total FORCE_EVAL \( QS \) energy "
    r"\[(?:hartree|a\.u\.)\]:?\s+([-0-9.Ee+]+)"
)


# ── Force-block extraction ──────────────────────────────────────────────
def _modern_force_blocks(text: str) -> list[np.ndarray]:
    """Return every modern ``FORCES|`` block as an (nat, 3) array.

    A block is a maximal run of consecutive data rows
    ``FORCES| <idx> <fx> <fy> <fz> <|f|>``.  Header / column-label lines
    and any non-FORCES line act as block boundaries, so each SCF step's
    forces become a separate block.  Forces are kept in raw CP2K units
    (hartree/bohr); column 5 (|f|) is dropped.
    """
    blocks: list[list[list[float]]] = []
    current: list[list[float]] = []
    for line in text.splitlines():
        if line.lstrip().startswith("FORCES|"):
            parts = line.split()
            if len(parts) == 6 and parts[1].lstrip("-").isdigit():
                try:
                    current.append(
                        [float(parts[2]), float(parts[3]), float(parts[4])])
                except ValueError:
                    pass
            else:
                # header / label row -> block boundary
                if current:
                    blocks.append(current)
                    current = []
        else:
            if current:
                blocks.append(current)
                current = []
    if current:
        blocks.append(current)
    return [np.array(b, dtype=float) for b in blocks if b]


def _legacy_force_blocks(text: str) -> list[np.ndarray]:
    """Return every legacy ``ATOMIC FORCES`` block as an (nat, 3) array.

    A legacy block starts at a line containing ``ATOMIC FORCES`` and runs
    until a blank line or a row whose first token is not an atom index.
    Rows may be ``idx <Elem> fx fy fz`` (5 col) or
    ``idx <Kind> <Elem> fx fy fz`` (6 col); forces are the last 3 tokens,
    so both layouts parse identically.  Only rows *inside* a block are
    accepted — never stray 5-column tables elsewhere in the output.
    """
    blocks: list[list[list[float]]] = []
    current: list[list[float]] = []
    in_block = False
    for line in text.splitlines():
        if "ATOMIC FORCES" in line:
            if current:
                blocks.append(current)
                current = []
            in_block = True
            continue
        if not in_block:
            continue
        parts = line.split()
        if not parts:
            # blank line: closes the block ONLY once real data has been
            # seen; a blank line between the header and the column-label
            # / data rows (common in CP2K output) must not close it.
            if current:
                blocks.append(current)
                current = []
                in_block = False
            continue
        # A force row begins with an atom index (integer).  Column-label
        # / comment rows ("# Atom ...", "Atom Element ...") start with a
        # non-integer token and are skipped WITHOUT closing the block.
        if not parts[0].lstrip("-").isdigit():
            if parts[0].startswith("#") or parts[0].startswith("Atom"):
                continue
            # a real section change -> end of block
            if current:
                blocks.append(current)
                current = []
            in_block = False
            continue
        try:
            current.append(
                [float(parts[-3]), float(parts[-2]), float(parts[-1])])
        except (ValueError, IndexError):
            pass
    if current:
        blocks.append(current)
    return [np.array(b, dtype=float) for b in blocks if b]


def _last_force_block(text: str) -> np.ndarray | None:
    """Return the last complete force block (raw hartree/bohr) or None.

    A real CP2K output uses exactly one of the two formats; when both
    somehow yield blocks the modern format wins (it is what current CP2K
    builds emit).  The caller validates that the returned row count
    matches its expected natoms.
    """
    modern = _modern_force_blocks(text)
    if modern:
        return modern[-1]
    legacy = _legacy_force_blocks(text)
    if legacy:
        return legacy[-1]
    return None


def parse_cp2k_out(path: Path):
    """Return ``{"energy_Ha": float, "forces": np.ndarray[nat,3]}`` or None.

    Raw CP2K units are preserved (energy in hartree, forces in
    hartree/bohr); call :func:`to_deepmd_units` to get eV / eV-Å.

    Returns None unless the run *completed successfully*: CP2K must have
    printed ``PROGRAM ENDED`` and must NOT have printed ``ABORT``.  A run
    that aborted after emitting an ``ENERGY|`` line is therefore rejected
    rather than silently feeding partial labels into the training set.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if "PROGRAM ENDED" not in text:
        return None  # did not complete
    if "ABORT" in text:
        return None  # CP2K wrote ABORT/ABORTED -> failed run
    m = _ENERGY_RE.search(text)
    if not m:
        return None
    e_ha = float(m.group(1))
    forces = _last_force_block(text)
    if forces is None or forces.size == 0:
        return None
    return {"energy_Ha": e_ha, "forces": forces}


def to_deepmd_units(parsed: dict) -> dict:
    """Convert parsed CP2K values (Ha, Ha/bohr) to DeepMD units (eV, eV/Å)."""
    return {
        "energy_eV": parsed["energy_Ha"] * HARTREE_TO_EV,
        "forces_eV_per_A": np.asarray(parsed["forces"]) * HARTREE_BOHR_TO_EV_ANGSTROM,
    }


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        r = parse_cp2k_out(Path(p))
        if r is None:
            print(f"{p}: UNPARSABLE / failed run")
        else:
            u = to_deepmd_units(r)
            print(f"{p}: E={u['energy_eV']:.4f} eV, "
                  f"forces {u['forces_eV_per_A'].shape} (eV/Å)")
