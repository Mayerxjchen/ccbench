"""Pure, dependency-light contract for Case 031 active selection.

This module defines the deterministic rules that the reference workflow and the
hidden verifier must both obey. It deliberately imports only ``numpy`` and the
standard library, so every rule is unit-testable on the host and inside the
pinned CPU image without deepmd or ase (the heavy MD/training helpers stay in
the orchestrators that run in the container).

Three contracts live here:

* :func:`select_informative` — inclusive ``[low, high]`` band selection that is
  de-duplicated by configuration hash, deterministic (decreasing deviation,
  then hash, then original index), capped, and never falls back to an
  out-of-band frame.
* :func:`next_batch` — advances through the predeclared exploration batches one
  at a time; an empty batch moves on without ever relaxing the band.
* :func:`assert_exact_growth` — a fail-closed check that the training dataset
  grew by exactly the unique selected configuration hashes, in order.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np


def teacher_label_sha256(
    configuration_hashes: list[str], energies: np.ndarray, forces: np.ndarray
) -> str:
    payload = {
        "configuration_hashes": list(configuration_hashes),
        "energies": [float(value) for value in np.asarray(energies).reshape(-1)],
        "forces": [
            np.asarray(value, dtype=float).reshape(-1).tolist() for value in forces
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def teacher_label_evidence_valid(
    recorded_digest: str,
    configuration_hashes: list[str],
    stored_energies: np.ndarray,
    stored_forces: np.ndarray,
    recomputed_energies: np.ndarray,
    recomputed_forces: np.ndarray,
) -> bool:
    """Validate exact submitted labels plus tolerant independent recomputation.

    The digest binds the labels that were actually committed to the next
    training dataset.  The pinned teacher is then recomputed independently and
    compared numerically, avoiding an invalid bit-identity requirement across
    DeepMD CPU and CUDA backends while still rejecting altered stored labels.
    """
    return (
        recorded_digest
        == teacher_label_sha256(configuration_hashes, stored_energies, stored_forces)
        and np.allclose(
            np.asarray(stored_energies), np.asarray(recomputed_energies),
            rtol=1e-8, atol=1e-7,
        )
        and np.allclose(
            np.asarray(stored_forces), np.asarray(recomputed_forces),
            rtol=1e-8, atol=1e-7,
        )
    )


def select_informative(
    deviations: np.ndarray,
    hashes: list[str],
    excluded: set[str],
    low: float,
    high: float,
    cap: int,
) -> list[int]:
    """Indices of the first ``cap`` in-band, unique, deterministic frames.

    A candidate frame is eligible only when its committee deviation satisfies
    ``low <= d <= high`` (the band is *inclusive* on both ends) and its
    configuration hash is not in ``excluded``. Eligible indices are sorted by
    ``(-deviation, hash, original_index)`` and de-duplicated by hash, keeping
    only the highest-ranked occurrence of each hash. The first ``cap`` indices
    are returned.

    There is **no fallback** to an out-of-band or excluded frame: when nothing
    is eligible the result is an empty list, and the caller must extend the
    declared exploration or stop — never silently pick a low-information frame.
    This is what makes the selection band a hard scientific contract.

    Raises:
        ValueError: if deviations and hashes differ in length, any deviation is
            non-finite, the band is invalid (``0 <= low <= high`` is required),
            or ``cap`` is not positive.
    """
    dev = np.asarray(deviations, dtype=float).reshape(-1)
    if dev.size != len(hashes):
        raise ValueError(
            "deviations and hashes must have the same length: "
            f"{dev.size} != {len(hashes)}"
        )
    if not np.all(np.isfinite(dev)):
        raise ValueError("deviations must all be finite")
    if not (0.0 <= low <= high):
        raise ValueError(f"selection band must satisfy 0 <= low <= high, got [{low}, {high}]")
    cap = int(cap)
    if cap <= 0:
        raise ValueError("cap must be a positive integer")

    excluded_hashes = set(excluded)
    candidates: list[tuple[float, str, int]] = []
    for index, (deviation, frame_hash) in enumerate(zip(dev, hashes)):
        if low <= deviation <= high and frame_hash not in excluded_hashes:
            candidates.append((-float(deviation), frame_hash, index))

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))

    seen: set[str] = set()
    selected: list[int] = []
    for _neg_deviation, frame_hash, index in candidates:
        if frame_hash in seen:
            continue  # de-duplicate: keep only the highest-ranked occurrence
        seen.add(frame_hash)
        selected.append(index)
        if len(selected) >= cap:
            break
    return selected


def select_informative_by_batch(
    deviations: np.ndarray,
    hashes: list[str],
    batch_stops: list[int],
    excluded: set[str],
    low: float,
    high: float,
    cap: int,
) -> list[int]:
    """Apply the fixed-band selection cap independently per MD batch."""
    dev = np.asarray(deviations, dtype=float).reshape(-1)
    if dev.size != len(hashes):
        raise ValueError("deviations and hashes must have the same length")
    if not batch_stops or batch_stops[-1] != len(hashes):
        raise ValueError("batch_stops must end at the candidate count")
    if any(stop <= 0 for stop in batch_stops):
        raise ValueError("batch_stops must be positive")
    if any(left >= right for left, right in zip(batch_stops, batch_stops[1:])):
        raise ValueError("batch_stops must be strictly increasing")

    selected: list[int] = []
    unavailable = set(excluded)
    start = 0
    for stop in batch_stops:
        local = select_informative(
            dev[start:stop], hashes[start:stop], unavailable,
            low, high, cap,
        )
        global_indices = [start + index for index in local]
        selected.extend(global_indices)
        unavailable.update(hashes[index] for index in global_indices)
        start = stop
    return selected


def next_batch(batches: list[dict], completed: int) -> dict | None:
    """Return the ``completed``-th (0-based) declared batch, or ``None``.

    An empty batch therefore advances the workflow to the next declared
    physical condition without ever changing the selection band — the caller
    simply asks for the next batch. Passing ``completed >= len(batches)``
    signals that every declared batch has been attempted.
    """
    if completed < 0 or completed >= len(batches):
        return None
    return dict(batches[completed])


def assert_exact_growth(
    before_hashes: list[str],
    selected_hashes: list[str],
    after_hashes: list[str],
) -> None:
    """Require the dataset to have grown by exactly the selected frames.

    Fails closed (``ValueError`` containing "exactly equal") unless
    ``after_hashes`` is *exactly* ``before_hashes + selected_hashes`` — same
    order, same values, no additions, removals, or reordering. This is the
    audit check that ties every active iteration's data growth to the unique
    teacher-labelled selection.
    """
    expected = list(before_hashes) + list(selected_hashes)
    actual = list(after_hashes)
    if actual != expected:
        raise ValueError(
            "dataset growth must be exactly equal to before + selected: "
            f"expected {expected!r}, got {actual!r}"
        )
