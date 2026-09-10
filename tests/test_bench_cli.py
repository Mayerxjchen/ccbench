"""bench thin CLI: one lifecycle entry, zero duplicated runner logic.

The CLI must orchestrate existing modules, not reimplement them, and the
private cluster profile must never live inside the repository.
"""

from __future__ import annotations

import json
from pathlib import Path


import bench.cli as cli


def test_setup_reports_environment_and_runtime_locks(capsys):
    code = cli.main(["setup", "--quiet"])
    out = capsys.readouterr().out
    assert code == 0
    report = json.loads(out)
    assert report["python_at_least_3_11"] is True
    assert report["template_present"] is True
    assert "cp2k" in report["runtime_locks"]
    assert "ai2kit" in report["runtime_locks"]


def test_site_configure_refuses_profile_inside_repo(tmp_path, capsys):
    code = cli.main(["site", "configure", "--out", str(Path(cli.ROOT) / "cluster_profile.toml")])
    assert code == 2
    assert "OUTSIDE the repository" in capsys.readouterr().err


def test_site_configure_refuses_overwrite(tmp_path):
    out = tmp_path / "cluster_profile.toml"
    out.write_text("existing")
    assert cli.main(["site", "configure", "--out", str(out)]) == 2


def test_site_configure_copies_and_validates_template(tmp_path, capsys):
    out = tmp_path / "nest" / "cluster_profile.toml"
    code = cli.main(["site", "configure", "--out", str(out), "--validate"])
    assert code == 0
    assert out.is_file()
    assert "template copied" in capsys.readouterr().err


def test_site_qualify_forwards_to_qualify_case(monkeypatch):
    from scripts.qualification import qualify_case

    seen: list[list[str]] = []

    def fake_main(argv):
        seen.append(list(argv))
        return 7

    monkeypatch.setattr(qualify_case, "main", fake_main)
    assert cli.main([
        "site", "qualify", "--profile", "/tmp/p.toml", "--case", "034", "--dry-run",
    ]) == 7
    assert seen[0] == ["--profile", "/tmp/p.toml", "--case", "034", "--dry-run"]


def test_eval_compatibility_entry_forwards_to_bench_run(monkeypatch):
    import importlib

    entry = importlib.import_module("eval")
    seen: list[list[str]] = []

    def fake_main(argv):
        seen.append(list(argv))
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    assert entry.main(["/tmp/paper-suite/cases/001", "--no-evaluate"]) == 0
    assert seen == [["run", "/tmp/paper-suite/cases/001", "--no-evaluate"]]


def test_run_starts_and_automatically_evaluates(monkeypatch, tmp_path, capsys):
    from bench import pilot

    seen: dict[str, object] = {}

    def fake_start(case, **kwargs):
        seen["case"] = case
        seen["start"] = kwargs
        return {"state": "CANDIDATE_COMPLETE"}

    def fake_evaluate(run_dir):
        seen["evaluate"] = run_dir
        return {"state": {"state": "EVALUATED"}, "result": {"passed": True}}

    monkeypatch.setattr(pilot, "start", fake_start)
    monkeypatch.setattr(pilot, "evaluate", fake_evaluate)
    out = tmp_path / "run"
    assert cli.main(["run", "case-001", "--out", str(out)]) == 0
    assert seen["case"] == "case-001"
    assert seen["evaluate"] == out
    assert json.loads(capsys.readouterr().out)["result"]["passed"] is True


def test_cli_is_pure_forwarding_no_new_logic():
    """The CLI module must not reach the gateway/adapter/transport layers."""
    text = Path(cli.__file__).read_text(encoding="utf-8")
    for forbidden in ("Gateway(", "SlurmAdapter(", "subprocess", "sbatch"):
        assert forbidden not in text, forbidden


def test_compute_configure_and_validate(tmp_path, capsys):
    out = tmp_path / "my_compute_profile.json"
    code = cli.main(["compute", "configure", "--out", str(out)])
    assert code == 0
    assert out.is_file()
    assert "template copied" in capsys.readouterr().err

    # Validate
    val_code = cli.main(["compute", "validate", "--profile", str(out)])
    assert val_code == 0
    assert "validates" in capsys.readouterr().err


def test_compute_configure_refuses_inside_repo(capsys):
    inside = Path(cli.ROOT) / "my_compute_profile.json"
    code = cli.main(["compute", "configure", "--out", str(inside)])
    assert code == 2
    assert "OUTSIDE the repository" in capsys.readouterr().err


def test_compute_qualify_cli(tmp_path, capsys, monkeypatch):
    import json
    from bench.experiments.compute_profile_qualification import (
        build_compute_profile_qualification_receipt,
    )
    from bench.hpc.compute_profile import ComputeProfile

    out = tmp_path / "hybrid.json"
    cli.main(["compute", "configure", "--template", "maintainer-hybrid", "--out", str(out)])
    capsys.readouterr()

    # 1. Fail closed if no receipt is provided
    code_no_rcpt = cli.main(["compute", "qualify", "--profile", str(out)])
    assert code_no_rcpt == 2
    assert "requires an official receipt" in capsys.readouterr().err

    # 2. Fail closed if --site-receipts-dir is missing for hybrid profile
    rcpt_file = tmp_path / "receipt.json"
    rcpt_file.write_text(json.dumps({"compute_profile_id": "mock"}))
    code_no_dir = cli.main(["compute", "qualify", "--profile", str(out), "--receipt", str(rcpt_file)])
    assert code_no_dir == 2
    assert "requires --site-receipts-dir" in capsys.readouterr().err

    # 3. Even with materialized site receipts, formal qualification requires
    # explicit operator trust anchors; the CLI must not fall back to examples
    # or the repository trust store.
    import hashlib
    site_dir = tmp_path / "site_receipts"
    site_dir.mkdir(parents=True, exist_ok=True)
    cpu_bytes = json.dumps({"site_id": "ikkem-cpu", "verdict": "PASS"}, sort_keys=True).encode("utf-8")
    gpu_bytes = json.dumps({"site_id": "compshare-gpu", "verdict": "PASS"}, sort_keys=True).encode("utf-8")
    (site_dir / "ikkem-cpu.receipt.json").write_bytes(cpu_bytes)
    (site_dir / "compshare-gpu.receipt.json").write_bytes(gpu_bytes)
    cpu_sha = f"sha256:{hashlib.sha256(cpu_bytes).hexdigest()}"
    gpu_sha = f"sha256:{hashlib.sha256(gpu_bytes).hexdigest()}"

    prof = ComputeProfile.from_file(out)
    receipt_doc = build_compute_profile_qualification_receipt(
        compute_profile_id=prof.profile_id,
        compute_profile_digest=prof.digest,
        routes=prof.routes,
        site_receipts={
            prof.routes.get("cpu", "cpu"): cpu_sha,
            prof.routes.get("gpu", "gpu"): gpu_sha,
        },
        cloud_recycling_evidence={
            "stock_checked": True,
            "instance_id": "inst-qual-probe",
            "image_id": "img-deepmd-gpu-v1",
            "gpu_type": "rtx4090",
            "gpu_vram_gb": 24,
            "task_executed": True,
            "fetch_verified": True,
            "credentials_isolated": True,
            "settlement_terminated": True,
            "active_instances_count": 0,
            "orphan_instances_count": 0,
        },
    )
    rcpt_file.write_text(json.dumps(receipt_doc, indent=2))

    # Mock live cloud instance_list to return 0 active instances
    from bench.hpc.drivers.compshare.cli import CompShareCli
    monkeypatch.setattr(CompShareCli, "instance_list", lambda self, **kw: [])

    # Mock verify_site_receipt to return successful derivation
    def _mock_verify_site_receipt(receipt, *, scheduler=None, root, receipt_dir, **kwargs):
        return {
            "receipt_dir": str(receipt_dir),
            "digest_ok": True,
            "problems": {},
            "derived": {
                "qualification_status": "PASS",
                "formal_qualified": True,
                "capabilities": {"dispatcher.cpu": "PASS", "dispatcher.gpu": "PASS"},
                "gates": {},
            },
        }
    from bench.experiments import compute_profile_qualification
    monkeypatch.setattr(compute_profile_qualification, "verify_site_receipt", _mock_verify_site_receipt)

    code = cli.main([
        "compute", "qualify",
        "--profile", str(out),
        "--receipt", str(rcpt_file),
        "--site-receipts-dir", str(site_dir),
    ])
    assert code == 2
    assert "--trust-store" in capsys.readouterr().err


def test_run_reports_compute_handoff_without_evaluating(monkeypatch, tmp_path, capsys):
    from bench import pilot

    monkeypatch.setattr(
        pilot,
        "start",
        lambda case, **kwargs: {"state": "COMPUTE_REQUIRED", "compute_requests": ["request.json"]},
    )
    monkeypatch.setattr(
        pilot,
        "evaluate",
        lambda run_dir: (_ for _ in ()).throw(AssertionError("must not evaluate before compute returns")),
    )
    out = tmp_path / "run"
    assert cli.main(["run", "case-002", "--out", str(out)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"]["state"] == "COMPUTE_REQUIRED"
    assert "bench pilot resume" in payload["next_action"]
