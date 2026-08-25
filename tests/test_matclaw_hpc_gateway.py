"""Trusted-gateway bridge for the MatClaw controller (031-033).

The gateway owns the real scheduler transport; the controller image ships no
SSH client, rsync, key, or config.  These tests pin:

* the HTTP wire protocol (JSON, bearer token, six ops + remote_arch);
* token enforcement (a wrong token is rejected, a valid one is accepted);
* controller-side ``GatewaySlurmTransport`` speaking the same ``SlurmTransport``
  interface as ``SshSlurmTransport`` so the controller domain logic is
  backend-agnostic;
* eval's host-side gateway env injection (controller picks the gateway
  transport when ``BENCH_HPC_GATEWAY_URL`` / ``BENCH_HPC_RUN_TOKEN`` are set,
  and falls back to the SSH profile otherwise).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scripts.ablation.transport.slurm_transport import JobState, SubmitOpts
from scripts.matclaw_hpc_gateway import (
    GATEWAY_URL_ENV,
    RUN_TOKEN_ENV,
    GatewaySlurmTransport,
    MatclawGatewayServer,
)


class _FakeTransport:
    """Scripted stand-in for the scheduler transport the gateway wraps."""

    def __init__(self, workspace: str | None = None) -> None:
        self.calls: list[str] = []
        self.remote_arch_result = "x86_64"
        self.workspace = workspace  # host path of the run workspace

    def remote_arch(self) -> str:
        self.calls.append("arch")
        return self.remote_arch_result

    def stage(self, local_paths, remote_dir):  # type: ignore[no-untyped-def]
        self.calls.append("stage")
        return [f"/remote/{remote_dir}/{Path(p).name}" for p in local_paths]

    def submit(self, script, opts=None):  # type: ignore[no-untyped-def]
        self.calls.append("submit")
        return "4242"

    def status(self, job_id):  # type: ignore[no-untyped-def]
        self.calls.append("status")
        return JobState("COMPLETED")

    def log(self, job_id, tail=None):  # type: ignore[no-untyped-def]
        self.calls.append("log")
        return "log-output"

    def cancel(self, job_id):  # type: ignore[no-untyped-def]
        self.calls.append("cancel")

    def fetch(self, remote_paths, local_dir):  # type: ignore[no-untyped-def]
        self.calls.append("fetch")
        return [Path(local_dir) / Path(remote_paths[0]).name]


@pytest.fixture()
def gateway(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "a.txt").write_text("x")
    (ws / "script.slurm").write_text("#!/bin/bash\necho ok\n")
    transport = _FakeTransport(workspace=str(ws))
    server = MatclawGatewayServer(transport, "tok-run1")
    url = server.start()
    yield transport, server, url
    server.close()


def test_roundtrip_all_ops(gateway) -> None:
    transport, server, url = gateway
    client = GatewaySlurmTransport(url, "tok-run1")

    assert client.remote_arch() == "x86_64"
    assert client.stage(["/app/a.txt"], "run-1") == ["/remote/run-1/a.txt"]
    opts = SubmitOpts(
        partition="gpu",
        gres="gpu:1",
        cpus_per_task="8",
        nodes="1",
        output="slurm-%j.out",
        chdir="/remote/.matclaw",
    )
    assert client.submit("/app/script.slurm", opts) == "4242"
    assert client.status("4242") == JobState("COMPLETED")
    assert client.log("4242") == "log-output"
    client.cancel("4242")
    # fetch's local dir is a gateway-side destination; only the /app-prefixed
    # controller view of it is remapped into the workspace
    assert client.fetch(["/remote/run-1"], "/local/out") == [Path("/local/out/run-1")]

    assert transport.calls == ["arch", "stage", "submit", "status", "log", "cancel", "fetch"]


def test_submit_without_opts(gateway) -> None:
    transport, server, url = gateway
    client = GatewaySlurmTransport(url, "tok-run1")
    assert client.submit("/app/script.slurm") == "4242"
    assert transport.calls == ["submit"]


# -- workspace containment (Task 10) ----------------------------------------


def test_stage_rejects_host_absolute_input(gateway) -> None:
    """The token must never read an arbitrary gateway-host file into a job."""
    _, server, url = gateway
    client = GatewaySlurmTransport(url, "tok-run1")
    with pytest.raises(Exception, match="workspace"):
        client.stage(["/etc/passwd"], "run-1")


def test_submit_rejects_script_outside_workspace(gateway) -> None:
    _, server, url = gateway
    client = GatewaySlurmTransport(url, "tok-run1")
    with pytest.raises(Exception, match="workspace"):
        client.submit("/etc/hosts", SubmitOpts())


def test_stage_without_workspace_root_fails_closed(gateway) -> None:
    _, server, url = gateway
    server._transport.workspace = None
    client = GatewaySlurmTransport(url, "tok-run1")
    with pytest.raises(Exception, match="workspace"):
        client.stage(["/app/a.txt"], "run-1")


def test_wrong_token_rejected(gateway) -> None:
    _, server, url = gateway
    client = GatewaySlurmTransport(url, "wrong-token")
    with pytest.raises(Exception, match="gateway"):
        client.remote_arch()


def test_unknown_op_path_rejected(gateway) -> None:
    _, server, url = gateway
    req = urllib.request.Request(
        url + "/ops/nope",
        data=b"{}",
        headers={"Authorization": "Bearer tok-run1"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 404


def test_malformed_body_rejected(gateway) -> None:
    _, server, url = gateway
    req = urllib.request.Request(
        url + "/ops/status",
        data=b"not-json",
        headers={"Authorization": "Bearer tok-run1"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 400


def test_stage_rejects_unsafe_remote_dir(gateway) -> None:
    _, server, url = gateway
    client = GatewaySlurmTransport(url, "tok-run1")
    with pytest.raises(Exception, match="unsafe|gateway"):
        client.stage(["/app/a.txt"], "../escape")


def test_controller_picks_gateway_transport_when_env_set(monkeypatch, tmp_path) -> None:
    """eval sets BENCH_HPC_GATEWAY_URL/RUN_TOKEN; the controller's
    ``_build_transport`` must return a GatewaySlurmTransport (no SSH)."""
    from scripts.matclaw_hpc_controller import MatClawHpcController
    from scripts.matclaw_hpc_gateway import GatewaySlurmTransport as G

    transport = _FakeTransport()
    server = MatclawGatewayServer(transport, "tok-run1")
    url = server.start()
    monkeypatch.setenv(GATEWAY_URL_ENV, url)
    monkeypatch.setenv(RUN_TOKEN_ENV, "tok-run1")
    try:
        ctl = MatClawHpcController(
            case_id="031",
            workspace=str(tmp_path / "ws"),
            locked_sif_sha="f104256e5b41f9cdc0304fe5d5ab4184c57b507daba564cfd3328f8c60b96d29",
        )
        assert isinstance(ctl._transport, G)
        assert ctl._transport.remote_arch() == "x86_64"
    finally:
        server.close()


def test_controller_falls_back_to_ssh_without_gateway_env(tmp_path) -> None:
    """No gateway env -> the controller builds the SSH transport from the
    cluster profile (the historical path; unit tests exercise it)."""
    from scripts.matclaw_hpc_controller import MatClawHpcController

    ctl = MatClawHpcController(
        case_id="031",
        workspace=str(tmp_path / "ws"),
        locked_sif_sha="f104256e5b41f9cdc0304fe5d5ab4184c57b507daba564cfd3328f8c60b96d29",
    )
    transport = ctl._build_transport()
    assert not isinstance(transport, GatewaySlurmTransport)
    assert transport.ssh.host == "<site-alias>"  # DEFAULT_PROFILE ssh host


def test_controller_local_container_never_requests_gpu() -> None:
    """GPU lives only on the remote Slurm job (``--gres`` from the profile);
    the local controller container must not pass ``--gpus device=0``.

    Task 11: dispatch is by execution class — the HpcExecutor (resolved from
    ``task.execution_class``) pins ``context.local_gpus = 0``; eval feeds that
    to the container backend, so ``docker_gpu_args`` -> [] for controller cases
    without any case-name branch.
    """
    eval_src = (Path(__file__).resolve().parents[1] / "eval.py").read_text(encoding="utf-8")
    hpc_exec_src = (
        Path(__file__).resolve().parents[1]
        / "dftworld_bench"
        / "executors"
        / "hpc.py"
    ).read_text(encoding="utf-8")
    # local container: controller cases run with gpus=0 so docker_gpu_args -> []
    assert "context.local_gpus = 0" in hpc_exec_src
    assert "gpus=context.local_gpus" in eval_src
    assert "execution_class=task.execution_class" in eval_src
    # remote side keeps the GPU: the controller renders --gres from the profile
    ctl_src = (Path(__file__).resolve().parents[1] / "scripts" / "matclaw_hpc_controller.py").read_text(
        encoding="utf-8"
    )
    assert "_assert_slurm_policy" in ctl_src  # gres is pinned from profile, not agent-chosen
    from scripts.matclaw_hpc_controller import DEFAULT_PROFILE

    assert "gpu" in DEFAULT_PROFILE["slurm"]["gres"]  # profile pins the GPU count
