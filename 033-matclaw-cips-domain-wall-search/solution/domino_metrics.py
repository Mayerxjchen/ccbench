"""Trajectory-derived domino metric used by the Case 033 workflow."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d


def _unwrap(z):
    z = np.mod(np.asarray(z), 1.0)
    ordered = np.sort(z)
    gaps = np.diff(np.concatenate((ordered, ordered[:1] + 1.0)))
    origin = ordered[(int(np.argmax(gaps)) + 1) % len(ordered)]
    return np.mod(z - origin, 1.0) + origin


def analyze(frames, frame_dt_ps=0.02):
    symbols = np.asarray(frames[0].get_chemical_symbols())
    cu = np.flatnonzero(symbols == "Cu")
    cu = cu[np.argsort(frames[0].get_scaled_positions(wrap=True)[cu, 1])]
    other = np.flatnonzero(symbols != "Cu")
    displacement = []
    for frame in frames:
        z = _unwrap(frame.get_scaled_positions(wrap=True)[:, 2]) * frame.cell.lengths()[2]
        displacement.append(z[cu] - np.mean(z[other]))
    displacement = np.asarray(displacement)
    smooth = gaussian_filter1d(displacement, sigma=0.5 / frame_dt_ps, axis=0, mode="nearest")
    flips = np.full(len(cu), np.nan)
    for site in range(len(cu)):
        crossing = np.flatnonzero((smooth[:-1, site] > 0) & (smooth[1:, site] <= 0))
        if len(crossing):
            flips[site] = (crossing[0] + 1) * frame_dt_ps
    distances, delays = [], []
    for distance in range(1, min(10, len(cu) - 1) + 1):
        samples = [abs(flips[i + distance] - flips[i]) for i in range(len(cu) - distance)
                   if np.isfinite(flips[i]) and np.isfinite(flips[i + distance])]
        if samples:
            distances.append(distance)
            delays.append(float(np.mean(samples)))
    slope = float(np.polyfit(distances, delays, 1)[0]) if len(distances) >= 3 else None
    return {"n_sites": len(cu), "n_flipped": int(np.sum(np.isfinite(flips))),
            "flip_times_ps": [None if not np.isfinite(x) else float(x) for x in flips],
            "distances_site": distances, "mean_abs_delay_ps": delays,
            "slope_ps_per_site": slope,
            "sequential_propagation": bool(slope is not None and slope > 0.3),
            "max_abs_displacement_A": float(np.max(np.abs(displacement - displacement[0])))}
