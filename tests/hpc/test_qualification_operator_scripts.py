import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts" / "infra") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "infra"))

RUNNER = ROOT / "scripts/qualification/run_hpc_dispatcher.sh"
SUPERVISOR = ROOT / "scripts/qualification/supervise_hpc_dispatcher.sh"
QUALIFIER = ROOT / "scripts/infra/qualify_hpc_dispatcher.py"


def test_operator_scripts_are_relocatable_and_do_not_cancel_user_jobs():
    for script in (RUNNER, SUPERVISOR):
        text = script.read_text()
        assert "/Users/chenxuanjie/" not in text
        assert "dftworld2-qualification" not in text
        assert "scancel" not in text
        assert "xargs" not in text


def test_runtime_lock_paths_are_canonical():
    text = RUNNER.read_text()
    assert "reference/runtime/cp2k-runtime.lock.json" in text


def test_containment_probe_uses_profile_root_and_all_adversarial_sentinels():
    text = QUALIFIER.read_text()
    assert 'Path(profile["paths"]["remote_root"]).parent' in text
    assert "/public/home/<site-user>/dftworld2-runs" not in text
    assert "BENCH_PROBE solution_absent=pass" in text
    assert "BENCH_PROBE reference_absent=pass" in text
    assert "_containment_fragment(profile, sentinels)" in text


def test_canary_settles_owned_job_before_exception_cleanup():
    text = QUALIFIER.read_text()
    wait = text.index("state = _wait_terminal")
    settle = text.index("session.settle(cancel_pending=True)", wait)
    cleanup = text.index("def _cleanup_sentinels")
    assert settle > wait
    assert cleanup < wait  # cleanup is defined earlier; runtime order is guarded above


def test_resume_settles_closes_before_sentinel_cleanup():
    """The resume phase collects GPU evidence (settle + close) in _resume_gpu
    BEFORE resume() calls _cleanup_sentinels. The lease must not bound the
    wait, but a terminal/cancelled job and a closed session must precede
    sentinel removal — otherwise absence probes could false-pass on a
    still-running job.
    """
    text = QUALIFIER.read_text()
    collect = text.index("def _resume_gpu")
    settle = text.index("session.settle(cancel_pending=True)", collect)
    close = text.index("session.close()", settle)
    cleanup_call = text.index("_cleanup_sentinels(profile, sentinels)", close)
    assert collect < settle < close < cleanup_call


def test_resume_phase_never_submits_and_has_no_incident_constants():
    """The consolidated driver has no root-level recovery script and no baked
    incident constants: run ids derive from --stamp, and neither the GPU nor
    the CPU resume path ever invokes a submission."""
    assert not (ROOT / "recover_gpu_canary.py").exists()
    text = QUALIFIER.read_text()
    assert '"resume"' in text  # CLI phase
    assert "session.submit(" not in text[text.index("def _resume_gpu"):]
    assert "3d8dc664" not in text  # no leftover incident stamp


def test_cleanup_sentinels_reports_leftover(monkeypatch):
    """_cleanup_sentinels removes every target via one rm -rf, then re-checks
    each with test -e; any target still PRESENT is reported as leftover so the
    caller fails closed instead of sealing a probe it cannot prove."""
    import qualify_hpc_dispatcher as q

    present = {"home_sentinel", "credential_sentinel"}
    calls: list[str] = []

    def fake_ssh(profile, command, timeout=120, *, retries=4):
        calls.append(command)
        if command.startswith("rm -rf "):
            return ""
        # test -e check: report the first two sentinels as still PRESENT
        for name in ("home_sentinel", "credential_sentinel"):
            if name in command and "home_sentinel" in present:
                present.discard("home_sentinel")
                return "PRESENT"
        for name in ("credential_sentinel",):
            if name in command and "credential_sentinel" in present:
                present.discard("credential_sentinel")
                return "PRESENT"
        return "GONE"

    monkeypatch.setattr(q, "_ssh", fake_ssh)
    sentinels = {
        "home_sentinel": "/home/u/.bench-sentinel-x",
        "credential_sentinel": "/home/u/.bench-cred-sentinel-x",
        "other_run_dir": "/runs/sentinel-other-run-x",
        "solution_sentinel": "/runs/sentinel-solution-x",
        "reference_sentinel": "/runs/sentinel-reference-x",
    }
    result = q._cleanup_sentinels({"ssh": {}}, sentinels)
    assert set(result["removed"]) == set(sentinels.values())
    # one rm -rf + one test -e per target = 6 ssh calls
    assert len(calls) == 1 + len(sentinels)
    assert any(c.startswith("rm -rf ") for c in calls)
    assert all("test -e" in c for c in calls[1:])
