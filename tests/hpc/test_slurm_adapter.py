"""SlurmAdapter: map bench-hpc JobSpec onto a real Slurm site.

The adapter is the site-owned half of the trusted-gateway contract. It takes a
validated JobSpec and a validated site config and produces scheduler
directives + a contained scientific script. The Candidate can never influence
partition/account/gres — those come only from the validated site/platform
config — and never injects ``#SBATCH`` directives (argv is serialized, not
parsed).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dftworld_bench.hpc.adapters.slurm import SlurmAdapter, SlurmAdapterError
from scripts.ablation.transport.slurm_transport import JobState

DIGEST = "mlip-compute@sha256:" + "a" * 64

SITE = {
    "site": "<site-alias>",
    "gateway_url": "https://gw.example.test",
    "run_token": "0123456789abcdef0123456789abcdef",
    "ssh_alias": "<site-alias>",
    "scratch": "/data/bench",
    "account": "mlip-bench",
    "platform_profile": {
        "name": "<site-alias>",
        "default_queue": "gpu",
        "queues": [
            {"name": "gpu", "max_cpus": 32, "max_memory_gb": 128,
             "max_gpus": 1, "max_walltime_minutes": 1440},
        ],
    },
}


def _spec(**overrides) -> dict:
    spec = {
        "schema_version": 1,
        "idempotency_key": "job-001",
        "runtime": DIGEST,
        "command": ["cp2k", "-i", "input.inp", "-o", "output.out"],
        "resources": {"cpus": 8, "memory_gb": 32, "gpus": 1, "walltime_minutes": 120},
        "inputs": ["input.inp"],
        "outputs": ["output.out"],
    }
    spec.update(overrides)
    return spec


class _FakeTransport:
    def __init__(self):
        self.submits: list[tuple] = []
        self._status = {}
        self.cancelled: list[str] = []

    def submit(self, script, opts):
        self.submits.append((script, opts))
        self.last_id = str(len(self.submits) + 1000)
        return self.last_id

    def status(self, job_id):
        return self._status.get(job_id, JobState.RUNNING)

    def log(self, job_id, tail=None):
        return f"log-{job_id}"

    def cancel(self, job_id):
        self.cancelled.append(job_id)

    def fetch(self, remote_paths, local_dir):
        return [Path(local_dir) / "output.out"]

    def alloc_tres(self, job_id):
        return ""

    def remote_arch(self):
        return "x86_64"


def _adapter(transport=None, **kwargs):
    return SlurmAdapter(SITE, transport or _FakeTransport(), case_id="water64", **kwargs)


def test_rejects_missing_site_fields() -> None:
    bad = dict(SITE, scratch="")
    with pytest.raises(SlurmAdapterError):
        SlurmAdapter(bad, _FakeTransport(), case_id="water64")


def test_rejects_unvalidated_site_config() -> None:
    bad = dict(SITE)
    del bad["platform_profile"]
    with pytest.raises(SlurmAdapterError):
        SlurmAdapter(bad, _FakeTransport(), case_id="water64")


def test_capabilities_come_from_platform_profile() -> None:
    cap = _adapter().capabilities()
    assert cap["adapter"] == "slurm"
    assert cap["queues"] == ["gpu"]
    assert cap["partition"] == "gpu"
    assert cap["gres"] == "gpu:1"


def test_directives_take_partition_account_only_from_site_config() -> None:
    rendered = _adapter().render(_spec(), job_id="job-0001", run_id="run-1", operation_id="test-op")
    opts = rendered.opts
    assert opts.partition == "gpu"  # from platform_profile.default_queue, never spec
    assert opts.account == "mlip-bench"  # from site config, never spec
    assert opts.gres == "gpu:1"  # <site-alias> profile, exactly one GPU
    assert opts.cpus_per_task == "8"
    assert opts.time == "02:00:00"  # walltime_minutes 120


def test_script_cannot_inject_sbatch_directives() -> None:
    spec = _spec(command=["cp2k", "#SBATCH", "--partition=backdoor", "-i", "input.inp"])
    rendered = _adapter().render(spec, job_id="job-0001", run_id="run-1", operation_id="test-op")
    # directives live in opts (typed values); the script body carries argv only
    assert rendered.opts.partition == "gpu"
    assert "backdoor" not in rendered.opts.to_argv()  # never lifted from argv
    # a hostile token must never start a directive comment line ...
    assert not any(line.startswith("#SBATCH") for line in rendered.script.splitlines())
    # ... it survives only as shlex-quoted apptainer argv, so the job literally
    # runs `cp2k '#SBATCH' --partition=backdoor ...` with no scheduler effect
    assert "'#SBATCH'" in rendered.script
    assert "--partition=backdoor" in rendered.script


def test_script_uses_explicit_run_workspace() -> None:
    rendered = _adapter().render(_spec(), job_id="job-0001", run_id="run-1", operation_id="test-op")
    workspace = rendered.workspace
    assert workspace == "/data/bench/water64/run-1/job-0001"
    assert rendered.opts.chdir == workspace
    assert rendered.opts.output == f"{workspace}/stdout.log"
    assert rendered.opts.error == f"{workspace}/stderr.log"


def test_script_launches_frozen_runtime_with_containment() -> None:
    rendered = _adapter().render(_spec(), job_id="job-0001", run_id="run-1", operation_id="test-op")
    assert rendered.script.startswith("#!/bin/bash")
    assert "--contain" in rendered.script
    assert "--cleanenv" in rendered.script
    assert "--no-home" in rendered.script
    assert DIGEST in rendered.script  # digest-pinned runtime, never :latest
    joined = "cp2k -i input.inp -o output.out"
    assert joined in rendered.script


def test_script_binds_only_declared_io_paths() -> None:
    rendered = _adapter().render(
        _spec(inputs=["input.inp"], outputs=["results/*.out"]),
        job_id="job-0001",
        run_id="run-1",
        operation_id="test-op",
    )
    binds = re.findall(r"--bind\s+(\S+)", rendered.script)
    assert binds, "expected at least the workspace rw bind"
    for bind in binds:
        host_path = bind.split(":", 1)[0]
        assert host_path.startswith("/data/bench/water64/run-1/job-0001"), bind
    assert "/data/bench/water64/run-1/job-0001/input.inp:input.inp:ro" in binds
    assert "/data/bench/water64/run-1/job-0001/results:/workspace-results" in binds
    assert "home" not in " ".join(binds).lower()


def test_submit_forwards_script_and_directives() -> None:
    transport = _FakeTransport()
    adapter = _adapter(transport)
    result = adapter.submit(_spec(), run_id="run-1", operation_id="OP-1")
    job_id = result["job_id"]
    assert job_id == "job-0001"
    assert transport.submits, "adapter must hand the script to the transport"
    script, opts = transport.submits[0]
    assert opts.partition == "gpu"


def test_duplicate_idempotency_key_returns_original_job() -> None:
    transport = _FakeTransport()
    adapter = _adapter(transport)
    first = adapter.submit(_spec(), run_id="run-1", operation_id="OP-1")
    second = adapter.submit(_spec(), run_id="run-1", operation_id="OP-1")
    assert first["job_id"] == second["job_id"]
    assert len(transport.submits) == 1  # launched once


def test_status_maps_slurm_state_to_bench_state() -> None:
    transport = _FakeTransport()
    adapter = _adapter(transport)
    result = adapter.submit(_spec(), run_id="run-1", operation_id="OP-1")
    job_id = result["job_id"]
    slurm_id = transport.last_id
    for slurm_state, bench_state in (
        (JobState.PENDING, "QUEUED"),
        (JobState.RUNNING, "RUNNING"),
        (JobState.COMPLETED, "SUCCEEDED"),
        (JobState.FAILED, "FAILED"),
        (JobState.CANCELLED, "CANCELLED"),
        (JobState.UNKNOWN, "LOST"),
    ):
        transport._status[slurm_id] = slurm_state
        assert adapter.status(job_id)["state"] == bench_state


def test_fetch_requires_success() -> None:
    transport = _FakeTransport()
    adapter = _adapter(transport)
    result = adapter.submit(_spec(), run_id="run-1", operation_id="OP-1")
    job_id = result["job_id"]
    transport._status[transport.last_id] = JobState.RUNNING
    with pytest.raises(SlurmAdapterError, match="before success"):
        adapter.fetch(job_id)
    transport._status[transport.last_id] = JobState.COMPLETED
    assert adapter.fetch(job_id)["state"] == "SUCCEEDED"


def test_cancel_delegates_to_transport() -> None:
    transport = _FakeTransport()
    adapter = _adapter(transport)
    result = adapter.submit(_spec(), run_id="run-1", operation_id="OP-1")
    adapter.cancel(result["job_id"])
    assert transport.cancelled


def test_usage_counts_submitted_jobs() -> None:
    adapter = _adapter()
    adapter.submit(_spec(), run_id="run-1", operation_id="OP-1")
    adapter.submit(_spec(idempotency_key="job-002"), run_id="run-2", operation_id="OP-2")
    assert adapter.usage()["jobs"] == 2


# -- operation identity (Task 10) -----------------------------------------


def test_operation_id_embedded_and_findable() -> None:
    transport = _FakeTransport()
    adapter = _adapter(transport)
    result = adapter.submit(_spec(), run_id="run-1", operation_id="OP-1")
    job_id = result["job_id"]
    assert adapter.find_by_operation_id("run-1", "OP-1") == job_id
    assert adapter.find_by_operation_id("run-1", "OP-2") is None
    assert adapter.find_by_operation_id("run-2", "OP-1") is None
    script, opts = transport.submits[0]
    # operation identity rides on the scheduler directive and the script marker
    assert "--comment=bench:water64:run-1:OP-1" in opts.to_argv()
    assert ".bench-operation-id" in script
    assert "OP-1" in script


def test_duplicate_operation_id_returns_prior_job_without_reschedule() -> None:
    transport = _FakeTransport()
    adapter = _adapter(transport)
    first = adapter.submit(_spec(), run_id="run-1", operation_id="OP-1")
    second = adapter.submit(
        _spec(idempotency_key="job-002"), run_id="run-1", operation_id="OP-1"
    )
    assert first["job_id"] == second["job_id"]
    assert second["duplicate"] is True
    assert len(transport.submits) == 1  # launched once


def test_dict_input_binding_uses_resolved_source(tmp_path: Path) -> None:
    staged = tmp_path / "ws" / "input.dat"
    staged.parent.mkdir(parents=True)
    staged.write_text("x")
    adapter = _adapter()
    spec = _spec(inputs=[{"source": str(staged), "destination": "input.dat"}])
    rendered = adapter.render(spec, job_id="job-0001", run_id="run-1", operation_id="test-op")
    assert f"{staged}:input.dat:ro" in rendered.script
