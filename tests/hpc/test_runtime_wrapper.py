"""The trusted runtime wrapper: deterministic, contained, Candidate-proof.

``render_runtime_wrapper`` turns a sealed ExecutionRequestV2 + SiteProfile
into one Apptainer exec script with exactly one rw run bind. Every attempt by
a Candidate to widen the container — extra binds, disabled containment,
host paths, runtime replacement, SBATCH injection, HOME access, blocked env,
nested containerization — must be rejected before rendering.
"""

from __future__ import annotations

import hashlib

import pytest

from ccbench.hpc.request import ExecutionRequestV2
from ccbench.hpc.site_profile import HpcSiteProfile
from ccbench.hpc.runtime_wrapper import (
    RuntimeWrapperError,
    render_runtime_wrapper,
)

DIGEST = "mlip-compute@sha256:" + "a" * 64


def _request(command: list[str], *, env: dict | None = None) -> ExecutionRequestV2:
    body = b"&GLOBAL\n&END\n"
    payload = {
        "schema_version": 2,
        "operation_id": "cp2k-round-01",
        "attempt": 1,
        "compute_class": "gpu",
        "runtime": DIGEST,
        "command": command,
        "resources": {"cpus": 8, "memory_gb": 32, "gpus": 1, "walltime_minutes": 60},
        "inputs": [{"path": "input.inp", "sha256": hashlib.sha256(body).hexdigest(),
                    "size_bytes": len(body)}],
        "outputs": ["out/"],
    }
    if env:
        payload["environment"] = env
    return ExecutionRequestV2.from_dict(payload)


def _site() -> HpcSiteProfile:
    return HpcSiteProfile.from_dict(
        {
            "schema_version": 1,
            "site_id": "site-v1",
            "scheduler": "slurm",
            "connection": {
                "credential_profile_id": "k1",
                "target_binding": "ssh://redacted:22",
                "remote_user": "svc",
                "remote_root_policy": "/data/bench/{run_id}",
            },
            "account": "acct-blocked",
            "queues": {
                "gpu": {"partition": "gpu-q", "qos": "long", "max_cpus": 32,
                        "max_memory_gb": 128, "max_gpus": 1,
                        "max_walltime_minutes": 1440},
            },
            "runtime_policy": {
                "requires_apptainer": True,
                "runtime_store": "/data/bench/runtimes",
                "gres_template": "--gres=gpu:1",
            },
        }
    )


def test_happy_path_renders_single_bind_and_sealed_argv():
    request = _request(["cp2k", "-i", "input.inp"])
    rendered = render_runtime_wrapper(request, _site(), "/data/bench/run-1")
    script = rendered.script
    # Exactly one rw bind: the run dir at /workspace.
    assert script.count("--bind") == 1
    assert "--bind /data/bench/run-1:/workspace:rw" in script
    # The pinned runtime digest appears verbatim; containment flags present.
    assert DIGEST in script
    assert "--cleanenv" in script
    # Command rendered via shlex.join, never a shell string.
    assert "cp2k -i input.inp" in script


def test_reject_nested_container_invocation():
    for argv in (
        ["apptainer", "exec", "x.sif", "ls"],
        ["singularity", "run", "y.sif"],
        ["/usr/bin/apptainer", "shell"],
    ):
        with pytest.raises(RuntimeWrapperError, match="NESTED"):
            render_runtime_wrapper(_request(argv), _site(), "/tmp/run")


def test_reject_bind_and_containment_smuggling():
    with pytest.raises(RuntimeWrapperError, match="BIND"):
        render_runtime_wrapper(
            _request(["tool", "--bind", "/home/x:/x"]), _site(), "/tmp/run"
        )
    with pytest.raises(RuntimeWrapperError, match="MOUNT"):
        render_runtime_wrapper(
            _request(["tool", "--no-mount", "home"]), _site(), "/tmp/run"
        )


def test_reject_sbatch_directive_injection():
    with pytest.raises(RuntimeWrapperError, match="SBATCH"):
        render_runtime_wrapper(
            _request(["bash", "-c", "#SBATCH --partition=secret"]), _site(), "/r"
        )


def test_reject_blocked_environment_exports():
    for env in (
        {"HOME": "/hacker"},
        {"LD_PRELOAD": "/evil.so"},
        {"APPTAINERENV_FOO": "bar"},
        {"SINGULARITYENV_BAR": "baz"},
    ):
        with pytest.raises(RuntimeWrapperError):
            render_runtime_wrapper(_request(["tool"], env=env), _site(), "/r")


def test_reject_host_workspace_escape_paths_in_argv():
    with pytest.raises(RuntimeWrapperError, match="HOST PATH"):
        render_runtime_wrapper(
            _request(["tool", "/public/home/<site-user>/old-solution/pot.dat"]),
            _site(),
            "/data/bench/run-1",
        )


def test_reject_runtime_substitution_attempt():
    """The wrapper renders the sealed digest; a tag-shaped runtime can never
    reach it because requests are digest-pinned upstream."""
    request = _request(["cp2k"])
    rendered = render_runtime_wrapper(request, _site(), "/r")
    assert ":latest" not in rendered.script
    assert "@sha256:" in rendered.script


def test_slurm_adapter_uses_wrapper_when_installed():
    """With the trusted wrapper installed, the adapter's sbatch script carries
    exactly one bind and drops the legacy per-input ro binds."""
    from tests.hpc.test_slurm_adapter import SITE, _FakeTransport
    from ccbench.hpc.adapters.slurm import SlurmAdapter

    captured = {}

    def fake_wrapper(spec, workspace):
        captured["workspace"] = workspace
        return (
            "#!/bin/bash\n"
            f"exec /usr/bin/apptainer exec --cleanenv --contain \\\n"
            f"  --bind {workspace}:/workspace:rw \\\n"
            f"  {spec['runtime']} \\\n"
            "  cp2k\n"
        )

    adapter = SlurmAdapter(
        SITE, _FakeTransport(), case_id="water64", runtime_wrapper=fake_wrapper
    )
    rendered = adapter.render(_request(["cp2k"]).to_job_spec_payload(),
                              job_id="job-0001", run_id="run-1",
                              operation_id="op-1")
    assert rendered.script.count("--bind") == 1
    assert ":rw" in rendered.script
    assert captured["workspace"].endswith("job-0001")
