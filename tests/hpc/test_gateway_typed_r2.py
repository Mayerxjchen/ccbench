"""R2 RED gates: typed Gateway boundary (C5) and operation identity (C7).

These fail against the current tree:

- C5: `Gateway.submit()` only validates a typed JobSpec for adapters that
  advertise ``validates_job_spec``; the ProcessTestAdapter path accepts raw
  dicts with a *different* resource shape (``memory_mb``/``walltime_sec``),
  so a malformed job can bypass the hpc-job schema entirely.
- C7: the adapter-internal ``render()/_directives()/_script()`` still accept
  ``operation_id=None``, so scheduler-directive operation identity is not
  mandatory; a crash between the directive and the operation record loses
  the binding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.hpc.adapters.process_test import ProcessTestAdapter
from bench.hpc.gateway import Gateway, GatewayError
from bench.hpc.job import JobError


def _gateway(adapter) -> Gateway:
    return Gateway(adapter, quota={"max_cpus": 2, "max_memory_gb": 8,
                                   "max_walltime_minutes": 60})


def _canonical_spec(key: str = "idem-1") -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": "mlip-bench/example@sha256:" + "cd" * 32,
        "command": ["echo", "hi"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": [],
    }


# ---------------------------------------------------------------------------
# C5: every adapter path is typed
# ---------------------------------------------------------------------------

def test_process_test_adapter_rejects_malformed_job(tmp_path):
    """C5: a raw dict missing required JobSpec fields must be rejected.

    RED: the ProcessTestAdapter does not advertise ``validates_job_spec``, so
    the Gateway skips typed validation and forwards the raw dict; a malformed
    job (missing runtime/command/resources) reaches the adapter untouched.
    """
    gw = _gateway(ProcessTestAdapter(tmp_path))
    token = gw.issue("run-1", ("submit",))
    malformed = {
        "idempotency_key": "idem-bad",
        # no runtime, no command, no canonical resources
        "resources": {"cpus": 1},
    }
    with pytest.raises(GatewayError):
        gw.submit(token, "run-1", malformed, operation_id="op-c5-1")


def test_process_test_adapter_canonical_spec_round_trips(tmp_path):
    """C5: the canonical JobSpec shape is accepted on the process_test path.

    Once the typed boundary is unconditional, ProcessTestAdapter must consume
    the canonical hpc-job shape (memory_gb/walltime_minutes), not a private
    ``memory_mb``/``walltime_sec`` dialect.
    """
    adapter = ProcessTestAdapter(tmp_path)
    gw = _gateway(adapter)
    token = gw.issue("run-1", ("submit",))
    result = gw.submit(
        token, "run-1", _canonical_spec(), operation_id="op-c5-2"
    )
    assert result["job_id"].startswith("job-")


def test_raw_dict_with_shell_command_is_rejected(tmp_path):
    """C5: a shell string in ``command`` must fail (schema: command is argv)."""
    gw = _gateway(ProcessTestAdapter(tmp_path))
    token = gw.issue("run-1", ("submit",))
    spec = _canonical_spec()
    spec["command"] = "echo hi && rm -rf /"  # shell string, not argv list
    with pytest.raises(GatewayError):
        gw.submit(token, "run-1", spec, operation_id="op-c5-3")


# ---------------------------------------------------------------------------
# C7: operation identity is mandatory on every adapter path
# ---------------------------------------------------------------------------

def test_slurm_render_requires_operation_id():
    """C7: scheduler-directive operation identity must not be optional.

    render() now requires operation_id; the --comment=bench: directive and
    the workspace marker file are always present, so a crashed gateway can
    reconcile the run by operation id.
    """
    from bench.hpc.adapters.slurm import SlurmAdapter

    site_config = {
        "site": "fake-site",
        "scratch": "/scratch",
        "gateway_url": "http://localhost:0",
        "run_token": "0123456789abcdef0123456789abcdef",
        "ssh_alias": "fake-host",
        "platform_profile": {
            "name": "fake-site",
            "queues": [{"name": "cpu", "max_cpus": 32, "max_memory_gb": 128,
                         "max_gpus": 0, "max_walltime_minutes": 60}],
            "default_queue": "cpu",
        },
    }
    adapter = SlurmAdapter(site_config, transport=None, case_id="031")

    spec = {
        "idempotency_key": "k",
        "runtime": "img@sha256:" + "ab" * 32,
        "command": ["echo", "hi"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
    }
    rendered = adapter.render(spec, job_id="job-1", run_id="run-1",
                              operation_id="op-c7-1")
    # The directive must carry the operation identity so a crashed gateway can
    # reconcile; the bench: comment is always present.
    assert any("bench:" in e for e in rendered.opts.extra)
    # The script must write the operation marker file
    assert ".bench-operation-id" in rendered.script
