"""Fail-closed runtime-lock tests for the MatClaw GPU SIF (Cases 031-033).

Formal paper runs are gated on a qualified GPU-amd64 Apptainer SIF, cross-checked
against an embedded A100 qualification receipt. ``formal_eligible`` is a hand-set
convenience flag and is never trusted: a true value with a non-qualifying receipt
is itself invalid. Every rejection test starts from a fully valid synthetic
lock+receipt and mutates exactly the one field under test.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# ``uv run pytest`` here resolves to the base-conda pytest, which does not put
# the repo root on sys.path; existing scripts-importing tests need the root too.
# Bootstrap it so the prescribed command works standalone.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from scripts.matclaw_runtime_lock import (
    RuntimeLockError,
    load_runtime_lock,
    validate_qualification,
    validate_runtime_lock,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "matclaw_runtime_lock.py"

SIF_SHA = "f" * 64
QUAL_SHA = "0" * 64
CPU_IMAGE = "sha256:" + "c" * 64
GPU_IMAGE = "sha256:" + "d" * 64
ARCHIVE_SHA = "e" * 64
SOURCE_COMMIT = "a" * 40

SIF_PATH = (
    "/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/"
    "matclaw-cips-2.2.11-gpu-amd64.sif"
)
QUAL_PATH = (
    "/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/locked/qualify_gpu.py"
)


def valid_receipt() -> dict:
    """A fully valid A100 qualification receipt for the locked SIF."""
    return {
        "schema_version": 2,
        "qualified": True,
        "qualify_job": "3536737",
        "node": "<site-node-gpu5>",
        "gpu": "NVIDIA A100-SXM4-80GB",
        "gpu_visible": True,
        "gpu_name": "NVIDIA A100-SXM4-80GB",
        "sif_sha256": SIF_SHA,
        "architecture": "amd64",
        "uname_m": "x86_64",
        "qualification_sha256": QUAL_SHA,
        "versions": {
            "python": "3.11.9",
            "tensorflow": "2.16.2",
            "deepmd": "2.2.11",
            "ase": "3.13.0",
        },
        "energy_abs_diff_eV": 0.0,
        "max_force_component_abs_diff_eV_A": 0.0,
        "parity_ok": True,
        "md_steps": 100,
        "md_frames": 101,
        "md_finite": True,
        "frames_ok": True,
        "elapsed_s": 123.4,
        "seconds_per_step": 1.234,
    }


def valid_lock(receipt: dict | None = None) -> dict:
    """A fully valid GPU-amd64 runtime lock embedding a clean receipt."""
    return {
        "schema_version": 2,
        "case": "matclaw-cips-runtime",
        "runtime_type": "gpu",
        "os": "linux",
        "architecture": "amd64",
        "compute_uname_m": "x86_64",
        "source_commit": SOURCE_COMMIT,
        "cpu_image_id": CPU_IMAGE,
        "gpu_image_id": GPU_IMAGE,
        "oci_repo_digest": None,
        "docker_archive_sha256": ARCHIVE_SHA,
        "sif_path_remote": SIF_PATH,
        "sif_sha256": SIF_SHA,
        "qualification_path_remote": QUAL_PATH,
        "qualification_sha256": QUAL_SHA,
        "formal_eligible": True,
        "qualification": receipt if receipt is not None else valid_receipt(),
    }


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


# --- 1. accepting case -----------------------------------------------------


def test_valid_gpu_amd64_lock_and_receipt_are_accepted() -> None:
    lock = valid_lock()
    assert validate_runtime_lock(lock) == []
    assert validate_runtime_lock(lock, require_formal=True) == []
    assert validate_qualification(lock, valid_receipt()) == []


def test_non_formal_lock_is_valid_unless_formal_is_required() -> None:
    lock = valid_lock()
    lock["formal_eligible"] = False
    lock.pop("qualification")
    assert validate_runtime_lock(lock) == []
    assert validate_runtime_lock(lock, require_formal=True) != []


# --- 2. runtime type -------------------------------------------------------


def test_reject_cpu_runtime_type() -> None:
    lock = valid_lock()
    lock["runtime_type"] = "cpu"
    errors = validate_runtime_lock(lock)
    assert any("runtime_type" in error for error in errors)


# --- 3. architecture -------------------------------------------------------


def test_reject_arm64_architecture() -> None:
    lock = valid_lock()
    lock["architecture"] = "arm64"
    errors = validate_runtime_lock(lock)
    assert any("architecture" in error for error in errors)


# --- 4/10. SIF digest binding ----------------------------------------------


def test_reject_sif_sha256_mismatch_in_embedded_receipt() -> None:
    lock = valid_lock()
    lock["qualification"]["sif_sha256"] = "1" * 64
    errors = validate_runtime_lock(lock)
    assert any("different SIF" in error for error in errors)


def test_reject_receipt_for_a_different_sif() -> None:
    receipt = valid_receipt()
    receipt["sif_sha256"] = "2" * 64
    errors = validate_qualification(valid_lock(), receipt)
    assert any("different SIF" in error for error in errors)


# --- 5. qualification script digest ----------------------------------------


def test_reject_qualification_sha256_mismatch() -> None:
    receipt = valid_receipt()
    receipt["qualification_sha256"] = "3" * 64
    errors = validate_qualification(valid_lock(), receipt)
    assert any("qualification_sha256" in error for error in errors)


# --- 6. GPU identity -------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "fragment"),
    [
        ("gpu", "Tesla V100", "A100"),
        ("gpu_visible", False, "gpu_visible"),
    ],
)
def test_reject_non_a100_or_hidden_gpu(
    field: str, value: object, fragment: str
) -> None:
    receipt = valid_receipt()
    receipt[field] = value
    errors = validate_qualification(valid_lock(), receipt)
    assert any(fragment in error for error in errors)


# --- 7. parity threshold (strictly < 1e-6) ---------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("energy_abs_diff_eV", 1e-6),  # boundary: must be strictly < 1e-6
        ("max_force_component_abs_diff_eV_A", 0.5),  # large force diff
    ],
)
def test_reject_parity_at_or_above_threshold(field: str, value: float) -> None:
    receipt = valid_receipt()
    receipt[field] = value
    errors = validate_qualification(valid_lock(), receipt)
    assert errors


# --- 8. MD shape -----------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("md_steps", 50),
        ("md_frames", 100),
    ],
)
def test_reject_wrong_md_step_or_frame_count(field: str, value: int) -> None:
    receipt = valid_receipt()
    receipt[field] = value
    errors = validate_qualification(valid_lock(), receipt)
    assert errors


# --- 9. MD finiteness ------------------------------------------------------


def test_reject_non_finite_md() -> None:
    receipt = valid_receipt()
    receipt["md_finite"] = False
    errors = validate_qualification(valid_lock(), receipt)
    assert any("md_finite" in error for error in errors)


# --- 11. hand-set formal_eligible ------------------------------------------


@pytest.mark.parametrize(
    "tamper",
    [
        lambda lock: lock["qualification"].update(
            {"energy_abs_diff_eV": 1e-3, "parity_ok": False}
        ),
        lambda lock: lock.pop("qualification"),
    ],
    ids=["parity-too-high", "receipt-absent"],
)
def test_reject_hand_set_formal_eligible_without_qualifying_receipt(
    tamper: object,
) -> None:
    lock = valid_lock()
    tamper(lock)  # type: ignore[misc]
    errors = validate_runtime_lock(lock)
    assert any("formal_eligible" in error for error in errors)


# --- 12. oci_repo_digest ---------------------------------------------------


def test_reject_bare_image_id_in_oci_repo_digest() -> None:
    lock = valid_lock()
    lock["oci_repo_digest"] = "f" * 64  # no sha256: prefix
    errors = validate_runtime_lock(lock)
    assert any("oci_repo_digest" in error for error in errors)


# --- 13. image id format ---------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cpu_image_id", "f" * 64),  # bare digest, missing sha256: prefix
        ("gpu_image_id", "sha256:zzz"),  # not 64 hex
    ],
)
def test_reject_non_sha256_image_id(field: str, value: str) -> None:
    lock = valid_lock()
    lock[field] = value
    errors = validate_runtime_lock(lock)
    assert any(field in error for error in errors)


# --- loading ---------------------------------------------------------------


def test_load_runtime_lock_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(valid_lock()), encoding="utf-8")
    assert load_runtime_lock(path)["schema_version"] == 2


def test_load_runtime_lock_rejects_unreadable(tmp_path: Path) -> None:
    with pytest.raises(RuntimeLockError):
        load_runtime_lock(tmp_path / "missing.json")


# --- 14. CLI exit codes ----------------------------------------------------


def test_cli_validate_exit_codes(tmp_path: Path) -> None:
    valid_path = tmp_path / "lock.json"
    valid_path.write_text(json.dumps(valid_lock()), encoding="utf-8")

    ok = run_cli("validate", str(valid_path))
    assert ok.returncode == 0
    assert ok.stderr == ""

    formal = run_cli("validate", str(valid_path), "--require-formal")
    assert formal.returncode == 0

    bad = valid_lock()
    bad["runtime_type"] = "cpu"
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    rejected = run_cli("validate", str(bad_path))
    assert rejected.returncode == 1
    assert "runtime_type" in rejected.stderr

    non_formal = valid_lock()
    non_formal["formal_eligible"] = False
    non_formal.pop("qualification")
    nf_path = tmp_path / "non-formal.json"
    nf_path.write_text(json.dumps(non_formal), encoding="utf-8")
    assert run_cli("validate", str(nf_path)).returncode == 0
    assert run_cli("validate", str(nf_path), "--require-formal").returncode == 1


def test_cli_validate_receipt_exit_codes(tmp_path: Path) -> None:
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(valid_lock()), encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(valid_receipt()), encoding="utf-8")

    ok = run_cli("validate-receipt", str(lock_path), str(receipt_path))
    assert ok.returncode == 0
    assert ok.stderr == ""

    bad = valid_receipt()
    bad["gpu"] = "Tesla V100"
    bad_path = tmp_path / "bad-receipt.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    rejected = run_cli("validate-receipt", str(lock_path), str(bad_path))
    assert rejected.returncode == 1
    assert "A100" in rejected.stderr
