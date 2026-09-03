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
    """The frozen dispatcher-ai2kit-runtime-lock/v2 lock carries the identity
    the driver renders: capability-sealed image_name (ai2kit-runtime-v1),
    software.ai2_kit 1.1.0 matching the 034 lock, and the two runtime fields
    the verifier's _derive_ai2kit binds.  The v2 lock records the R2
    derived-runtime lineage: the runtime IS the trusted CP2K SIF rootfs —
    unrecoverable original-image fields stay null, never fabricated."""
    lock = _load_ai2kit_lock()
    assert lock["schema"] == "dispatcher-ai2kit-runtime-lock/v2"
    assert lock["image_name"] == "ai2kit-runtime-v1"
    assert lock["capability"] == "ai2kit"
    assert lock["mode"] == "derived-runtime-reuse"
    assert lock["software"]["ai2_kit"] == "1.1.0"
    assert "sif_path_remote" in lock["runtime"]
    assert lock["runtime"]["sif_sha256"] == (
        "05f708b1b03d949af095a770c00ca7930fea293b161a2383d71ea99e5cfef5dd"
    )
    # derived lineage: parent is the locked CP2K SIF, same source image id as
    # the 034 lock runtime_image.image_id (8a840aa2e477)
    lineage = lock["lineage"]
    assert lineage["parent_sif"]["sif_sha256"] == lock["runtime"]["sif_sha256"]
    assert lineage["imported_oci_image_digest"] is None
    assert lineage["dockerfile_sha256"] is None
    assert lineage["rootfs_manifest_sha256"]
    assert lineage["apptainer_version"] == "1.4.0"
    assert lineage["base_image_source_id"].startswith("sha256:8a840aa2e477")


def test_ai2kit_script_imports_version_config_and_probes():
    """The canary script asserts import + version==lock + a minimal config
    round-trip through /workspace, then the standard containment probe and
    marker, with rc taken from the python step so any assertion failure fails
    the job (a SUCCEEDED ai2kit job therefore proves all four facts).
    The version must come from importlib.metadata — ai2_kit 1.1.0 ships an
    empty __init__.py with no __version__ (Architecture Freeze §4)."""
    import qualify_hpc_dispatcher as q

    lock = _load_ai2kit_lock()
    script = q._ai2kit_script(lock, "BENCH_PROBE workspace_rw=pass")
    assert "import ai2_kit as _a" in script
    assert "_md.version('ai2_kit')" in script
    assert "importlib.metadata" in script
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


# -- C5: qualification completeness (cancel probe, orphan gate, --site) ------


def test_cancel_phase_is_wired_authorization_gated_and_merge_only():
    """--phase cancel exists as its own scope; the merge refuses without a
    base receipt, refuses to overwrite existing cancel evidence, and refuses
    an inconsistent base (same discipline as the runtime gates)."""
    text = QUALIFIER.read_text()
    assert '"cancel"' in text  # --phase choice
    assert 'args.phase == "cancel"' in text
    assert "--phase cancel requires --authorized" in text
    body = text[text.index("def cancel_phase"):text.index("def build_receipt")]
    assert "run --phase canary first" in body
    assert "cancel_gate evidence is already sealed" in body
    assert "refusing merge" in body


def test_cancel_probe_never_lands_in_canary_or_resume():
    """The canary and the durable resume path stay two-job / submit-free:
    _run_cancel_probe is called ONLY from cancel_phase, and RunSession.cancel
    appears nowhere in canary."""
    text = QUALIFIER.read_text()
    assert text.count("_run_cancel_probe(") == 2  # def + one call site
    call = text.index("_run_cancel_probe(profile", text.index("def cancel_phase"))
    assert text.index("def cancel_phase") < call < text.index("def build_receipt")
    canary_body = text[text.index("def canary"):text.index("def _cp2k_script")]
    assert "_run_cancel_probe" not in canary_body
    assert "session.cancel(" not in canary_body
    resume_body = text[text.index("def resume"):text.index("def main")]
    assert "_run_cancel_probe" not in resume_body
    assert "session.submit(" not in resume_body


def test_canary_refuses_to_overwrite_a_sealed_receipt():
    """Stale-evidence rule (retain, never overwrite): the live canary phase
    refuses to clobber an existing receipt — re-qualification needs a fresh
    --site dir."""
    text = QUALIFIER.read_text()
    assert "refusing to overwrite sealed receipt" in text
    main_body = text[text.index("def main"):]
    guard = main_body.index("args.phase == \"canary\" and RECEIPT_PATH.is_file()")
    run = main_body.index("evidence = canary(profile, lock)")
    assert guard < run


def test_orphan_sweep_wired_into_canary_and_resume(monkeypatch):
    """Both collector paths record a terminal scheduler sweep, and the sweep
    recomputes active_total from the per-run match (no trust in the count)."""
    import qualify_hpc_dispatcher as q

    text = QUALIFIER.read_text()
    assert text.count('evidence["orphan_check"] = _orphan_sweep(') == 2

    def fake_ssh(profile, command, timeout=120, *, retries=4):
        if command.startswith("squeue"):
            return "100\n200\n"
        if "show job 100" in command:
            return "JobId=100 WorkDir=/root/dispatcher-qual/run-a/job-0001"
        return "JobId=200 WorkDir=/elsewhere"

    monkeypatch.setattr(q, "_ssh", fake_ssh)
    result = q._orphan_sweep({"ssh": {}}, ["run-a", "run-b"])
    assert result["runs"] == {"run-a": "100", "run-b": None}
    assert result["active_total"] == 1
    assert "WorkDir" in result["method"]


def test_set_site_rebinds_evidence_paths_and_rejects_unsafe_names():
    """--site parameterizes where the receipt and per-run evidence land;
    traversal-ish names fail closed before any path is built."""
    import qualify_hpc_dispatcher as q

    orig = (q.CANARY_ROOT, q.RECEIPT_PATH)
    try:
        q.set_site("site-v3")
        assert q.CANARY_ROOT.name == "site-v3"
        assert q.RECEIPT_PATH == q.CANARY_ROOT / "receipt.json"
        assert q.CANARY_ROOT.is_relative_to(q.ROOT / "evidence")
        for bad in ("../escape", "a/b", "", ".", "..", "-hidden"):
            with pytest.raises(q.QualifyError, match="unsafe"):
                q.set_site(bad)
    finally:
        q.CANARY_ROOT, q.RECEIPT_PATH = orig


def test_preflight_refuses_multi_queue_partitions():
    """Full-GPU and MIG are separate profiles and separate qualifications
    (P4 scope); preflight rejects a comma list before any submission,
    matching SiteProfile.from_cluster_config."""
    text = QUALIFIER.read_text()
    body = text[text.index("def preflight"):text.index("def _build_site_profile")]
    assert "names multiple queues" in body
    # def + the two call sites: partition and cpu_partition
    assert body.count("_known_queue(") == 3
