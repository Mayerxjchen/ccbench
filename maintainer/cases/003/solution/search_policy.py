#!/opt/matclaw/bin/python
"""Hidden sequential-search policy for Case 033.

The paper protocol is a *sequential adaptive* search: the agent proposes a round of
candidate (Ez, temperature) points, measures the domino-flip response, and each later
round's proposal is a deterministic function of those measured rows — never a preset
schedule. The strategy parameters (start point, domain bounds, round cap, step sizes)
live here in solution/ and are never published to public/.

Round 1 is the locked start point plus a paired stronger-field probe and requires no
measurements. Every later round:
  * takes the measured row with the largest *valid* domino slope (a row whose Cu
    flipping is sparse, < 30 % of sites, is a paper-defined gray-X non-event and is
    excluded — never the anchor, never the best),
  * proposes two jobs: a *cross-climb* toward (more negative Ez, lower T) —
    (best.Ez - ez_step, best.temperature_K - t_step), the quadrant where T << Tc
    and a field above the coercive threshold produces a domain wall — and a
    low-T subdivision at the base Ez, both clamped into the search bounds,
  * deduplicates against every already-tried (Ez, T) point, falling back to a
    deterministic bounded-grid sweep if a fresh point cannot be produced.
If no measured row has a valid finite slope, the same bounded-grid sweep is used
(still deterministic). The MD seed never participates in a decision — it only feeds
the downstream Langevin RNG — so the path is a pure function of the measured rows
and is reproducible by the verifier.

The search runs one measured round at a time. Each executed round additionally commits
its decision — the SHA-256 of the canonical JSON summary of every preceding measured
job, the decision timestamp, and the count of preceding jobs — into the run checkpoint
*before* any trajectory runs, so a restart can never re-decide a prior round under the
same run identity.

``canonical_feedback`` and ``feedback_sha256`` are the single serialization used by the
solution and are mirrored by ``tests/verifier.py``; a host test asserts the two agree,
so a digest recorded here is exactly the digest the verifier recomputes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def canonical_feedback(history: list[dict]) -> bytes:
    """Canonical JSON bytes of completed measured jobs (sorted keys, compact)."""
    jobs = []
    for row in history:
        jobs.append({
            "iteration": row["iteration"],
            "job_in_iteration": row["job_in_iteration"],
            "Ez_V_A": row["Ez_V_A"],
            "temperature_K": row["temperature_K"],
            "n_flipped": row["n_flipped"],
            "n_sites": row["n_sites"],
            "slope_ps_per_site": row.get("slope_ps_per_site"),
            "trajectory_sha256": row["trajectory_sha256"],
        })
    return json.dumps(jobs, sort_keys=True, separators=(",", ":")).encode("utf-8")


def feedback_sha256(history: list[dict]) -> str:
    """SHA-256 of the canonical feedback summary of completed measured jobs."""
    return hashlib.sha256(canonical_feedback(history)).hexdigest()


def policy_for(profile: str) -> dict:
    """Load the strategy parameters for a profile from solution/run_profiles.json."""
    profiles = json.loads((HERE / "run_profiles.json").read_text())
    return profiles[profile]


def _policy() -> dict:
    return policy_for(os.environ.get("MATCLAW_PROFILE", "paper"))


# --- deterministic adaptive proposal core -----------------------------------

# A row whose Cu flipping is sparse (< 30 % of sites) is not a genuine domino
# propagation: the paper marks such points as invalid gray crosses, so they can
# never anchor the next round's climb nor be declared the best. The threshold is
# mirrored verbatim by tests/verifier.py (a host test asserts the two agree).
_MIN_VALID_FLIP_RATIO = 0.30


def _flip_ratio(row: dict) -> float:
    n_sites = row.get("n_sites") or 0
    if n_sites <= 0:
        return 1.0
    return float(row.get("n_flipped", 0)) / float(n_sites)


def _slope_of(row: dict) -> float:
    slope = row.get("slope_ps_per_site")
    if slope is None:
        return -1e9
    if _flip_ratio(row) < _MIN_VALID_FLIP_RATIO:
        return -1e9
    return float(slope)


def _in_band(row: dict, policy: dict) -> bool:
    """True when the row's (Ez, T) lies inside the paper's literature bands.

    The coercive field Ez is the reproducible physical target (the paper reports
    Ez=-0.16 V/A at T=50 K); the domino slope is a seed-sensitive propagation
    quality and is NOT used to select the best row. ``early_stop_band`` gates Ez
    and ``early_stop_temperature_band`` gates T (both optional for other profiles).
    """
    ez_band = policy.get("early_stop_band")
    t_band = policy.get("early_stop_temperature_band")
    ez_ok = ez_band is None or float(ez_band[0]) <= float(row["Ez_V_A"]) <= float(ez_band[1])
    t_ok = t_band is None or float(t_band[0]) <= float(row["temperature_K"]) <= float(t_band[1])
    return ez_ok and t_ok


def select_best(history: list[dict], policy: dict) -> dict:
    """Select the best row: the max-slope *valid* row inside the literature bands,
    falling back to the global max-slope valid row when no in-band row exists."""
    valid = [r for r in history if _slope_of(r) > -1e9]
    if not valid:
        return {}
    in_band = [r for r in valid if _in_band(r, policy)]
    return max(in_band if in_band else valid, key=_slope_of)


def _clamp_point(ez: float, temperature: int, policy: dict) -> tuple[float, int]:
    bounds = policy["bounds"]
    ez_min, ez_max = float(bounds["Ez_V_A"][0]), float(bounds["Ez_V_A"][1])
    t_min, t_max = float(bounds["temperature_K"][0]), float(bounds["temperature_K"][1])
    ez = min(max(float(ez), ez_min), ez_max)
    temperature = int(min(max(int(temperature), t_min), t_max))
    return round(ez, 4), temperature


def _round1_jobs(policy: dict) -> list[tuple[float, int]]:
    start = policy["start"]
    probe = float(policy["round1_probe_ez"])
    return [(float(start["Ez_V_A"]), int(start["temperature_K"])),
            (probe, int(start["temperature_K"]))]


def _sweep_fallback(policy: dict) -> list[tuple[float, int]]:
    """Exhaustive bounded-grid sweep, deterministic ordering, used as a last resort."""
    ez_step = float(policy["ez_step"])
    t_step = int(policy["t_step"])
    bounds = policy["bounds"]
    ez_min, ez_max = float(bounds["Ez_V_A"][0]), float(bounds["Ez_V_A"][1])
    t_min, t_max = float(bounds["temperature_K"][0]), float(bounds["temperature_K"][1])
    ez_values: list[float] = []
    ez = float(policy["start"]["Ez_V_A"])
    while ez >= ez_min - 1e-9:
        ez_values.append(round(ez, 4))
        ez -= ez_step
    ez = float(policy["start"]["Ez_V_A"]) + ez_step
    while ez <= ez_max + 1e-9:
        ez_values.append(round(ez, 4))
        ez += ez_step
    t_values: list[int] = []
    t = int(policy["start"]["temperature_K"])
    while t >= t_min:
        t_values.append(t)
        t -= t_step
    t = int(policy["start"]["temperature_K"]) + t_step
    while t <= t_max:
        t_values.append(t)
        t += t_step
    return [(ez, t) for ez in ez_values for t in t_values]


def _adaptive_jobs(preceding: list[dict], policy: dict) -> list[tuple[float, int]]:
    """Two deterministic jobs: one cross-climbing toward (more negative Ez, lower T),
    one subdividing temperature toward low T at the base Ez, anchored on the best
    *valid* measured row (or the start point when no row has a valid finite slope),
    deduplicated against every tried (Ez, T) point.

    The cross-climb is the paper's observed strategy: the domain wall lives where
    T << Tc and the field is above the coercive threshold, a quadrant the previous
    "climb Ez at best.T / lower T at best.Ez" pair could never cross into. Lowering
    T while stepping to stronger Ez reaches that quadrant from the best row."""
    cap = int(policy["max_jobs_per_iteration"])
    tried = {(float(r["Ez_V_A"]), int(r["temperature_K"])) for r in preceding}
    best = max(preceding, key=_slope_of)
    if _slope_of(best) > -1e9:
        base_ez, base_t = float(best["Ez_V_A"]), int(best["temperature_K"])
    else:
        base_ez = float(policy["start"]["Ez_V_A"])
        base_t = int(policy["start"]["temperature_K"])
    ez_step = float(policy["ez_step"])
    t_step = int(policy["t_step"])

    jobs: list[tuple[float, int]] = []
    for i in range(1, 9):  # job 1: cross-climb toward more negative Ez AND lower T
        point = _clamp_point(base_ez - i * ez_step, base_t - i * t_step, policy)
        if point not in tried and point not in jobs:
            jobs.append(point)
            break
    for i in range(1, 9):  # job 2: subdivide temperature toward low T at the base Ez
        point = _clamp_point(base_ez, base_t - i * t_step, policy)
        if point not in tried and point not in jobs:
            jobs.append(point)
            break
    if len(jobs) < cap:
        for point in _sweep_fallback(policy):
            if point not in tried and point not in jobs:
                jobs.append(point)
                if len(jobs) >= cap:
                    break
    if len(jobs) != cap:
        raise ValueError("unable to produce a full round within the search domain")
    return jobs


def propose_round_jobs(previous: list[dict], round_index: int, policy: dict) -> list[tuple[float, int]]:
    """Deterministic (Ez, temperature) proposals for a 1-based round index.

    ``previous`` carries the measured rows of completed earlier rounds. Round 1 is the
    locked start plus its paired stronger-field probe and must be proposed before any
    measurement exists. Every later round requires *all* preceding rounds to be present
    and complete (contiguous, exactly ``cap`` rows each), so a proposal is a function of
    prior measurements and cannot run ahead of them. ``seed`` never enters the decision.
    """
    cap = int(policy["max_jobs_per_iteration"])
    max_rounds = int(policy["max_rounds"])
    if not 1 <= round_index <= max_rounds:
        raise ValueError(f"round {round_index} outside protocol rounds 1..{max_rounds}")
    if round_index == 1:
        if previous:
            raise ValueError("round 1 is the locked start; no preceding measurements expected")
        return _round1_jobs(policy)
    preceding = sorted(previous, key=lambda r: (int(r.get("iteration", -1)), int(r.get("job_in_iteration", 0))))
    preceding_iterations = sorted({int(row.get("iteration", -1)) for row in preceding})
    if preceding_iterations != list(range(1, round_index)):
        raise ValueError(
            f"round {round_index} requires complete measured rounds 1..{round_index - 1}; "
            f"have iterations {preceding_iterations}"
        )
    for iteration in preceding_iterations:
        count = sum(1 for row in preceding if int(row.get("iteration", -1)) == iteration)
        if count != cap:
            raise ValueError(
                f"round {round_index} needs exactly {cap} measured jobs in round {iteration}; have {count}"
            )
    return _adaptive_jobs(preceding, policy)


def propose_round(previous: list[dict], round_index: int, seed: int) -> list[tuple[float, int]]:
    """Return the (Ez, temperature) jobs for the given 1-based round index.

    ``seed`` is intentionally ignored by the decision logic: it only feeds the
    downstream MD RNG, so the path is a pure function of the measured rows and is
    reproducible by the verifier.
    """
    return propose_round_jobs(previous, round_index, _policy())


def should_stop(history: list[dict], policy: dict) -> bool:
    """Deterministic early-stop rule (mirrored by the verifier).

    Stop when the best measured row has a finite sequential slope above
    ``early_stop_slope`` and lies inside ``early_stop_band``; otherwise continue up to
    ``max_rounds``. Profiles without an early-stop band never stop early.
    """
    if "early_stop_slope" not in policy or "early_stop_band" not in policy:
        return False
    if not history:
        return False
    best = select_best(history, policy)
    if not best or _slope_of(best) <= -1e9:  # no valid (>= 30 % flip) measured row yet
        return False
    slope = best.get("slope_ps_per_site")
    if slope is None or float(slope) <= float(policy["early_stop_slope"]):
        return False
    # Stop only when the selected best row is actually inside the literature bands.
    return _in_band(best, policy)
