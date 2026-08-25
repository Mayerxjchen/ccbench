"""HPC JobSpec contract: schema + security validation.

A JobSpec is the only thing a benchmark site hands to the bench-hpc client.
It must be:

- pure argv (never a shell string),
- self-identifying (idempotency_key, digest-pinned runtime),
- path-safe (relative inputs/outputs, no ``..``, no workspace escape),
- within the platform's resource profile.

These tests pin the validation failures the trusted gateway relies on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dftworld_bench.hpc.job import JobError, JobResources, JobSpec

TMP = Path(__file__).resolve().parents[2] / "tmp"


def _write_job(tmp_path: Path, text: str, name: str = "job.yaml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _valid_yaml() -> str:
    return """\
schema_version: 1
idempotency_key: "run-20260818-001"
runtime: "mlip-compute@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
command:
  - cp2k
  - -i
  - input.inp
  - -o
  - output.out
resources:
  cpus: 16
  memory_gb: 64
  gpus: 1
  walltime_minutes: 120
environment:
  OMP_NUM_THREADS: "8"
inputs:
  - input.inp
  - data/geom.xyz
outputs:
  - output.out
  - trajectory/
"""


def test_load_accepts_valid_spec(tmp_path: Path) -> None:
    spec = JobSpec.load(_write_job(tmp_path, _valid_yaml()))
    assert spec.idempotency_key == "run-20260818-001"
    assert spec.command == ("cp2k", "-i", "input.inp", "-o", "output.out")
    assert spec.resources == JobResources(cpus=16, memory_gb=64, gpus=1, walltime_minutes=120)
    assert spec.environment == {"OMP_NUM_THREADS": "8"}
    assert spec.inputs == ("input.inp", "data/geom.xyz")
    assert spec.outputs == ("output.out", "trajectory/")


def test_reject_shell_string_command(tmp_path: Path) -> None:
    path = _write_job(
        tmp_path,
        _valid_yaml().replace("  - cp2k\n  - -i\n  - input.inp\n  - -o\n  - output.out",
                              '  "cp2k -i input.inp -o output.out"'),
    )
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_reject_empty_command(tmp_path: Path) -> None:
    path = _write_job(tmp_path, _valid_yaml().replace("  - cp2k\n  - -i\n", "  - \"\"\n  - -i\n"))
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_reject_missing_idempotency_key(tmp_path: Path) -> None:
    path = _write_job(tmp_path, _valid_yaml().replace('idempotency_key: "run-20260818-001"\n', ""))
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_reject_runtime_without_digest(tmp_path: Path) -> None:
    path = _write_job(
        tmp_path,
        _valid_yaml().replace(
            '"mlip-compute@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
            '"mlip-compute:latest"',
        ),
    )
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_reject_absolute_input(tmp_path: Path) -> None:
    path = _write_job(tmp_path, _valid_yaml().replace("  - input.inp\n", "  - /etc/passwd\n"))
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_reject_absolute_output(tmp_path: Path) -> None:
    path = _write_job(tmp_path, _valid_yaml().replace("  - output.out\n", "  - /tmp/output.out\n"))
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_reject_dotdot_input(tmp_path: Path) -> None:
    path = _write_job(tmp_path, _valid_yaml().replace("  - data/geom.xyz\n", "  - ../secret.xyz\n"))
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_reject_dotdot_output(tmp_path: Path) -> None:
    path = _write_job(tmp_path, _valid_yaml().replace("  - trajectory/\n", "  - ../trajectory/\n"))
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_reject_unknown_resource(tmp_path: Path) -> None:
    path = _write_job(
        tmp_path,
        _valid_yaml().replace("  walltime_minutes: 120\n", "  walltime_minutes: 120\n  shm_mb: 1024\n"),
    )
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_reject_noninteger_resource(tmp_path: Path) -> None:
    path = _write_job(tmp_path, _valid_yaml().replace("  cpus: 16\n", "  cpus: 16.5\n"))
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_reject_walltime_outside_profile(tmp_path: Path) -> None:
    profile = {"name": "mlip-compute", "max_cpus": 32, "max_memory_gb": 128,
               "max_gpus": 1, "max_walltime_minutes": 60}
    path = _write_job(tmp_path, _valid_yaml())  # job asks 120 > profile max 60
    with pytest.raises(JobError, match="walltime"):
        JobSpec.load(path, platform_profile=profile)


def test_walltime_equal_to_profile_maximum_is_accepted(tmp_path: Path) -> None:
    profile = {"name": "mlip-compute", "max_cpus": 32, "max_memory_gb": 128,
               "max_gpus": 1, "max_walltime_minutes": 120}
    path = _write_job(tmp_path, _valid_yaml())
    spec = JobSpec.load(path, platform_profile=profile)
    assert spec.resources.walltime_minutes == 120


def test_reject_resources_beyond_profile(tmp_path: Path) -> None:
    profile = {"name": "mlip-compute", "max_cpus": 32, "max_memory_gb": 64,
               "max_gpus": 1, "max_walltime_minutes": 120}
    path = _write_job(tmp_path, _valid_yaml().replace("  memory_gb: 64\n", "  memory_gb: 65\n"))
    with pytest.raises(JobError, match="memory_gb"):
        JobSpec.load(path, platform_profile=profile)


def test_reject_output_glob_escaping_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = _write_job(tmp_path, _valid_yaml().replace("  - trajectory/\n", "  - \"../outside/*.out\"\n"))
    with pytest.raises(JobError):
        JobSpec.load(path, workspace_root=workspace)


def test_output_glob_inside_workspace_is_accepted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "results").mkdir(parents=True)
    path = _write_job(
        tmp_path,
        _valid_yaml().replace("  - trajectory/\n", "  - \"results/*.out\"\n"),
    )
    spec = JobSpec.load(path, workspace_root=workspace)
    assert spec.outputs == ("output.out", "results/*.out")


def test_load_rejects_unknown_top_level_keys(tmp_path: Path) -> None:
    path = _write_job(tmp_path, _valid_yaml() + "not_a_real_field: 42\n")
    with pytest.raises(JobError):
        JobSpec.load(path)


def test_to_dict_is_a_clean_json_payload(tmp_path: Path) -> None:
    spec = JobSpec.load(_write_job(tmp_path, _valid_yaml()))
    payload = spec.to_dict()
    assert payload["schema_version"] == 1
    assert payload["idempotency_key"] == "run-20260818-001"
    assert payload["resources"] == {"cpus": 16, "memory_gb": 64, "gpus": 1, "walltime_minutes": 120}
    assert payload["command"] == ["cp2k", "-i", "input.inp", "-o", "output.out"]
    assert set(payload) == {
        "schema_version", "idempotency_key", "runtime", "command", "resources",
        "environment", "inputs", "outputs",
    }


def test_runtime_pattern_accepts_image_plus_digest() -> None:
    assert JobSpec.RUNTIME_RE.match("mlip-compute@sha256:" + "a" * 64) is not None
    assert JobSpec.RUNTIME_RE.match("mlip-compute:latest") is None
    assert JobSpec.RUNTIME_RE.match("mlip-compute@sha256:" + "a" * 63) is None
