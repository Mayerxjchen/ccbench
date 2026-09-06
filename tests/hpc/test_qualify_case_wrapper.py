"""Tests for the thin ``bench hpc qualify`` wrapper (scripts/qualification/
qualify_case.py, spec stage 3).  Pure-planning pieces are tested directly; the
CLI is exercised with --dry-run so no submission ever happens in tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.qualification.qualify_case as qc  # noqa: E402


def _caps() -> dict:
    return {name: "NOT_RUN" for name in (
        "dispatcher.cpu", "dispatcher.gpu", "dispatcher.cancel",
        "runtime.matclaw-gpu", "runtime.ai2kit", "runtime.cp2k",
    )}


def _pass(*names: str) -> dict:
    caps = _caps()
    for name in names:
        caps[name] = "PASS"
    return caps


# -- case / site resolution ---------------------------------------------------


def test_case_dir_for_shorthand_and_full_id():
    full = qc.case_dir_for("004-ai2kit-water64-end-to-end-potential")
    assert full.name == "004-ai2kit-water64-end-to-end-potential"
    shorthand = qc.case_dir_for("004")
    assert shorthand == full
    with pytest.raises(qc.QualifyPlanError):
        qc.case_dir_for("no-such-case-999")


def test_case_dir_for_fails_closed_when_not_unique():
    # Ids are NNN-name; "0" matches nothing. Whatever the prefix, the wrapper
    # fails closed instead of guessing a unique match.
    with pytest.raises(qc.QualifyPlanError):
        qc.case_dir_for("0")
    with pytest.raises(qc.QualifyPlanError):
        qc.case_dir_for("03")  # "03-" prefixes nothing → no candidates


def test_site_root_validates_site_name():
    assert qc.site_root("site-v1").name == "site-v1"
    with pytest.raises(qc.QualifyPlanError):
        qc.site_root("../escape")
    # '.' and '..' match the loose regex many sites once used; they are path
    # components, not evidence dirs (same hardening as the driver, C5)
    for bad in (".", "..", "-hidden", "a/b", ""):
        with pytest.raises(qc.QualifyPlanError):
            qc.site_root(bad)


# -- planning ----------------------------------------------------------------


def test_plan_034_fresh_site_runs_canary_then_runtime_gates():
    from dftworld_bench.experiments.release_builder import (  # noqa: PLC2701
        _case_qualification_requires,
    )
    requires = list(_case_qualification_requires(
        ROOT / "004-ai2kit-water64-end-to-end-potential"
    ))
    assert sorted(requires) == [
        "dispatcher.cpu", "dispatcher.gpu", "runtime.ai2kit", "runtime.cp2k",
    ]
    assert qc.plan_phases(requires, {}, receipt_present=False) == [
        "canary", "ai2kit", "cp2k",
    ]


def test_plan_all_pass_runs_nothing():
    requires = ["dispatcher.cpu", "runtime.ai2kit"]
    assert qc.plan_phases(requires, _pass(*requires), receipt_present=True) == []


def test_plan_fail_halts_without_rerun():
    caps = _pass("dispatcher.cpu", "dispatcher.gpu")
    caps["runtime.ai2kit"] = "FAIL"
    with pytest.raises(qc.QualifyPlanError, match="FAIL"):
        qc.plan_phases(["dispatcher.cpu", "runtime.ai2kit"], caps,
                       receipt_present=True)


def test_plan_unknown_capability_is_an_error():
    with pytest.raises(qc.QualifyPlanError, match="unknown"):
        qc.plan_phases(["dispatcher.cpu", "runtime.deepmd-jax"],
                       _caps(), receipt_present=True)


def test_plan_runtime_only_prepends_canary_without_receipt():
    # A runtime gate merges into an existing consistent receipt, so the
    # wrapper must establish it first even when no dispatcher cell is needed.
    assert qc.plan_phases(["runtime.cp2k"], {}, receipt_present=False) == [
        "canary", "cp2k",
    ]
    assert qc.plan_phases(
        ["runtime.cp2k"], _caps(), receipt_present=True
    ) == ["cp2k"]


def test_plan_matclaw_case_needs_canary_only():
    requires = ["dispatcher.gpu", "runtime.matclaw-gpu"]
    assert qc.plan_phases(requires, _caps(), receipt_present=False) == ["canary"]


def test_plan_dispatcher_cancel_is_a_merge_phase():
    """dispatcher.cancel rides its own --phase cancel (P4 step 7).  It merges
    into an existing receipt, so without one the plan establishes canary
    first; with one, cancel alone is planned."""
    assert qc.CAPABILITY_PHASE["dispatcher.cancel"] == "cancel"
    assert qc.plan_phases(["dispatcher.cancel"], {},
                          receipt_present=False) == ["canary", "cancel"]
    assert qc.plan_phases(["dispatcher.cancel"], _caps(),
                          receipt_present=True) == ["cancel"]
    assert qc.plan_phases(
        ["dispatcher.cancel"], _pass("dispatcher.cancel"), receipt_present=True
    ) == []


# -- driver argv construction -------------------------------------------------


def test_driver_argv_canary_pins_runtime_lock():
    argv = qc.driver_argv("canary", profile_path=Path("/p"), site_name="site-t",
                          runtime_lock="x.json", ai2kit_lock="a.json",
                          cp2k_lock="c.json", authorized=False)
    assert "--phase" in argv and argv[argv.index("--phase") + 1] == "canary"
    assert argv[argv.index("--runtime-lock") + 1].endswith("x.json")
    # every phase must land in the SAME site evidence dir the wrapper reads
    # from — the driver would otherwise default to site-v1 (C5 fix)
    assert argv[argv.index("--site") + 1] == "site-t"


def test_driver_argv_runtime_phases_require_explicit_authorization():
    for phase in ("ai2kit", "cp2k", "cancel"):
        with pytest.raises(qc.QualifyPlanError, match="--authorized"):
            qc.driver_argv(phase, profile_path=Path("/p"), site_name="site-t",
                           runtime_lock="r", ai2kit_lock="a", cp2k_lock="c",
                           authorized=False)
        argv = qc.driver_argv(phase, profile_path=Path("/p"), site_name="site-t",
                              runtime_lock="r", ai2kit_lock="a", cp2k_lock="c",
                              authorized=True)
        assert "--authorized" in argv
        assert argv[argv.index("--phase") + 1] == phase
    for phase in ("ai2kit", "cp2k"):
        argv = qc.driver_argv(phase, profile_path=Path("/p"), site_name="site-t",
                              runtime_lock="r", ai2kit_lock="a", cp2k_lock="c",
                              authorized=True)
        assert "--ai2kit-lock" in argv or "--cp2k-lock" in argv


def test_driver_argv_resume_carries_stamp_and_since():
    argv = qc.driver_argv("resume", profile_path=Path("/p"), site_name="site-t",
                          runtime_lock="r", ai2kit_lock="a", cp2k_lock="c",
                          authorized=False,
                          stamp="abc1", gpu_job_id="3512345", since="2026-09-01")
    assert "--stamp" in argv and argv[argv.index("--stamp") + 1] == "abc1"
    assert argv[argv.index("--gpu-job-id") + 1] == "3512345"
    assert argv[argv.index("--since") + 1] == "2026-09-01"


# -- state persistence + stamp detection --------------------------------------


def test_state_roundtrip_and_canary_stamp_detection(tmp_path: Path):
    qc.write_state(tmp_path, {"case": "034", "since": "2026-09-01"})
    assert qc.load_state(tmp_path)["since"] == "2026-09-01"
    (tmp_path / "run-gpu-nvidia-probe-containment-aa11").mkdir()
    assert qc._canary_stamp(tmp_path) == "aa11"
    (tmp_path / "run-gpu-nvidia-probe-containment-bb22").mkdir()
    assert qc._canary_stamp(tmp_path) == "bb22"  # newest wins


# -- CLI end-to-end (dry-run; never submits) ---------------------------------


class TestQualifyCliDryRun:
    def test_dry_run_004_plans_canary_ai2kit_cp2k(self, tmp_path: Path,
                                                   capsys):
        rc = qc.main(["--case", "004", "--site", "site-zz-" + tmp_path.name,
                      "--profile", "scripts/hpc/cluster_profile.toml",
                      "--dry-run", "--verbose"])
        out = capsys.readouterr().out
        assert rc == 0, out
        assert "['canary', 'ai2kit', 'cp2k']" in out
        assert "runtime.ai2kit" in out

    def test_dry_run_001_plans_canary_without_authorization(self, tmp_path: Path,
                                                            capsys):
        rc = qc.main(["--case", "001", "--site", "site-zz-" + tmp_path.name,
                      "--profile", "scripts/hpc/cluster_profile.toml",
                      "--dry-run"])
        out = capsys.readouterr().out
        assert rc == 0, out
        assert "['canary']" in out
        assert "--authorized" not in out

    def test_runtime_phases_require_authorized_even_outside_dry_run(
            self, tmp_path: Path, capsys, monkeypatch):
        import scripts.qualification.qualify_case as qc_mod

        monkeypatch.setattr(qc_mod, "subprocess", _FakeSubprocess_runs())
        rc = qc.main(["--case", "004", "--site", "site-zz-" + tmp_path.name,
                      "--profile", "scripts/hpc/cluster_profile.toml"])
        assert rc == 2  # authorized gate fires before any phase runs


class _FakeSubprocess_runs:
    """Never reached: the authorization gate must fire before any phase."""

    def run(self, argv):  # pragma: no cover — would indicate a gate bug
        raise AssertionError(f"must not run a phase: {argv}")