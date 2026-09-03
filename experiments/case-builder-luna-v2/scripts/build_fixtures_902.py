#!/usr/bin/env python3
"""Builder helper: generate the case-adapted executable fixtures for
902-mvp-cips-curie-temperature (published_model_execution chain).

Deterministic: fixed content, real bytes copied from the sealed public model,
sha256s computed from staged files. Run with the repository interpreter:
    .venv/bin/python scripts/build_fixtures_902.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

CASE = Path("/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v2/generated/902-mvp-cips-curie-temperature")
SRC_MODEL = CASE / "public" / "teacher_model.pb"
CASE_ID = "902-mvp-cips-curie-temperature"
MODEL_SHA = "a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d"
FORGED_SHA = "0" * 64

CURVE_CSV = """temperature_K,mean_abs_eta_A,sem_abs_eta_A,saved_frames,used_frames,sign_changes_eq
100,1.2541,0.0014,1501,751,0
150,1.2446,0.0041,1501,751,0
200,1.1394,0.0156,1501,751,0
250,0.9531,0.0330,1501,751,0
275,0.2003,0.0263,2501,1251,10
300,0.1565,0.0291,2501,1251,33
325,0.1228,0.0200,2501,1251,35
350,0.1105,0.0199,2501,1251,37
375,0.1103,0.0153,2501,1251,35
400,0.1523,0.0328,2501,1251,35
450,0.1224,0.0516,1501,751,25
500,0.2240,0.0626,1501,751,23
600,0.0991,0.0134,1501,751,34
"""

REPORT_MD = """# Curie temperature estimate (fixture submission, structural smoke)

Method: plateau half-height between the low-T (~1.15 A) and high-T (~0.15 A)
Q(T) plateaus, cross-checked with a piecewise-linear breakpoint.
Estimate: tc_estimate_K = 262.0, uncertainty 12 K (grid spacing + fit spread).
Convergence: sign-change counts rise from 0 (<=250 K) to 10-37 (275-600 K);
refined 100 ps runs near the transition. See curves file for raw data.
"""

SWEEP_LOG = "md_T350K: NVT Langevin, dt=2.0 fs, 50000 steps, saved every 20; finished exit 0\n"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def model_copy(fixture: Path) -> Path:
    dest = fixture / "artifacts" / "model" / "teacher_used.pb"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SRC_MODEL, dest)
    assert sha(dest) == MODEL_SHA
    return dest


def dump_manifest(fixture: Path, manifest: dict) -> None:
    (fixture / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def base_receipts(backend: str) -> list[dict]:
    return [
        {"backend": backend, "exit_status": "completed", "job_id": "sweep-100-250K", "walltime_sec": 3400.0},
        {"backend": backend, "exit_status": "0", "job_id": "refine-275-400K", "walltime_sec": 7100.0},
        {"backend": backend, "exit_status": "completed", "job_id": "sweep-450-600K", "walltime_sec": 3300.0},
    ]


def structural() -> None:
    f = CASE / "tests" / "fixtures" / "positive" / "structural-minimal"
    for stale in (f / "artifacts" / "dataset", f / "artifacts" / "labels"):
        if stale.exists():
            shutil.rmtree(stale)
    model = model_copy(f)
    curve = write(f / "artifacts" / "results" / "order_parameter.csv", CURVE_CSV)
    report = write(f / "artifacts" / "results" / "tc_report.md", REPORT_MD)
    log = write(f / "artifacts" / "logs" / "sweep-T350K.log", SWEEP_LOG)
    dump_manifest(f, {
        "schema_version": 1,
        "case_id": CASE_ID,
        "provenance_sources": [
            "public/teacher_model.pb sha256 " + MODEL_SHA + " (frozen published DeePMD model, AIS Square record 109)",
            "public/CuInP2S6.cif sha256 b9e3b0c4470274d5e3e1ce19e7c8834bda323e50483eef9791b1e744bbd629de",
            "MatClaw arXiv:2604.02688v3 task-2 protocol",
        ],
        "artifacts": [
            {"path": model.relative_to(f).as_posix(), "role": "teacher_model", "sha256": sha(model),
             "source": "public/teacher_model.pb (sealed published model, executed unmodified)"},
            {"path": curve.relative_to(f).as_posix(), "role": "dataset", "sha256": sha(curve),
             "source": "NVT Langevin sweep executed with public/teacher_model.pb"},
            {"path": report.relative_to(f).as_posix(), "role": "report", "sha256": sha(report)},
            {"path": log.relative_to(f).as_posix(), "role": "log", "sha256": sha(log)},
        ],
        "lineage": [
            {"round": 1, "action": "build 6x6x1 supercell from public/CuInP2S6.cif; run 100-600 K NVT sweep with the frozen model",
             "dataset": curve.relative_to(f).as_posix(), "model": model.relative_to(f).as_posix()},
            {"round": 2, "action": "refine near-transition temperatures; compute Q(T); fit Tc half-height + breakpoint",
             "dataset": curve.relative_to(f).as_posix(), "model": model.relative_to(f).as_posix()},
        ],
        "runtime_receipts": base_receipts("slurm"),
        "metrics": [
            {"name": "tc_estimate_K", "value": 262.0, "unit": "K", "dataset": report.relative_to(f).as_posix()},
            {"name": "tc_uncertainty_K", "value": 12.0, "unit": "K", "dataset": report.relative_to(f).as_posix()},
        ],
    })


def alternative_valid() -> None:
    f = CASE / "tests" / "fixtures" / "alternative-valid" / "alt-hybrid-analysis"
    model = model_copy(f)
    curve = write(f / "artifacts" / "curves" / "Q_of_T.csv", CURVE_CSV)
    report = write(f / "artifacts" / "analysis" / "tc_sigmoid_fit.md",
                   "# Tc via sigmoid fit of Q(T)\n\nAlternative valid estimator: fitted logistic\ncentroid at 265.5 K, uncertainty 15 K.\n")
    log = write(f / "artifacts" / "logs" / "run.log", SWEEP_LOG)
    dump_manifest(f, {
        "schema_version": 1,
        "case_id": CASE_ID,
        "provenance_sources": [
            "public/teacher_model.pb sha256 " + MODEL_SHA,
            "public/CuInP2S6.cif (10-atom slab -> 360-atom 6x6x1 supercell)",
        ],
        "artifacts": [
            {"path": model.relative_to(f).as_posix(), "role": "teacher_model", "sha256": sha(model),
             "source": "public/teacher_model.pb, used unmodified"},
            {"path": curve.relative_to(f).as_posix(), "role": "dataset", "sha256": sha(curve),
             "source": "coarse sweep 90-620 K + refinement 260-410 K"},
            {"path": report.relative_to(f).as_posix(), "role": "report", "sha256": sha(report)},
            {"path": log.relative_to(f).as_posix(), "role": "log", "sha256": sha(log)},
        ],
        "lineage": [
            {"round": 1, "action": "single-pass coarse sweep with the frozen model",
             "dataset": curve.relative_to(f).as_posix(), "model": model.relative_to(f).as_posix()},
            {"round": 2, "action": "sigmoid fit for Tc (alternative valid estimator)",
             "dataset": curve.relative_to(f).as_posix(), "model": model.relative_to(f).as_posix()},
        ],
        "runtime_receipts": base_receipts("custom-runner"),
        "metrics": [
            {"name": "tc_estimate_K", "value": 265.5, "unit": "K", "dataset": report.relative_to(f).as_posix()},
        ],
    })


def forged_manifest() -> None:
    f = CASE / "tests" / "fixtures" / "negative" / "forged-manifest"
    for stale in (f / "artifacts" / "dataset",):
        if stale.exists():
            shutil.rmtree(stale)
    model = model_copy(f)
    dump_manifest(f, {
        "schema_version": 1,
        "case_id": CASE_ID,
        "provenance_sources": ["public/teacher_model.pb"],
        "artifacts": [
            {"path": model.relative_to(f).as_posix(), "role": "teacher_model", "sha256": FORGED_SHA,
             "source": "public/teacher_model.pb"},
        ],
        "lineage": [
            {"round": 1, "action": "sweep + analysis",
             "dataset": model.relative_to(f).as_posix(), "model": model.relative_to(f).as_posix()},
        ],
        "runtime_receipts": [{"backend": "slurm", "exit_status": "0", "job_id": "j1"}],
        "metrics": [
            {"name": "tc_estimate_K", "value": 262.0, "unit": "K", "dataset": "manifest-placeholder"},
        ],
    })
    # NOTE: manifest hash intentionally all-zero while staged bytes are the
    # real sealed model -> V2 hash-mismatch failure path.


def missing_model() -> None:
    f = CASE / "tests" / "fixtures" / "negative" / "missing-model"
    if (f / "artifacts" / "dataset").exists():
        shutil.rmtree(f / "artifacts" / "dataset")
    curve = write(f / "artifacts" / "results" / "order_parameter.csv", CURVE_CSV)
    dump_manifest(f, {
        "schema_version": 1,
        "case_id": CASE_ID,
        "provenance_sources": ["public/teacher_model.pb (claimed)"],
        "artifacts": [
            {"path": curve.relative_to(f).as_posix(), "role": "dataset", "sha256": sha(curve),
             "source": "MD sweep (model not shipped)"},
        ],
        "lineage": [
            {"round": 1, "action": "sweep + analysis",
             "dataset": curve.relative_to(f).as_posix(), "model": curve.relative_to(f).as_posix()},
        ],
        "runtime_receipts": [{"backend": "slurm", "exit_status": "0", "job_id": "j1"}],
        "metrics": [
            {"name": "tc_estimate_K", "value": 262.0, "unit": "K", "dataset": curve.relative_to(f).as_posix()},
        ],
    })


def broken_lineage() -> None:
    f = CASE / "tests" / "fixtures" / "negative" / "broken-lineage"
    for stale in (f / "artifacts" / "dataset",):
        if stale.exists():
            shutil.rmtree(stale)
    model = model_copy(f)
    curve = write(f / "artifacts" / "results" / "order_parameter.csv", CURVE_CSV)
    report = write(f / "artifacts" / "results" / "tc_report.md", REPORT_MD)
    dump_manifest(f, {
        "schema_version": 1,
        "case_id": CASE_ID,
        "provenance_sources": ["public/teacher_model.pb sha256 " + MODEL_SHA, "public/CuInP2S6.cif"],
        "artifacts": [
            {"path": model.relative_to(f).as_posix(), "role": "teacher_model", "sha256": sha(model),
             "source": "public/teacher_model.pb"},
            {"path": curve.relative_to(f).as_posix(), "role": "dataset", "sha256": sha(curve),
             "source": "NVT sweep with the frozen model"},
            {"path": report.relative_to(f).as_posix(), "role": "report", "sha256": sha(report)},
        ],
        "lineage": [
            {"round": 1, "action": "coarse sweep",
             "dataset": curve.relative_to(f).as_posix(), "model": model.relative_to(f).as_posix()},
            {"round": 3, "action": "analysis (round 2 missing -> non-contiguous lineage)",
             "dataset": curve.relative_to(f).as_posix(), "model": model.relative_to(f).as_posix()},
        ],
        "runtime_receipts": [{"backend": "slurm", "exit_status": "0", "job_id": "j1"}],
        "metrics": [
            {"name": "tc_estimate_K", "value": 262.0, "unit": "K", "dataset": report.relative_to(f).as_posix()},
        ],
    })


def empty() -> None:
    f = CASE / "tests" / "fixtures" / "negative" / "empty"
    f.mkdir(parents=True, exist_ok=True)
    (f / ".gitkeep").touch()


if __name__ == "__main__":
    structural()
    alternative_valid()
    forged_manifest()
    missing_model()
    broken_lineage()
    empty()
    print("fixtures generated")
