"""D11 qualification receipt: derivation verifier and adversarial negatives.

Every fixture lives strictly under ``tmp_path`` — nothing here writes to the
repo's ``evidence/`` tree or generates a formal ``receipt.json``.  The golden
bundle's settlement report and hash-chained audit ledger come from a REAL
dispatcher session over the ProcessTestAdapter, so the ledger/digest anchors
exercise production code paths; scheduler-side facts (sacct lines, GPU probe)
are realistic canned shapes.

The attacker model for the negatives: a forger who can edit any part of the
receipt *and recompute its content digest*.  Each negative shows at least one
anchor still breaking — the property that makes the verdict-free receipt
trustworthy offline.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shlex
import shutil
import time
from pathlib import Path

import pytest

from dftworld_bench.experiments import qualification_receipt as qr
from dftworld_bench.experiments.release_builder import check_qualification_receipt
from dftworld_bench.hpc.dispatcher import HpcDispatcher

GPU_NAME = "NVIDIA A100-SXM4-80GB"
SIF_SHA = "99aefeff8f457cd6b4f57e1592db511f167ab493730f970bfd7baa68993250c3"
SIF_REMOTE = "/public/home/<site-user>/dftworld2-runs/matclaw/runtime.sif"
LOCK_RELPATH = (
    "033-matclaw-cips-domain-wall-search/reference/compute-runtime.lock.json"
)
RECEIT_DIRNAME = "evidence/hpc-dispatcher/qualification/site-v1"
AUTHZ = (
    "2026-08-22 user authorization: one combined GPU job "
    "(echo/hostname + nvidia-smi + containment); no CP2K, no image "
    "transfer, nothing else"
)

CLUSTER_PROFILE_TOML = """\
[ssh]
host = "<site-alias>.hpc.example"
user = "<site-user>"
port = 22

[ssh.options]
StrictHostKeyChecking = "accept-new"

[slurm]
partition = "gpu"
cpu_partition = "cpu"
account = "acct-blocked"
qos = "normal"
cpus_per_task = 8
mem = "64G"
time_paper = "08:00:00"

[paths]
apptainer = "/opt/apptainer/bin/apptainer"
remote_root = "/public/home/<site-user>/dftworld2-runs"

[runtime]
expected_node_arch = "x86_64"
"""

PROBE_LINES = [
    "HOST=qual-node",
    "USER=<site-user>",
    "BENCH_PROBE workspace_rw=pass",
    "BENCH_PROBE home_sentinel_absent=pass",
    "BENCH_PROBE credential_sentinel_absent=pass",
    "BENCH_PROBE other_run_dir_absent=pass",
    "BENCH_PROBE solution_absent=pass",
    "BENCH_PROBE reference_absent=pass",
    "BENCH_PROBE runs_root_not_listable=pass",
    f"BENCH_GPU_DEVICE mem_mib=81920 name={GPU_NAME}",
    qr.MARKER_GPU,
    qr.MARKER_CONTAINMENT,
]

# cpu-partition nodes have no GPU: no device line, no GPU marker.
CPU_PROBE_LINES = [
    line for line in PROBE_LINES
    if not line.startswith("BENCH_GPU_DEVICE") and line != qr.MARKER_GPU
]

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT", "LOST"}


# -- fixture tree ---------------------------------------------------------------


def _write_anchors(root: Path) -> None:
    """Code identity stubs, frozen runtime lock, private cluster profile."""
    for rel in qr.CODE_IDENTITY_PATHS:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# code identity stub: {rel}\n", encoding="utf-8")
    lock_path = root / LOCK_RELPATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "schema": "matclaw-compute-runtime-lock/v1",
                "runtime": {
                    "sif_path_remote": SIF_REMOTE,
                    "sif_sha256": SIF_SHA,
                },
                "qualification": {"qualified": True, "gpu": GPU_NAME},
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    profile_path = root / qr.DEFAULT_PROFILE_RELPATH
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(CLUSTER_PROFILE_TOML, encoding="utf-8")


def _run_real_dispatcher_job(
    base: Path,
    *,
    site_dir: str,
    run_name: str,
    operation_id: str,
    idem_key: str,
    command_lines: list[str],
    artifacts_dir: str | None = None,
) -> dict:
    """One real DispatcherSession over ProcessTestAdapter: real audit chain,
    real settlement report, real subprocess stdout.  When ``artifacts_dir``
    is given, the job's fetched logs are copied there (manifest anchoring)."""
    run_id = f"{run_name}-fixt"
    disp = HpcDispatcher.process_test(base / site_dir)
    ws = base / f"ws-{site_dir}"
    ws.mkdir(parents=True, exist_ok=True)
    session = disp.open_run(run_id, workspace=ws)
    try:
        command = ["bash", "-c", "printf '%s\\n' " + " ".join(
            shlex.quote(line) for line in command_lines
        )]
        spec = {
            "schema_version": 1,
            "idempotency_key": idem_key,
            "runtime": f"matclaw-cips@sha256:{SIF_SHA}",
            "command": command,
            "resources": {
                "cpus": 2, "memory_gb": 8, "gpus": 1, "walltime_minutes": 15,
            },
            "inputs": [],
            "outputs": [],
        }
        submitted = session.submit(spec, operation_id=operation_id, attempt=1)
        job_id = submitted["job_id"]
        for _ in range(200):
            if session.status(job_id)["state"] in TERMINAL:
                break
            time.sleep(0.05)
        state = session.status(job_id)["state"]
        assert state == "SUCCEEDED", state
        logs = session.logs(job_id)
        report = session.settle(cancel_pending=True)
        if artifacts_dir is not None:
            work = session.gateway._adapter._jobs[job_id]["work"]
            target = base / artifacts_dir
            target.mkdir(exist_ok=True)
            for name in ("stdout.log", "stderr.log"):
                shutil.copy2(work / name, target / name)
    finally:
        session.close()
    return {
        "run_id": run_id,
        "operation_id": operation_id,
        "job_id": job_id,
        "stdout": logs["stdout"],
        "stderr": logs.get("stderr", ""),
        "report": report.to_dict(),
        "digest": report.digest,
    }


def _accounting(gpus: int, sacct_id: str) -> dict:
    gpu_terms = "" if gpus == 0 else f"gpu:tesla={gpus},"
    return {
        "raw_state": "COMPLETED",
        "exit_code_raw": "0:0",
        "partition": "gpu" if gpus else "cpu",
        "node_list": "gpu001" if gpus else "cpu001",
        "req_tres": f"{gpu_terms}cpu=8,mem=64G",
        "alloc_tres": f"{gpu_terms}cpu=8,mem=64G",
        "source": f"sacct -X -P -n --format=JobID,State,ExitCode,ReqTRES,"
                  f"AllocTRES -j {sacct_id}",
    }


def _job_record(
    real: dict, *, name: str, sacct_id: str, gpus: int, probe_class: str,
    artifacts_dir: str, audit_log: str, parsed: dict | None = None,
) -> dict:
    probes = {
        key: True
        for key in (
            "workspace_rw", "home_sentinel_absent", "credential_sentinel_absent",
            "other_run_dir_absent", "solution_absent", "reference_absent",
            "runs_root_not_listable",
        )
    }
    if probe_class == "gpu":
        gpu_parsed = parsed or qr.parse_probe_stdout(real["stdout"])
        probes["gpu_device_name"] = gpu_parsed["gpu_device_name"]
        probes["gpu_memory_total_mb"] = gpu_parsed["gpu_memory_total_mb"]
    return {
        "canary": name,
        "run_id": real["run_id"],
        "operation_id": real["operation_id"],
        "attempt": 1,
        "job_id": real["job_id"],
        "scheduler_job_id": sacct_id,
        "runtime_decl": f"matclaw-cips@sha256:{SIF_SHA}",
        "state": "SUCCEEDED",
        "exit_code": 0,
        "gpus_requested": gpus,
        "requested_resources": {
            "cpus": 8, "memory_gb": 64, "gpus": gpus,
            "walltime_minutes": 15,
        },
        "probe_class": probe_class,
        "accounting": _accounting(gpus, sacct_id),
        "probe_results": probes,
        "stdout_tail": real["stdout"],
        "stderr_tail": real["stderr"],
        "settlement": {"report": real["report"], "digest": real["digest"]},
        "fetch_manifest": {"job_id": real["job_id"], "entries": real["entries"]},
        "artifacts_dir": artifacts_dir,
        "audit_log": audit_log,
    }


def _manifest_entries(artifacts: Path) -> list[dict]:
    entries = []
    for path in sorted(artifacts.iterdir()):
        data = path.read_bytes()
        entries.append(
            {
                "path": path.name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )
    return entries


def _build_golden(base: Path) -> dict:
    """Full fixture tree + sealed golden receipt at its canonical path.

    Two canary jobs after cpu-ACL restoration: a CPU job on the native cpu
    queue and a GPU job under --gres, each with its own dispatcher session,
    audit chain, settlement report and manifest-anchored artifacts.
    """
    base.mkdir(parents=True, exist_ok=True)
    _write_anchors(base)

    real_cpu = _run_real_dispatcher_job(
        base,
        site_dir="site-cpu",
        run_name="run-cpu-echo-probe-containment",
        operation_id="qual-cpu-canary-01",
        idem_key="qual-cpu-fixt",
        command_lines=CPU_PROBE_LINES,
        artifacts_dir="artifacts-cpu",
    )
    real_gpu = _run_real_dispatcher_job(
        base,
        site_dir="site-gpu",
        run_name="run-gpu-nvidia-probe-containment",
        operation_id="qual-gpu-canary-01",
        idem_key="qual-gpu-fixt",
        command_lines=PROBE_LINES,
        artifacts_dir="artifacts-gpu",
    )
    real_cpu["entries"] = _manifest_entries(base / "artifacts-cpu")
    real_gpu["entries"] = _manifest_entries(base / "artifacts-gpu")

    import tomllib

    config = tomllib.loads(
        (base / qr.DEFAULT_PROFILE_RELPATH).read_text(encoding="utf-8")
    )
    from dftworld_bench.hpc.site_profile import HpcSiteProfile

    site = HpcSiteProfile.from_cluster_config(config)

    def _resolved_dict(r) -> dict:
        return {
            "workload_type": r.workload_type,
            "queue_name": r.queue_name,
            "partition": r.partition,
            "account": r.account,
            "qos": r.qos,
            "gres": r.gres,
            "mapping_note": r.mapping_note,
        }

    evidence = {
        "phase": "canary",
        "authorization": AUTHZ,
        "site_profile_digest": site.digest,
        "resolved_workloads": {
            "cpu": _resolved_dict(site.resolve_workload("cpu")),
            "gpu": _resolved_dict(site.resolve_workload("gpu")),
        },
        "native_cpu_partition_accessible": True,
        "sentinel_cleanup": {
            "removed": [
                "/home/<site-user>/.bench-sentinel-fixture",
                "/home/<site-user>/.bench-cred-sentinel-fixture",
                "/public/home/<site-user>/dftworld2-runs/sentinel-other-run-fixture",
            ],
            "leftover": [],
        },
        "single_mount_check": (
            "wrapper renders exactly one --bind <workdir>:/workspace:rw; the "
            "in-container probe additionally proves the runs root is not listable"
        ),
        "jobs": [
            _job_record(
                real_cpu,
                name="cpu-echo-probe-containment",
                sacct_id="3537001",
                gpus=0,
                probe_class="cpu",
                artifacts_dir="../../../../artifacts-cpu",
                audit_log="../../../../site-cpu/audit.jsonl",
            ),
            _job_record(
                real_gpu,
                name="gpu-nvidia-probe-containment",
                sacct_id="3537002",
                gpus=1,
                probe_class="gpu",
                artifacts_dir="../../../../artifacts-gpu",
                audit_log="../../../../site-gpu/audit.jsonl",
                parsed=qr.parse_probe_stdout(real_gpu["stdout"]),
            ),
        ],
        "cp2k_gate": {
            "detail": "CP2K ENERGY canary awaits separate authorization; "
                      "absent evidence derives NOT_RUN",
        },
    }
    body = {
        "kind": qr.RECEIPT_KIND,
        "schema_id": qr.SCHEMA_ID,
        "profile_site": "<site-alias>.hpc.example",
        "site_profile_digest": site.digest,
        "source_commit": "a" * 40,
        "code_identity": qr.code_identity(base),
        "runtime_lock": {
            "path": LOCK_RELPATH,
            "sif_path_remote": SIF_REMOTE,
            "sif_sha256": SIF_SHA,
        },
        "authorization_scope": AUTHZ,
        "evidence": evidence,
    }
    receipt = qr.seal_receipt(body)
    receipt_dir = base / RECEIT_DIRNAME
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / "receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


@pytest.fixture()
def golden(tmp_path: Path) -> Path:
    """Build the tree; return the repo-root used for verification."""
    _build_golden(tmp_path)
    return tmp_path


# -- cp2k fixture extension ------------------------------------------------------

CP2K_SIF_SHA = "e" * 64
CP2K_SIF_REMOTE = "/public/home/<site-user>/dftworld2-runs/cp2k/cp2k-2025.2-cpu-amd64.sif"
CP2K_LOCK_RELPATH = "reference/cp2k-runtime.lock.json"
CP2K_VERSION = "CP2K version 2025.2"
CP2K_ENERGY = "-17.163797486199930"

# Realistic CP2K 2025.2 ENERGY output (format-identical to the real 017
# reference runs, content abbreviated): version header, converged SCF, and
# the total-energy line the parser keys on.
CANNED_CP2K_OUTPUT = f"""\
 CP2K| version string:                                       {CP2K_VERSION}
 CP2K| source code revision number:                           git:abc1234
 Global| Running with                         1 MPI process
 Global| OpenMP running with                            8 threads
 &GLOBAL ... PROJECT cp2k-energy-canary
 &DFT ... XC_FUNCTIONAL PBE
 Self-interaction consistency check: ...
 *** SCF run converged in    14 steps ***
 Quickstep energy components [hartree]
 ...
  1. QS electronic kinetic energy              1.234567890123456E+01
 ...
 ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]            {CP2K_ENERGY}
"""


def _write_cp2k_lock(root: Path) -> None:
    lock_path = root / CP2K_LOCK_RELPATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "schema": "dispatcher-cp2k-runtime-lock/v1",
                "runtime": {
                    "sif_path_remote": CP2K_SIF_REMOTE,
                    "sif_sha256": CP2K_SIF_SHA,
                },
                "cp2k": {"binary": "cp2k.psmp", "version": "2025.2"},
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_real_cp2k_dispatcher_job(base: Path) -> dict:
    """Second real dispatcher session: its audit chain and settlement report
    anchor the cp2k job record exactly like the echo canary's."""
    run_id = "run-cp2k-energy-canary-fixt"
    operation_id = "qual-cp2k-energy-01"
    disp = HpcDispatcher.process_test(base / "site-cp2k")
    ws = base / "ws-cp2k"
    ws.mkdir(parents=True, exist_ok=True)
    session = disp.open_run(run_id, workspace=ws)
    try:
        command = ["bash", "-c", "printf '%s\\n' " + " ".join(
            shlex.quote(line) for line in CPU_PROBE_LINES
        )]
        spec = {
            "schema_version": 1,
            "idempotency_key": "qual-cp2k-fixt",
            "runtime": f"dftworld-cp2k@sha256:{CP2K_SIF_SHA}",
            "command": command,
            "resources": {
                "cpus": 2, "memory_gb": 8, "gpus": 0, "walltime_minutes": 15,
            },
            "inputs": [],
            "outputs": [],
        }
        submitted = session.submit(spec, operation_id=operation_id, attempt=1)
        job_id = submitted["job_id"]
        for _ in range(200):
            if session.status(job_id)["state"] in TERMINAL:
                break
            time.sleep(0.05)
        state = session.status(job_id)["state"]
        assert state == "SUCCEEDED", state
        logs = session.logs(job_id)
        report = session.settle(cancel_pending=True)
    finally:
        session.close()
    return {
        "run_id": run_id,
        "operation_id": operation_id,
        "job_id": job_id,
        "stdout": logs["stdout"],
        "stderr": logs.get("stderr", ""),
        "report": report.to_dict(),
        "digest": report.digest,
    }


def _add_cp2k(golden_root: Path, *, output_text: str = CANNED_CP2K_OUTPUT) -> None:
    """Merge well-formed CP2K evidence into the golden receipt on disk.

    Mirrors the producer's merge path: second dispatcher session, manifest-
    anchored input/output artifacts under their own artifacts dir, own audit
    ledger, own runtime lock — then re-seal.
    """
    _write_cp2k_lock(golden_root)
    real = _run_real_cp2k_dispatcher_job(golden_root)

    artifacts = golden_root / "artifacts-cp2k"
    artifacts.mkdir(exist_ok=True)
    input_text = qr.CP2K_INPUT_TEXT
    (artifacts / "cp2k-energy.inp").write_text(input_text, encoding="utf-8")
    (artifacts / "cp2k.out").write_text(output_text, encoding="utf-8")

    parsed = qr.parse_probe_stdout(real["stdout"])
    entries = []
    for name in ("cp2k-energy.inp", "cp2k.out"):
        data = (artifacts / name).read_bytes()
        entries.append(
            {
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )

    job = {
        "canary": "cp2k-energy-canary",
        "run_id": real["run_id"],
        "operation_id": real["operation_id"],
        "attempt": 1,
        "job_id": real["job_id"],
        "scheduler_job_id": "3537003",
        "runtime_decl": f"dftworld-cp2k@sha256:{CP2K_SIF_SHA}",
        "state": "SUCCEEDED",
        "exit_code": 0,
        "gpus_requested": 0,
        "requested_resources": {
            "cpus": 8, "memory_gb": 64, "gpus": 0,
            "walltime_minutes": 15,
        },
        "probe_class": "cpu",
        "accounting": _accounting(0, "3537003"),
        "probe_results": {
            key: parsed["probes"].get(key) == "pass"
            for key in (
                "workspace_rw",
                "home_sentinel_absent",
                "credential_sentinel_absent",
                "other_run_dir_absent",
                "solution_absent",
                "reference_absent",
                "runs_root_not_listable",
            )
        },
        "stdout_tail": real["stdout"],
        "stderr_tail": real["stderr"],
        "settlement": {"report": real["report"], "digest": real["digest"]},
        "fetch_manifest": {"job_id": real["job_id"], "entries": entries},
        "artifacts_dir": "../../../../artifacts-cp2k",
        "audit_log": "../../../../site-cp2k/audit.jsonl",
    }
    receipt = _load(golden_root)
    receipt["evidence"]["cp2k_gate"] = {
        "detail": "CP2K ENERGY canary under separate authorization",
        "evidence": {
            "job": job,
            "input": {
                "name": "cp2k-energy.inp",
                "text": input_text,
                "sha256": hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
            },
            "output_artifact": "cp2k.out",
            "parsed": {
                "version_string": CP2K_VERSION,
                "energy_eh": CP2K_ENERGY,
                "scf_converged": True,
            },
            "runtime_lock": {
                "path": CP2K_LOCK_RELPATH,
                "sif_path_remote": CP2K_SIF_REMOTE,
                "sif_sha256": CP2K_SIF_SHA,
            },
        },
    }
    receipt_dir = golden_root / RECEIT_DIRNAME
    (receipt_dir / "receipt.json").write_text(
        json.dumps(_reseal(receipt), indent=2) + "\n", encoding="utf-8"
    )


@pytest.fixture()
def full(golden: Path) -> Path:
    """Golden receipt with well-formed CP2K evidence merged in."""
    _add_cp2k(golden)
    return golden


class TestCp2kDerivation:
    def test_golden_with_cp2k_derives_pass(self, full: Path) -> None:
        result = _verify(full, _load(full))
        assert _problems(result) == [], _problems(result)
        derived = result["derived"]
        assert derived["qualification_status"] == "PASS"
        assert derived["formal_qualified"] is True
        assert derived["site_acl_blocked"] is False
        assert derived["gates"]["cp2k_gate"] == "PASS"

    def test_release_builder_sees_pass(self, full: Path) -> None:
        assert check_qualification_receipt(full)["status"] == "PASS"

    def test_energy_tampered(self, full: Path) -> None:
        receipt = copy.deepcopy(_load(full))
        receipt["evidence"]["cp2k_gate"]["evidence"]["parsed"]["energy_eh"] = (
            "-1.000000000000000"
        )
        result = _verify(full, _reseal(receipt))
        assert result["consistent"] is False
        assert result["derived"]["qualification_status"] == "INVALID"
        assert result["derived"]["formal_qualified"] is False
        assert any("energy mismatch" in p for p in _problems(result))

    def test_output_artifact_byte_flip(self, full: Path) -> None:
        artifact = full / "artifacts-cp2k" / "cp2k.out"
        artifact.write_text(
            artifact.read_text(encoding="utf-8").replace(
                CP2K_ENERGY, "-17.163797486199931"
            ),
            encoding="utf-8",
        )
        result = _verify(full, _load(full))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "energy mismatch" in joined
        assert "artifact digest drift" in joined

    def test_input_text_edited_consistently(self, full: Path) -> None:
        """Attacker rewrites the declared input AND its self-hash — the
        staged artifact bytes still disagree."""
        receipt = copy.deepcopy(_load(full))
        ev = receipt["evidence"]["cp2k_gate"]["evidence"]
        ev["input"]["text"] = qr.CP2K_INPUT_TEXT.replace("400", "600", 1)
        ev["input"]["sha256"] = hashlib.sha256(
            ev["input"]["text"].encode("utf-8")
        ).hexdigest()
        result = _verify(full, _reseal(receipt))
        assert result["consistent"] is False
        assert any(
            "differs from declared input text" in p for p in _problems(result)
        )

    def test_version_absent_from_output(self, tmp_path: Path) -> None:
        golden_root = tmp_path
        _build_golden(golden_root)
        stripped = "\n".join(
            line for line in CANNED_CP2K_OUTPUT.splitlines()
            if "version string" not in line
        )
        _add_cp2k(golden_root, output_text=stripped)
        result = _verify(golden_root, _load(golden_root))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "no CP2K version line" in joined

    def test_unconverged_output(self, tmp_path: Path) -> None:
        golden_root = tmp_path
        _build_golden(golden_root)
        stripped = "\n".join(
            line for line in CANNED_CP2K_OUTPUT.splitlines()
            if "SCF run converged" not in line
        )
        _add_cp2k(golden_root, output_text=stripped)
        result = _verify(golden_root, _load(golden_root))
        assert result["consistent"] is False
        assert any(
            "SCF convergence marker absent" in p for p in _problems(result)
        )

    def test_accounting_reports_failure(self, full: Path) -> None:
        """Schema-legal forgery: SUCCEEDED label kept, scheduler truth
        forged to FAILED — the cp2k job derivation fails closed."""
        receipt = copy.deepcopy(_load(full))
        job = receipt["evidence"]["cp2k_gate"]["evidence"]["job"]
        job["accounting"]["raw_state"] = "FAILED"
        job["accounting"]["exit_code_raw"] = "1:0"
        result = _verify(full, _reseal(receipt))
        assert result["consistent"] is False
        assert result["derived"]["qualification_status"] == "INVALID"
        assert any("not scheduler-COMPLETED" in p for p in _problems(result))

    def test_runtime_lock_swapped(self, full: Path) -> None:
        receipt = copy.deepcopy(_load(full))
        ev = receipt["evidence"]["cp2k_gate"]["evidence"]
        ev["runtime_lock"]["sif_sha256"] = "d" * 64
        result = _verify(full, _reseal(receipt))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "runtime_lock.sif_sha256" in joined
        assert "does not bind the frozen SIF" in joined

    def test_missing_parsed_subfield(self, full: Path) -> None:
        receipt = copy.deepcopy(_load(full))
        del receipt["evidence"]["cp2k_gate"]["evidence"]["parsed"]["energy_eh"]
        result = _verify(full, _reseal(receipt))
        assert result["consistent"] is False
        assert any("energy_eh" in p for p in _problems(result))

    def test_audit_chain_tamper(self, full: Path) -> None:
        audit_path = full / "site-cp2k" / "audit.jsonl"
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        target = next(
            i for i, line in enumerate(lines)
            if json.loads(line).get("event", {}).get("kind") == "SUBMIT_INTENT"
        )
        entry = json.loads(lines[target])
        entry["event"]["operation_id"] = "forged-cp2k-op"
        lines[target] = json.dumps(entry, sort_keys=True)
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = _verify(full, _load(full))
        assert result["consistent"] is False
        assert any("hash chain broken" in p for p in _problems(result))

    def test_output_removed_from_manifest(self, full: Path) -> None:
        """An output the manifest does not vouch for cannot derive, even if
        the bytes on disk would parse cleanly."""
        receipt = copy.deepcopy(_load(full))
        entries = receipt["evidence"]["cp2k_gate"]["evidence"]["job"][
            "fetch_manifest"
        ]["entries"]
        entries[:] = [e for e in entries if e["path"] != "cp2k.out"]
        result = _verify(full, _reseal(receipt))
        assert result["consistent"] is False
        assert any(
            "'cp2k.out' not in fetch_manifest" in p for p in _problems(result)
        )


def _load(golden_root: Path) -> dict:
    return json.loads(
        (golden_root / RECEIT_DIRNAME / "receipt.json").read_text(encoding="utf-8")
    )


def _reseal(mutated: dict) -> dict:
    """Attacker edits the receipt AND recomputes its content digest — no
    schema check, exactly like a real forger shipping raw bytes."""
    mutated.pop("digest", None)
    mutated["digest"] = qr.canonical_digest(mutated)
    return mutated


def _verify(golden_root: Path, receipt: dict) -> dict:
    return qr.verify_receipt(
        receipt,
        root=golden_root,
        receipt_dir=golden_root / RECEIT_DIRNAME,
    )


def _problems(result: dict) -> list[str]:
    return result["problems"]


# -- positives --------------------------------------------------------------------


class TestGoldenDerives:
    def test_golden_receipt_derives_partial(self, golden: Path) -> None:
        result = _verify(golden, _load(golden))
        assert _problems(result) == [], _problems(result)
        derived = result["derived"]
        assert derived["qualification_status"] == "PARTIAL"
        assert derived["formal_qualified"] is False
        # cpu partition accessible again: no ACL constraint blocks this receipt
        assert derived["site_acl_blocked"] is False
        gates = derived["gates"]
        assert gates["cp2k_gate"] == "NOT_RUN"
        assert gates.get("canary_coverage") == "PASS"
        for name, status in gates.items():
            if name != "cp2k_gate":
                assert status == "PASS", (name, status)
        assert result["digest_ok"] is True

    def test_release_builder_sees_blocked_qualification(self, golden: Path) -> None:
        """PARTIAL without the ACL constraint (cpu partition accessible again)
        blocks qualification, not site ACL."""
        checked = check_qualification_receipt(golden)
        assert checked["status"] == "BLOCKED_QUALIFICATION"
        assert "PARTIAL" in checked["detail"]

    def test_missing_receipt_is_blocked_qualification(self, tmp_path: Path) -> None:
        checked = check_qualification_receipt(tmp_path)
        assert checked["status"] == "BLOCKED_QUALIFICATION"

    def test_probe_parser_roundtrip(self) -> None:
        parsed = qr.parse_probe_stdout("\n".join(PROBE_LINES))
        assert parsed["gpu_device_name"] == GPU_NAME
        assert parsed["gpu_memory_total_mb"] == 81920
        assert parsed["probes"]["runs_root_not_listable"] == "pass"


# -- negatives ----------------------------------------------------------------------


class TestTamperedReceiptsFailClosed:
    def test_missing_required_field(self, golden: Path) -> None:
        receipt = copy.deepcopy(_load(golden))
        del receipt["evidence"]["jobs"][0]["exit_code"]
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        assert any("exit_code" in p for p in _problems(result))

    def test_plain_tamper_breaks_content_digest(self, golden: Path) -> None:
        receipt = copy.deepcopy(_load(golden))
        receipt["runtime_lock"]["sif_sha256"] = "f" * 64
        result = _verify(golden, receipt)
        assert result["consistent"] is False
        assert result["digest_ok"] is False
        assert any("digest mismatch" in p for p in _problems(result))

    def test_wrong_runtime_resealed(self, golden: Path) -> None:
        """A swapped SIF digest breaks BOTH the lock anchor and each job's
        runtime_decl binding — even after re-hashing."""
        receipt = copy.deepcopy(_load(golden))
        receipt["runtime_lock"]["sif_sha256"] = "f" * 64
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "runtime_lock.sif_sha256" in joined
        assert "runtime_decl" in joined

    def test_wrong_site_profile_resealed(self, golden: Path) -> None:
        receipt = copy.deepcopy(_load(golden))
        forged = "sha256:" + "b" * 64
        receipt["site_profile_digest"] = forged
        receipt["evidence"]["site_profile_digest"] = forged
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        assert any("SiteProfile digest mismatch" in p for p in _problems(result))

    def test_wrong_job_id_resealed(self, golden: Path) -> None:
        """Rewriting the ledger job id desynchronizes settlement AND the
        durable SUBMIT_ACCEPTED lineage."""
        receipt = copy.deepcopy(_load(golden))
        receipt["evidence"]["jobs"][0]["job_id"] = "job-9999"
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "settlement attempts" in joined
        assert "never accepted" in joined

    def test_audit_chain_tamper_detected(self, golden: Path) -> None:
        """Editing one past ledger entry breaks the hash chain for every
        entry after it."""
        audit_path = golden / "site-cpu" / "audit.jsonl"
        lines = audit_path.read_text(encoding="utf-8").splitlines()
        target = next(
            i for i, line in enumerate(lines)
            if json.loads(line).get("event", {}).get("kind") == "SUBMIT_INTENT"
        )
        entry = json.loads(lines[target])
        entry["event"]["operation_id"] = "forged-op"
        lines[target] = json.dumps(entry, sort_keys=True)
        audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = _verify(golden, _load(golden))
        assert result["consistent"] is False
        assert any("hash chain broken" in p for p in _problems(result))

    def test_scheduler_reports_failure_but_state_claims_success(
        self, golden: Path
    ) -> None:
        """Schema-legal forgery: keep state=SUCCEEDED/exit 0, forge what the
        scheduler actually said — derivation catches it."""
        receipt = copy.deepcopy(_load(golden))
        job = receipt["evidence"]["jobs"][0]
        job["accounting"]["raw_state"] = "FAILED"
        job["accounting"]["exit_code_raw"] = "1:0"
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "not scheduler-COMPLETED" in joined
        assert "exit_code_raw='1:0'" in joined

    def test_nonzero_exit_code_rejected(self, golden: Path) -> None:
        receipt = copy.deepcopy(_load(golden))
        receipt["evidence"]["jobs"][0]["exit_code"] = 1
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False

    def _gpu_job(self, receipt: dict) -> dict:
        return next(
            j for j in receipt["evidence"]["jobs"]
            if j["probe_class"] == "gpu"
        )

    def test_tres_allocation_mismatch(self, golden: Path) -> None:
        """Zero GPUs allocated against a requested GPU job — the C4 gate."""
        receipt = copy.deepcopy(_load(golden))
        self._gpu_job(receipt)["accounting"]["alloc_tres"] = "cpu=8,mem=64G"
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "AllocTRES shows 0 GPUs but 1 were requested" in joined
        assert result["derived"]["gates"]["tres_reconciliation"] == "FAIL"

    def test_requested_memory_must_match_scheduler_tres(self, golden: Path) -> None:
        """A declared 32 GiB request cannot derive from Slurm's 48 GiB default."""
        receipt = copy.deepcopy(_load(golden))
        job = self._gpu_job(receipt)
        job["requested_resources"]["memory_gb"] = 32
        job["accounting"]["req_tres"] = "gpu:tesla=1,cpu=8,mem=48G"
        job["accounting"]["alloc_tres"] = "gpu:tesla=1,cpu=8,mem=48G"
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        assert any("expected 32G" in p for p in _problems(result))

    def test_requested_cpu_must_match_scheduler_tres(self, golden: Path) -> None:
        receipt = copy.deepcopy(_load(golden))
        job = self._gpu_job(receipt)
        job["accounting"]["req_tres"] = "gpu:tesla=1,cpu=4,mem=64G"
        job["accounting"]["alloc_tres"] = "gpu:tesla=1,cpu=4,mem=64G"
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        assert any("expected 8" in p for p in _problems(result))

    def test_actual_partition_must_be_in_resolved_set(self, golden: Path) -> None:
        receipt = copy.deepcopy(_load(golden))
        self._gpu_job(receipt)["accounting"]["partition"] = "unqualified-gpu"
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        assert any("outside the resolved set" in p for p in _problems(result))

    def test_cpu_job_carrying_gpu_allocation(self, golden: Path) -> None:
        """The inverse drift: a zero-GPU cpu canary must not show GPU terms
        in either TRES string (the ACL-era routing is gone)."""
        receipt = copy.deepcopy(_load(golden))
        cpu_job = next(
            j for j in receipt["evidence"]["jobs"]
            if j["probe_class"] == "cpu"
        )
        cpu_job["accounting"]["req_tres"] = "gpu:tesla=1,cpu=8,mem=64G"
        cpu_job["accounting"]["alloc_tres"] = "gpu:tesla=1,cpu=8,mem=64G"
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "ReqTRES shows 1 GPUs but the spec requested 0" in joined
        assert "AllocTRES shows 1 GPUs but 0 were requested" in joined

    def test_canary_coverage_missing(self, tmp_path: Path) -> None:
        """A receipt whose jobs never cover both routes cannot derive — the
        qualification must prove cpu AND gpu paths."""
        golden_root = tmp_path
        _build_golden(golden_root)
        receipt = _load(golden_root)
        receipt["evidence"]["jobs"] = [
            j for j in receipt["evidence"]["jobs"] if j["probe_class"] == "gpu"
        ]
        result = _verify(golden_root, _reseal(receipt))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "must include both probe classes" in joined
        assert result["derived"]["qualification_status"] == "INVALID"

    def test_cpu_job_claiming_gpu_probe_fields_rejected(
        self, golden: Path
    ) -> None:
        """Schema-level: probe_results shape follows probe_class; a cpu job
        smuggling GPU fields violates the conditional contract."""
        receipt = copy.deepcopy(_load(golden))
        cpu_job = next(
            j for j in receipt["evidence"]["jobs"]
            if j["probe_class"] == "cpu"
        )
        cpu_job["probe_results"]["gpu_device_name"] = GPU_NAME
        cpu_job["probe_results"]["gpu_memory_total_mb"] = 81920
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False

    def test_fetch_artifact_byte_flip(self, golden: Path) -> None:
        artifact = golden / "artifacts-cpu" / "stdout.log"
        data = artifact.read_bytes()
        artifact.write_bytes(data + b"# tampered\n")
        result = _verify(golden, _load(golden))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "artifact digest drift" in joined or "artifact size drift" in joined

    def test_fetch_manifest_hash_forged(self, golden: Path) -> None:
        receipt = copy.deepcopy(_load(golden))
        receipt["evidence"]["jobs"][0]["fetch_manifest"]["entries"][0]["sha256"] = (
            "c" * 64
        )
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        assert any("artifact digest drift" in p for p in _problems(result))

    def test_settlement_block_missing(self, golden: Path) -> None:
        receipt = copy.deepcopy(_load(golden))
        del receipt["evidence"]["jobs"][0]["settlement"]
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False

    def test_settlement_attempts_empty(self, golden: Path) -> None:
        """Inner-report forgery with a recomputed inner digest still fails:
        the report must reproduce the job's own attempt lineage."""
        receipt = copy.deepcopy(_load(golden))
        job = receipt["evidence"]["jobs"][0]
        job["settlement"]["report"]["attempts"] = []
        job["settlement"]["digest"] = qr.compute_settlement_digest(
            job["settlement"]["report"]
        )
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        joined = " | ".join(_problems(result))
        assert "settlement attempts" in joined

    def test_settlement_cancels_present(self, golden: Path) -> None:
        receipt = copy.deepcopy(_load(golden))
        job = receipt["evidence"]["jobs"][0]
        job["settlement"]["report"]["cancelled_jobs"] = ["job-0002"]
        job["settlement"]["digest"] = qr.compute_settlement_digest(
            job["settlement"]["report"]
        )
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        assert any("cancelled_jobs" in p for p in _problems(result))

    def test_bare_cp2k_status_declaration_rejected(self, golden: Path) -> None:
        """The verdict-free contract has no cp2k status field: smuggling one
        in violates the schema even after re-hashing."""
        receipt = copy.deepcopy(_load(golden))
        receipt["evidence"]["cp2k_gate"] = {
            "status": "PASS",
            "detail": "energy=-76.352 Eh (forged)",
        }
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        assert any("schema violation" in p for p in _problems(result))
        assert result["derived"]["qualification_status"] == "INVALID"
        assert result["derived"]["formal_qualified"] is False

    def test_partial_disguised_as_pass_is_structurally_impossible(
        self, golden: Path
    ) -> None:
        """The verdict fields do not exist in the contract; smuggling them in
        violates the schema, and the derivation stays PARTIAL/false."""
        receipt = copy.deepcopy(_load(golden))
        receipt["qualification_status"] = "PASS"
        receipt["formal_qualified"] = True
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
        assert any("schema violation" in p for p in _problems(result))
        derived = result["derived"]
        assert derived["formal_qualified"] is False

    def test_code_identity_drift_after_release(self, golden: Path) -> None:
        """Any trusted module changing after qualification invalidates the
        receipt without touching it."""
        target = golden / qr.CODE_IDENTITY_PATHS[2]  # dispatcher.py stub
        target.write_text("# drifted\n", encoding="utf-8")
        result = _verify(golden, _load(golden))
        assert result["consistent"] is False
        assert any("code identity changed" in p for p in _problems(result))

    def test_missing_cluster_profile_is_fail_closed(self, golden: Path) -> None:
        """Without the private profile the site anchor is undecidable — never
        silently trusted."""
        (golden / qr.DEFAULT_PROFILE_RELPATH).unlink()
        result = _verify(golden, _load(golden))
        assert result["consistent"] is False
        assert any("anchor undecidable" in p for p in _problems(result))

    def test_leftover_sentinel_rejected_by_schema(self, golden: Path) -> None:
        receipt = copy.deepcopy(_load(golden))
        receipt["evidence"]["sentinel_cleanup"]["leftover"] = [
            "/home/<site-user>/.bench-sentinel-leftover"
        ]
        result = _verify(golden, _reseal(receipt))
        assert result["consistent"] is False
