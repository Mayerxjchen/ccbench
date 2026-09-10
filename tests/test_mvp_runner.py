from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from bench.core.digests import sha256_file
from bench.mvp import (
    MvpError,
    check_bundle,
    export_case,
    freeze_submission,
    validate_compute_request,
    verify_sealed_submission,
)


def _case(root: Path) -> Path:
    case = root / "fixture"
    (case / "input").mkdir(parents=True)
    (case / "input" / "system.json").write_text('{"atoms": 3}\n')
    (case / "input" / "run_profiles.json").write_text('{}\n')
    (case / "task.md").write_text("Solve the public task.\n")
    (case / "case.toml").write_text(
        'schema_version = "1.2"\n'
        'case_version = "1.0.0"\n'
        '[execution]\nclass = "local_sandbox"\n'
        '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
        'legacy_submission_layout = false\n'
        'files = [{ destination = ".", source = "input/**", strip_prefix = "input" }]\n'
        '[task]\nname = "benchmark/fixture"\ndescription = "fixture"\n'
        '[verifier]\ntimeout_sec = 30\n'
        '[environment]\ncpus = 1\nmemory_mb = 1024\nstorage_mb = 1024\n'
        'gpus = 0\nallow_internet = false\n',
        encoding="utf-8",
    )
    return case


def test_export_is_public_only_and_has_operator_lock(tmp_path: Path) -> None:
    case = _case(tmp_path)
    (case / "solution").mkdir()
    (case / "solution" / "answer.txt").write_text("secret")
    bundle = tmp_path / "candidate-run"
    payload = export_case(case, bundle)
    assert payload["state"] == "CASE_DEV"
    assert (bundle / "instruction.md").is_file()
    assert (bundle / "system.json").is_file()
    assert (bundle / "CLAUDE.md").is_file()
    assert (bundle / "final").is_dir()
    assert not (bundle / "solution").exists()
    assert (tmp_path / "candidate-run.lock.json").is_file()


def test_publish_candidate_messages_is_outside_workspace_and_read_only(tmp_path: Path) -> None:
    from bench.pilot import _publish_candidate_messages

    run_dir = tmp_path / "run"
    thread_dir = run_dir / "threads" / run_dir.name
    thread_dir.mkdir(parents=True)
    source = thread_dir / "messages.jsonl"
    source.write_bytes(b'{"type":"tool_result"}\n')

    published = _publish_candidate_messages(run_dir, thread_dir)

    assert published == str(run_dir / "messages.jsonl")
    destination = run_dir / "messages.jsonl"
    assert destination.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o444
    assert destination.parent != (run_dir / "candidate")


def test_publish_candidate_messages_rejects_hardlink_and_symlink(tmp_path: Path) -> None:
    from bench.pilot import _publish_candidate_messages

    run_dir = tmp_path / "run"
    thread_dir = run_dir / "threads" / run_dir.name
    thread_dir.mkdir(parents=True)
    source = thread_dir / "messages.jsonl"
    source.write_text("diagnostic\n", encoding="utf-8")
    hardlink = thread_dir / "hardlink.jsonl"
    hardlink.hardlink_to(source)
    assert _publish_candidate_messages(run_dir, hardlink.parent) is None
    source.unlink()
    source.symlink_to(run_dir / "secret.txt")
    assert _publish_candidate_messages(run_dir, thread_dir) is None


def test_check_uses_operator_lock_not_candidate_manifest(tmp_path: Path) -> None:
    bundle = tmp_path / "candidate-run"
    export_case(_case(tmp_path), bundle)
    (bundle / "system.json").write_text("tampered")
    candidate_manifest = json.loads((bundle / "mvp-run.json").read_text())
    candidate_manifest["immutable_files"]["system.json"]["sha256"] = "0" * 64
    (bundle / "mvp-run.json").write_text(json.dumps(candidate_manifest))
    with pytest.raises(MvpError, match="drift"):
        check_bundle(bundle)


def test_check_rejects_unexpected_root_entry(tmp_path: Path) -> None:
    bundle = tmp_path / "candidate-run"
    export_case(_case(tmp_path), bundle)
    (bundle / "stolen-reference.json").write_text("no")
    with pytest.raises(MvpError, match="unexpected"):
        check_bundle(bundle)


def test_freeze_seals_only_final_and_rejects_symlink(tmp_path: Path) -> None:
    bundle = tmp_path / "candidate-run"
    export_case(_case(tmp_path), bundle)
    (bundle / "work" / "scratch.txt").write_text("scratch")
    (bundle / "final" / "answer.json").write_text('{"ok": true}\n')
    sealed = tmp_path / "sealed"
    seal = freeze_submission(bundle, sealed)
    assert seal.file_count == 1
    assert (sealed / "answer.json").is_file()
    assert not (sealed / "scratch.txt").exists()

    second = tmp_path / "candidate-run-2"
    export_case(_case(tmp_path / "other"), second)
    (second / "final" / "escape").symlink_to(second / "system.json")
    with pytest.raises(Exception, match="symlink"):
        freeze_submission(second, tmp_path / "sealed-2")


def test_export_refuses_repository_destination() -> None:
    from bench.paths import ROOT

    with pytest.raises(MvpError, match="outside"):
        export_case(ROOT / "cases" / "001-matclaw-cips-active-distillation", ROOT / "bad-run")


def test_compute_request_is_resource_and_digest_checked(tmp_path: Path) -> None:
    bundle = tmp_path / "candidate-run"
    export_case(_case(tmp_path), bundle)
    request = bundle / "compute-requests" / "cpu.json"
    request.write_text(
        json.dumps({
            "schema_version": "1.0",
            "compute_class": "cpu",
            "command": ["python", "run.py"],
            "resources": {
                "nodes": 1, "ntasks": 4, "cpus_per_task": 2,
                "memory_gb_per_node": 32, "walltime_min": 30, "gpus": 0,
            },
            "inputs": [{"path": "system.json", "sha256": sha256_file(bundle / "system.json"), "size": (bundle / "system.json").stat().st_size}],
            "outputs": ["compute-results/result.json"],
        }),
        encoding="utf-8",
    )
    assert validate_compute_request(bundle, request)["compute_class"] == "cpu"
    payload = json.loads(request.read_text())
    payload["resources"]["gpus"] = 1
    request.write_text(json.dumps(payload))
    with pytest.raises(MvpError, match="gpus=0"):
        validate_compute_request(bundle, request)


def test_compute_request_uses_per_node_memory_and_gpu_min_memory(tmp_path: Path) -> None:
    bundle = tmp_path / "candidate-run"
    export_case(_case(tmp_path), bundle)
    write = lambda name, body: (bundle / "compute-requests" / name).write_text(
        json.dumps(body), encoding="utf-8"
    )

    # GPU requests are per-node memory + a minimum per-GPU memory floor.
    gpu = {
        "schema_version": "1.0",
        "compute_class": "gpu",
        "command": ["dp", "train", "work/input.json"],
        "resources": {
            "nodes": 1, "ntasks": 1, "cpus_per_task": 8,
            "memory_gb_per_node": 64, "gpus": 1,
            "minimum_gpu_memory_gb": 24, "walltime_min": 120,
        },
        "inputs": [{"path": "system.json", "sha256": sha256_file(bundle / "system.json"), "size": (bundle / "system.json").stat().st_size}],
        "outputs": ["compute-results/model.ckpt"],
        "validation": {"success_markers": ["finished training"], "reject_if": ["nan"]},
    }
    write("gpu.json", gpu)
    assert validate_compute_request(bundle, bundle / "compute-requests" / "gpu.json")["compute_class"] == "gpu"

    # Flat memory_gb (pre-per-node schema) is rejected as an unknown key.
    cpu_flat = json.loads(json.dumps(gpu))
    cpu_flat.update(compute_class="cpu")
    cpu_flat["resources"] = {"nodes": 1, "ntasks": 1, "cpus_per_task": 2,
                             "memory_gb": 32, "gpus": 0, "walltime_min": 30}
    write("cpu-flat.json", cpu_flat)
    with pytest.raises(MvpError, match="unknown keys"):
        validate_compute_request(bundle, bundle / "compute-requests" / "cpu-flat.json")

    # A CPU request may not carry a GPU memory floor.
    cpu_bad = json.loads(json.dumps(gpu))
    cpu_bad.update(compute_class="cpu", command=["python", "run.py"])
    cpu_bad["resources"] = {**cpu_bad["resources"], "gpus": 0}
    write("cpu-bad.json", cpu_bad)
    with pytest.raises(MvpError, match="minimum_gpu_memory_gb"):
        validate_compute_request(bundle, bundle / "compute-requests" / "cpu-bad.json")

    # A GPU request must name a per-GPU memory floor.
    gpu_bad = json.loads(json.dumps(gpu))
    del gpu_bad["resources"]["minimum_gpu_memory_gb"]
    write("gpu-bad.json", gpu_bad)
    with pytest.raises(MvpError, match="minimum_gpu_memory_gb"):
        validate_compute_request(bundle, bundle / "compute-requests" / "gpu-bad.json")


def test_sealed_submission_detects_post_freeze_drift(tmp_path: Path) -> None:
    bundle = tmp_path / "candidate-run"
    export_case(_case(tmp_path), bundle)
    (bundle / "final" / "answer.txt").write_text("good")
    sealed = tmp_path / "sealed"
    freeze_submission(bundle, sealed)
    verify_sealed_submission(sealed)
    (sealed / "answer.txt").write_text("changed")
    with pytest.raises(MvpError, match="drift"):
        verify_sealed_submission(sealed)


def test_sealed_manifest_symlink_is_rejected_before_json_load(tmp_path: Path) -> None:
    bundle = tmp_path / "candidate-run"
    export_case(_case(tmp_path), bundle)
    (bundle / "final" / "answer.txt").write_text("good")
    sealed = tmp_path / "sealed"
    freeze_submission(bundle, sealed)
    original = sealed / "manifest.json"
    original.unlink()
    target = tmp_path / "outside-manifest.json"
    target.write_text(json.dumps({"files": []}), encoding="utf-8")
    original.symlink_to(target)
    with pytest.raises(MvpError, match="manifest"):
        verify_sealed_submission(sealed)


def test_container_pilot_uses_docker_runner_and_keeps_secrets_out(tmp_path: Path, monkeypatch) -> None:
    """Pilot delegates to the container adapter; no host Claude subprocess exists."""
    import bench.pilot as pilot
    from bench.pilot import start

    monkeypatch.setenv("BENCH_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("BENCH_API_KEY", "trusted-secret")
    monkeypatch.setenv("BENCH_MODEL", "deepseek-v4-pro[1M]")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-cross")
    run_dir = tmp_path / "pilot"
    captured = {}

    def fake_container(**kwargs):
        captured.update(kwargs)
        final = run_dir / "candidate/final"
        final.mkdir(exist_ok=True)
        (final / "fake.json").write_text("ok")
        return {"image_digest": "sha256:" + "a" * 64, "engine": "claude-code"}

    monkeypatch.setattr(pilot, "_run_container_candidate", fake_container)
    state = start(str(_case(tmp_path)), run_dir=run_dir)
    assert state["runner"] == "container_claude_code"
    assert state["model_id"] == "deepseek-v4-pro[1M]"
    assert state["candidate_image_digest"].startswith("sha256:")
    assert captured["resume_session"] is False
    assert "trusted-secret" not in json.dumps(state)
    assert "must-not-cross" not in json.dumps(state)


def test_formal_host_credential_accepts_auth_token_alias(monkeypatch) -> None:
    from bench.pilot import _resolve_host_credential

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "host-only-token")
    value, names = _resolve_host_credential("ANTHROPIC_API_KEY")
    assert value == "host-only-token"
    assert names == ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def test_container_pilot_resume_uses_same_session(tmp_path: Path, monkeypatch) -> None:
    from bench.pilot import import_results, resume, start

    run_dir = tmp_path / "pilot"
    events = []
    def fake_container(**kwargs):
        events.append(kwargs)
        if len(events) == 1:
            (run_dir / "candidate/compute-requests/request.json").write_text(json.dumps({
                "schema_version": "1.0", "compute_class": "cpu", "command": ["python", "run.py"],
                "resources": {"nodes": 1, "ntasks": 1, "cpus_per_task": 1, "memory_gb_per_node": 1, "walltime_min": 1, "gpus": 0},
                "inputs": [{"path": "run_profiles.json"}], "outputs": ["compute-results/result.dat"],
            }), encoding="utf-8")
        else:
            (run_dir / "candidate/final/resumed.txt").write_text("ok")
        return {"image_digest": "sha256:" + "b" * 64, "engine": "claude-code"}
    monkeypatch.setattr("bench.pilot._run_container_candidate", fake_container)
    first = start(str(_case(tmp_path)), run_dir=run_dir)
    assert first["state"] == "COMPUTE_REQUIRED"
    operator = tmp_path / "operator" / "compute-results"
    operator.mkdir(parents=True)
    (operator / "result.dat").write_text("external result", encoding="utf-8")
    import_results(run_dir, operator.parent)
    resumed = resume(run_dir)
    assert resumed["session_id"] == first["session_id"]
    records = [json.loads(line) for line in (run_dir / "transcript.jsonl").read_text().splitlines()]
    assert events[-1]["resume_session"] is True
    assert events[-1]["session_id"] == first["session_id"]
    assert [record for record in records if record.get("event") == "start"][-1]["session_id"] == first["session_id"]


def test_container_pilot_resume_reuses_secret_free_config_snapshot(tmp_path: Path, monkeypatch) -> None:
    from bench.pilot import import_results, resume, start

    monkeypatch.setenv("BENCH_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("BENCH_API_KEY", "host-only-secret")
    monkeypatch.setenv("BENCH_MODEL", "deepseek-v4-pro[1M]")
    monkeypatch.setenv("BENCH_CANDIDATE_IMAGE", "user/candidate:v2")
    monkeypatch.setenv("BENCH_SIDECAR_IMAGE", "user/gateway:v1")
    config_path = tmp_path / "candidate.toml"
    config_path.write_text(
        '[model]\nrequested_id="deepseek-v4-pro[1M]"\ncli_model="claude-sonnet-4-6"\n'
        'endpoint_env="BENCH_BASE_URL"\ncredential_env="BENCH_API_KEY"\n'
        '[candidate]\nimage="user/candidate:v2"\nsidecar_image="user/gateway:v1"\n'
        '[compute]\nbackend="ikkem"\nprofile="operator/ikkem.json"\n',
        encoding="utf-8",
    )
    run_dir = tmp_path / "pilot-config"
    events: list[dict] = []

    def fake_container(**kwargs):
        events.append(kwargs)
        if len(events) == 1:
            (run_dir / "candidate/compute-requests/request.json").write_text(json.dumps({
                "schema_version": "1.0", "compute_class": "cpu", "command": ["python", "run.py"],
                "resources": {"nodes": 1, "ntasks": 1, "cpus_per_task": 1, "memory_gb_per_node": 1, "walltime_min": 1, "gpus": 0},
                "inputs": [{"path": "run_profiles.json"}], "outputs": ["compute-results/result.dat"],
            }), encoding="utf-8")
        else:
            (run_dir / "candidate/final/resumed.txt").write_text("ok", encoding="utf-8")
        return {"image_digest": "sha256:" + "c" * 64, "engine": "claude-code"}

    monkeypatch.setattr("bench.pilot._run_container_candidate", fake_container)
    first = start(str(_case(tmp_path)), run_dir=run_dir, config_path=config_path)
    assert first["compute_backend"] == "ikkem"
    assert first["candidate_config"]["model"]["cli_model"] == "claude-sonnet-4-6"
    assert "host-only-secret" not in json.dumps(first)

    operator = tmp_path / "operator-config" / "compute-results"
    operator.mkdir(parents=True)
    (operator / "result.dat").write_text("external result", encoding="utf-8")
    import_results(run_dir, operator.parent)
    resumed = resume(run_dir)
    assert resumed["candidate_config_digest"] == first["candidate_config_digest"]
    assert events[-1]["candidate_config"]["compute"]["backend"] == "ikkem"


def test_pilot_passes_config_limits_and_persists_effective_snapshot(tmp_path: Path, monkeypatch) -> None:
    from bench.pilot import start

    config_path = tmp_path / "limits.toml"
    config_path.write_text(
        "[limits]\nmax_turns=7\nmax_total_tokens=1234\n"
        "agent_timeout_sec=42\nmax_budget_usd=0.0\n", encoding="utf-8"
    )
    run_dir = tmp_path / "limits-run"
    captured = {}

    def fake_container(**kwargs):
        captured.update(kwargs)
        return {"image_digest": "sha256:" + "d" * 64, "engine": "claude-code",
                "model_gateway": {"turn_count": 1, "tokens_used": 9}}

    monkeypatch.setattr("bench.pilot._run_container_candidate", fake_container)
    state = start(str(_case(tmp_path)), run_dir=run_dir, config_path=config_path)
    assert captured["effective_limits"] == {
        "max_turns": 7, "max_total_tokens": 1234,
        "agent_timeout_sec": 42.0, "max_budget_usd": 0.0,
    }
    snapshot = json.loads((run_dir / "effective-limits.json").read_text())
    assert snapshot["limits"] == captured["effective_limits"]
    assert snapshot["digest"] == state["effective_limits_digest"]
    assert state["effective_limits_source"] == "config"
    assert state["budget_usage"]["model_turns"] == 1


def test_resume_budget_is_recomputed_from_completed_transcript(tmp_path: Path) -> None:
    from bench.pilot import MvpError, _previous_budget, _recompute_completed_budget

    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"event": "start"}) + "\n" +
        json.dumps({"event": "candidate_complete", "phase_usage": {
            "model_turns": 3, "total_tokens": 90,
            "candidate_phase_walltime_sec": 1.25,
        }}) + "\n", encoding="utf-8"
    )
    recomputed = _recompute_completed_budget(transcript)
    state = {"budget_usage": recomputed}
    assert _previous_budget(state) == recomputed
    tampered = {"budget_usage": {**recomputed, "model_turns": 0, "total_tokens": 0,
                                  "candidate_phase_walltime_sec": 0.0}}
    assert _previous_budget(tampered) != recomputed
    transcript.write_text(json.dumps({"event": "start"}) + "\n", encoding="utf-8")
    with pytest.raises(MvpError, match="complete resumable"):
        _recompute_completed_budget(transcript)

    transcript.write_text(
        json.dumps({"event": "start"}) + "\n" +
        json.dumps({"event": "start"}) + "\n" +
        json.dumps({"event": "candidate_complete", "phase_usage": {
            "model_turns": 1, "total_tokens": 2,
            "candidate_phase_walltime_sec": 0.1,
        }}) + "\n", encoding="utf-8"
    )
    with pytest.raises(MvpError, match="incomplete prior phase"):
        _recompute_completed_budget(transcript)


def test_resume_rejects_noncanonical_transcript_pointer(tmp_path: Path, monkeypatch) -> None:
    import bench.pilot as pilot
    from bench.pilot import start, resume

    run_dir = tmp_path / "pointer-run"
    def fake_container(**kwargs):
        (run_dir / "candidate/compute-requests/request.json").write_text(json.dumps({
            "schema_version": "1.0", "compute_class": "cpu", "command": ["python", "run.py"],
            "resources": {"nodes": 1, "ntasks": 1, "cpus_per_task": 1, "memory_gb_per_node": 1, "walltime_min": 1, "gpus": 0},
            "inputs": [{"path": "run_profiles.json"}], "outputs": ["compute-results/result.dat"],
        }), encoding="utf-8")
        return {"image_digest": "sha256:" + "e" * 64, "engine": "claude-code",
                "model_gateway": {"turn_count": 1, "tokens_used": 2}}
    monkeypatch.setattr(pilot, "_run_container_candidate", fake_container)
    first = start(str(_case(tmp_path)), run_dir=run_dir)
    state_path = run_dir / "run-state.json"
    state = json.loads(state_path.read_text())
    state["transcript"] = str(tmp_path / "other-transcript.jsonl")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(MvpError, match="canonical run-scoped transcript"):
        resume(run_dir)


def test_signal_failure_preserves_safe_adapter_telemetry(tmp_path: Path, monkeypatch) -> None:
    import bench.pilot as pilot

    monkeypatch.setenv("BENCH_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("BENCH_API_KEY", "host-only-secret")

    class InterruptingAdapter:
        def __init__(self, **kwargs):
            self.container_id = "candidate-id"
            self.sidecar_cid = None
            self.internal_net = None
            self.resource_run_uid = None
            self.thread_dir = str(tmp_path / "threads")
            self._closed = False

        async def prepare(self):
            return None

        async def start(self, prompt):
            raise KeyboardInterrupt()

        async def close(self):
            self.container_id = None
            self._closed = True

        def collect_logs(self):
            return {
                "thread_dir": self.thread_dir,
                "candidate_exit_code": 130,
                "model_gateway": {"turn_count": 4, "tokens_used": 321,
                                   "budget_exceeded_reason": None},
            }

    monkeypatch.setattr("bench.agents.ClaudeCodeAdapter", InterruptingAdapter)
    with pytest.raises(KeyboardInterrupt) as caught:
        pilot._run_container_candidate(
            profile={"runner": "container_claude_code", "image": "candidate:test",
                     "model": "model-test"},
            case_dir=tmp_path, workspace=tmp_path / "workspace",
            run_dir=tmp_path / "run", prompt="safe prompt", session_id="session",
            resume_session=False, resource_run_uid=None,
            effective_limits={"max_turns": 8, "max_total_tokens": 1000,
                              "agent_timeout_sec": 10, "max_budget_usd": 0.0},
        )
    safe_logs = getattr(caught.value, "safe_logs")
    assert safe_logs["candidate_exit_code"] == 130
    assert safe_logs["model_gateway"]["tokens_used"] == 321
