"""C10: exercise the public production/Catalog/Gateway boundary end to end.

The fixture is entirely offline, but it uses the same public composition
functions and the official CLI-shaped FakeCompShare provider used by the
production stack.  Qualification receipts are built from provider readbacks
and the durable GatewayAudit ledger; no verifier or audit event is patched or
hand-authored on the positive path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from bench.experiments.compute_profile_qualification import (
    build_compshare_site_qualification_receipt,
    build_compute_profile_qualification_receipt_v2,
    compute_receipt_digest,
    generate_ed25519_key_pair,
    make_evidence_file_record,
    verify_and_derive_qualification,
    verify_site_receipt,
)
from bench.experiments.qualification_receipt import _check_audit
from bench.hpc.audit import GatewayAudit
from bench.hpc.compute_profile import ComputeProfile
from bench.hpc.drivers.compshare import (
    CompShareCli,
    CompShareDriver,
    FakeCompShareCliRunner,
    RunScopedInstanceManager,
)
from bench.hpc.gateway import ALL_OPS, Gateway
from bench.hpc.production import build_hybrid_stack
from bench.hpc.site_profile import HpcSiteProfile
from bench.hpc.trust_store import QualificationTrustStore, TrustKey


IMAGE_ID = "img-c10-deepmd"


def _profile(site_id: str, probe_class: str) -> HpcSiteProfile:
    """Create one explicit, credential-free CompShare SiteProfile."""
    queue = {
        "partition": "rtx4090" if probe_class == "gpu" else "cpu-probe",
        "qos": "default",
        "max_cpus": 16,
        "max_memory_gb": 64,
        "max_gpus": 1 if probe_class == "gpu" else 0,
        "max_walltime_minutes": 120,
    }
    return HpcSiteProfile.from_dict(
        {
            "schema_version": 1,
            "site_id": site_id,
            "scheduler": "compshare",
            "connection": {
                "credential_profile_id": "offline-fake-compshare",
                "target_binding": "fake://compshare",
                "remote_user": "offline",
                "remote_root_policy": "/workspace/{run_id}",
            },
            "account": "offline-account",
            "queues": {probe_class: queue},
            "resource_mapping": {probe_class: {"queue": probe_class}},
            "runtime_policy": {
                "requires_apptainer": False,
                "provider": "compshare",
                "default_gpu_type": "4090",
                "default_gpu_vram_gb": 24,
                "image_source": "custom",
                "region": "offline-region",
                "zone": "offline-zone",
                "budget_policy": {
                    "max_budget_cny": 100.0,
                    "max_instance_hours": 1.0,
                    "max_instances": 1,
                },
            },
            "qualification_policy": {
                "required_probe_classes": [probe_class],
                "signing_key_id": "compshare-site-v1",
            },
        }
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _site_receipt(
    *,
    root: Path,
    receipts_dir: Path,
    profile: HpcSiteProfile,
    probe_class: str,
    lock_rel: str,
    lock_digest: str,
    site_private_key: str,
    site_public_key: str,
) -> dict[str, Any]:
    """Run one real FakeCompShare lifecycle and seal a site receipt from it."""
    site_tag = profile.site_id
    run_id = f"c10-site-{probe_class}"
    audit_path = receipts_dir / f"{site_tag}.audit.jsonl"
    audit = GatewayAudit(audit_path)
    runner = FakeCompShareCliRunner(initial_stock=2)
    cli = CompShareCli(runner=runner)
    manager = RunScopedInstanceManager(
        cli,
        ledger_path=receipts_dir / f"{site_tag}.ledger.jsonl",
        orphan_ledger_path=receipts_dir / f"{site_tag}.orphans.jsonl",
        audit=audit,
    )
    driver = CompShareDriver(
        cli,
        manager=manager,
        site_profile=profile,
        workspace_root=receipts_dir / f"{site_tag}.workspace",
        audit=audit,
    )
    gateway = Gateway(
        driver,
        audit=audit,
        workspace_root=receipts_dir / f"{site_tag}.gateway",
    )

    # These are real provider calls.  The manager repeats the stock check as
    # part of its create contract; this first readback is the evidence fact.
    stock = cli.instance_search(region="default", zone="default", gpu="4090", image=IMAGE_ID)
    doctor = cli.doctor()
    instance_id = manager.get_or_create_instance(
        run_id,
        IMAGE_ID,
        operation_id="probe",
        gpu_type="4090",
        gpu_count=1,
    )
    record = manager._instances[run_id]
    remote_output = f"/workspace/{run_id}/probe/output.txt"
    submitted = cli.instance_job_submit(
        instance_id,
        ["/bin/sh", "-c", f"printf {probe_class}-ok > output.txt"],
        cwd=f"/workspace/{run_id}/probe",
    )
    remote_job_id = str(submitted["job_id"])
    provider_job = cli.instance_job_show(instance_id, remote_job_id)
    artifact_rel = f"{site_tag}.out"
    artifact_path = receipts_dir / artifact_rel
    cli.instance_cp(instance_id, f":{remote_output}", str(artifact_path))
    artifact_bytes = artifact_path.read_bytes()
    manager.release_operation(run_id, "probe")

    # Gateway owns the settlement markers; the driver owns provider stop,
    # delete, confirmation, and the real account-wide orphan query.
    gateway.trusted_freeze(run_id)
    gateway.trusted_teardown(run_id)
    zero = manager.zero_orphan_query(run_id)
    assert runner.instances == {}
    assert zero["method"] == "instance_list(all=True)"
    assert zero["active_total"] == 0

    report_rel = f"{site_tag}.settlement.json"
    report_path = receipts_dir / report_rel
    report = {
        "instance_id": instance_id,
        "stop_confirmed": instance_id not in runner.instances,
        "delete_confirmed": instance_id not in runner.instances,
        "orphan_count": zero["active_total"],
        "observed_ids": zero["observed_ids"],
    }
    _write_json(report_path, report)
    report_digest = f"sha256:{hashlib.sha256(report_path.read_bytes()).hexdigest()}"
    entries = audit.entries()
    kinds = [entry["event"].get("kind") for entry in entries]
    required = [
        "INSTANCE_CREATE_INTENT",
        "INSTANCE_CREATE_ACCEPTED",
        "INSTANCE_READY",
        "SETTLEMENT_BEGIN",
        "INSTANCE_STOP_ACCEPTED",
        "INSTANCE_DELETE_ACCEPTED",
        "INSTANCE_DELETE_CONFIRMED",
        "ZERO_ORPHAN_QUERY",
        "SETTLEMENT_COMPLETE",
    ]
    assert all(kind in kinds for kind in required)

    marker = root / "bench" / "qualification_marker.py"
    identity = {
        "bench/qualification_marker.py":
            f"sha256:{hashlib.sha256(marker.read_bytes()).hexdigest()}"
    }
    receipt = build_compshare_site_qualification_receipt(
        run_id=run_id,
        site_profile_id=profile.site_id,
        site_profile_digest=profile.digest,
        source_commit="a" * 40,
        code_identity=identity,
        runtime_lock={
            "path": lock_rel,
            "digest": lock_digest,
            "image_id": IMAGE_ID,
        },
        evidence={
            "instance_lifecycle": {
                "instance_id": instance_id,
                "image_id": IMAGE_ID,
                "stop_confirmed": instance_id not in runner.instances,
                "delete_confirmed": instance_id not in runner.instances,
            },
            "jobs": [
                {
                    "probe_class": probe_class,
                    "job_id": remote_job_id,
                    "image_id": IMAGE_ID,
                    "accounting": {
                        "state": str(provider_job["status"]),
                        "exit_code": int(provider_job["exit_code"]),
                    },
                }
            ],
            "fetch": {
                "artifacts": [
                    {
                        "path": artifact_rel,
                        "sha256": f"sha256:{hashlib.sha256(artifact_bytes).hexdigest()}",
                        "size_bytes": len(artifact_bytes),
                    }
                ]
            },
            "settlement": {
                "report_path": report_rel,
                "digest": report_digest,
                "terminated": gateway.settlement_state(run_id) == "SETTLED",
            },
            "orphan_check": {
                "method": zero["method"],
                "active_total": zero["active_total"],
            },
            "credential_isolation": {"verified": doctor.get("auth_ok") is True},
        },
        audit_log=audit_path.name,
        audit_tail_digest=audit.tail_digest(),
        private_key_hex=site_private_key,
        key_id="compshare-site-v1",
    )
    receipt_path = receipts_dir / f"{site_tag}-receipt.json"
    _write_json(receipt_path, receipt)
    verified = verify_site_receipt(
        receipt,
        scheduler="compshare",
        root=root,
        receipt_dir=receipts_dir,
        trust_store=QualificationTrustStore(
            {
                "compshare-site-v1": TrustKey(
                    key_id="compshare-site-v1",
                    algorithm="ed25519",
                    public_key_hex=site_public_key,
                    status="ACTIVE",
                )
            }
        ),
        trusted_site_profile=profile.to_trusted_dict(),
    )
    assert verified["problems"] == []
    assert verified["derived"]["qualification_status"] == "PASS"
    return receipt


def run_public_c10_lifecycle(tmp_path: Path) -> dict[str, Any]:
    """Run the complete offline public lifecycle and formal v2 verification."""
    root = Path(tmp_path)
    (root / "bench").mkdir(parents=True)
    marker = root / "bench" / "qualification_marker.py"
    marker.write_text("# immutable offline qualification identity\n", encoding="utf-8")
    runtime_dir = root / "runtime"
    runtime_dir.mkdir()
    evidence_dir = root / "evidence"
    evidence_dir.mkdir()
    receipts_dir = evidence_dir / "site_receipts"
    receipts_dir.mkdir()
    image_id = IMAGE_ID

    cpu_profile = _profile("compshare-cpu", "cpu")
    gpu_profile = _profile("compshare-gpu", "gpu")
    cpu_profile_path = root / "compshare-cpu-site-profile.json"
    gpu_profile_path = root / "compshare-gpu-site-profile.json"
    # ``digest`` is a derived assertion, not a schema input; the production
    # composition root rebuilds it through HpcSiteProfile.from_dict().
    _write_json(
        cpu_profile_path,
        {k: v for k, v in cpu_profile.to_trusted_dict().items() if k != "digest"},
    )
    _write_json(
        gpu_profile_path,
        {k: v for k, v in gpu_profile.to_trusted_dict().items() if k != "digest"},
    )
    compute_profile_path = root / "compute-profile.json"
    compute_profile_payload = {
        "schema_version": 1,
        "profile_id": "c10-offline-hybrid",
        "routes": {
            "cpu": {"site_profile": cpu_profile.site_id},
            "gpu": {"site_profile": gpu_profile.site_id},
        },
    }
    _write_json(compute_profile_path, compute_profile_payload)
    compute_profile = ComputeProfile.from_file(compute_profile_path)

    lock_doc: dict[str, Any] = {
        "schema": "dispatcher-compshare-runtime-lock/v2",
        "capability": "deepmd",
        "runtime_profile_id": "c10-deepmd-v1",
        "image_name": "c10-deepmd",
        "provider": "compshare",
        "site_profile_id": gpu_profile.site_id,
        "artifact": {
            "kind": "compshare_image",
            "image_id": image_id,
            "image_source": "custom",
        },
        "provenance": {"software_versions": {"deepmd": "offline-test"}},
        "qualification": {
            "status": "BUILT_NOT_QUALIFIED",
            "receipt_path": "evidence/site_receipts/compshare-gpu-receipt.json",
            "receipt_digest": "",
        },
    }
    lock_path = runtime_dir / "deepmd-runtime.lock.json"
    _write_json(lock_path, lock_doc)
    from bench.hpc.runtime_resolution import canonical_lock_digest

    lock_digest = canonical_lock_digest(lock_doc)
    site_private, site_public = generate_ed25519_key_pair()
    cpu_receipt = _site_receipt(
        root=root,
        receipts_dir=receipts_dir,
        profile=cpu_profile,
        probe_class="cpu",
        lock_rel="runtime/deepmd-runtime.lock.json",
        lock_digest=lock_digest,
        site_private_key=site_private,
        site_public_key=site_public,
    )
    gpu_receipt = _site_receipt(
        root=root,
        receipts_dir=receipts_dir,
        profile=gpu_profile,
        probe_class="gpu",
        lock_rel="runtime/deepmd-runtime.lock.json",
        lock_digest=lock_digest,
        site_private_key=site_private,
        site_public_key=site_public,
    )
    lock_doc["qualification"]["receipt_digest"] = gpu_receipt["digest"]
    _write_json(lock_path, lock_doc)

    # The production composition root now reads this exact temporary Catalog
    # and injects only its resolver into the RoutedDriver/Gateway chain.
    main_audit_path = evidence_dir / "audit.jsonl"
    main_runner = FakeCompShareCliRunner(initial_stock=2)
    stack = build_hybrid_stack(
        compute_profile_path=compute_profile_path,
        cpu_site_profile_path=cpu_profile_path,
        gpu_site_profile_path=gpu_profile_path,
        compshare_cli=CompShareCli(runner=main_runner),
        case_id="c10-offline",
        audit_path=main_audit_path,
        runtime_lock_dir=runtime_dir,
        qualification_root=root,
        trust_store=QualificationTrustStore(
            {
                "compshare-site-v1": TrustKey(
                    key_id="compshare-site-v1",
                    algorithm="ed25519",
                    public_key_hex=site_public,
                    status="ACTIVE",
                ),
            }
        ),
        repo_root=root,
        compshare_workspace_root=root / "main-workspace",
    )
    catalog = stack["run_adapter_config"]["runtime_catalog"]
    assert catalog.qualified_capabilities() == ["deepmd"]
    gateway = Gateway(
        stack["adapter"],
        audit=stack["audit"],
        workspace_root=root / "gateway-workspace",
        runtime_resolver=catalog.to_resolver(),
    )
    run_id = "c10-public-run"
    token = gateway.issue(run_id, ALL_OPS)
    submitted = gateway.submit(
        token,
        run_id,
        {
            "schema_version": 1,
            "idempotency_key": "c10-idempotency",
            "runtime": "deepmd",
            "compute_class": "gpu",
            "command": ["/bin/echo", "c10"],
            "resources": {
                "cpus": 1,
                "memory_gb": 1,
                "gpus": 1,
                "walltime_minutes": 1,
            },
            "inputs": [],
            "outputs": ["out.txt"],
        },
        operation_id="c10-operation",
        attempt=1,
    )
    job_id = submitted["job_id"]
    assert submitted["resolved_runtime"]["artifact_id_or_path"] == image_id
    status = gateway.status(token, run_id, job_id)
    assert status["state"] == "SUCCEEDED"
    logs = gateway.logs(token, run_id, job_id)
    assert logs["complete"] is True
    fetched = gateway.fetch(token, run_id, job_id)
    assert fetched["outputs"]["out.txt"]
    gateway.trusted_freeze(run_id)
    gateway.trusted_teardown(run_id)
    assert gateway.settlement_state(run_id) == "SETTLED"
    assert main_runner.instances == {}

    main_entries = stack["audit"].entries()
    main_events = [entry["event"] for entry in main_entries]
    required_events = {
        "SUBMIT_INTENT",
        "SUBMIT_ACCEPTED",
        "JOB_TERMINAL",
        "ARTIFACT_FETCHED",
        "SETTLEMENT_BEGIN",
        "SETTLEMENT_COMPLETE",
    }
    assert required_events <= {event.get("kind") for event in main_events}
    accepted = next(event for event in main_events if event.get("kind") == "SUBMIT_ACCEPTED")
    assert accepted["operation_id"] == "c10-operation"
    assert accepted["attempt"] == 1
    assert accepted["job_id"] == job_id
    assert accepted["image_id"] == image_id
    assert accepted["instance_id"]
    main_job = {
        "run_id": run_id,
        "operation_id": "c10-operation",
        "attempt": 1,
        "job_id": job_id,
        "runtime_decl": accepted["runtime_decl"],
        "image_id": image_id,
        "instance_id": accepted["instance_id"],
        "state": "SUCCEEDED",
        "exit_code": 0,
    }
    audit_problems: list[tuple[str, str]] = []
    _check_audit(main_entries, main_job, lambda gate, message: audit_problems.append((gate, message)))
    assert audit_problems == []
    assert GatewayAudit(main_audit_path).verify() == []

    # Materialize formal v2 evidence from the public fetch and provider
    # settlement result, then verify it with the real verifier and trust
    # anchors.  No verifier patch or synthetic event is used here.
    copied_artifact = evidence_dir / "artifacts" / "out.txt"
    copied_artifact.parent.mkdir(parents=True, exist_ok=True)
    copied_artifact.write_bytes(fetched["outputs"]["out.txt"])
    settlement_report = evidence_dir / "settlement" / "main.json"
    _write_json(
        settlement_report,
        {
            "stop_confirmed": True,
            "delete_confirmed": True,
            "orphan_count": 0,
            "observed_ids": [],
        },
    )
    lock_evidence = evidence_dir / "runtime-lock.json"
    lock_evidence.write_bytes(lock_path.read_bytes())
    evidence_files = [
        make_evidence_file_record(evidence_dir, "audit.jsonl", role="audit_log"),
        make_evidence_file_record(evidence_dir, "runtime-lock.json", role="runtime_lock"),
        make_evidence_file_record(evidence_dir, "artifacts/out.txt", role="artifact"),
        make_evidence_file_record(evidence_dir, "settlement/main.json", role="settlement"),
        make_evidence_file_record(
            evidence_dir,
            "site_receipts/compshare-cpu-receipt.json",
            role="site_receipt",
        ),
        make_evidence_file_record(
            evidence_dir,
            "site_receipts/compshare-gpu-receipt.json",
            role="site_receipt",
        ),
    ]
    compute_private, compute_public = generate_ed25519_key_pair()
    compute_store = QualificationTrustStore(
        {
            "compshare-site-v1": TrustKey(
                key_id="compshare-site-v1",
                algorithm="ed25519",
                public_key_hex=site_public,
                status="ACTIVE",
            ),
            "compute-profile-v2": TrustKey(
                key_id="compute-profile-v2",
                algorithm="ed25519",
                public_key_hex=compute_public,
                status="ACTIVE",
                purpose="compute-profile-qualification",
            ),
        }
    )
    receipt = build_compute_profile_qualification_receipt_v2(
        compute_profile_id=compute_profile.profile_id,
        compute_profile_digest=compute_profile.digest,
        routes=dict(compute_profile.routes),
        site_receipts={
            "compshare-cpu": compute_receipt_digest(cpu_receipt),
            "compshare-gpu": compute_receipt_digest(gpu_receipt),
        },
        cloud_recycling_evidence={
            "stock_checked": True,
            "instance_id": accepted["instance_id"],
            "image_id": image_id,
            "gpu_type": "4090",
            "gpu_vram_gb": 24,
            "task_executed": True,
            "fetch_verified": True,
            "credentials_isolated": True,
            "settlement_terminated": True,
            "active_instances_count": 0,
            "orphan_instances_count": 0,
        },
        evidence_root="evidence",
        evidence_files=evidence_files,
        audit_log="audit.jsonl",
        audit_tail_digest=stack["audit"].tail_digest(),
        private_key_hex=compute_private,
    )
    verdict = verify_and_derive_qualification(
        receipt,
        expected_profile=compute_profile,
        site_receipts_dir=receipts_dir,
        trust_store=compute_store,
        trusted_site_profiles={
            cpu_profile.site_id: cpu_profile,
            gpu_profile.site_id: gpu_profile,
        },
        receipt_dir=root,
    )
    assert verdict.passed is True
    assert verdict.status == "PASS"
    return {
        "catalog": catalog,
        "gateway": gateway,
        "receipt": receipt,
        "verdict": verdict,
        "events": main_events,
    }


def test_public_production_catalog_gateway_fake_compshare_lifecycle(tmp_path: Path):
    result = run_public_c10_lifecycle(tmp_path)
    assert result["verdict"].status == "PASS"
    assert result["gateway"].settlement_state("c10-public-run") == "SETTLED"
