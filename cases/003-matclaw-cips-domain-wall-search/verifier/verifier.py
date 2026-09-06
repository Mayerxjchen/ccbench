"""Self-contained hidden evaluator for Case 033 sequential domain-wall search.

Security model: inspection is limited to the graded submission directory
(default /app). Profile constants and identity hashes are embedded; every metric
is recomputed from the delivered raw trajectories. The search's chronology is
enforced for both profiles with an independent digest chain: each round after the
first must record ``decision_input_sha256`` equal to the SHA-256 of the canonical
JSON summary of every preceding measured job, a ``decision_made_at`` timestamp
that postdates every prior job's ``completed_at``, and a correct
``preceding_job_count``. The electric-field protocol is derived from actual
calculator behavior and raw trajectories — the forces stored in a trajectory
frame must equal the pinned model's DP forces plus ``q_i * E`` for the claimed
field. Nothing is read from public/ or reference/ at runtime, so the same file
grades both in-container (/tests) and via the reference wrapper.

The search path must be *evidence-adaptive*: the verifier replays the mirrored
deterministic policy (``propose_round_jobs`` + the early-stop rule) against the
measured history and requires every declared round's jobs to equal the policy's
proposal given the preceding measured rows — a preset literature-answer schedule
is rejected. The paper formal gate additionally requires the best measured row to
have ``Ez_V_A`` inside the literature band ``[-0.18, -0.14]`` V/Å with a
sequential slope above ``MIN_SEQUENTIAL_SLOPE``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from ase.io.trajectory import Trajectory
from scipy.ndimage import gaussian_filter1d

EXPECTED_STRUCTURE = "c1fac0de0180624d818886602aa33721f708c757900fde24581f4e723b036180"
EXPECTED_MODEL = "a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d"
EXPECTED_CHARGES = {"Cu": 0.765, "In": -0.085, "P": -0.085, "S": -0.085}
START = {"Ez_V_A": -0.01, "temperature_K": 200}
BOUNDS = {"Ez_V_A": (-0.3, 0.0), "temperature_K": (0, 250)}
TWO_JOB_CAP = 2
SMOKE_ROUNDS = 2
SMOKE_JOBS = 4
PAPER_ROUNDS = 7
PAPER_JOBS = 14
# SMOKE_JOBS / PAPER_JOBS are the nominal full-path job counts; an evidence-adaptive
# path may stop earlier (early-stop rule), so the accepted counts come from the replay.
MIN_SEQUENTIAL_SLOPE = 0.3
PAPER_EZ_BAND = (-0.18, -0.14)
PAPER_TEMPERATURE_BAND = (30, 70)
FRAME_DT_PS = 0.02
FRAME_INTERVAL_STEPS = 10
FIELD_TOLERANCE_EV_A = 1e-5
_DEFERRED_DP: dict[str, Any] = {}

# The deterministic adaptive search strategy, mirrored from solution/run_profiles.json.
# A host test (test_analysis.py) asserts these constants agree with the file, so a
# digest / proposal the solution records is exactly the one the verifier recomputes.
_POLICY_BASE = {
    "max_jobs_per_iteration": TWO_JOB_CAP,
    "start": START,
    "bounds": BOUNDS,
}
_POLICY_SMOKE = {
    **_POLICY_BASE,
    "max_rounds": SMOKE_ROUNDS,
    "ez_step": 0.03,
    "t_step": 40,
    "round1_probe_ez": -0.05,
}
_POLICY_PAPER = {
    **_POLICY_BASE,
    "max_rounds": PAPER_ROUNDS,
    "ez_step": 0.03,
    "t_step": 40,
    "round1_probe_ez": -0.05,
    "early_stop_slope": MIN_SEQUENTIAL_SLOPE,
    "early_stop_band": list(PAPER_EZ_BAND),
    "early_stop_temperature_band": list(PAPER_TEMPERATURE_BAND),
}


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _dp_calculator(model_path: Path):
    """Load (and cache) a deepmd calculator for the pinned teacher model."""
    key = str(model_path)
    if key in _DEFERRED_DP:
        return _DEFERRED_DP[key]
    from deepmd.calculator import DP

    calculator = DP(model=str(model_path))
    _DEFERRED_DP[key] = calculator
    return calculator


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def equivalent(left: Any, right: Any, tolerance: float = 1e-8) -> bool:
    if left is None or right is None:
        return left is right
    return bool(np.isclose(float(left), float(right), atol=tolerance, rtol=1e-7))


def unwrap_cluster(z_fractional: np.ndarray) -> np.ndarray:
    z = np.mod(np.asarray(z_fractional, dtype=float), 1.0)
    if z.ndim != 1 or z.size == 0:
        raise ValueError("fractional z coordinates must be a non-empty vector")
    ordered = np.sort(z)
    gaps = np.diff(np.concatenate((ordered, ordered[:1] + 1.0)))
    origin = ordered[(int(np.argmax(gaps)) + 1) % ordered.size]
    return np.mod(z - origin, 1.0) + origin


def cu_displacements_A(frames: list[Any]) -> tuple[np.ndarray, np.ndarray]:
    if not frames:
        raise ValueError("trajectory contains no frames")
    symbols = np.asarray(frames[0].get_chemical_symbols())
    cu_indices = np.flatnonzero(symbols == "Cu")
    other_indices = np.flatnonzero(symbols != "Cu")
    site_order = np.argsort(frames[0].get_scaled_positions(wrap=True)[cu_indices, 1])
    cu_indices = cu_indices[site_order]
    displacement: list[np.ndarray] = []
    for frame in frames:
        if frame.get_chemical_symbols() != frames[0].get_chemical_symbols():
            raise ValueError("atom ordering changed within trajectory")
        z = unwrap_cluster(frame.get_scaled_positions(wrap=True)[:, 2]) * frame.cell.lengths()[2]
        displacement.append(z[cu_indices] - float(np.mean(z[other_indices])))
    return np.asarray(displacement), cu_indices


def analyze_displacements(displacement_A: np.ndarray, frame_dt_ps: float = FRAME_DT_PS,
                          smoothing_ps: float = 0.5, max_distance: int = 10) -> dict:
    values = np.asarray(displacement_A, dtype=float)
    if values.ndim != 2 or values.shape[0] < 2 or values.shape[1] < 2:
        raise ValueError("displacements must be a time-by-site matrix")
    sigma_frames = smoothing_ps / frame_dt_ps
    smooth = gaussian_filter1d(values, sigma=sigma_frames, axis=0, mode="nearest")
    flip_times = np.full(values.shape[1], np.nan)
    for site in range(values.shape[1]):
        crossings = np.flatnonzero((smooth[:-1, site] > 0) & (smooth[1:, site] <= 0))
        if crossings.size:
            flip_times[site] = (int(crossings[0]) + 1) * frame_dt_ps
    delays: list[float] = []
    distances: list[int] = []
    upper = min(max_distance, values.shape[1] - 1)
    for distance in range(1, upper + 1):
        pairs = [(flip_times[i], flip_times[i + distance]) for i in range(values.shape[1] - distance)]
        valid = [abs(right - left) for left, right in pairs if np.isfinite(left) and np.isfinite(right)]
        if valid:
            distances.append(distance)
            delays.append(float(np.mean(valid)))
    slope: float | None = None
    intercept: float | None = None
    if len(distances) >= 3:
        slope, intercept = (float(x) for x in np.polyfit(distances, delays, 1))
    return {"n_sites": int(values.shape[1]), "n_flipped": int(np.sum(np.isfinite(flip_times))),
            "flip_times_ps": [None if not np.isfinite(x) else float(x) for x in flip_times],
            "distances_site": distances, "mean_abs_delay_ps": delays,
            "slope_ps_per_site": slope, "intercept_ps": intercept,
            "sequential_propagation": bool(slope is not None and slope > MIN_SEQUENTIAL_SLOPE)}


def analyze_frames(frames: list[Any], frame_dt_ps: float = FRAME_DT_PS) -> dict:
    displacement, indices = cu_displacements_A(frames)
    result = analyze_displacements(displacement, frame_dt_ps=frame_dt_ps)
    result["cu_atom_indices"] = [int(index) for index in indices]
    result["max_abs_displacement_A"] = float(np.max(np.abs(displacement - displacement[0])))
    return result


def canonical_feedback(history: list[dict]) -> str:
    """Canonical JSON summary of completed measured jobs (sorted keys, compact)."""
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
    return json.dumps(jobs, sort_keys=True, separators=(",", ":"))


def feedback_sha256(history: list[dict]) -> str:
    return hashlib.sha256(canonical_feedback(history).encode()).hexdigest()


def policy_for(profile: str) -> dict:
    """Return the mirrored adaptive-policy parameters for a profile."""
    return dict(_POLICY_PAPER if profile == "paper" else _POLICY_SMOKE)


# --- deterministic adaptive proposal core (mirrors solution/search_policy.py) ---


_MIN_VALID_FLIP_RATIO = 0.30


def _flip_ratio(row: dict) -> float:
    n_sites = row.get("n_sites") or 0
    if n_sites <= 0:
        return 1.0
    return float(row.get("n_flipped", 0)) / float(n_sites)


def _slope_or_neg(row: dict) -> float:
    slope = row.get("slope_ps_per_site")
    if slope is None:
        return -1e9
    if _flip_ratio(row) < _MIN_VALID_FLIP_RATIO:
        return -1e9
    return float(slope)


def _in_band(row: dict, policy: dict) -> bool:
    """Mirror of ``search_policy._in_band``: (Ez, T) inside the literature bands."""
    ez_band = policy.get("early_stop_band")
    t_band = policy.get("early_stop_temperature_band")
    ez_ok = ez_band is None or float(ez_band[0]) <= float(row["Ez_V_A"]) <= float(ez_band[1])
    t_ok = t_band is None or float(t_band[0]) <= float(row["temperature_K"]) <= float(t_band[1])
    return ez_ok and t_ok


def _select_best(history: list[dict], policy: dict) -> dict:
    """Mirror of ``search_policy.select_best``: max-slope valid in-band row."""
    valid = [r for r in history if _slope_or_neg(r) > -1e9]
    if not valid:
        return {}
    in_band = [r for r in valid if _in_band(r, policy)]
    return max(in_band if in_band else valid, key=_slope_or_neg)


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
    """Mirror of the solution's adaptive proposer: one cross-climb job (toward more
    negative Ez AND lower T) and one low-T subdivision job, anchored on the best
    *valid* measured row (or the start point when no row has a valid finite slope),
    deduplicated against every tried (Ez, T) point."""
    cap = int(policy["max_jobs_per_iteration"])
    tried = {(float(r["Ez_V_A"]), int(r["temperature_K"])) for r in preceding}
    best = max(preceding, key=_slope_or_neg)
    if _slope_or_neg(best) > -1e9:
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
    return jobs


def propose_round_jobs(previous: list[dict], round_index: int, policy: dict) -> list[tuple[float, int]]:
    """Deterministic (Ez, temperature) proposals for a 1-based round index.

    Mirrors ``solution/search_policy.py::propose_round_jobs`` so the verifier can
    recompute every round's proposal from the recorded measurements alone. Round 1 is
    the locked start plus its paired stronger-field probe; later rounds climb the best
    measured slope and deduplicate against every already-tried point.
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


def _early_stop(history: list[dict], round_index: int, policy: dict) -> bool:
    """Mirror of ``search_policy.should_stop`` evaluated after ``round_index``."""
    if "early_stop_slope" not in policy or "early_stop_band" not in policy:
        return False
    measured = [r for r in history if int(r.get("iteration", 0)) <= round_index]
    if not measured:
        return False
    best = _select_best(measured, policy)
    if not best or _slope_or_neg(best) <= -1e9:  # no valid (>= 30 % flip) measured row yet
        return False
    slope = best.get("slope_ps_per_site")
    if slope is None or float(slope) <= float(policy["early_stop_slope"]):
        return False
    # Stop only when the selected best row is actually inside the literature bands.
    return _in_band(best, policy)


def expected_round_jobs(history: list[dict], policy: dict) -> dict[int, list[tuple[float, int]]]:
    """Replay the deterministic adaptive policy against the measured history.

    Returns ``{round_index: [(Ez, T), ...]}`` for every round the policy would run,
    honoring the early-stop rule so variable-length adaptive paths are accepted. If the
    declared history is incomplete or inconsistent, replay returns whatever replayed so
    far and the contiguity / per-round checks flag the discrepancy.
    """
    expected: dict[int, list[tuple[float, int]]] = {}
    max_rounds = int(policy["max_rounds"])
    round_index = 1
    try:
        while round_index <= max_rounds:
            previous = [r for r in history if int(r.get("iteration", 0)) < round_index]
            previous = sorted(previous, key=lambda r: (int(r.get("iteration", 0)), int(r.get("job_in_iteration", 0))))
            expected[round_index] = propose_round_jobs(previous, round_index, policy)
            if _early_stop(history, round_index, policy):
                break
            round_index += 1
    except (ValueError, KeyError, TypeError):
        pass
    return expected


def _derive_field_errors(submission: Path, history: list[dict]) -> list[str]:
    """Derive the field protocol from actual calculator behavior and raw trajectories.

    The forces stored in a trajectory frame are the full physical force (DP plus the
    uniform electric field). Recomputing the DP contribution with the pinned teacher
    model, ``stored - DP`` must equal ``q_i * E`` for the field the submission claims on
    that job. This is a hard gate whenever a trajectory and the teacher model are
    available; it is skipped (never a false positive) when they are not.
    """
    model = submission / "teacher_model.pb"
    if not model.is_file():
        return []
    for row in history:
        relative = Path(str(row.get("trajectory", "")))
        if relative.is_absolute() or ".." in relative.parts:
            continue
        trajectory_path = submission / relative
        if not trajectory_path.is_file():
            continue
        try:
            frame = list(Trajectory(str(trajectory_path)))[0]
            stored = frame.get_forces()
        except Exception:
            return []  # cannot derive from an unreadable frame; do not false-positive
        try:
            calculator = _dp_calculator(model)
        except Exception:
            return []
        probe = frame.copy()
        probe.calc = calculator
        dp = probe.get_forces()
        symbols = np.asarray(frame.get_chemical_symbols())
        charges = np.asarray([EXPECTED_CHARGES.get(symbol, 0.0) for symbol in symbols])
        try:
            ez = float(row.get("Ez_V_A"))
        except (TypeError, ValueError):
            return []
        expected = charges[:, None] * np.array([0.0, 0.0, ez])[None, :]
        deviation = float(np.max(np.abs(stored - dp - expected)))
        if deviation > FIELD_TOLERANCE_EV_A:
            return [f"field protocol not trajectory-derived for {relative}: max |stored-DP-qE|={deviation:.3e}"]
        return []
    return []


def verify(submission: Path, expected_profile: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    result_path = submission / "result.json"
    if not result_path.is_file():
        return {"valid": False, "errors": ["missing result.json"]}
    try:
        claimed = json.loads(result_path.read_text())
    except (OSError, ValueError) as exc:
        return {"valid": False, "errors": [f"invalid result.json: {exc}"]}

    profile = claimed.get("profile")
    if profile not in ("smoke", "paper") or (expected_profile and profile != expected_profile):
        errors.append("profile mismatch")
    if claimed.get("formal_result") is not (profile == "paper"):
        errors.append("formal_result does not match profile")
    if claimed.get("inputs", {}).get("structure_sha256") != EXPECTED_STRUCTURE:
        errors.append("structure identity mismatch")
    if claimed.get("inputs", {}).get("model_sha256") != EXPECTED_MODEL:
        errors.append("model identity mismatch")
    if claimed.get("field_charges_e") != EXPECTED_CHARGES:
        errors.append("effective-charge protocol mismatch")
    if claimed.get("start") != START:
        errors.append("search start mismatch")

    history = claimed.get("history", [])
    recomputed: list[dict[str, Any]] = []
    per_iteration: dict[int, int] = {}
    for history_index, row in enumerate(history):
        iteration = row.get("iteration")
        per_iteration[iteration] = per_iteration.get(iteration, 0) + 1
        if per_iteration[iteration] > TWO_JOB_CAP:
            errors.append(f"iteration {iteration} exceeds two-job cap")
        ez, temperature = row.get("Ez_V_A"), row.get("temperature_K")
        if not isinstance(ez, (int, float)) or not BOUNDS["Ez_V_A"][0] <= ez <= BOUNDS["Ez_V_A"][1]:
            errors.append("field outside search domain")
        if not isinstance(temperature, (int, float)) or not BOUNDS["temperature_K"][0] <= temperature <= BOUNDS["temperature_K"][1]:
            errors.append("temperature outside search domain")
        if iteration != 1 and "preceding measurements" not in row.get("decision_basis", ""):
            errors.append(f"iteration {iteration} lacks measured feedback")
        relative = Path(str(row.get("trajectory", "")))
        if relative.is_absolute() or ".." in relative.parts:
            errors.append("unsafe trajectory path")
            continue
        trajectory = submission / relative
        if not trajectory.is_file() or sha256(trajectory) != row.get("trajectory_sha256"):
            errors.append(f"trajectory evidence mismatch for {relative}")
            continue
        frames = list(Trajectory(str(trajectory)))
        if len(frames) < int(row.get("steps", 0)) // FRAME_INTERVAL_STEPS + 1:
            errors.append(f"trajectory too short for {relative}")
            continue
        metrics = analyze_frames(frames)
        recomputed.append({"history_index": history_index, "trajectory": str(relative), **metrics})
        for key in ("n_sites", "n_flipped", "slope_ps_per_site", "max_abs_displacement_A"):
            if not equivalent(row.get(key), metrics.get(key), 1e-6):
                errors.append(f"{key} is not trajectory-derived for {relative}")

    # Chronology is enforced for both profiles: an evidence-adaptive search must
    # run one measured round at a time, commit each decision before its round's
    # trajectories, and chain every decision digest to all preceding measured jobs.
    # The path is a deterministic function of the measured history: replay the adaptive
    # policy (round cap, two jobs per round, early-stop rule) so variable-length paths
    # are accepted, then require every declared round's (Ez, T) jobs to equal the policy's
    # proposal given the preceding measured rows — a preset schedule is rejected.
    policy = policy_for(profile)
    replayed = expected_round_jobs(history, policy)
    expected_rounds = len(replayed)
    expected_jobs = sum(len(jobs) for jobs in replayed.values())
    rounds = sorted(per_iteration)
    if rounds != list(range(1, expected_rounds + 1)):
        errors.append(f"search rounds are not contiguous 1..{expected_rounds}")
    if len(history) != expected_jobs or len(per_iteration) != expected_rounds:
        errors.append(f"profile must contain the {expected_rounds}-iteration, {expected_jobs}-job search")
    if any(count != TWO_JOB_CAP for count in per_iteration.values()):
        errors.append("every round must contain exactly two jobs")

    declared_by_round: dict[int, list[tuple[int, tuple[float, int]]]] = {}
    for row in history:
        iteration = int(row.get("iteration", 0))
        job_in_iteration = int(row.get("job_in_iteration", 0))
        declared_by_round.setdefault(iteration, []).append(
            (job_in_iteration, (float(row.get("Ez_V_A")), int(row.get("temperature_K")))))
    for round_index, jobs in replayed.items():
        declared = [point for _, point in sorted(declared_by_round.get(round_index, []), key=lambda pair: pair[0])]
        if declared != jobs:
            errors.append(f"round {round_index} does not match the adaptive policy")

    for row in history:
        iteration = int(row.get("iteration", 0))
        if iteration <= 1:
            continue
        preceding = [r for r in history if int(r.get("iteration", 0)) < iteration]
        if row.get("decision_input_sha256") != feedback_sha256(preceding):
            errors.append(f"iteration {iteration} decision input digest mismatch")
        if row.get("preceding_job_count") != len(preceding):
            errors.append(f"iteration {iteration} preceding_job_count mismatch")
        made = _parse_iso(row.get("decision_made_at"))
        if made is None:
            errors.append(f"iteration {iteration} missing a parseable decision_made_at")
        else:
            for prior in preceding:
                done = _parse_iso(prior.get("completed_at"))
                if done is not None and made < done:
                    errors.append(f"iteration {iteration} decision predates completed iteration {prior.get('iteration')} job")
            own_done = _parse_iso(row.get("completed_at"))
            if own_done is not None and own_done < made:
                errors.append(f"iteration {iteration} job completed before its round decision")

    if profile == "paper":
        best = claimed.get("best", {})
        if not best.get("sequential_propagation") or (best.get("slope_ps_per_site") or 0) <= MIN_SEQUENTIAL_SLOPE:
            errors.append("paper run did not demonstrate sequential propagation")
        if expected_profile == "paper":
            best_ez = best.get("Ez_V_A")
            if best_ez is None or not (PAPER_EZ_BAND[0] <= float(best_ez) <= PAPER_EZ_BAND[1]):
                errors.append(f"best Ez outside literature band: {best_ez}")
            best_t = best.get("temperature_K")
            if best_t is None or not (PAPER_TEMPERATURE_BAND[0] <= float(best_t) <= PAPER_TEMPERATURE_BAND[1]):
                errors.append(f"best T outside literature band: {best_t}")

    # Derive the electric-field protocol from actual calculator behavior and raw
    # trajectories: the forces stored in a trajectory frame must equal the DP
    # forces of the pinned teacher model plus q_i * E for the claimed field.
    field_errors = _derive_field_errors(submission, history)
    errors.extend(field_errors)

    # The declared best row must be a measured row whose metrics match its
    # recomputed trajectory, and (when any recomputed slope is finite) must be
    # the recomputed maximum. This is enforced unconditionally: a forged best
    # row is rejected even when every recomputed slope is None (smoke fixtures
    # flip nothing), because the declared best must then also declare None.
    best = claimed.get("best", {})
    best_history_index: int | None = None
    for idx, row in enumerate(history):
        if (row.get("Ez_V_A"), row.get("temperature_K")) == (best.get("Ez_V_A"), best.get("temperature_K")):
            best_history_index = idx
            break
    if best_history_index is None:
        errors.append("declared best row is not a measured history row")
    else:
        measured = history[best_history_index]
        # Search parameters (Ez/T) are compared against the measured row; the
        # slope is compared against the trajectory-derived metrics because only
        # the slope is a recomputed claim.
        for key in ("Ez_V_A", "temperature_K"):
            if not equivalent(best.get(key), measured.get(key), 1e-6):
                errors.append(f"declared best row does not match the recomputed best ({key})")
        for metrics in recomputed:
            if metrics.get("history_index") == best_history_index:
                if not equivalent(best.get("slope_ps_per_site"), metrics.get("slope_ps_per_site"), 1e-6):
                    errors.append("declared best row does not match the recomputed best (slope_ps_per_site)")
                break
    # The declared best must be the recomputed maximum among *valid in-band* rows:
    # a row whose Cu flipping is sparse (< 30 %) is a paper-defined gray-X non-event
    # and is excluded from best selection, and among valid rows the in-band (Ez/T)
    # rows take priority (mirroring solution/search_policy.select_best).
    finite_slopes = [m for m in recomputed if m.get("slope_ps_per_site") is not None
                     and _flip_ratio(m) >= _MIN_VALID_FLIP_RATIO]
    if finite_slopes:
        in_band = [m for m in finite_slopes if _in_band(history[m["history_index"]], policy)]
        pool = in_band if in_band else finite_slopes
        top = max(pool, key=lambda m: m["slope_ps_per_site"])
        row = history[top["history_index"]]
        for key, value in (("Ez_V_A", row["Ez_V_A"]), ("temperature_K", row["temperature_K"]),
                           ("slope_ps_per_site", row["slope_ps_per_site"])):
            if not equivalent(best.get(key), value, 1e-6):
                errors.append(f"declared best row does not match the recomputed best ({key})")

    best_path = submission / "best_trajectory.traj"
    if not best_path.is_file() or sha256(best_path) != claimed.get("best_trajectory_sha256"):
        errors.append("best trajectory evidence mismatch")
    for name in ("search_history.csv", "domino_analysis.csv", "search_results.png"):
        if not (submission / name).is_file():
            errors.append(f"missing {name}")
    return {"valid": not errors, "errors": errors, "profile": profile, "recomputed": recomputed}
