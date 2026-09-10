"""Versioned dual-mode Case Contract loader tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.contracts.case import CaseContractError, CaseSpec


@pytest.fixture
def case_raw():
    """Minimal valid case manifest as a raw dict."""
    return {
        "schema_version": "1.2",
        "task": {"name": "test/case", "description": "fixture"},
        "execution": {"class": "local_sandbox"},
        "candidate": {
            "instruction": "instruction.md",
            "submission_root": ".",
            "legacy_submission_layout": True,
        },
        "agent": {"timeout_sec": 600.0},
        "verifier": {"timeout_sec": 600.0},
        "environment": {
            "cpus": 2,
            "memory_mb": 4096,
            "storage_mb": 10240,
            "gpus": 0,
            "allow_internet": False,
        },
    }


def _write_manifest(case: Path, data: dict) -> None:
    lines: list[str] = [f'schema_version = "{data["schema_version"]}"', ""]

    task = data["task"]
    lines.append("[task]")
    lines.append(f'name = "{task["name"]}"')
    lines.append(f'description = "{task["description"]}"')
    if "execution_backend" in task:
        lines.append(f'execution_backend = "{task["execution_backend"]}"')

    if "execution" in data:
        lines.extend(["", "[execution]", f'class = "{data["execution"]["class"]}"'])

    candidate = data.get("candidate")
    if candidate:
        lines.extend(
            [
                "",
                "[candidate]",
                f'instruction = "{candidate["instruction"]}"',
                f'submission_root = "{candidate["submission_root"]}"',
            ]
        )
        if candidate.get("legacy_submission_layout"):
            lines.append("legacy_submission_layout = true")
        for rule in candidate.get("files") or []:
            lines.extend(
                ["", "[[candidate.files]]", f'source = "{rule["source"]}"']
            )
            if "destination" in rule:
                lines.append(f'destination = "{rule["destination"]}"')
            if rule.get("strip_prefix") is not None:
                lines.append(f'strip_prefix = "{rule["strip_prefix"]}"')

    hpc = data.get("hpc")
    if hpc:
        caps = ", ".join(f'"{c}"' for c in hpc["required_capabilities"])
        lines.extend(
            [
                "",
                "[hpc]",
                f'contract_version = "{hpc["contract_version"]}"',
                f"required_capabilities = [{caps}]",
            ]
        )

    lines.extend(["", "[agent]", f'timeout_sec = {data["agent"]["timeout_sec"]}'])
    lines.extend(["", "[verifier]", f'timeout_sec = {data["verifier"]["timeout_sec"]}', "", "[verifier.env]"])

    env = data["environment"]
    lines.extend(
        [
            "",
            "[environment]",
            f"cpus = {env['cpus']}",
            f"memory_mb = {env['memory_mb']}",
            f"storage_mb = {env['storage_mb']}",
            f"gpus = {env['gpus']}",
            f"allow_internet = {str(env['allow_internet']).lower()}",
            "",
            "[environment.env]",
            "",
            "[solution.env]",
        ]
    )
    (case / "task.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_case(root: Path, name: str = "case", execution: str | None = None, execution_backend: str | None = None) -> Path:
    """Build a minimal loadable case manifest (canonical or legacy)."""
    case = root / name
    case.mkdir()
    data = {
        "schema_version": "1.2",
        "task": {"name": f"benchmark/{name}", "description": "fixture"},
        "agent": {"timeout_sec": 600.0},
        "verifier": {"timeout_sec": 600.0},
        "environment": {"cpus": 2, "memory_mb": 4096, "storage_mb": 10240, "gpus": 0, "allow_internet": False},
        "candidate": {
            "instruction": "instruction.md",
            "submission_root": ".",
            "legacy_submission_layout": True,
        },
    }
    if execution_backend is not None:
        data["task"]["execution_backend"] = execution_backend
    if execution is not None:
        data["execution"] = {"class": execution}
    if execution == "hpc_controller" or execution_backend in ("hpc_controller", "real_hpc_controller"):
        data["hpc"] = {
            "contract_version": "hpc-execution/v1",
            "required_capabilities": ["batch_jobs", "gpu", "artifact_fetch"],
        }
    _write_manifest(case, data)
    return case


def test_legacy_execution_value_is_normalized_and_recorded(tmp_path):
    case = make_case(tmp_path, execution_backend="real_hpc_controller")
    spec = CaseSpec.load(case)
    assert spec.execution_class == "hpc_controller"
    assert spec.legacy_execution_value == "real_hpc_controller"


def test_execution_is_never_inferred_from_case_number(tmp_path):
    case = make_case(tmp_path, name="031-example", execution=None)
    with pytest.raises(CaseContractError, match="execution.class"):
        CaseSpec.load(case)


def test_both_toml_and_yaml_fail_closed(tmp_path):
    case = make_case(tmp_path, execution="local_sandbox")
    (case / "task.yaml").write_text("schema_version: 2\n")
    with pytest.raises(CaseContractError, match="both task.toml and task.yaml"):
        CaseSpec.load(case)


def test_unknown_execution_value_is_rejected(tmp_path):
    case = make_case(tmp_path, execution="janky_backend")
    with pytest.raises(CaseContractError, match="unknown execution value"):
        CaseSpec.load(case)


def test_hpc_class_requires_hpc_block(tmp_path):
    case = make_case(tmp_path, execution="hpc_controller")
    (case / "task.toml").write_text(
        (case / "task.toml").read_text().replace("\n[hpc]", ""), encoding="utf-8"
    )
    with pytest.raises(CaseContractError, match="requires an \\[hpc\\] block"):
        CaseSpec.load(case)


def test_hpc_block_rejected_for_local_sandbox(tmp_path):
    case = make_case(tmp_path, execution="local_sandbox")
    (case / "task.toml").write_text(
        (case / "task.toml").read_text().replace(
            "[execution]",
            '[execution]\n[removed]\nhpc = {}\n[back]',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(CaseContractError):
        CaseSpec.load(case)


def test_parent_traversing_public_source_is_rejected(tmp_path):
    case = make_case(tmp_path, execution="local_sandbox")
    (case / "task.toml").write_text(
        (case / "task.toml").read_text().replace(
            "legacy_submission_layout = true",
            "legacy_submission_layout = true\n\n[[candidate.files]]\nsource = \"../solution/x\"\ndestination = \"x\"",
        ),
        encoding="utf-8",
    )
    with pytest.raises(CaseContractError, match="unsafe source path"):
        CaseSpec.load(case)


def test_canonical_hpc_manifest_round_trips(tmp_path):
    case = make_case(tmp_path, name="case-hpc", execution="hpc_controller")
    spec = CaseSpec.load(case)
    assert spec.execution_class == "hpc_controller"
    assert spec.legacy_execution_value is None
    assert spec.case_id == "benchmark/case-hpc"
    assert spec.schema_version == "1.2"
    assert spec.submission_root == "."
    assert spec.legacy_submission_layout is True


def test_explicit_files_override_implicit_public_glob(tmp_path):
    case = make_case(tmp_path, name="cp2k", execution="local_sandbox")
    (case / "task.toml").write_text(
        (case / "task.toml").read_text().replace(
            "legacy_submission_layout = true",
            "legacy_submission_layout = true\n\n[[candidate.files]]\nsource = \"environment/H2O.inp\"\ndestination = \"H2O.inp\"",
        ),
        encoding="utf-8",
    )
    spec = CaseSpec.load(case)
    assert len(spec.public_files) == 1
    rule = spec.public_files[0]
    assert rule.source == "environment/H2O.inp"
    assert rule.destination == "H2O.inp"
    assert rule.strip_prefix is None


# --- Task 1: Case Contract Hard Boundary and Runtime Requirements ---

@pytest.mark.parametrize("field", [
    "provider", "model", "endpoint", "api_key", "retry",
    "request_timeout", "max_turns", "agent_walltime", "skills", "fallback",
])
def test_case_rejects_infra_owned_agent_fields(case_raw, field):
    """Infra-owned fields in the case manifest must be rejected."""
    case_raw[field] = "forbidden"
    with pytest.raises(CaseContractError, match="Infra-owned"):
        CaseSpec._from_raw(case_raw, Path("case"))


def test_case_declares_requirements_not_image(case_raw):
    """Cases declare runtime requirements, not concrete images."""
    case_raw["runtime"] = {
        "requirements": [
            {"name": "dpmp", "family": "deepmd-jax", "version": ">=0.2"}
        ]
    }
    spec = CaseSpec._from_raw(case_raw, Path("case"))
    assert spec.runtime_requirements[0].name == "dpmp"
    assert spec.candidate_image is None
