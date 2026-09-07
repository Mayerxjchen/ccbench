#!/usr/bin/env python3
"""10-gate read-only readiness audit for Cases 001–004.

Executable translation of the project-lead readiness directive: before any
pilot and before Task 14 freeze, each of 001–004 must be checked against ten
gates.  Every gate is *derived from evidence on disk* — never from a
hand-written ``VALIDATION.json``/``benchmark_valid.json`` boolean.

Gate engines:

- 001/002/003: ``matclaw_validation.derive_case`` (the fail-closed derive
  engine; it never reads the hand-written gate booleans).  The ten directive
  gates are mapped onto the derived G0–G12 gates plus the release manifest.
- 004: no derive engine exists yet (new HPC contract).  Its A–R acceptance
  board is SELF-REPORTED: the audit reports ``engine: "board"`` and treats
  the board booleans as evidence of the *current declared state*, never as a
  substitute for evidence-derived validity.

The ten directive gates:

  G1  constructed
  G2  benchmark_valid
  G3  expert oracle PASS
  G4  negative fixtures all FAIL as expected
  G5  alternative-valid fixture PASS
  G6  verifier independent and reproducible
  G7  instruction / public / thresholds frozen and hashed
  G8  compute runtime / resource profile / platform profile frozen
  G9  HPC reference run complete loop
  G10 no unresolved scientific or runtime blocker

Usage::

    python scripts/ablation/readiness_audit.py [--case all|001|002|003|004]

Writes nothing; prints a per-case JSON report.  Exit 0.  This is a
diagnostic, never a gate itself — the pilot/freeze decision is the lead's.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.matclaw_validation import (  # noqa: E402
    RUN_IDS,
    derive_case,
    load_policy,
    sha256_file,
    validate_run_manifest,
)

DIRECTIVE_CASES = ("001", "002", "003", "004")
RELEASE_FILE = ROOT / "releases" / "ablation-ready-v0.json"
EVIDENCE_ROOT = ROOT / "evidence" / "matclaw" / "formal"
POLICY = ROOT / "benchmark" / "sources" / "matclaw" / "acceptance.json"

CONSTRUCTION_GATES = ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G9", "G10", "G11")

CASE_DIRS = {
    "001": "001-matclaw-cips-active-distillation",
    "002": "002-matclaw-cips-curie-temperature",
    "003": "003-matclaw-cips-domain-wall-search",
    "004": "004-ai2kit-water64-end-to-end-potential",
}

GATE_LABELS = {
    1: "constructed",
    2: "benchmark_valid",
    3: "expert oracle PASS",
    4: "negative fixtures FAIL as expected",
    5: "alternative-valid PASS",
    6: "verifier independent & reproducible",
    7: "instruction/public/thresholds frozen+hashed",
    8: "compute runtime / resource / platform frozen",
    9: "HPC reference run complete loop",
    10: "no unresolved scientific or runtime blocker",
}

# Documented open items per case.  These are annotations for the report;
# whether they block a directive gate is judged from evidence, not assumed.
KNOWN_NOTES = {
    "001": "agent SSH transport tension (SSH socket vs gateway HTTP) is a "
           "pilot finding, not an oracle gate blocker",
    "002": "agent SSH transport tension (same as 001); pilot case selected",
    "003": "agent SSH transport tension (same as 001)",
    "004": "GPU-amd64 SIF still in build; runtime not validated on real HPC",
}


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def hash_dir(d: Path) -> str:
    """Deterministic dir digest, matching gen_release: ``rel:sha`` joined by NUL."""
    files = sorted(p for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    if not files:
        return _sha_text("__empty__")
    return _sha_text("\0".join(f"{p.relative_to(d)}:{sha256_file(p)}" for p in files))


# ------------------------------------------------------------- release checks

def _load_release() -> dict[str, Any]:
    return json.loads(RELEASE_FILE.read_text(encoding="utf-8"))


def _component_for(release: dict[str, Any], key: str, case_dir: str) -> dict | None:
    for entry in release["components"].get(key, []):
        if entry.get("case_id") == case_dir or entry.get("path", "").startswith(case_dir):
            return entry
    return None


MAINTAINER_CASE_DIRS = {
    "001": "001",
    "002": "002",
    "003": "003",
    "004": "004",
}


def _get_case_dir(case_id: str) -> Path:
    target_name = CASE_DIRS[case_id]
    p = ROOT / "cases" / target_name
    return p if p.is_dir() else ROOT / target_name


def _get_maintainer_dir(case_id: str) -> Path:
    num = MAINTAINER_CASE_DIRS.get(case_id, case_id)
    return ROOT / "maintainer" / "cases" / num


def _release_freeze_gates(release: dict[str, Any], case_id: str,
                          case_dir: Path) -> dict[str, dict]:
    HISTORICAL_CASE_DIRS = {
        "001": "001-matclaw-cips-active-distillation",
        "002": "002-matclaw-cips-curie-temperature",
        "003": "003-matclaw-cips-domain-wall-search",
        "004": "004-ai2kit-water64-end-to-end-potential",
    }
    dir_name = HISTORICAL_CASE_DIRS.get(case_id, CASE_DIRS.get(case_id, ""))
    out: dict[str, dict] = {}
    m_dir = _get_maintainer_dir(case_id)

    case_entry = _component_for(release, "cases", dir_name)
    checks: list[tuple[str, str | None, Path | None]] = []
    if case_entry:
        for key, rels in (
            ("task_toml_sha256", ("task.toml", "case.toml")),
            ("instruction_sha256", ("instruction.md", "task.md")),
        ):
            disk = None
            for rel in rels:
                if (case_dir / rel).is_file():
                    disk = sha256_file(case_dir / rel)
                    break
            checks.append((key, case_entry.get(key), disk))
        public_digest = case_entry.get("public_digest")
        pub_path = case_dir / "public" if (case_dir / "public").is_dir() else case_dir / "input"
        disk_pub = hash_dir(pub_path) if pub_path.is_dir() else None
        checks.append(("public_digest", public_digest, disk_pub))
    out["G7"] = {
        "passed": bool(case_entry) and all(
            pinned is not None and disk == pinned for _, pinned, disk in checks
        ),
        "evidence": f"release cases entry {dir_name}",
        "detail": "; ".join(
            f"{k} disk={disk or 'missing'} pinned={pinned or 'missing'}"
            for k, pinned, disk in checks
        ),
    }

    prof_checks = []
    for key, name in (
        ("resource_profiles", "profiles/resource.yaml"),
        ("platform_profiles", "profiles/platform.yaml"),
        ("compute_runtimes", "reference/compute-runtime.lock.json"),
    ):
        entry = _component_for(release, key, dir_name)
        path = m_dir / name if (m_dir / name).is_file() else case_dir / name
        disk = sha256_file(path) if path.is_file() else None
        pinned = entry.get("sha256") if entry else None
        prof_checks.append((key, pinned, disk))
    out["G8"] = {
        "passed": all(pinned is not None and disk == pinned for _, pinned, disk in prof_checks),
        "evidence": "release resource/platform/runtime entries",
        "detail": "; ".join(
            f"{k} disk={disk or 'missing'} pinned={pinned or 'missing'}"
            for k, pinned, disk in prof_checks
        ),
    }
    return out


# ------------------------------------------------------------------ derive path

def _derive_gates(case_id: str, release: dict[str, Any]) -> dict[str, dict]:
    case_dir = _get_case_dir(case_id)
    report = derive_case(case_dir, EVIDENCE_ROOT, POLICY)
    gates = report["gates"]
    reasons = list(report["reasons"])
    policy = load_policy(POLICY)

    construction = [g for g in CONSTRUCTION_GATES if not gates.get(g)]
    valid_manifests = {}
    for run_id in RUN_IDS:
        mp = EVIDENCE_ROOT / case_id / run_id / "manifest.json"
        if mp.is_file():
            result = validate_run_manifest(mp, case_id, policy)
            if result["eligible"]:
                data = json.loads(mp.read_text(encoding="utf-8"))
                valid_manifests[run_id] = data

    out: dict[str, dict] = {}

    out["G1"] = {
        "passed": not construction,
        "evidence": "derive construction gates G0-G6,G9-G11",
        "detail": f"state={report['state']}; failing={construction or 'none'}",
    }
    out["G2"] = {
        "passed": bool(report["benchmark_valid"]),
        "evidence": "derive benchmark_valid (2 agreeing formal runs + science)",
        "detail": f"state={report['state']}; reasons={reasons}",
    }
    out["G3"] = {
        "passed": bool(gates.get("G8")) and bool(gates.get("G9")),
        "evidence": "derive G8 science within bounds + G9 oracle PASS",
        "detail": f"G8={gates.get('G8')} G9={gates.get('G9')}",
    }
    out["G4"] = {
        "passed": bool(gates.get("G10")),
        "evidence": "derive G10 negative fixtures FAIL as expected",
        "detail": f"G10={gates.get('G10')}",
    }
    out["G5"] = {
        "passed": bool(gates.get("G11")),
        "evidence": "derive G11 alternative-valid PASS",
        "detail": f"G11={gates.get('G11')}",
    }
    verifier_entry = _component_for(release, "verifiers", CASE_DIRS[case_id])
    out["G6"] = {
        "passed": bool(gates.get("G0")) and bool(gates.get("G6")) and bool(verifier_entry),
        "evidence": "derive G0 public isolation + G6 smoke + release verifier digest",
        "detail": f"G0={gates.get('G0')} G6={gates.get('G6')} verifier_pinned={bool(verifier_entry)}",
    }
    freeze = _release_freeze_gates(release, case_id, case_dir)
    out["G7"] = freeze["G7"]
    out["G8"] = freeze["G8"]
    out["G9"] = {
        "passed": len(valid_manifests) == 2 and bool(gates.get("G7")) and bool(gates.get("G12")),
        "evidence": "derive G7 regenerated reference + G12 reproducibility + 2 eligible runs",
        "detail": f"eligible_runs={len(valid_manifests)} G7={gates.get('G7')} "
                  f"G12={gates.get('G12')}",
    }
    out["G10"] = {
        "passed": not reasons,
        "evidence": "derive reasons (no unresolved blocker)",
        "detail": f"reasons={reasons or 'none'}",
    }
    return out, report


# ------------------------------------------------------------------ board path

def _board_gates(case_id: str, release: dict[str, Any]) -> dict[str, dict]:
    case_dir = _get_case_dir(case_id)
    m_dir = _get_maintainer_dir(case_id)
    board_path = m_dir / "benchmark_valid.json"
    if not board_path.is_file():
        board_path = case_dir / "benchmark_valid.json"
    board = json.loads(board_path.read_text(encoding="utf-8"))
    bg = board.get("gates", {})

    t_dir = ROOT / "tests" / "cases" / MAINTAINER_CASE_DIRS.get(case_id, case_id)
    def _has_component(name: str) -> bool:
        if name in ("task.toml", "case.toml"):
            return (case_dir / "task.toml").is_file() or (case_dir / "case.toml").is_file()
        if name in ("instruction.md", "task.md"):
            return (case_dir / "instruction.md").is_file() or (case_dir / "task.md").is_file()
        if name in ("public", "input"):
            return (case_dir / "public").is_dir() or (case_dir / "input").is_dir()
        if name == "Dockerfile":
            return (case_dir / "Dockerfile").is_file() or (ROOT / "runtimes" / "recipes" / "ai2kit-controller" / "Dockerfile").is_file()
        if name == "tests":
            return t_dir.is_dir() or (case_dir / "tests").is_dir() or (case_dir / "verifier").is_dir()
        if name in ("reference", "solution", "profiles"):
            return (m_dir / name).is_dir() or (case_dir / name).is_dir()
        return (case_dir / name).exists()

    required = ("task.toml", "instruction.md", "Dockerfile", "public", "tests",
                "reference", "solution", "profiles")
    missing = [r for r in required if not _has_component(r)]

    out: dict[str, dict] = {}

    out["G1"] = {
        "passed": not missing and bool(bg.get("B_isolation")) and bool(bg.get("D_prompt_fidelity")),
        "evidence": "board B_isolation + D_prompt_fidelity + dir completeness",
        "detail": f"missing={missing or 'none'} B={bg.get('B_isolation')} D={bg.get('D_prompt_fidelity')}",
    }
    out["G2"] = {
        "passed": bool(board.get("benchmark_valid")),
        "evidence": "board benchmark_valid (SELF-REPORTED)",
        "detail": f"status={board.get('status')}",
    }
    oracles = [k for k in ("E_geopt_oracle", "F_aimd_oracle", "G_active_learning_oracle")
               if not bg.get(k)]
    out["G3"] = {
        "passed": not oracles,
        "evidence": "board E/F/G oracle gates",
        "detail": f"failing={oracles or 'none'}",
    }
    out["G4"] = {
        "passed": bool(bg.get("M_negative_fixtures")),
        "evidence": "board M_negative_fixtures",
        "detail": f"M={bg.get('M_negative_fixtures')}",
    }
    out["G5"] = {
        "passed": bool(bg.get("N_alternative_valid")),
        "evidence": "board N_alternative_valid",
        "detail": f"N={bg.get('N_alternative_valid')}",
    }
    verifier_entry = _component_for(release, "verifiers", CASE_DIRS[case_id])
    out["G6"] = {
        "passed": bool(bg.get("B_isolation")) and bool(bg.get("K_hidden_verifier"))
                  and bool(verifier_entry),
        "evidence": "board B_isolation + K_hidden_verifier + release verifier digest",
        "detail": (f"B={bg.get('B_isolation')} K={bg.get('K_hidden_verifier')} "
                   f"verifier_pinned={bool(verifier_entry)}"),
    }
    freeze = _release_freeze_gates(release, case_id, case_dir)
    out["G7"] = freeze["G7"]
    out["G8"] = freeze["G8"]
    out["G9"] = {
        "passed": bool(bg.get("P_reproducibility")) and bool(bg.get("H_expert_validation")),
        "evidence": "board P_reproducibility + H_expert_validation (no HPC evidence dir)",
        "detail": f"P={bg.get('P_reproducibility')} H={bg.get('H_expert_validation')}",
    }
    blockers = [k for k in ("C_runtime", "O_eval_reward_chain", "R_final_freeze")
                if not bg.get(k)]
    out["G10"] = {
        "passed": not blockers,
        "evidence": "board C_runtime + O_eval_reward_chain + R_final_freeze",
        "detail": f"blocking={blockers or 'none'}",
    }
    return out, board


# ----------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="all",
                    choices=["all", *DIRECTIVE_CASES])
    args = ap.parse_args(argv)

    cases = DIRECTIVE_CASES if args.case == "all" else (args.case,)
    release = _load_release()
    reports: list[dict[str, Any]] = []

    for case_id in cases:
        if case_id in ("001", "002", "003"):
            gates, source = _derive_gates(case_id, release)
            engine = "derive"
            reasons = source.get("reasons", [])
        else:
            gates, source = _board_gates(case_id, release)
            engine = "board"
            reasons = [f"{k}={v}" for k, v in source.get("gates", {}).items() if not v]

        report = {
            "case_id": case_id,
            "engine": engine,
            "gates": {str(n): {"label": GATE_LABELS[n], **gates[f"G{n}"]} for n in range(1, 11)},
            "pilot_eligible": all(gates[f"G{n}"]["passed"] for n in range(1, 11)),
            "benchmark_valid": gates["G2"]["passed"],
            "notes": [KNOWN_NOTES[case_id]],
        }
        reports.append(report)

    print(json.dumps(reports, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
