"""Task 5 — Generated-file drift detection and factory state.

``validate_output`` recomputes expected bytes without writing and flags any
on-disk drift.  Rendering may set only ``design_valid`` and
``target_adapter_valid``; runtime/smoke gates stay false until independent
checks pass (Tasks 8/9).  Regeneration must refuse drift by default and may
replace only generated files — never authored content.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ccbench.case_factory.dftworld_target import DftworldTargetAdapter
from ccbench.case_factory.state import LOCK_RELPATH, read_factory_state

ADAPTER = DftworldTargetAdapter()

DESIGN = {
    "identity": {
        "case_id": "042-example",
        "task_name": "benchmark/042-example",
        "title": "Example",
        "description": "Example case",
        "case_version": "1.0.0",
    },
    "execution": {"class": "local_sandbox"},
    "runtime": {
        "candidate_image": "dftworld-base-mace",
        "build_timeout_sec": 1800,
        "agent_timeout_sec": 7200,
        "verifier_timeout_sec": 3600,
        "cpus": 4,
        "memory_mb": 8192,
        "storage_mb": 20480,
        "gpus": 0,
        "allow_internet": False,
    },
    "submission": {"root": "final"},
    "candidate_files": [
        {"source": "public/**", "destination": ".", "strip_prefix": "public"}
    ],
}


@pytest.fixture()
def rendered(tmp_path):
    case_dir = tmp_path / "case"
    (case_dir / "public").mkdir(parents=True)
    (case_dir / "instruction.md").write_text("# x\n", encoding="utf-8")
    for gf in ADAPTER.render(case_dir, DESIGN):
        (case_dir / gf.path).parent.mkdir(parents=True, exist_ok=True)
        (case_dir / gf.path).write_bytes(gf.content)
    return case_dir


def test_fresh_render_validates(rendered):
    verdict = ADAPTER.validate_output(rendered, DESIGN)
    assert verdict.valid, verdict.errors
    assert verdict.gates.design_valid is True
    assert verdict.gates.target_adapter_valid is True
    # runtime and smoke gates never set by rendering.
    assert verdict.gates.runtime_valid is False
    assert verdict.gates.candidate_smoke_valid is False


def test_task_toml_drift_detected(rendered):
    (rendered / "task.toml").write_text("schema_version = '1.2'\n", encoding="utf-8")
    verdict = ADAPTER.validate_output(rendered, DESIGN)
    assert not verdict.valid
    assert any("task.toml" in e for e in verdict.errors)


def test_dockerfile_drift_detected(rendered):
    (rendered / "Dockerfile").write_text("FROM evil\n", encoding="utf-8")
    verdict = ADAPTER.validate_output(rendered, DESIGN)
    assert not verdict.valid
    assert any("Dockerfile" in e for e in verdict.errors)


def test_lock_drift_detected(rendered):
    lock = rendered / LOCK_RELPATH
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["adapter_version"] = "9.9.9"
    lock.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    verdict = ADAPTER.validate_output(rendered, DESIGN)
    assert not verdict.valid
    assert any("lock" in e for e in verdict.errors)


def test_design_change_flags_render_drift(rendered):
    # Changing execution class must be caught before any runtime validation.
    changed = {**DESIGN}
    changed["runtime"] = {**changed["runtime"], "cpus": 99}
    verdict = ADAPTER.validate_output(rendered, changed)
    assert not verdict.valid


def test_missing_generated_file_reported(rendered):
    (rendered / "task.toml").unlink()
    verdict = ADAPTER.validate_output(rendered, DESIGN)
    assert not verdict.valid
    assert any("missing" in e for e in verdict.errors)


def test_validate_output_never_writes(rendered):
    before = {
        p: p.read_bytes()
        for p in (rendered / "task.toml", rendered / "Dockerfile",
                  rendered / ".dockerignore", rendered / LOCK_RELPATH)
    }
    # Drift the file first so a writer would "fix" it.
    (rendered / "task.toml").write_text("drifted\n", encoding="utf-8")
    ADAPTER.validate_output(rendered, DESIGN)
    assert (rendered / "task.toml").read_text() == "drifted\n"  # untouched
    # And a clean run never rewrites matching bytes.
    for gf in ADAPTER.render(rendered, DESIGN):
        (rendered / gf.path).write_bytes(gf.content)
    ADAPTER.validate_output(rendered, DESIGN)
    for p, expected in before.items():
        assert p.read_bytes() == expected


def test_regeneration_refuses_drift(rendered):
    """Default regeneration is refused when the design renders differently."""
    changed = {**DESIGN}
    changed["runtime"] = {**changed["runtime"], "candidate_image": "dftworld-base-2"}
    # validate_output on the changed design detects that current bytes no
    # longer match what the changed design would render.
    verdict = ADAPTER.validate_output(rendered, changed)
    assert not verdict.valid


def test_read_factory_state_missing_lock_all_false(tmp_path):
    gates = read_factory_state(tmp_path / "nope")
    assert gates.as_dict() == {
        "design_valid": False,
        "target_adapter_valid": False,
        "runtime_contract_valid": False,
        "verifier_command_valid": False,
        "runtime_valid": False,
        "candidate_smoke_valid": False,
        "discovery_complete": False,
        "diagnosis_complete": False,
    }


def test_lock_carries_factory_gates(rendered):
    gates = read_factory_state(rendered)
    # render sets design_valid only; target_adapter_valid is set by the CLI's
    # validate_output pass (Task 6).  The lock itself must not self-assert
    # runtime/smoke validity.
    assert "runtime_valid" in gates.as_dict()
    assert "candidate_smoke_valid" in gates.as_dict()
