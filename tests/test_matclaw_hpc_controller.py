#!/usr/bin/env python3
"""Task 9 RED: restricted Case 031 sandbox-to-HPC controller.

Security tests (no scheduler, no SSH — the transport is mocked):
  * remote run ids: reject ``..``, absolute paths, non-031 cases;
  * no arbitrary remote shell field / run-kind injection;
  * Slurm policy is pinned to the gpu partition with exactly one GPU;
  * fetch before COMPLETED is rejected;
  * a fetched artifact whose SHA-256 differs from the remote manifest is rejected;
  * UNKNOWN is non-terminal and polling continues;
  * the controller image carries ssh/rsync/CLI but no private key or hidden
    solution/reference/tests trees (static Dockerfile checks + opt-in runtime).
"""
from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pytest

from scripts.matclaw_hpc_controller import (
    CASE_POLICIES,
    REMOTE_ROOT,
    ControllerError,
    MatClawHpcController,
    _FLAGS,
    _parse_env_flags,
    _parse_flags,
    main,
    sha256_file,
)
from scripts.cluster_profile import (
    ProfileError,
    load_profile,
    validate_profile,
)

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER_DF = ROOT / "base-env-build" / "matclaw-cips-controller" / "Dockerfile"
TASK_DF = ROOT / "031-matclaw-cips-active-distillation" / "Dockerfile"
CONTROLLER_IMAGE = "dftworld-base-matclaw-cips:2.2.11-controller"

# eval.py's controller-launch flags need pagentv4 (venv-only).  Make them
# importable under the host pytest by borrowing the venv site-packages; the
# tests skip when that is unavailable.
_VENV_SP = ROOT / ".venv" / "lib" / "python3.13" / "site-packages"
if _VENV_SP.is_dir() and str(_VENV_SP) not in sys.path:
    sys.path.insert(0, str(_VENV_SP))
try:
    from eval import controller_docker_args  # noqa: E402

    _HAVE_EVAL = True
except Exception:  # noqa: BLE001 — pagentv4 absent; skip eval.py tests
    controller_docker_args = None  # type: ignore[assignment]
    _HAVE_EVAL = False


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _MockTransport:
    """Scripted stand-in for SshSlurmTransport (no SSH, no scheduler)."""

    def __init__(
        self,
        *,
        status_result: Union[Any, List[Any]] = "PENDING",
        artifact_content: str = "ORIGINAL",
        declared_hash: Optional[str] = None,
        remote_arch_result: str = "x86_64",
    ) -> None:
        self.status_result = status_result
        self.artifact_content = artifact_content
        self.declared_hash = declared_hash or sha256_bytes(artifact_content.encode())
        self.remote_arch_result = remote_arch_result
        self.stage_calls: List[tuple] = []
        self.submit_calls: List[tuple] = []
        self.fetch_calls: List[tuple] = []
        self.cancel_calls: List[str] = []
        self.log_result = ""
        self._next_job = 1000

    def remote_arch(self) -> str:
        return self.remote_arch_result

    def _status_value(self, job_id: str):
        if isinstance(self.status_result, list):
            return self.status_result.pop(0) if self.status_result else "UNKNOWN"
        return self.status_result

    def stage(self, local_paths, remote_dir):  # type: ignore[no-untyped-def]
        self.stage_calls.append(([str(p) for p in local_paths], remote_dir))
        return [
            f"{REMOTE_ROOT}/{remote_dir}/{Path(p).name}" for p in local_paths
        ]

    def submit(self, script, opts=None):  # type: ignore[no-untyped-def]
        self.submit_calls.append((str(script), opts))
        self._next_job += 1
        return str(self._next_job)

    def status(self, job_id):  # type: ignore[no-untyped-def]
        # Normalize the scripted string to the transport JobState for parity.
        from scripts.ablation.transport.slurm_transport import JobState

        return JobState(self._status_value(job_id))

    def log(self, job_id, tail=None):  # type: ignore[no-untyped-def]
        return self.log_result

    def cancel(self, job_id):  # type: ignore[no-untyped-def]
        self.cancel_calls.append(job_id)

    def fetch(self, remote_paths, local_dir):  # type: ignore[no-untyped-def]
        self.fetch_calls.append(([str(p) for p in remote_paths], str(local_dir)))
        # Simulate a real rsync fetch: write a run tree at
        # <local_dir>/<remote-basename>/ with manifest + artifact.
        base = Path(remote_paths[0]).name
        tree = Path(local_dir) / base
        tree.mkdir(parents=True, exist_ok=True)
        (tree / "out.dat").write_text(self.artifact_content, encoding="utf-8")
        manifest = {
            "artifacts": [{"path": "out.dat", "sha256": self.declared_hash}]
        }
        (tree / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return [tree]


TEST_LOCKED_SIF_SHA = "f104256e5b41f9cdc0304fe5d5ab4184c57b507daba564cfd3328f8c60b96d29"

# The three MatClaw cases the controller serves.
CASE_IDS = ("031", "032", "033")


def valid_qualification_receipt() -> Dict[str, Any]:
    """A fully valid A100 qualification receipt for the locked SIF."""
    return {
        "schema_version": 2,
        "qualified": True,
        "qualify_job": "3536737",
        "node": "<site-node-gpu5>",
        "gpu": "NVIDIA A100-SXM4-80GB",
        "gpu_visible": True,
        "gpu_name": "NVIDIA A100-SXM4-80GB",
        "sif_sha256": TEST_LOCKED_SIF_SHA,
        "architecture": "amd64",
        "uname_m": "x86_64",
        "qualification_sha256": "0" * 64,
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
    }


def valid_runtime_lock(receipt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """A fully valid GPU-amd64 runtime lock embedding a clean receipt."""
    return {
        "schema_version": 2,
        "case": "matclaw-cips-runtime",
        "runtime_type": "gpu",
        "os": "linux",
        "architecture": "amd64",
        "compute_uname_m": "x86_64",
        "source_commit": "a" * 40,
        "cpu_image_id": "sha256:" + "c" * 64,
        "gpu_image_id": "sha256:" + "d" * 64,
        "oci_repo_digest": None,
        "docker_archive_sha256": "e" * 64,
        "sif_path_remote": (
            "/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/"
            "matclaw-cips-2.2.11-gpu-amd64.sif"
        ),
        "sif_sha256": TEST_LOCKED_SIF_SHA,
        "qualification_path_remote": (
            "/public/home/<site-user>/dftworld2-runs/matclaw-031/runtime/locked/qualify_gpu.py"
        ),
        "qualification_sha256": "0" * 64,
        "formal_eligible": True,
        "qualification": receipt if receipt is not None else valid_qualification_receipt(),
    }


def make_controller(
    tmp_path: Path,
    mock: Optional[_MockTransport] = None,
    **kwargs: Any,
) -> MatClawHpcController:
    mock = mock or _MockTransport()
    kwargs.setdefault("transport", mock)
    kwargs.setdefault("workspace", str(tmp_path / "ws"))
    kwargs.setdefault("case_id", "031")
    kwargs.setdefault("locked_sif_sha", TEST_LOCKED_SIF_SHA)
    # Provide a valid shared GPU runtime lock by default so paper/smoke submits
    # pass the fail-closed lock gate (tests override runtime_lock_path to probe
    # the gate's failure modes).
    lock_path = tmp_path / "runtime-lock.json"
    lock_path.write_text(json.dumps(valid_runtime_lock()), encoding="utf-8")
    kwargs.setdefault("runtime_lock_path", str(lock_path))
    return MatClawHpcController(**kwargs)


def make_test_profile() -> Dict[str, Any]:
    """A complete, valid profile with deliberately non-the site values, so tests
    prove the profile actually drives the controller (not a stale default)."""
    return {
        "ssh": {
            "host": "test-login",
            "user": "tester",
            "port": 2222,
            "options": {
                "BatchMode": "yes",
                "ConnectTimeout": "30",
                "IdentitiesOnly": "yes",
                "ControlMaster": "no",
                "StrictHostKeyChecking": "yes",
            },
        },
        "slurm": {
            "account": "test-account",
            "partition": "accelerator",
            "qos": "express",
            "gres": "gpu:2",
            "nodes": "2",
            "cpus_per_task": 4,
            "mem": "32G",
            "time_paper": "02:00:00",
            "time_smoke": "00:10:00",
            "time_probe": "00:05:00",
        },
        "paths": {
            "remote_root": "/scratch/tester/matclaw-031",
            "apptainer": "/opt/apptainer/bin/apptainer",
            "run_suffix": "jobs",
        },
        "runtime": {
            "expected_node_arch": "x86_64",
            "sync_strategy": "sync_back",
        },
    }


def _dump_toml(profile: Dict[str, Any]) -> str:
    """Serialize the flat-nested profile dict to TOML text (test helper)."""
    lines: List[str] = []

    def walk(node: Dict[str, Any], path: str) -> None:
        for key, value in node.items():
            dotted = f"{path}.{key}" if path else key
            if isinstance(value, dict):
                lines.append(f"[{dotted}]")
                walk(value, dotted)
            elif isinstance(value, bool):
                lines.append(f"{key} = {'true' if value else 'false'}")
            elif isinstance(value, int):
                lines.append(f"{key} = {value}")
            else:
                lines.append(f"{key} = {json.dumps(str(value))}")

    walk(profile, "")
    return "\n".join(lines) + "\n"


@pytest.fixture
def mock_transport() -> _MockTransport:
    return _MockTransport()


# --------------------------------------------------------------------------- #
# Remote run id validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_id",
    [
        "../escape",
        "031-run/../../etc",
        "031-..evil",
        "031-./etc",
        "",
    ],
)
def test_remote_run_id_rejects_dotdot(tmp_path, mock_transport, bad_id):
    ctl = make_controller(tmp_path, mock_transport)
    with pytest.raises(ControllerError):
        ctl.stage(tmp_path / "run", bad_id)


@pytest.mark.parametrize(
    "bad_id",
    [
        "/etc/passwd",
        "/public/home/<site-user>/dftworld2-runs/matclaw-031/031-x",
        "~/evil",
        "~<site-user>/evil",
    ],
)
def test_remote_run_id_rejects_absolute(tmp_path, mock_transport, bad_id):
    ctl = make_controller(tmp_path, mock_transport)
    with pytest.raises(ControllerError):
        ctl.stage(tmp_path / "run", bad_id)


@pytest.mark.parametrize(
    "bad_id",
    [
        "032-run-001",
        "033-matclaw",
        "run-001",
        "matclaw-031-run",
        "031",
    ],
)
def test_remote_run_id_rejects_non_031(tmp_path, mock_transport, bad_id):
    ctl = make_controller(tmp_path, mock_transport)
    with pytest.raises(ControllerError):
        ctl.stage(tmp_path / "run", bad_id)


def test_stage_requires_existing_local_dir(tmp_path, mock_transport):
    ctl = make_controller(tmp_path, mock_transport)
    with pytest.raises(ControllerError):
        ctl.stage(tmp_path / "does-not-exist", "031-run-001")


# --------------------------------------------------------------------------- #
# No arbitrary remote command / run-kind injection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_kind",
    [
        "; reboot",
        "paper; id",
        "rm -rf /",
        "--gres=gpu:4",
        "--partition=cpu",
        "paper && whoami",
    ],
)
def test_submit_rejects_arbitrary_run_kind(tmp_path, mock_transport, bad_kind):
    ctl = make_controller(tmp_path, mock_transport)
    with pytest.raises(ControllerError):
        ctl.submit("031-run-001", bad_kind)


@pytest.mark.parametrize(
    "bad_job_id",
    ["1; id", "abc", "42.5", "", "42|cat /etc/passwd"],
)
def test_job_id_must_be_scheduler_integer(tmp_path, mock_transport, bad_job_id):
    ctl = make_controller(tmp_path, mock_transport)
    with pytest.raises(ControllerError):
        ctl.status(bad_job_id)
    with pytest.raises(ControllerError):
        ctl.cancel(bad_job_id)
    with pytest.raises(ControllerError):
        ctl.log(bad_job_id)


def test_cli_operations_are_only_the_six(tmp_path):
    assert sorted(_FLAGS) == ["cancel", "fetch", "log", "stage", "status", "submit"]
    # No operation exposes a raw command/shell/key field.
    for allowed in _FLAGS.values():
        for flag in allowed:
            assert "--" in flag
            assert flag not in ("--shell", "--exec", "--command", "--key",
                                "--password", "--ssh-option", "-o")


def test_cli_rejects_unsupported_operations():
    assert main(["shell", "echo hacked"]) != 0
    assert main(["exec", "rm", "-rf", "/"]) != 0
    assert main(["command", "whoami"]) != 0
    assert main([]) != 0


def test_cli_rejects_unexpected_arguments():
    assert main(["status", "--job-id", "1001", "EXTRA"]) != 0
    assert main(["status", "--job-id", "1001", "-o", "foo"]) != 0
    assert main(["stage"]) != 0  # missing required flags


def test_cli_rejects_shell_metacharacters_in_job_id():
    rc = main(["status", "--job-id", "1; id"])
    assert rc != 0
    rc = main(["cancel", "--job-id", "42 && reboot"])
    assert rc != 0


def test_parse_flags_rejects_unknown(tmp_path):
    with pytest.raises(ControllerError):
        _parse_flags(["--remote-run-id", "031-x", "--oops", "v"], ["--remote-run-id"])


# --------------------------------------------------------------------------- #
# Slurm policy: gpu partition, exactly one GPU
# --------------------------------------------------------------------------- #


def test_slurm_opts_pinned_to_gpu_one_gpu(tmp_path, mock_transport):
    ctl = make_controller(tmp_path, mock_transport)
    opts = ctl._slurm_opts()
    assert opts.partition == "gpu"
    assert opts.gres == "gpu:1"
    assert "cpu" not in (opts.partition or "")
    assert "gpu:1" in (opts.gres or "")


def test_assert_slurm_policy_accepts_profile_script(tmp_path, mock_transport):
    ctl = make_controller(tmp_path, mock_transport)
    ctl._assert_slurm_policy(
        "#SBATCH --partition=gpu\n#SBATCH --gres=gpu:1\n"
    )


@pytest.mark.parametrize(
    "tampered",
    [
        "#SBATCH --partition=cpu\n#SBATCH --gres=gpu:1\n",
        "#SBATCH --partition=gpu\n#SBATCH --gres=gpu:2\n",
        "#SBATCH --partition=gpu\n#SBATCH --gres=gpu:8\n",
        "#SBATCH --partition=gpu\n#SBATCH --gres=gpu:1\n#SBATCH --gres=gpu:2\n",
        "#SBATCH --partition=fat\n#SBATCH --gres=gpu:1\n",
        "#SBATCH --partition=gpu\n#SBATCH --gres=shard:4\n",
        "#SBATCH --gres=gpu:1\n",
    ],
)
def test_assert_slurm_policy_rejects_non_profile_or_multi_gpu(tmp_path, mock_transport, tampered):
    ctl = make_controller(tmp_path, mock_transport)
    with pytest.raises(ControllerError):
        ctl._assert_slurm_policy(tampered)


def test_submit_passes_pinned_opts_to_transport(tmp_path, mock_transport):
    ctl = make_controller(tmp_path, mock_transport)
    job_id = ctl.submit("031-run-001", "probe")
    assert job_id == "1001"
    script, opts = mock_transport.submit_calls[0]
    assert opts.partition == "gpu"
    assert opts.gres == "gpu:1"
    assert Path(script).is_file()
    rendered = Path(script).read_text(encoding="utf-8")
    assert "#SBATCH --partition=gpu" in rendered
    assert "#SBATCH --gres=gpu:1" in rendered


def test_probe_script_carries_gpu_policy(tmp_path, mock_transport):
    ctl = make_controller(tmp_path, mock_transport)
    text = ctl._render_probe_slurm()
    assert "#SBATCH --partition=gpu" in text
    assert "#SBATCH --gres=gpu:1" in text
    assert "gpu:2" not in text
    assert "hostname" in text and "CUDA_VISIBLE_DEVICES" in text


# --------------------------------------------------------------------------- #
# Cluster profile: TOML loader, schema, and profile-driven behavior
# --------------------------------------------------------------------------- #


def test_reference_profile_toml_is_valid():
    """The in-repo reference template must load and validate unchanged."""
    profile = load_profile(ROOT / "scripts" / "hpc" / "cluster_profile.toml")
    assert profile["ssh"]["host"] == "<site-alias>"
    assert profile["slurm"]["partition"] == "gpu,gpu-mig-2g-20gb"
    assert profile["slurm"]["cpu_partition"] == "cpu"
    assert profile["slurm"]["gres"] == "gpu:1"


def test_profile_missing_key_fails_fast():
    profile = make_test_profile()
    del profile["slurm"]["qos"]
    with pytest.raises(ProfileError, match="slurm.qos"):
        validate_profile(profile)


def test_profile_wrong_type_fails_fast():
    profile = make_test_profile()
    profile["ssh"]["port"] = "2222"  # str, not int
    with pytest.raises(ProfileError, match="ssh.port"):
        validate_profile(profile)


def test_profile_loads_from_toml_file(tmp_path):
    path = tmp_path / "cluster_profile.toml"
    path.write_text(_dump_toml(make_test_profile()), encoding="utf-8")
    profile = load_profile(path)
    assert profile["slurm"]["partition"] == "accelerator"
    assert profile["slurm"]["cpus_per_task"] == 4


def test_profile_load_rejects_bad_toml(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("[slurm\naccount = ", encoding="utf-8")
    with pytest.raises(ProfileError, match="TOML"):
        load_profile(path)


def test_profile_load_rejects_missing_file(tmp_path):
    with pytest.raises(ProfileError, match="not found"):
        load_profile(tmp_path / "nope.toml")


def test_constructor_accepts_profile_path(tmp_path):
    """profile= can be a path to a TOML file (the third-party entry point)."""
    path = tmp_path / "cluster_profile.toml"
    path.write_text(_dump_toml(make_test_profile()), encoding="utf-8")
    ctl = MatClawHpcController(
        transport=_MockTransport(), profile=str(path), case_id="031"
    )
    assert ctl.profile["slurm"]["partition"] == "accelerator"
    assert ctl.remote_root == "/scratch/tester/matclaw-031"


def test_custom_profile_drives_defaults():
    """Constructor defaults (remote root, arch) come from the profile.

    The SIF and its SHA are NOT resolved from the profile anymore: F1/F2 make
    the runtime lock the single truth for SIF identity, so both stay ``None``
    until the lock is loaded and validated at paper/smoke submit.
    """
    ctl = MatClawHpcController(
        transport=_MockTransport(), profile=make_test_profile(), case_id="031"
    )
    assert ctl.remote_root == "/scratch/tester/matclaw-031"
    assert ctl.matclaw_sif is None
    assert ctl.locked_sif_sha is None
    assert ctl.expected_node_arch == "x86_64"


# --- F1: security-critical facts never come from agent-controlled env --------


def test_trusted_facts_reject_env_sif(monkeypatch):
    """F1: MATCLAW_SIF in the process env fails closed — even set to the fixed
    value.  The fixed read-only source is authoritative; presence alone is a
    stale/leaky caller signal."""
    monkeypatch.setenv("MATCLAW_SIF", "/scratch/tester/runtime/matclaw.sif")
    with pytest.raises(ControllerError, match="MATCLAW_SIF"):
        MatClawHpcController(
            transport=_MockTransport(), profile=make_test_profile(), case_id="031"
        )


def test_trusted_facts_reject_env_locked_sif_sha(monkeypatch):
    """F1: MATCLAW_LOCKED_SIF_SHA in env fails closed (even if it equals the
    locked SHA)."""
    monkeypatch.setenv("MATCLAW_LOCKED_SIF_SHA", TEST_LOCKED_SIF_SHA)
    with pytest.raises(ControllerError, match="MATCLAW_LOCKED_SIF_SHA"):
        MatClawHpcController(
            transport=_MockTransport(), profile=make_test_profile(), case_id="031"
        )


def test_trusted_facts_reject_env_solution_dir(monkeypatch):
    """F1: MATCLAW_SOLUTION_DIR in env fails closed (even to the policy value)."""
    monkeypatch.setenv("MATCLAW_SOLUTION_DIR", f"{REMOTE_ROOT}/_solution")
    with pytest.raises(ControllerError, match="MATCLAW_SOLUTION_DIR"):
        MatClawHpcController(
            transport=_MockTransport(), profile=make_test_profile(), case_id="031"
        )


def test_trusted_facts_reject_all_blocked_env(monkeypatch):
    """All three blocked vars set at once still fail closed before anything is
    resolved (the blocklist is checked first, in __init__)."""
    monkeypatch.setenv("MATCLAW_SIF", "/scratch/tester/runtime/matclaw.sif")
    monkeypatch.setenv("MATCLAW_LOCKED_SIF_SHA", TEST_LOCKED_SIF_SHA)
    monkeypatch.setenv("MATCLAW_SOLUTION_DIR", f"{REMOTE_ROOT}/_solution")
    with pytest.raises(ControllerError, match="security-critical env"):
        MatClawHpcController(
            transport=_MockTransport(), profile=make_test_profile(), case_id="031"
        )


def test_trusted_facts_constructor_injection_still_works(tmp_path):
    """F1 only blocks the *env* path — explicit constructor injection (the unit
    test seam) still drives the SIF / SHA."""
    ctl = make_controller(
        tmp_path,
        _MockTransport(),
        matclaw_sif="/public/sif/matclaw.sif",
        locked_sif_sha=TEST_LOCKED_SIF_SHA,
    )
    assert ctl.matclaw_sif == "/public/sif/matclaw.sif"
    assert ctl.locked_sif_sha == TEST_LOCKED_SIF_SHA


# --- F2: lock + receipt come from fixed read-only mounts, never env ----------


def test_trusted_facts_reject_env_runtime_lock(monkeypatch):
    """F2: MATCLAW_RUNTIME_LOCK in env fails closed — the mount is the only
    source for the runtime lock path."""
    monkeypatch.setenv("MATCLAW_RUNTIME_LOCK", "/scratch/tester/runtime-lock.json")
    with pytest.raises(ControllerError, match="MATCLAW_RUNTIME_LOCK"):
        MatClawHpcController(
            transport=_MockTransport(), profile=make_test_profile(), case_id="031"
        )


def test_trusted_facts_reject_env_qualification_receipt(monkeypatch):
    """F2: MATCLAW_QUALIFICATION_RECEIPT in env fails closed."""
    monkeypatch.setenv(
        "MATCLAW_QUALIFICATION_RECEIPT", "/scratch/tester/receipt.json"
    )
    with pytest.raises(ControllerError, match="MATCLAW_QUALIFICATION_RECEIPT"):
        MatClawHpcController(
            transport=_MockTransport(), profile=make_test_profile(), case_id="031"
        )


def test_constructor_defaults_to_fixed_lock_and_receipt_mounts(tmp_path):
    """F2: without an injected path, the controller reads the fixed harness
    mounts — the agent cannot choose where the lock/receipt come from."""
    ctl = make_controller(
        tmp_path,
        _MockTransport(),
        runtime_lock_path=None,
        qualification_receipt_path=None,
    )
    assert ctl.runtime_lock_path == "/run/dftworld/runtime-lock.json"
    assert ctl.qualification_receipt_path == (
        "/run/dftworld/qualification-receipt.json"
    )


def test_build_transport_uses_profile_ssh():
    ctl = MatClawHpcController(
        transport=_MockTransport(), profile=make_test_profile(), case_id="031"
    )
    transport = ctl._build_transport()
    assert transport.ssh.host == "test-login"
    assert transport.ssh.user == "tester"
    assert transport.ssh.port == 2222
    assert transport.ssh.options["ConnectTimeout"] == "30"
    assert transport.sync == "sync_back"
    assert transport.remote_workspace == "/scratch/tester/matclaw-031"


def test_custom_profile_drives_slurm_opts(tmp_path):
    ctl = make_controller(tmp_path, _MockTransport(), profile=make_test_profile())
    opts = ctl._slurm_opts()
    assert opts.partition == "accelerator"
    assert opts.gres == "gpu:2"
    assert opts.cpus_per_task == "4"  # int in the profile normalized to str
    assert opts.nodes == "2"


def test_custom_profile_drives_probe_render(tmp_path):
    ctl = make_controller(tmp_path, _MockTransport(), profile=make_test_profile())
    text = ctl._render_probe_slurm()
    assert "#SBATCH --account=test-account" in text
    assert "#SBATCH --partition=accelerator" in text
    assert "#SBATCH --gres=gpu:2" in text
    assert "#SBATCH --time=00:05:00" in text  # time_probe


def test_custom_profile_drives_paper_render(tmp_path):
    ctl = make_controller(
        tmp_path,
        _MockTransport(),
        profile=make_test_profile(),
        matclaw_sif="/public/sif/matclaw.sif",
        solution_dir=f"{REMOTE_ROOT}/_solution",
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    remote_run = ctl.stage(local_run, "031-run-001")
    text = ctl._render_slurm(remote_run, run_kind="paper")
    assert "#SBATCH --account=test-account" in text
    assert "#SBATCH --partition=accelerator" in text
    assert "#SBATCH --gres=gpu:2" in text
    assert "#SBATCH --time=02:00:00" in text  # time_paper
    assert "APPTAINER=/opt/apptainer/bin/apptainer" in text
    for tok in (
        "__SBATCH_ACCOUNT__", "__SBATCH_PARTITION__", "__SBATCH_GRES__",
        "__SBATCH_TIME__", "__APPTAINER_PATH__",
    ):
        assert tok not in text
    # run_kind selects the time limit
    assert "#SBATCH --time=00:10:00" in ctl._render_slurm(
        remote_run, run_kind="smoke"
    )


def test_assert_slurm_policy_checks_against_profile(tmp_path):
    ctl = make_controller(tmp_path, _MockTransport(), profile=make_test_profile())
    # matches profile -> ok
    ctl._assert_slurm_policy(
        "#SBATCH --partition=accelerator\n#SBATCH --gres=gpu:2\n"
    )
    # partition differs from profile -> reject
    with pytest.raises(ControllerError, match="partition"):
        ctl._assert_slurm_policy(
            "#SBATCH --partition=gpu\n#SBATCH --gres=gpu:2\n"
        )
    # gres differs from profile -> reject
    with pytest.raises(ControllerError, match="gres"):
        ctl._assert_slurm_policy(
            "#SBATCH --partition=accelerator\n#SBATCH --gres=gpu:1\n"
        )


# --------------------------------------------------------------------------- #
# Paper/smoke rendering and stage-before-submit ordering
# --------------------------------------------------------------------------- #


def test_paper_render_needs_sif_and_solution(tmp_path, mock_transport):
    ctl = make_controller(tmp_path, mock_transport, matclaw_sif=None, solution_dir=None)
    with pytest.raises(ControllerError):
        ctl.submit("031-run-001", "paper")


def test_paper_submit_requires_prior_stage(tmp_path, mock_transport):
    ctl = make_controller(
        tmp_path,
        mock_transport,
        matclaw_sif="/public/sif/matclaw.sif",
        solution_dir=f"{REMOTE_ROOT}/_solution",
    )
    with pytest.raises(ControllerError, match="stage"):
        ctl.submit("031-run-001", "paper")


def test_paper_render_contains_apptainer_bind(tmp_path, mock_transport):
    ctl = make_controller(
        tmp_path,
        mock_transport,
        matclaw_sif="/public/sif/matclaw.sif",
        solution_dir=f"{REMOTE_ROOT}/_solution",
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    remote_run = ctl.stage(local_run, "031-run-001")
    text = ctl._render_slurm(remote_run)
    assert "APPTAINER=/public/software/apptainer/bin/apptainer" in text
    assert "--nv --containall" in text
    # The rendered script defines the path-confined values and binds via the
    # equivalent $RUN_DIR / $SOLUTION_DIR / $MATCLAW_SIF form.
    assert f"RUN_DIR={shlex.quote(remote_run)}" in text
    assert '--bind "$RUN_DIR:/app"' in text
    assert f"SOLUTION_DIR={shlex.quote(f'{REMOTE_ROOT}/_solution')}" in text
    assert '--bind "$SOLUTION_DIR:/solution:ro"' in text
    assert f'MATCLAW_SIF={shlex.quote("/public/sif/matclaw.sif")}' in text
    assert '"$MATCLAW_SIF" /solution/solve.sh' in text
    assert "set -euo pipefail" in text


def _rendered_paper(tmp_path, mock_transport) -> str:
    ctl = make_controller(
        tmp_path,
        mock_transport,
        matclaw_sif="/public/sif/matclaw.sif",
        solution_dir=f"{REMOTE_ROOT}/_solution",
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    remote_run = ctl.stage(local_run, "031-run-001")
    return ctl, remote_run


def test_render_without_env_injects_no_exports(tmp_path, mock_transport):
    ctl, remote_run = _rendered_paper(tmp_path, mock_transport)
    text = ctl._render_slurm(remote_run)
    assert "__MATCLAW_ENV_FLAGS__" not in text
    assert "--env MATCLAW_" not in text


def test_render_injects_sorted_quoted_env_flags(tmp_path, mock_transport):
    ctl, remote_run = _rendered_paper(tmp_path, mock_transport)
    text = ctl._render_slurm(
        remote_run,
        {"MATCLAW_OUTPUT": "/app", "MATCLAW_SEED": "2026081199", "MATCLAW_PROFILE": "paper"},
    )
    # Sorted keys: OUTPUT, PROFILE, SEED — rendered as apptainer --env flags
    # (host exports are dropped by --containall/--cleanenv, so they must reach
    # the container through --env).
    assert text.index("--env MATCLAW_OUTPUT=/app") < text.index(
        "--env MATCLAW_PROFILE=paper"
    ) < text.index("--env MATCLAW_SEED=2026081199")
    # Values are shell-quoted; a value with spaces must not break the flag.
    text2 = ctl._render_slurm(remote_run, {"WEIRD": "a b; touch /tmp/x"})
    assert "--env WEIRD='a b; touch /tmp/x'" in text2
    assert "__MATCLAW_ENV_FLAGS__" not in text


def test_submit_writes_env_into_staged_script(tmp_path, mock_transport):
    ctl, remote_run = _rendered_paper(tmp_path, mock_transport)
    ctl.submit(
        "031-run-001",
        "paper",
        env={"MATCLAW_PROFILE": "paper", "MATCLAW_SEED": "2026081199"},
    )
    script_path = Path(mock_transport.submit_calls[0][0])
    rendered = script_path.read_text(encoding="utf-8")
    assert "--env MATCLAW_PROFILE=paper" in rendered
    assert "--env MATCLAW_SEED=2026081199" in rendered
    # The exec line carries the flags inside the container command.
    assert "exec \"$APPTAINER\" exec --nv --containall \\\n  --env MATCLAW_PROFILE=paper" in rendered


def test_parse_flags_accepts_repeatable_env():
    flags = _parse_flags(
        ["--remote-run-id", "031-x", "--run-kind", "paper",
         "--env", "A=1", "--env", "B=2"],
        _FLAGS["submit"],
    )
    assert flags["--env"] == ["A=1", "B=2"]
    assert flags["--remote-run-id"] == "031-x"


def test_parse_env_flags_rejects_malformed_pairs():
    assert _parse_env_flags(["A=1", "B=2"]) == {"A": "1", "B": "2"}
    for bad in ["A", "=1", "A B=1", "1A=1", "A=", "--export=X=1"]:
        with pytest.raises(ControllerError):
            _parse_env_flags([bad])


def test_stage_returns_confined_remote_path(tmp_path, mock_transport):
    ctl = make_controller(tmp_path, mock_transport)
    local_run = tmp_path / "run-001"
    local_run.mkdir()
    remote = ctl.stage(local_run, "031-run-001")
    assert remote == f"{REMOTE_ROOT}/031-run-001/run-001"
    assert remote.startswith(REMOTE_ROOT + "/")
    assert ".." not in remote


# --------------------------------------------------------------------------- #
# Fetch: COMPLETED gate + manifest SHA-256 verification
# --------------------------------------------------------------------------- #


def test_fetch_before_completed_rejected(tmp_path):
    mock = _MockTransport(status_result="RUNNING")
    ctl = make_controller(tmp_path, mock)
    ctl.submit("031-run-001", "probe")
    with pytest.raises(ControllerError, match="before COMPLETED"):
        ctl.fetch("031-run-001", tmp_path / "local")


def test_fetch_without_submit_rejected(tmp_path, mock_transport):
    ctl = make_controller(tmp_path, mock_transport)
    local = tmp_path / "local"
    local.mkdir()
    with pytest.raises(ControllerError, match="submit"):
        ctl.fetch("031-run-001", local)


def test_fetch_verifies_matching_sha(tmp_path):
    mock = _MockTransport(status_result="COMPLETED", artifact_content="ORIGINAL")
    ctl = make_controller(tmp_path, mock)
    ctl.submit("031-run-001", "probe")
    hashes = ctl.fetch("031-run-001", tmp_path / "local")
    assert hashes == {"out.dat": sha256_bytes(b"ORIGINAL")}


def test_fetch_rejects_sha_mismatch(tmp_path):
    mock = _MockTransport(
        status_result="COMPLETED",
        artifact_content="TAMPERED",
        declared_hash=sha256_bytes(b"ORIGINAL"),
    )
    ctl = make_controller(tmp_path, mock)
    ctl.submit("031-run-001", "probe")
    with pytest.raises(ControllerError, match="SHA-256 mismatch"):
        ctl.fetch("031-run-001", tmp_path / "local")


def test_fetch_requires_manifest(tmp_path):
    mock = _MockTransport(status_result="COMPLETED")
    ctl = make_controller(tmp_path, mock)

    class _NoManifestFetch(_MockTransport):
        def fetch(self, remote_paths, local_dir):
            base = Path(remote_paths[0]).name
            tree = Path(local_dir) / base
            tree.mkdir(parents=True, exist_ok=True)
            (tree / "out.dat").write_text("X", encoding="utf-8")
            return [tree]

    ctl._transport = _NoManifestFetch(status_result="COMPLETED")
    ctl.submit("031-run-001", "probe")
    with pytest.raises(ControllerError, match="manifest"):
        ctl.fetch("031-run-001", tmp_path / "local")


def test_fetch_normalizes_run_tree_into_local_dir(tmp_path):
    mock = _MockTransport(status_result="COMPLETED", artifact_content="ORIGINAL")
    ctl = make_controller(tmp_path, mock)
    ctl.submit("031-run-001", "probe")
    local = tmp_path / "local"
    ctl.fetch("031-run-001", local)
    # transport.fetch writes <local>/<basename>/; the controller hoists contents.
    assert (local / "out.dat").is_file()
    assert (local / "manifest.json").is_file()
    assert not (local / "031-run-001").exists()


# --------------------------------------------------------------------------- #
# UNKNOWN is non-terminal; polling continues
# --------------------------------------------------------------------------- #


def test_unknown_is_not_terminal():
    from scripts.ablation.transport.slurm_transport import JobState

    assert JobState.UNKNOWN.is_terminal is False


def test_wait_polls_through_unknown(tmp_path):
    mock = _MockTransport(
        status_result=["UNKNOWN", "UNKNOWN", "COMPLETED"]
    )
    ctl = make_controller(tmp_path, mock)
    ctl._jobs["031-run-001"] = "1001"
    final = ctl.wait_for("1001", poll=0.0, timeout=30)
    assert final.value == "COMPLETED"


def test_wait_timeout_cancels_never_success_on_unknown(tmp_path):
    mock = _MockTransport(status_result=[])
    ctl = make_controller(tmp_path, mock)
    ctl._jobs["031-run-001"] = "1001"
    with pytest.raises(TimeoutError):
        ctl.wait_for("1001", poll=0.0, timeout=0)
    assert mock.cancel_calls == ["1001"]


# --------------------------------------------------------------------------- #
# Controller image contents: ssh/rsync/CLI, no secrets, no hidden trees
# --------------------------------------------------------------------------- #


def test_controller_dockerfile_installs_cli_but_no_ssh_tools():
    """The controller image ships the restricted controller surface but NO ssh
    client or rsync: transport access lives on the gateway host."""
    text = CONTROLLER_DF.read_text(encoding="utf-8")
    assert "FROM dftworld-base-matclaw-cips:2.2.11-cpu" in text
    assert "openssh-client" not in text
    assert "rsync" not in text
    assert "matclaw_hpc_controller.py" in text
    assert "slurm_transport.py" in text
    assert "matclaw_runtime_lock.py" in text  # F2: paper/smoke lock gate
    assert "/opt/dftworld/controller/" in text


def test_controller_dockerfile_copies_all_case_templates():
    """F2: the image carries the fixed Slurm template for every case (031/032/033)
    — a paper/smoke submit must render on the node, never fall back to a
    missing-template error."""
    text = CONTROLLER_DF.read_text(encoding="utf-8")
    for case in ("031", "032", "033"):
        assert f"scripts/hpc/{case}_matclaw_gpu.slurm" in text
        assert f"/opt/dftworld/controller/hpc/{case}_matclaw_gpu.slurm" in text


def test_controller_dockerfile_has_no_secrets_or_hidden_trees():
    """No COPY/ADD directive may reference secrets or hidden task trees.

    We inspect the actual COPY/ADD lines (not prose comments) so a comment that
    merely names 'solution/' cannot mask a real COPY of it.
    """
    text = CONTROLLER_DF.read_text(encoding="utf-8")
    copy_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip().upper().startswith(("COPY ", "ADD "))
    ]
    assert copy_lines, "expected COPY/ADD lines to inspect"
    forbidden = (
        "id_rsa",
        "id_ed25519",
        ".pem",
        "known_hosts",
        "solution",
        "reference",
        "/tests",
        ".ssh",
        "private",
    )
    for line in copy_lines:
        lowered = line.lower()
        for secret in forbidden:
            assert secret not in lowered, f"{line!r} references forbidden {secret!r}"


def test_task_dockerfile_copies_only_public():
    text = TASK_DF.read_text(encoding="utf-8")
    assert "COPY public/ /app/" in text
    for forbidden in ("solution", "reference", "/tests", "id_rsa", ".ssh"):
        assert forbidden not in text
    # The task image must derive from the controller base so the agent container
    # carries the controller CLI.
    assert "dftworld-base-matclaw-cips:2.2.11-controller" in text


def test_controller_image_runtime_opt_in():
    """If the controller image is built, verify it really contains the tools and
    not the secrets.  Skipped when the image is absent (image build is a later
    step of the reconstruction plan)."""
    listed = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
    )
    if CONTROLLER_IMAGE not in (listed.stdout or "").splitlines():
        pytest.skip(f"{CONTROLLER_IMAGE} not built; static checks only")
    probe = (
        "test -f /opt/dftworld/controller/matclaw_hpc_controller.py && "
        "test -f /opt/dftworld/controller/slurm_transport.py && "
        "! find /opt/dftworld/controller -name 'id_rsa*' | grep -q . && "
        "! find /opt/dftworld/controller -name '*.pem' | grep -q ."
    )
    proc = subprocess.run(
        ["docker", "run", "--rm", CONTROLLER_IMAGE, "bash", "-lc", probe],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"image probe failed: {proc.stderr or proc.stdout}"


def test_sha256_file_helper(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello")
    assert sha256_file(p) == sha256_bytes(b"hello")


# --------------------------------------------------------------------------- #
# eval.py controller launch flags (opt-in; needs pagentv4)
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not _HAVE_EVAL, reason="pagentv4 unavailable (eval import failed)")
def test_eval_controller_args_non_controller_task():
    # Task 11: dispatch is by execution class, never by task name
    assert controller_docker_args("local_sandbox", "001") == []
    assert controller_docker_args("local_sandbox", "034") == []


@pytest.mark.skipif(not _HAVE_EVAL, reason="pagentv4 unavailable (eval import failed)")
@pytest.mark.parametrize("case_id", ["031", "032", "033"])
def test_eval_controller_args_pin_matclaw_case(monkeypatch, tmp_path, case_id):
    """F3: every hpc_controller case mounts a case-policy document pinned to
    its case at the fixed read-only path — MATCLAW_CASE env is never used (the
    agent could override it), and no key material or ssh material is forwarded."""
    import eval as eval_mod

    monkeypatch.setattr(eval_mod, "_CASE_POLICY_DIR", tmp_path)
    args = controller_docker_args("hpc_controller", case_id)
    # the case is pinned by a read-only mount, not by MATCLAW_CASE env
    assert not any(a.startswith("MATCLAW_CASE=") for a in args)
    policy_src = tmp_path / f"case-policy-{case_id}.json"
    assert policy_src.is_file()
    assert (
        f"{policy_src}:/run/dftworld/case-policy.json:ro" in args
    )
    assert json.loads(policy_src.read_text(encoding="utf-8"))["case_id"] == case_id
    # the controller is the control layer only — ssh/scheduler access lives on
    # the gateway host; nothing ssh-shaped is mounted into the agent
    assert not any("/run/dftworld-ssh-agent" in a for a in args)
    assert not any("SSH_AUTH_SOCK" in a for a in args)
    assert not any("/root/.ssh/" in a for a in args)
    assert all("id_rsa" not in a and "id_ed25519" not in a for a in args)


@pytest.mark.skipif(not _HAVE_EVAL, reason="pagentv4 unavailable (eval import failed)")
def test_eval_controller_args_forwards_no_ssh_credentials(monkeypatch, tmp_path):
    """Even with a live SSH_AUTH_SOCK and a real ~/.ssh on the host, the
    controller mounts nothing ssh-shaped: the gateway owns the transport."""
    import eval as eval_mod

    sock_path = tmp_path / "agent.sock"
    sock_path.write_bytes(b"x")
    monkeypatch.setenv("SSH_AUTH_SOCK", str(sock_path))
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "config").write_text(
        "Host <site-alias>\n    HostName <site-host-ip>\n", encoding="utf-8"
    )
    (ssh_dir / "known_hosts").write_text("dummy host key\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(eval_mod, "_CASE_POLICY_DIR", tmp_path)
    args = controller_docker_args("hpc_controller", "031")
    assert not any("/run/dftworld-ssh-agent" in a for a in args)
    assert not any("SSH_AUTH_SOCK" in a for a in args)
    assert not any("/root/.ssh/" in a for a in args)
    # never a private-key path
    assert all("id_rsa" not in a and "id_ed25519" not in a for a in args)


@pytest.mark.skipif(not _HAVE_EVAL, reason="pagentv4 unavailable (eval import failed)")
def test_eval_controller_args_mounts_lock_and_receipt_readonly(monkeypatch, tmp_path):
    """F2: when the formal pipeline has issued the lock/receipt, they are
    mounted at the fixed read-only paths inside the controller container."""
    import eval as eval_mod

    lock_src = tmp_path / "runtime-gpu-amd64.lock.json"
    receipt_src = tmp_path / "qualification-receipt.json"
    lock_src.write_text("{}", encoding="utf-8")
    receipt_src.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(eval_mod, "_CONTROLLER_RUNTIME_LOCK_SRC", lock_src)
    monkeypatch.setattr(
        eval_mod, "_CONTROLLER_QUALIFICATION_RECEIPT_SRC", receipt_src
    )
    args = controller_docker_args("hpc_controller", "031")
    assert f"{lock_src}:/run/dftworld/runtime-lock.json:ro" in args
    assert (
        f"{receipt_src}:/run/dftworld/qualification-receipt.json:ro" in args
    )


@pytest.mark.skipif(not _HAVE_EVAL, reason="pagentv4 unavailable (eval import failed)")
def test_eval_controller_args_skips_lock_mount_when_absent(monkeypatch, tmp_path):
    """F2: mount-if-present — before the lock is issued no mount is added; the
    controller fails closed at paper/smoke submit instead of at launch."""
    import eval as eval_mod

    monkeypatch.setattr(
        eval_mod, "_CONTROLLER_RUNTIME_LOCK_SRC", tmp_path / "nope-lock.json"
    )
    monkeypatch.setattr(
        eval_mod,
        "_CONTROLLER_QUALIFICATION_RECEIPT_SRC",
        tmp_path / "nope-receipt.json",
    )
    args = controller_docker_args("hpc_controller", "031")
    assert not any("/run/dftworld/runtime-lock.json" in a for a in args)
    assert not any(
        "/run/dftworld/qualification-receipt.json" in a for a in args
    )


# --------------------------------------------------------------------------- #
# Pre-submit arch gate: cluster must run the locked SIF's arch
# --------------------------------------------------------------------------- #


def test_submit_arch_gate_passes_on_matching_arch(tmp_path, mock_transport):
    """Default mock reports x86_64; a staged paper submit must go through."""
    ctl, _ = _rendered_paper(tmp_path, mock_transport)
    job_id = ctl.submit("031-run-001", "paper")
    assert job_id == "1001"
    assert len(mock_transport.submit_calls) == 1


def test_submit_arch_gate_rejects_mismatch(tmp_path):
    mock = _MockTransport(remote_arch_result="aarch64")
    ctl, _ = _rendered_paper(tmp_path, mock)
    with pytest.raises(ControllerError, match="refusing to submit paper"):
        ctl.submit("031-run-001", "paper")
    assert mock.submit_calls == []  # nothing hit sbatch


def test_probe_submit_skips_arch_gate(tmp_path):
    """The probe is self-contained (no SIF) — arch mismatch must not block it."""
    mock = _MockTransport(remote_arch_result="aarch64")
    ctl = make_controller(tmp_path, mock)
    job_id = ctl.submit("031-run-001", "probe")
    assert job_id == "1001"
    assert len(mock.submit_calls) == 1


def test_submit_arch_gate_fails_closed_on_transport_error(tmp_path):
    from scripts.ablation.transport.slurm_transport import TransportError

    class _FlakyArchTransport(_MockTransport):
        def remote_arch(self) -> str:
            raise TransportError("ssh refused")

    mock = _FlakyArchTransport()
    ctl, _ = _rendered_paper(tmp_path, mock)
    with pytest.raises(ControllerError, match="cannot verify cluster arch"):
        ctl.submit("031-run-001", "paper")
    assert mock.submit_calls == []


def test_paper_render_resolves_sif_and_sha_from_lock(tmp_path):
    """F2: with a validated runtime lock, a paper submit resolves the SIF path
    and its SHA from the lock — the single truth — with no caller injection."""
    mock = _MockTransport()
    ctl = make_controller(
        tmp_path,
        mock,
        matclaw_sif=None,
        solution_dir=f"{REMOTE_ROOT}/_solution",
        locked_sif_sha=None,
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    ctl.stage(local_run, "031-run-001")
    job_id = ctl.submit("031-run-001", "paper")
    assert job_id == "1001"
    assert len(mock.submit_calls) == 1
    script_path = Path(mock.submit_calls[0][0])
    rendered = script_path.read_text(encoding="utf-8")
    lock = valid_runtime_lock()
    assert lock["sif_path_remote"] in rendered  # MATCLAW_SIF from the lock
    assert TEST_LOCKED_SIF_SHA in rendered  # locked-SHA infra gate from the lock
    assert "__MATCLAW_LOCKED_SIF_SHA__" not in rendered


def test_paper_render_requires_locked_sif_sha_without_lock(tmp_path):
    """Still fail-closed when neither the caller nor the lock provides a SHA:
    a lock that embeds no SIF SHA is ineligible, so the submit never renders."""
    mock = _MockTransport()
    lock = valid_runtime_lock()
    lock.pop("sif_sha256")  # ineligible lock
    lock_path = tmp_path / "bad-lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    ctl = make_controller(
        tmp_path,
        mock,
        matclaw_sif="/public/sif/matclaw.sif",
        solution_dir=f"{REMOTE_ROOT}/_solution",
        locked_sif_sha=None,
        runtime_lock_path=str(lock_path),
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    ctl.stage(local_run, "031-run-001")
    with pytest.raises(ControllerError):
        ctl.submit("031-run-001", "paper")
    assert mock.submit_calls == []


def test_paper_render_contains_locked_sif_sha_and_infra_gates(tmp_path, mock_transport):
    ctl, _ = _rendered_paper(tmp_path, mock_transport)
    text = ctl._render_slurm(f"{REMOTE_ROOT}/031-run-001/run")
    assert TEST_LOCKED_SIF_SHA in text
    assert "__MATCLAW_LOCKED_SIF_SHA__" not in text
    assert "infrastructure_failed" in text
    assert "nvidia-smi" in text
    assert "infra_gates=pass" in text


# --------------------------------------------------------------------------- #
# Case 031/032/033 generalization: fixed CASE_POLICIES facts (ACCEPTING)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case_policy_resolves_facts(case_id):
    """The immutable policy fixes every per-case fact; the template exists."""
    policy = CASE_POLICIES[case_id]
    assert policy.case_id == case_id
    assert policy.run_id_prefix == f"{case_id}-"
    assert policy.job_prefix == f"matclaw-{case_id}"
    assert policy.remote_root.endswith(f"/matclaw-{case_id}")
    assert policy.solution_dir == f"{policy.remote_root}/_solution"
    assert policy.slurm_template == f"{case_id}_matclaw_gpu.slurm"
    assert policy.allowed_run_kinds == ("probe", "smoke", "paper")
    assert (ROOT / "scripts" / "hpc" / policy.slurm_template).is_file()


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_controller_resolves_per_case_facts(tmp_path, case_id):
    """The controller resolves its case facts straight from the fixed table."""
    ctl = make_controller(tmp_path, _MockTransport(), case_id=case_id)
    policy = CASE_POLICIES[case_id]
    assert ctl.case_id == case_id
    assert ctl.remote_root == policy.remote_root
    assert ctl.solution_dir == policy.solution_dir
    assert ctl.slurm_template == ROOT / "scripts" / "hpc" / policy.slurm_template
    assert ctl._policy is policy


@pytest.mark.parametrize("case_id", CASE_IDS)
@pytest.mark.parametrize("run_kind", ("smoke", "paper"))
def test_paper_submit_ok_per_case(tmp_path, case_id, run_kind):
    """paper/smoke stage+submit works for every case with the shared runtime
    lock present, and the job carries the case-policy name + solution dir."""
    mock = _MockTransport()
    ctl = make_controller(
        tmp_path,
        mock,
        case_id=case_id,
        matclaw_sif="/public/sif/matclaw.sif",
    )
    policy = CASE_POLICIES[case_id]
    local_run = tmp_path / "run"
    local_run.mkdir()
    ctl.stage(local_run, f"{case_id}-run-001")
    job_id = ctl.submit(f"{case_id}-run-001", run_kind)
    assert job_id == "1001"
    script_path = Path(mock.submit_calls[0][0])
    rendered = script_path.read_text(encoding="utf-8")
    assert f"#SBATCH --job-name={policy.job_prefix}-{run_kind}" in rendered
    assert f"SOLUTION_DIR={shlex.quote(policy.solution_dir)}" in rendered


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_probe_job_name_per_case(tmp_path, case_id):
    ctl = make_controller(tmp_path, _MockTransport(), case_id=case_id)
    text = ctl._render_probe_slurm()
    assert f"#SBATCH --job-name=matclaw-{case_id}-probe" in text


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_probe_submit_allowed_without_runtime_lock(tmp_path, case_id):
    """The probe canary is self-contained — it never needs the shared runtime
    lock, so it must succeed even when the lock is absent."""
    mock = _MockTransport()
    ctl = make_controller(
        tmp_path,
        mock,
        case_id=case_id,
        runtime_lock_path=str(tmp_path / "missing-lock.json"),
    )
    job_id = ctl.submit(f"{case_id}-run-001", "probe")
    assert job_id == "1001"
    assert len(mock.submit_calls) == 1


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_all_case_render_pins_profile_slurm_policy(tmp_path, case_id):
    """Every case's rendered paper script pins the profile's partition/gres
    exactly once and leaves no template placeholder behind."""
    ctl = make_controller(
        tmp_path,
        _MockTransport(),
        case_id=case_id,
        matclaw_sif="/public/sif/matclaw.sif",
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    remote_run = ctl.stage(local_run, f"{case_id}-run-001")
    text = ctl._render_slurm(remote_run, run_kind="paper")
    assert text.count("#SBATCH --partition=gpu") == 1
    assert text.count("#SBATCH --gres=gpu:1") == 1
    for tok in (
        "__SBATCH_PARTITION__", "__SBATCH_GRES__", "__RUN_DIR__",
        "__SOLUTION_DIR__", "__MATCLAW_SIF__", "__MATCLAW_ENV_FLAGS__",
    ):
        assert tok not in text


# --------------------------------------------------------------------------- #
# Case 031/032/033 generalization: fail-closed rejections
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_case", ["034", "031x", "031 ", "case031"])
def test_unknown_case_id_rejected(tmp_path, bad_case):
    with pytest.raises(ControllerError, match="unknown MatClaw case"):
        MatClawHpcController(
            transport=_MockTransport(),
            workspace=str(tmp_path / "ws"),
            case_id=bad_case,
        )


def test_missing_case_policy_mount_rejected(tmp_path):
    """F3: with no explicit case_id, the controller needs the mounted
    case-policy document — a missing mount fails closed before anything runs."""
    with pytest.raises(ControllerError, match="case-policy mount not found"):
        MatClawHpcController(
            transport=_MockTransport(),
            workspace=str(tmp_path / "ws"),
            case_policy_path=str(tmp_path / "nope-case-policy.json"),
        )


def test_trusted_facts_reject_env_matclaw_case(monkeypatch):
    """F3: MATCLAW_CASE in the process env fails closed — the case is fixed by
    the read-only mount, and even an env equal to the active case is a stale or
    leaky caller signal."""
    monkeypatch.setenv("MATCLAW_CASE", "033")
    with pytest.raises(ControllerError, match="MATCLAW_CASE"):
        MatClawHpcController(
            transport=_MockTransport(), profile=make_test_profile(), case_id="031"
        )


def _write_case_policy(tmp_path: Path, case_id: str) -> Path:
    """Write a valid case-policy document (a projection of CASE_POLICIES)."""
    policy = CASE_POLICIES[case_id]
    path = tmp_path / "case-policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": policy.case_id,
                "run_id_prefix": policy.run_id_prefix,
                "solution_dir": policy.solution_dir,
                "slurm_template": policy.slurm_template,
                "job_prefix": policy.job_prefix,
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case_policy_mount_drives_case(tmp_path, case_id):
    """F3: the mounted case-policy pins the active case — the agent cannot
    choose it via env, and the per-case facts follow the pinned case."""
    path = _write_case_policy(tmp_path, case_id)
    ctl = MatClawHpcController(
        transport=_MockTransport(),
        workspace=str(tmp_path / "ws"),
        case_policy_path=str(path),
    )
    assert ctl.case_id == case_id
    assert ctl.solution_dir == CASE_POLICIES[case_id].solution_dir
    assert ctl._policy.slurm_template == CASE_POLICIES[case_id].slurm_template


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_case_policy_mount_mismatch_rejected(tmp_path, case_id):
    """A mounted document whose facts disagree with the immutable policy fails
    closed (a stale or tampered mount never silently reconfigures the case)."""
    policy = CASE_POLICIES[case_id]
    path = tmp_path / "case-policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "case_id": policy.case_id,
                "run_id_prefix": policy.run_id_prefix,
                "solution_dir": "/evil/other/solution",
                "slurm_template": policy.slurm_template,
                "job_prefix": policy.job_prefix,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ControllerError, match="mismatches the immutable policy"):
        MatClawHpcController(
            transport=_MockTransport(),
            workspace=str(tmp_path / "ws"),
            case_policy_path=str(path),
        )


def test_case_policy_mount_unknown_case_rejected(tmp_path):
    path = tmp_path / "case-policy.json"
    path.write_text(
        json.dumps({"schema_version": 1, "case_id": "999"}), encoding="utf-8"
    )
    with pytest.raises(ControllerError, match="unknown case"):
        MatClawHpcController(
            transport=_MockTransport(),
            workspace=str(tmp_path / "ws"),
            case_policy_path=str(path),
        )


@pytest.mark.parametrize(
    "case_id, other_id",
    [
        ("031", "032-run-001"),
        ("031", "033-run-001"),
        ("032", "031-run-001"),
        ("032", "033-run-001"),
        ("033", "031-run-001"),
        ("033", "032-run-001"),
    ],
)
def test_run_id_with_other_case_prefix_rejected(tmp_path, case_id, other_id):
    ctl = make_controller(tmp_path, _MockTransport(), case_id=case_id)
    with pytest.raises(ControllerError, match="Case"):
        ctl.stage(tmp_path / "run", other_id)


def test_rejects_caller_supplied_remote_root(tmp_path, mock_transport):
    with pytest.raises(ControllerError, match="remote_root"):
        make_controller(tmp_path, mock_transport, remote_root="/evil/root")


def test_rejects_caller_supplied_solution_dir(tmp_path, mock_transport):
    with pytest.raises(ControllerError, match="solution_dir"):
        make_controller(tmp_path, mock_transport, solution_dir="/evil/solution")


def test_cli_rejects_case_specific_override_flags(monkeypatch):
    """No CLI flag can override the fixed case facts / Slurm policy."""
    monkeypatch.setenv("MATCLAW_CASE", "031")
    for bad_flag in (
        "--sif", "--remote-root", "--solution", "--partition",
        "--gpus", "--account", "--qos", "--host",
    ):
        rc = main([
            "submit", "--remote-run-id", "031-x", "--run-kind", "probe",
            bad_flag, "value",
        ])
        assert rc != 0, f"{bad_flag} must be rejected"


def test_submit_rejects_disallowed_env_keys(tmp_path, mock_transport):
    ctl = make_controller(tmp_path, mock_transport)
    with pytest.raises(ControllerError, match="MATCLAW_PROFILE"):
        ctl.submit("031-run-001", "probe", env={"EVIL": "1"})
    assert mock_transport.submit_calls == []


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_paper_submit_fails_closed_without_runtime_lock(tmp_path, case_id):
    mock = _MockTransport()
    ctl = make_controller(
        tmp_path,
        mock,
        case_id=case_id,
        matclaw_sif="/public/sif/matclaw.sif",
        runtime_lock_path=str(tmp_path / "missing-lock.json"),
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    ctl.stage(local_run, f"{case_id}-run-001")
    with pytest.raises(ControllerError, match="runtime lock"):
        ctl.submit(f"{case_id}-run-001", "paper")
    assert mock.submit_calls == []


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_paper_submit_fails_closed_on_ineligible_lock(tmp_path, case_id):
    mock = _MockTransport()
    lock = valid_runtime_lock()
    lock["formal_eligible"] = False
    del lock["qualification"]
    lock_path = tmp_path / "runtime-lock-ineligible.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    ctl = make_controller(
        tmp_path,
        mock,
        case_id=case_id,
        matclaw_sif="/public/sif/matclaw.sif",
        runtime_lock_path=str(lock_path),
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    ctl.stage(local_run, f"{case_id}-run-001")
    with pytest.raises(ControllerError, match="runtime lock ineligible"):
        ctl.submit(f"{case_id}-run-001", "paper")
    assert mock.submit_calls == []


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_paper_submit_missing_receipt_mount_uses_embedded(tmp_path, case_id):
    """F2: an absent receipt mount is not a failure — the lock's embedded
    receipt (already required valid by the lock gate) is used."""
    mock = _MockTransport()
    ctl = make_controller(
        tmp_path,
        mock,
        case_id=case_id,
        matclaw_sif="/public/sif/matclaw.sif",
        qualification_receipt_path=str(tmp_path / "nope-receipt.json"),
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    ctl.stage(local_run, f"{case_id}-run-001")
    job_id = ctl.submit(f"{case_id}-run-001", "paper")
    assert job_id == "1001"
    assert len(mock.submit_calls) == 1


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_paper_submit_fails_closed_on_unreadable_receipt_mount(tmp_path, case_id):
    """F2: an existing receipt mount that is not valid JSON fails closed."""
    mock = _MockTransport()
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text("{ not json", encoding="utf-8")
    ctl = make_controller(
        tmp_path,
        mock,
        case_id=case_id,
        matclaw_sif="/public/sif/matclaw.sif",
        qualification_receipt_path=str(receipt_path),
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    ctl.stage(local_run, f"{case_id}-run-001")
    with pytest.raises(ControllerError, match="qualification receipt unreadable"):
        ctl.submit(f"{case_id}-run-001", "paper")
    assert mock.submit_calls == []


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_paper_submit_rejects_mismatched_qualification_receipt(tmp_path, case_id):
    mock = _MockTransport()
    receipt = valid_qualification_receipt()
    receipt["gpu"] = "Tesla V100"
    receipt["gpu_name"] = "Tesla V100"
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    ctl = make_controller(
        tmp_path,
        mock,
        case_id=case_id,
        matclaw_sif="/public/sif/matclaw.sif",
        qualification_receipt_path=str(receipt_path),
    )
    local_run = tmp_path / "run"
    local_run.mkdir()
    ctl.stage(local_run, f"{case_id}-run-001")
    with pytest.raises(ControllerError, match="does not match the locked SIF"):
        ctl.submit(f"{case_id}-run-001", "paper")
    assert mock.submit_calls == []


@pytest.mark.parametrize(
    "case_id, other_id",
    [
        ("031", "032-run-001"),
        ("032", "033-run-001"),
        ("033", "031-run-001"),
    ],
)
def test_cross_case_fetch_rejected(tmp_path, case_id, other_id):
    mock = _MockTransport(status_result="COMPLETED")
    ctl = make_controller(tmp_path, mock, case_id=case_id)
    local = tmp_path / "local"
    local.mkdir()
    with pytest.raises(ControllerError, match="must be a Case"):
        ctl.fetch(other_id, local)


def test_infrastructure_failure_never_invokes_verifier_or_reward():
    """The controller is fail-closed: an infra fault is classified on the node
    (INFRA_FAILED.txt / infrastructure_failed) and the scientific verifier /
    reward path is not reachable from the controller at all."""
    src = (ROOT / "scripts" / "matclaw_hpc_controller.py").read_text(encoding="utf-8")
    for needle in (
        "verifier(", "verifier.py", "import verifier", "from verifier",
        "def verify", "reward(", "reward.txt", "reward =",
    ):
        assert needle not in src, f"controller must not reference {needle!r}"
    assert "infrastructure_failed" in src
    # The only verifier in the controller is the artifact-hash integrity check
    # (manifest SHA-256 after fetch) — never the scientific verifier.
    assert "def _verify_manifest" in src
    for case_id in CASE_IDS:
        template = (
            ROOT / "scripts" / "hpc" / f"{case_id}_matclaw_gpu.slurm"
        ).read_text(encoding="utf-8")
        assert "infrastructure_failed" in template
        assert "INFRA_FAILED.txt" in template
        assert "infra_gates=pass" in template
