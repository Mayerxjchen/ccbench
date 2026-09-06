"""Pure, host-testable helpers for Case 031 active distillation.

This module deliberately uses only ``numpy`` and the standard library, so the
selection/checkpoint logic can be unit-tested on the host without deepmd or ase
(the heavy MD/training helpers stay in the scripts that run in the container).

It provides three families of functions:

* identity: :func:`sha256`, :func:`configuration_hash`
* selection: :func:`select_informative` — the band-inclusive, capped selection
  used for the active step
* auditability: :func:`checkpoint`, :func:`load_checkpoint`,
  :func:`record_artifact`, :func:`record_dir`, :func:`stage_complete` — an
  atomic, hash-verified run state so a paper run is resumable and auditable
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configuration_hash(atoms) -> str:
    """Stable identity hash for one configuration (cell + positions + species).

    ``atoms`` is duck-typed so the function works with any object exposing
    ``.cell.array``, ``.positions``, and ``.get_chemical_symbols()`` — the ase
    Atoms interface, or a tiny test double.
    """
    digest = hashlib.sha256()
    digest.update(np.asarray(atoms.cell.array, dtype="<f8").tobytes())
    digest.update(np.asarray(atoms.positions, dtype="<f8").tobytes())
    digest.update(" ".join(atoms.get_chemical_symbols()).encode())
    return digest.hexdigest()


def select_informative(
    deviation: np.ndarray, low: float, high: float, cap: int
) -> np.ndarray:
    """Indices of the first ``cap`` frames whose committee deviation lies in-band.

    The band is *inclusive* on both ends (``low <= d <= high``). Frames outside
    the band are never selected and there is **no fallback** to an out-of-band
    frame: when nothing is in-band the result is an empty array, and the caller
    must extend exploration or stop — never silently pick a low-information
    frame. This is what makes the selection band a hard scientific contract.
    """
    deviation = np.asarray(deviation, dtype=float).reshape(-1)
    in_band = np.flatnonzero((deviation >= low) & (deviation <= high))
    return in_band[: int(cap)]


def checkpoint(output: Path, state: dict[str, Any]) -> None:
    """Atomically persist run state: write ``checkpoint.json.tmp`` then rename.

    ``Path.replace`` is atomic on POSIX, so a crash mid-write can never leave a
    torn ``checkpoint.json`` — a reader sees either the previous complete state
    or the new one.
    """
    output.mkdir(parents=True, exist_ok=True)
    target = output / "checkpoint.json"
    tmp = output / "checkpoint.json.tmp"
    tmp.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    tmp.replace(target)


def load_checkpoint(
    output: Path, expected: dict[str, Any]
) -> dict[str, Any] | None:
    """Resume state only when identity and every recorded artifact still match.

    Returns ``None`` when no checkpoint exists. Raises ``RuntimeError`` when the
    checkpoint's identity fields (profile/seed/structure/teacher) or any recorded
    artifact hash no longer matches the workspace — an incompatible state is
    **never** reused, so a tampered or partially-overwritten workspace fails
    closed instead of silently resuming from stale artifacts.
    """
    path = output / "checkpoint.json"
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"unreadable checkpoint.json: {exc}") from exc
    for key, value in expected.items():
        if state.get(key) != value:
            raise RuntimeError(f"checkpoint identity mismatch for {key}")
    for rel, digest in state.get("artifacts", {}).items():
        artifact = output / rel
        if not artifact.is_file() or sha256(artifact) != digest:
            raise RuntimeError(f"checkpoint artifact mismatch: {rel}")
    return state


def record_artifact(state: dict[str, Any], output: Path, path: Path) -> None:
    """Record one artifact's hash in ``state["artifacts"]`` (relative to output)."""
    rel = str(path.resolve().relative_to(output.resolve()))
    state.setdefault("artifacts", {})[rel] = sha256(path)


def record_dir(state: dict[str, Any], output: Path, directory: Path) -> None:
    """Record every file under ``directory`` as an artifact."""
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            record_artifact(state, output, path)


def _recorded_unchanged(state: dict[str, Any], output: Path, path: Path) -> bool:
    rel = str(path.resolve().relative_to(output.resolve()))
    recorded = state.get("artifacts", {}).get(rel)
    return recorded is not None and path.is_file() and sha256(path) == recorded


def stage_complete(state: dict[str, Any], output: Path, paths: list[Path]) -> bool:
    """True when every path is recorded and still byte-identical on disk.

    A directory is complete when *every* file under it is recorded with a
    matching hash. Used on resume to skip stages whose artifacts are already
    committed and verified. Any missing or modified artifact returns False,
    forcing the stage to re-run deterministically.
    """
    if not state:
        return False
    for path in paths:
        if path.is_dir():
            files = [p for p in path.rglob("*") if p.is_file()]
            if not files or any(not _recorded_unchanged(state, output, p) for p in files):
                return False
        elif not _recorded_unchanged(state, output, path):
            return False
    return True
