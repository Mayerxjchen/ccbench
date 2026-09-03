"""P1 runtime-capability boundary (Architecture Freeze §3).

The Agent's runtime field names a capability; trusted infra resolves the
locked SIF identity. These cover the resolver itself, the gateway sealing
step (both submit paths, audit, capabilities enrichment), and the adapter's
digest -> SIF path store.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dftworld_bench.hpc.adapters.process_test import ProcessTestAdapter
from dftworld_bench.hpc.adapters.slurm import SlurmAdapter, SlurmAdapterError
from dftworld_bench.hpc.gateway import Gateway, GatewayError
from dftworld_bench.hpc.runtime_resolution import (
    RuntimeResolutionError,
    RuntimeResolver,
    split_runtime,
)
from scripts.ablation.transport.slurm_transport import JobState

CP2K_SHA = "ab" * 32
AI2KIT_SHA = "cd" * 32
OTHER_SHA = "ef" * 32


def _lock_dir(tmp_path: Path) -> Path:
    locks = tmp_path / "runtime"
    locks.mkdir()
    (locks / "cp2k-runtime.lock.json").write_text(
        json.dumps({
            "schema": "dispatcher-cp2k-runtime-lock/v1",
            "image_name": "dftworld-cp2k",
            "runtime": {
                "sif_path_remote": "/site/runtimes/cp2k-2025.2.sif",
                "sif_sha256": CP2K_SHA,
            },
        })
    )
    (locks / "ai2kit-runtime.lock.json").write_text(
        json.dumps({
            "schema": "dispatcher-ai2kit-runtime-lock/v1",
            "image_name": "dftworld-base-ai2kit",
            "runtime": {
                "sif_path_remote": "/site/runtimes/ai2kit.sif",
                "sif_sha256": AI2KIT_SHA,
            },
        })
    )
    # A locked-but-uncaptured runtime: qualification gates stay NOT_RUN.
    (locks / "deepmd-jax-runtime.lock.json").write_text(
        json.dumps({
            "schema": "dispatcher-deepmd-jax-runtime-lock/v1",
            "image_name": "dftworld-deepmd-jax",
            "runtime": {"sif_path_remote": "/site/runtimes/deepmd.sif", "sif_sha256": ""},
        })
    )
    return locks


def _resolver(tmp_path: Path) -> RuntimeResolver:
    return RuntimeResolver.from_lock_dir(_lock_dir(tmp_path))


def _spec(runtime: str, key: str = "idem-1") -> dict:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": runtime,
        "command": ["echo", "hi"],
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": [],
    }


# ---------------------------------------------------------------------------
# declaration grammar
# ---------------------------------------------------------------------------

def test_split_runtime_capability_and_compat_forms():
    assert split_runtime("cp2k") == ("cp2k", None)
    assert split_runtime("deepmd-jax") == ("deepmd-jax", None)
    assert split_runtime("dftworld-cp2k@sha256:" + "a" * 64) == (
        "dftworld-cp2k", "a" * 64)


@pytest.mark.parametrize("decl", ["", "CP2K", "python:3.11", "a@sha256:" + "a" * 63])
def test_split_runtime_rejects_malformed(decl):
    with pytest.raises(RuntimeResolutionError):
        split_runtime(decl)


# ---------------------------------------------------------------------------
# resolver fail-closed matrix
# ---------------------------------------------------------------------------

def test_capability_resolves_to_locked_runtime(tmp_path):
    resolver = _resolver(tmp_path)
    resolved = resolver.resolve("cp2k")
    assert resolved.capability == "cp2k"
    assert resolved.sif_path == "/site/runtimes/cp2k-2025.2.sif"
    assert resolved.sif_sha256 == CP2K_SHA
    assert resolved.qualification == "runtime.cp2k"
    assert resolved.declaration == f"cp2k@sha256:{CP2K_SHA}"
    canon = json.dumps(
        {"capability": "cp2k", "image_name": "dftworld-cp2k",
         "sif_path": "/site/runtimes/cp2k-2025.2.sif", "sif_sha256": CP2K_SHA},
        sort_keys=True, separators=(",", ":"))
    assert resolved.runtime_profile_digest == hashlib.sha256(
        canon.encode("utf-8")).hexdigest()


def test_image_name_alias_resolves_to_canonical_capability(tmp_path):
    resolver = _resolver(tmp_path)
    resolved = resolver.resolve("dftworld-cp2k")
    assert resolved.capability == "cp2k"  # evidence normalizes to the capability


def test_unknown_capability_fails_closed(tmp_path):
    resolver = _resolver(tmp_path)
    with pytest.raises(RuntimeResolutionError, match="unknown runtime capability"):
        resolver.resolve("lammps")


def test_uncaptured_digest_fails_closed(tmp_path):
    resolver = _resolver(tmp_path)
    with pytest.raises(RuntimeResolutionError, match="no captured"):
        resolver.resolve("deepmd-jax")


def test_compat_digest_assertion_checked(tmp_path):
    resolver = _resolver(tmp_path)
    ok = resolver.resolve(f"cp2k@sha256:{CP2K_SHA}")
    assert ok.sif_sha256 == CP2K_SHA
    with pytest.raises(RuntimeResolutionError, match="does not match"):
        resolver.resolve(f"cp2k@sha256:{OTHER_SHA}")


def test_compat_unknown_name_passes_through(tmp_path):
    """Hidden compat path until Phase 8: a locked-in-nowhere digest passes
    so the frozen qualification driver keeps working; capability tokens never
    get this exemption (test_unknown_capability_fails_closed)."""
    resolver = _resolver(tmp_path)
    resolved = resolver.resolve(f"matclaw-cips@sha256:{OTHER_SHA}")
    assert resolved.sif_path == ""
    assert resolved.qualification == "runtime.matclaw-gpu"


def test_qualification_mapping_ai2kit(tmp_path):
    assert _resolver(tmp_path).resolve("ai2kit").qualification == "runtime.ai2kit"


def test_capabilities_listing(tmp_path):
    assert _resolver(tmp_path).capabilities() == [
        "ai2kit", "cp2k", "deepmd-jax"]


def test_runtime_store_only_finalized(tmp_path):
    store = _resolver(tmp_path).runtime_store()
    assert store == {
        CP2K_SHA: "/site/runtimes/cp2k-2025.2.sif",
        AI2KIT_SHA: "/site/runtimes/ai2kit.sif",
    }


def test_placeholder_path_fails_closed(tmp_path):
    locks = _lock_dir(tmp_path)
    (locks / "lammps-runtime.lock.json").write_text(json.dumps({
        "image_name": "dftworld-lammps",
        "runtime": {
            "sif_path_remote": "/public/home/<site-user>/lammps.sif",
            "sif_sha256": OTHER_SHA,
        },
    }))
    resolver = RuntimeResolver.from_lock_dir(locks)
    with pytest.raises(RuntimeResolutionError, match="no concrete"):
        resolver.resolve("lammps")


def test_conflicting_aliases_fail_at_load(tmp_path):
    locks = tmp_path / "runtime"
    locks.mkdir()
    doc = {"image_name": "same-alias",
           "runtime": {"sif_path_remote": "/p.sif", "sif_sha256": CP2K_SHA}}
    for name in ("a-runtime.lock.json", "b-runtime.lock.json"):
        (locks / name).write_text(json.dumps(doc))
    with pytest.raises(RuntimeResolutionError, match="two sources"):
        RuntimeResolver.from_lock_dir(locks)


# ---------------------------------------------------------------------------
# gateway sealing
# ---------------------------------------------------------------------------

def _gateway(adapter, resolver=None, audit=None):
    return Gateway(adapter, quota=None, audit=audit, runtime_resolver=resolver)


def test_gateway_resolves_capability_before_adapter(tmp_path):
    resolver = _resolver(tmp_path)
    adapter = ProcessTestAdapter(tmp_path / "jobs")
    seen: list[dict] = []
    real_submit = adapter.submit

    def spy(spec, **kw):
        seen.append(dict(spec))
        return real_submit(spec, **kw)

    adapter.submit = spy  # type: ignore[method-assign]
    gw = _gateway(adapter, resolver)
    token = gw.issue("run-1", ("submit",))
    result = gw.submit(token, "run-1", _spec("cp2k"), operation_id="op-1")
    assert seen[0]["runtime"] == f"cp2k@sha256:{CP2K_SHA}"
    rr = result["resolved_runtime"]
    assert rr["capability"] == "cp2k"
    assert rr["sif_sha256"] == CP2K_SHA
    assert rr["qualification"] == "runtime.cp2k"
    assert len(rr["runtime_profile_digest"]) == 64


def test_gateway_audit_records_resolution(tmp_path):
    from dftworld_bench.hpc.audit import GatewayAudit

    audit = GatewayAudit(tmp_path / "audit.jsonl")
    gw = _gateway(ProcessTestAdapter(tmp_path / "jobs"), _resolver(tmp_path), audit)
    token = gw.issue("run-1", ("submit",))
    gw.submit(token, "run-1", _spec("ai2kit"), operation_id="op-1")
    kinds = [e["event"]["kind"] for e in audit.entries()]
    assert "runtime_resolved" in kinds
    event = next(e["event"] for e in audit.entries()
                 if e["event"]["kind"] == "runtime_resolved")
    assert event["requested"] == "ai2kit"
    assert event["sif_sha256"] == AI2KIT_SHA


def test_gateway_v2_resolves_on_attempt_path(tmp_path):
    from dftworld_bench.hpc.audit import GatewayAudit

    audit = GatewayAudit(tmp_path / "audit.jsonl")
    gw = _gateway(ProcessTestAdapter(tmp_path / "jobs"), _resolver(tmp_path), audit)
    token = gw.issue("run-1", ("submit",))
    result = gw.submit(token, "run-1", _spec("cp2k"),
                       operation_id="op-1", attempt=1)
    assert result["resolved_runtime"]["capability"] == "cp2k"


def test_gateway_unknown_capability_denied(tmp_path):
    gw = _gateway(ProcessTestAdapter(tmp_path / "jobs"), _resolver(tmp_path))
    token = gw.issue("run-1", ("submit",))
    with pytest.raises(GatewayError, match="runtime resolution failed"):
        gw.submit(token, "run-1", _spec("lammps"), operation_id="op-1")


def test_gateway_uncaptured_runtime_denied(tmp_path):
    gw = _gateway(ProcessTestAdapter(tmp_path / "jobs"), _resolver(tmp_path))
    token = gw.issue("run-1", ("submit",))
    with pytest.raises(GatewayError, match="no captured"):
        gw.submit(token, "run-1", _spec("deepmd-jax"), operation_id="op-1")


def test_capability_denied_without_resolver(tmp_path):
    gw = _gateway(ProcessTestAdapter(tmp_path / "jobs"))
    token = gw.issue("run-1", ("submit",))
    with pytest.raises(GatewayError, match="no runtime resolver"):
        gw.submit(token, "run-1", _spec("cp2k"), operation_id="op-1")


def test_compat_digest_unchanged_without_resolver(tmp_path):
    """Byte-identical pre-P1 behavior when the site has no resolver."""
    gw = _gateway(ProcessTestAdapter(tmp_path / "jobs"))
    token = gw.issue("run-1", ("submit",))
    result = gw.submit(token, "run-1", _spec("mlip-bench/example@sha256:" + "cd" * 32),
                       operation_id="op-1")
    assert result == {"job_id": result["job_id"], "duplicate": False}


def test_capabilities_advertise_runtime_names(tmp_path):
    gw = _gateway(ProcessTestAdapter(tmp_path / "jobs"), _resolver(tmp_path))
    token = gw.issue("run-1", ("capabilities",))
    out = gw.capabilities(token, "run-1")
    assert out["runtime_capabilities"] == ["ai2kit", "cp2k", "deepmd-jax"]


def test_capabilities_omit_field_without_resolver(tmp_path):
    gw = _gateway(ProcessTestAdapter(tmp_path / "jobs"))
    token = gw.issue("run-1", ("capabilities",))
    assert "runtime_capabilities" not in gw.capabilities(token, "run-1")


# ---------------------------------------------------------------------------
# adapter runtime store (digest -> on-site SIF path)
# ---------------------------------------------------------------------------

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


class _FakeTransport:
    def __init__(self):
        self.submits: list[tuple] = []

    def submit(self, script, opts):
        self.submits.append((script, opts))
        return "1001"

    def status(self, job_id):
        return JobState.RUNNING

    def log(self, job_id, tail=None):
        return "log"

    def cancel(self, job_id):
        return True


def _slurm_adapter(**kw) -> SlurmAdapter:
    return SlurmAdapter(SITE, _FakeTransport(), case_id="case-x", **kw)


def test_adapter_legacy_storeless_behavior_unchanged():
    adapter = _slurm_adapter()
    script = adapter._script(
        _spec("cp2k@sha256:" + CP2K_SHA), "/ws", operation_id="op")
    assert "cp2k@sha256:" + CP2K_SHA in script


def test_adapter_store_maps_digest_to_sif_path():
    adapter = _slurm_adapter(runtime_store={CP2K_SHA: "/site/runtimes/cp2k.sif"})
    script = adapter._script(
        _spec("cp2k@sha256:" + CP2K_SHA), "/ws", operation_id="op")
    assert "apptainer run --contain --cleanenv --no-home" in script
    assert "/site/runtimes/cp2k.sif echo" in script
    assert "cp2k@sha256" not in script


def test_adapter_store_rejects_unlocked_digest():
    adapter = _slurm_adapter(runtime_store={CP2K_SHA: "/site/cp2k.sif"})
    with pytest.raises(SlurmAdapterError, match="not a locked runtime"):
        adapter._script(_spec("mlip/example@sha256:" + OTHER_SHA), "/ws", operation_id="op")


def test_adapter_rejects_unsealed_capability_token(tmp_path):
    """Defense in depth: a raw capability must never reach the adapter."""
    adapter = _slurm_adapter(runtime_store={CP2K_SHA: "/site/cp2k.sif"})
    with pytest.raises(SlurmAdapterError, match="not a locked runtime"):
        adapter._script(_spec("cp2k"), "/ws", operation_id="op")


def test_set_runtime_store_after_construction():
    adapter = _slurm_adapter()
    adapter.set_runtime_store({CP2K_SHA: "/site/cp2k.sif"})
    script = adapter._script(
        _spec("cp2k@sha256:" + CP2K_SHA), "/ws", operation_id="op")
    assert "/site/cp2k.sif" in script
