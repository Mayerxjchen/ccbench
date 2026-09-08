from __future__ import annotations

import json
from pathlib import Path

import pytest

from ccbench.core.digests import sha256_file
from ccbench.mvp import (
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
    from ccbench.paths import ROOT

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
            "inputs": [{"path": "system.json", "sha256": sha256_file(bundle / "system.json")}],
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
        "inputs": [{"path": "system.json", "sha256": sha256_file(bundle / "system.json")}],
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
