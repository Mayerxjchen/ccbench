#!/usr/bin/env python3
"""Validate one No-Skill/With-Skill pilot pair for the pairing machinery.

A pilot pair exists to validate the MACHINERY, not the science: two trials of
the same case whose ONLY difference is Skill availability, both recorded
through the same harness, with the independent verifier's result.json and a
sealed submission.  Scientific PASS/FAIL of the Agent is recorded but never a
gate here.

Usage::

    python scripts/ablation/validate_pilot_pair.py \\
        --no-skill   jobs/<tsA>__032-matclaw-cips-curie-temperature/run-record.json \\
        --with-skill jobs/<tsB>__032-matclaw-cips-curie-temperature/run-record.json \\
        [--release releases/ablation-ready-v0.json] \\
        [--protocol experiments/skill-ablation-v1/protocol.yaml]

Exit code 0 = the pairing machinery held.  Nonzero = a pairing defect (report
it as a pilot finding, never a science change).

Checks (Task 14 Step 5 pilot scope):
  1. both records load and validate against run-record.schema.json
  2. comparability_errors() == []   — every frozen identity field matches
  3. no-skill arm: skills_source == "none", skills_sha is null
  4. with-skill arm: skills_source non-none, skills_sha equals the protocol's
     frozen bundle digest
  5. independent verifier output: thread/verifier-logs/result.json exists and
     conforms to result.schema.json (VALID_RESULT or classified failure)
  6. submission seal: thread/sealed-submission exists and is non-empty
  7. lifecycle closes in a terminal phase (COMPLETED / PASS / FAIL)
  8. pilot is never formal: experiment_id contains "pilot"; the pair is
     excluded from formal summaries by experiment id
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ccbench.contracts.run_record import RunRecord  # noqa: E402
from ccbench.contracts.result import BenchmarkResult  # noqa: E402
from ccbench.experiments import ablation  # noqa: E402
from ccbench.experiments.comparison import compare_lock  # noqa: E402

TERMINAL_PHASES = frozenset({
    # harness lifecycle terminal phases (ccbench/core/harness.py)
    "COMPLETED", "FAILED_AGENT", "INVALID_INFRA",
    # legacy / per-case terminal phase names
    "PASS", "FAIL", "AGENT_FAILURE", "INFRA_INVALID", "TIMEOUT", "CANCELLED",
})
RESULT_SCHEMA = ROOT / "schemas" / "result.schema.json"


def _failures(record: RunRecord) -> list[str]:
    out: list[str] = []
    tdir = Path(record.thread_dir) if record.thread_dir else None

    # 5. independent verifier output
    result_json = tdir / "verifier-logs" / "result.json" if tdir else None
    if result_json is None or not result_json.is_file():
        out.append(f"missing independent verifier output: {result_json}")
    else:
        try:
            data = json.loads(result_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            out.append(f"result.json unreadable: {exc}")
        else:
            if data.get("result_class") not in ("VALID_RESULT", "AGENT_FAILURE",
                                                "INFRA_INVALID"):
                out.append(f"result.json result_class unexpected: {data.get('result_class')}")

    # 6. submission seal
    sealed = tdir / "sealed-submission" if tdir else None
    if sealed is None or not sealed.is_dir() or not any(sealed.iterdir()):
        out.append(f"sealed submission missing or empty: {sealed}")

    # 7. lifecycle closure
    events = record.lifecycle_events or []
    if not events:
        out.append("run-record has no lifecycle events")
    elif events[-1]["phase"] not in TERMINAL_PHASES:
        out.append(f"lifecycle does not close in a terminal phase: {events[-1]['phase']!r}")
    return out


def _lock_comparison_errors(no_skill_record: RunRecord, with_skill_record: RunRecord) -> list[str]:
    """Load both resolved run locks and enforce the treatment-only comparator.

    The skill-ablation treatment permits ONLY ``experiment.condition_id`` and
    ``agent.skill_bundle_digest`` to differ.  Any other difference between the
    two locks is a confound that invalidates the pairing machinery — fail
    before looking at scientific results.
    """
    out: list[str] = []
    locks: dict[str, dict] = {}
    for cond, record in (("no-skill", no_skill_record), ("with-skill", with_skill_record)):
        tdir = Path(record.thread_dir) if record.thread_dir else None
        lock_path = tdir / "resolved-run-lock.json" if tdir else None
        if lock_path is None or not lock_path.is_file():
            out.append(f"{cond} resolved-run-lock.json missing: {lock_path}")
            continue
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and "payload" in payload:
                payload = payload["payload"]
            locks[cond] = payload
        except (json.JSONDecodeError, OSError) as exc:
            out.append(f"{cond} resolved-run-lock.json unreadable: {exc}")

    if len(locks) == 2:
        diff = compare_lock(locks["no-skill"], locks["with-skill"], "skill_availability")
        if not diff.valid:
            out.append(
                "resolved locks differ beyond the declared skill-availability "
                f"treatment: {sorted(diff.unexpected_differences)}"
            )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-skill", required=True, type=Path, help="no-skill run-record.json")
    ap.add_argument("--with-skill", required=True, type=Path, help="with-skill run-record.json")
    ap.add_argument("--release", type=Path, default=ROOT / "releases" / "ablation-ready-v0.json")
    ap.add_argument("--protocol", type=Path,
                    default=ROOT / "experiments" / "skill-ablation-v1" / "protocol.yaml")
    args = ap.parse_args(argv)

    errors: list[str] = []
    records: dict[str, RunRecord] = {}
    for cond, path in (("no-skill", args.no_skill), ("with-skill", args.with_skill)):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rec = RunRecord.from_dict(payload)
            rec.validate()  # run-record.schema.json invariants
            records[cond] = rec
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{cond} run-record invalid: {type(exc).__name__}: {exc}")

    if records.get("no-skill") and records.get("with-skill"):
        no_skill = records["no-skill"]
        with_skill = records["with-skill"]

        # 2. pair comparability: legacy run-record field check
        errors += ablation.comparability_errors(no_skill.__dict__, with_skill.__dict__)

        # 2b. treatment-only lock comparator: fail on any unexpected difference
        errors += _lock_comparison_errors(no_skill, with_skill)

        # 3 / 4. arm identities vs protocol
        protocol = {}
        if args.protocol.is_file():
            import yaml
            protocol = yaml.safe_load(args.protocol.read_text(encoding="utf-8"))
        if no_skill.skills_source != "none" or no_skill.skills_sha is not None:
            errors.append("no-skill arm must have skills_source 'none' and null skills_sha")
        conds = {c["id"]: c for c in (protocol.get("conditions") or [])}
        frozen_sha = (conds.get("with-skill") or {}).get("skills_sha")
        if not (with_skill.skills_source and with_skill.skills_source != "none"
                and with_skill.skills_sha):
            errors.append("with-skill arm must carry skills_source and skills_sha")
        elif frozen_sha and with_skill.skills_sha != frozen_sha:
            errors.append(
                f"with-skill skills_sha {with_skill.skills_sha!r} != protocol frozen "
                f"{frozen_sha!r}"
            )

        # 8. pilot never formal
        for cond, rec in (("no-skill", no_skill), ("with-skill", with_skill)):
            if not ablation.is_pilot_experiment(rec.experiment_id):
                errors.append(f"{cond} experiment_id {rec.experiment_id!r} is not a pilot")

        for cond, rec in (("no-skill", no_skill), ("with-skill", with_skill)):
            errors += [f"{cond}: {e}" for e in _failures(rec)]

    no_skill = records.get("no-skill")
    with_skill = records.get("with-skill")
    rc = lambda r: getattr(getattr(r, "result", None), "result_class", None)
    rc_v = lambda r: getattr(rc(r), "value", rc(r))
    print(json.dumps({
        "comparable": not errors,
        "no_skill_run_id": no_skill.run_id if no_skill else None,
        "with_skill_run_id": with_skill.run_id if with_skill else None,
        "no_skill_result": rc_v(no_skill) if no_skill else None,
        "with_skill_result": rc_v(with_skill) if with_skill else None,
        "errors": errors,
    }, indent=2, sort_keys=True, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
