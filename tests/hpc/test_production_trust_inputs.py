"""Production composition must receive runtime trust inputs explicitly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dftworld_bench.hpc import production
from dftworld_bench.hpc.trust_store import QualificationTrustStore


def _cluster_with_runtime_lock(tmp_path: Path) -> tuple[Path, Path]:
    template = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "hpc"
        / "cluster_profile.toml"
    )
    lock_dir = tmp_path / "runtime-locks"
    lock_dir.mkdir()
    (lock_dir / "probe-runtime.lock.json").write_text(
        json.dumps(
            {
                "schema": "dispatcher-compshare-runtime-lock/v2",
                "capability": "probe",
                "runtime_profile_id": "probe-v1",
                "provider": "compshare",
                "artifact": {
                    "kind": "compshare_image",
                    "image_id": "img-probe",
                },
                "qualification": {"status": "NOT_RUN"},
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "cluster.toml"
    config.write_text(
        template.read_text(encoding="utf-8").replace(
            'lock_dir = "reference/runtime"',
            f'lock_dir = "{lock_dir}"',
        ),
        encoding="utf-8",
    )
    return config, lock_dir


def test_slurm_stack_rejects_missing_formal_runtime_inputs(tmp_path: Path) -> None:
    config, _lock_dir = _cluster_with_runtime_lock(tmp_path)

    with pytest.raises(RuntimeError, match="explicit qualification_root"):
        production.build_slurm_stack(
            cluster_profile_path=config,
            case_id="trust-inputs",
            audit_path=tmp_path / "audit.jsonl",
        )

    with pytest.raises(RuntimeError, match="explicit qualification trust_store"):
        production.build_slurm_stack(
            cluster_profile_path=config,
            case_id="trust-inputs",
            audit_path=tmp_path / "audit.jsonl",
            qualification_root=tmp_path,
        )


def test_slurm_stack_catalog_uses_explicit_root_and_trust_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _lock_dir = _cluster_with_runtime_lock(tmp_path)

    class FakeTransport:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAdapter:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr(production, "SshSlurmTransport", FakeTransport)
    monkeypatch.setattr(production, "SlurmAdapter", FakeAdapter)
    trust_store = QualificationTrustStore()
    qualification_root = tmp_path / "qualification"
    qualification_root.mkdir()

    stack = production.build_slurm_stack(
        cluster_profile_path=config,
        case_id="trust-inputs",
        audit_path=tmp_path / "audit.jsonl",
        qualification_root=qualification_root,
        trust_store=trust_store,
    )
    catalog = stack["run_adapter_config"]["runtime_catalog"]
    assert catalog.qualification_root == qualification_root
    assert set(catalog.trusted_site_profiles) == {stack["site_profile"].site_id}
    # An explicit but unconfigured trust store cannot promote a lock entry.
    assert catalog.qualified_capabilities() == []
    assert stack["run_adapter_config"]["qualification_root"] == str(
        qualification_root
    )
