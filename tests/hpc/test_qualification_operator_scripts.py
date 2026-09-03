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


def _load_ai2kit_lock() -> dict:
    import json as _json

    return _json.loads(
        (ROOT / "reference/runtime/ai2kit-runtime.lock.json").read_text(
            encoding="utf-8"
        )
    )


def test_ai2kit_phase_is_wired_and_authorization_gated():
    """--phase ai2kit exists, defaults to the frozen controller lock, and is
    gated behind --authorized exactly like cp2k (no ai2kit canary scope is on
    file)."""
    text = QUALIFIER.read_text()
    assert '"ai2kit"' in text  # --phase choice
    assert "--ai2kit-lock" in text
    assert "reference/runtime/ai2kit-runtime.lock.json" in text
    assert 'args.phase == "ai2kit"' in text
    # the empty-digest guard stays BEFORE the receipt/preflight/network step
    body = text[text.index("def ai2kit_phase"):]
    guard = body.index("has no runtime.sif_sha256 yet")
    merge = body.index("receipt to merge into")
    assert guard < merge


def test_ai2kit_lock_file_is_valid_and_self_consistent():
    """The frozen dispatcher-ai2kit-runtime-lock/v1 lock carries the identity
    the driver renders: image_name (job-schema-safe), software.ai2_kit 1.1.0
    matching the 034 lock / registry, and the two runtime fields the
    verifier's _derive_ai2kit binds."""
    lock = _load_ai2kit_lock()
    assert lock["schema"] == "dispatcher-ai2kit-runtime-lock/v1"
    assert lock["image_name"] == "dftworld-base-ai2kit-0.1.0-cpu-controller"
    assert lock["software"]["ai2_kit"] == "1.1.0"
    assert "sif_path_remote" in lock["runtime"]
    assert lock["runtime"]["sif_sha256"] == ""
    # docker image id prefix matches the 034 lock runtime_image.image_id
    # (8a840aa2e477) — the controller is the same image lineage.
    assert lock["source"]["docker_image_id"].startswith(
        "sha256:8a840aa2e477"
    )


def test_ai2kit_script_imports_version_config_and_probes():
    """The canary script asserts import + version==lock + a minimal config
    round-trip through /workspace, then the standard containment probe and
    marker, with rc taken from the python step so any assertion failure fails
    the job (a SUCCEEDED ai2kit job therefore proves all four facts)."""
    import qualify_hpc_dispatcher as q

    lock = _load_ai2kit_lock()
    script = q._ai2kit_script(lock, "BENCH_PROBE workspace_rw=pass")
    assert "import ai2kit as _a" in script
    assert 'assert version == "1.1.0"' in script
    assert "AI2KIT_CONFIG_LOAD=pass" in script
    assert "qual-ai2kit-min-config.json" in script
    assert "BENCH_PROBE workspace_rw=pass" in script
    assert q.MARKER_CONTAINMENT in script
    assert "rc=$?" in script
    assert "exit $rc" in script


def test_ai2kit_version_from_stdout_parses_marker():
    import qualify_hpc_dispatcher as q

    assert q._ai2kit_version_from_stdout("AI2KIT_VERSION=1.1.0\n") == "1.1.0"
    # not fooled by a partial/tampered line
    assert q._ai2kit_version_from_stdout("AI2KIT_VERSIONx=1.1.0\n") == ""
    assert q._ai2kit_version_from_stdout("no marker\n") == ""
    assert (
        q._ai2kit_version_from_stdout(
            "x AI2KIT_VERSION=2.0.0\nAI2KIT_VERSION=1.1.0\n"
        )
        == "1.1.0"
    )


def test_ai2kit_phase_refuses_empty_sif_digest_before_any_io():
    """An unfilled controller SIF digest fails cleanly in-process: no receipt
    load, no profile parse, no ssh — runtime.ai2kit stays NOT_RUN until the
    gateway-run digest lands in the lock."""
    import qualify_hpc_dispatcher as q
    import pytest

    lock = dict(_load_ai2kit_lock())
    lock["runtime"] = {"sif_path_remote": "/runs/x.sif", "sif_sha256": ""}
    with pytest.raises(q.QualifyError) as exc:
        q.ai2kit_phase({}, lock, ai2kit_lock_relpath="x", profile_path=Path())
    assert "sif_sha256" in str(exc.value)
