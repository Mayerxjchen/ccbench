"""MatClaw cases 001/002/003 — portable Candidate contract.

Each case must declare frozen profiles (resource/platform/smoke/formal) that
preserve their scientific inputs and runtime profile while using the shared
Candidate container.  Compute placement is selected by an operator profile,
not by a case-owned scheduler/controller.  The shared matclaw GPU SIF remains
pinned by digest, with a
paper/formal verifier timeout of at least 7200s matching ``task.toml``, and the
per-case structure/teacher-model hashes from ``source.lock.json``.  Smoke
success is never labeled scientific success.  The verifier entry emits the
common ``result.json`` while keeping the scientific gates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SHARED_LOCK = ROOT / "evidence" / "matclaw" / "formal" / "runtime-gpu-amd64.lock.json"

def _case_path(name: str) -> Path:
    p = ROOT / "cases" / name
    return p if p.is_dir() else (ROOT / name)


CASES = [
    (_case_path("001-matclaw-cips-active-distillation"), "001", "distill"),
    (_case_path("002-matclaw-cips-curie-temperature"), "002", "curie"),
    (_case_path("003-matclaw-cips-domain-wall-search"), "003", "domain-wall"),
]

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _load(case: Path, name: str) -> dict:
    cands = [
        case / "profiles" / name,
        ROOT / "maintainer" / "cases" / case.name[:3] / "profiles" / name,
    ]
    p = next((c for c in cands if c.is_file()), cands[0])
    assert p.is_file(), f"missing profile {p}"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _lock(case: Path) -> dict:
    cands = [
        case / "reference" / "compute-runtime.lock.json",
        ROOT / "maintainer" / "cases" / case.name[:3] / "reference" / "compute-runtime.lock.json",
        ROOT / "maintainer" / "cases" / case.name[:3] / "baseline" / "compute-runtime.lock.json",
    ]
    p = next((c for c in cands if c.is_file()), cands[0])
    return json.loads(p.read_text(encoding="utf-8"))


def _task_toml(case: Path) -> str:
    cands = [
        case / "case.toml",
        case / "task.toml",
        ROOT / "cases" / case.name / "case.toml",
        ROOT / "cases" / case.name / "task.toml",
    ]
    p = next((c for c in cands if c.is_file()), cands[1])
    return p.read_text(encoding="utf-8")


@pytest.mark.parametrize("case,case_id,science", CASES, ids=[c[2] for c in CASES])
def test_cases_declare_portable_candidate_contract(case, case_id, science) -> None:
    toml = _task_toml(case)
    assert 'class = "local_sandbox"' in toml
    assert 'runner = "container_claude_code"' in toml
    assert "[compute]" in toml


@pytest.mark.parametrize("case,case_id,science", CASES, ids=[c[2] for c in CASES])
def test_profiles_exist(case, case_id, science) -> None:
    for name in ("resource.yaml", "platform.yaml", "smoke.yaml", "formal.yaml"):
        cands = [
            case / "profiles" / name,
            ROOT / "maintainer" / "cases" / case_id / "profiles" / name,
        ]
        assert any(c.is_file() for c in cands), name


@pytest.mark.parametrize("case,case_id,science", CASES, ids=[c[2] for c in CASES])
def test_resource_profile_pins_one_gpu_and_shared_runtime(case, case_id, science) -> None:
    res = _load(case, "resource.yaml")
    assert res["gpus"] == 1
    assert res["cpus"] == 8
    assert res["memory_mb"] == 16384
    shared = json.loads(SHARED_LOCK.read_text(encoding="utf-8"))
    assert res["runtime"]["sif_sha256"] == shared["sif_sha256"]
    assert HEX64.match(res["runtime"]["sif_sha256"])


@pytest.mark.parametrize("case,case_id,science", CASES, ids=[c[2] for c in CASES])
def test_platform_profile_declares_hpc_contract(case, case_id, science) -> None:
    plat = _load(case, "platform.yaml")
    assert plat["contract_version"] == "hpc-execution/v1"
    assert plat["default_queue"] == "gpu"
    assert set(plat["required_capabilities"]) == {"batch_jobs", "gpu", "artifact_fetch"}
    assert plat["gpus"] == 1
    assert plat["architecture"] == "linux/amd64"


@pytest.mark.parametrize("case,case_id,science", CASES, ids=[c[2] for c in CASES])
def test_formal_verifier_timeout_at_least_7200_matches_task_toml(case, case_id, science) -> None:
    formal = _load(case, "formal.yaml")
    assert formal["verifier_timeout_sec"] >= 7200
    toml = _task_toml(case)
    assert f"timeout_sec = 10800" in toml or f"timeout_sec = {formal['verifier_timeout_sec']:.1f}" in toml or f"timeout_sec = {formal['verifier_timeout_sec']}" in toml


@pytest.mark.parametrize("case,case_id,science", CASES, ids=[c[2] for c in CASES])
def test_formal_preserves_case_policy_scientific_identity(case, case_id, science) -> None:
    cands = [
        case / "reference" / "source.lock.json",
        ROOT / "maintainer" / "cases" / case_id / "reference" / "source.lock.json",
    ]
    p = next((c for c in cands if c.is_file()), cands[1])
    prov = json.loads(p.read_text(encoding="utf-8"))
    formal = _load(case, "formal.yaml")
    assert formal["matclaw_profile"] == "paper"
    # scientific identity carries over from the provenance lock untouched
    assert formal["structure"]["file"] == prov["structure"]["file"]
    assert formal["structure"]["sha256"] == prov["structure"]["sha256"]
    assert formal["teacher_model"]["sha256"] == prov["teacher_model"]["sha256"]
    assert formal["paper"]["arxiv"] == prov["paper"]["arxiv"]


@pytest.mark.parametrize("case,case_id,science", CASES, ids=[c[2] for c in CASES])
def test_smoke_is_never_scientific_success(case, case_id, science) -> None:
    assert _load(case, "smoke.yaml")["scientific"] is False
    assert _load(case, "formal.yaml")["scientific"] is True
    assert _load(case, "smoke.yaml")["verifier_timeout_sec"] < _load(case, "formal.yaml")["verifier_timeout_sec"]


@pytest.mark.parametrize("case,case_id,science", CASES, ids=[c[2] for c in CASES])
def test_compute_runtime_lock_pins_shared_sif(case, case_id, science) -> None:
    lock = _lock(case)
    shared = json.loads(SHARED_LOCK.read_text(encoding="utf-8"))
    assert lock["schema"].startswith("matclaw-compute-runtime-lock/")
    assert lock["case_id"] in (case_id, f"03{case_id[-1]}")
    assert lock["runtime"]["sif_sha256"] == shared["sif_sha256"]
    assert lock["runtime"]["sif_path_remote"] == shared["sif_path_remote"]
    assert lock["qualification"]["qualified"] is True
    assert lock["gpus"] == 1
    assert ":latest" not in json.dumps(lock)


@pytest.mark.parametrize("case,case_id,science", CASES, ids=[c[2] for c in CASES])
def test_test_sh_emits_common_result_json(case, case_id, science) -> None:
    sh_path = case / "verifier" / "test.sh" if (case / "verifier" / "test.sh").is_file() else case / "tests" / "test.sh"
    text = sh_path.read_text(encoding="utf-8")
    assert "result.json" in text
    assert "result_class" in text
    assert "VALID_RESULT" in text
    # Scientific failures are valid, counted verifier verdicts; AGENT_FAILURE
    # is reserved for agent-side invalid submissions and must not silently
    # remove a failed science case from the denominator.
    assert "SCIENTIFIC_FAIL" in text
    assert '"result_class": "INFRA_INVALID"' in text
    assert "retryable" in text
    if case_id in {"002", "003"}:
        assert "--junitxml=/tmp/bench-pytest.xml" in text
        assert "xml.etree.ElementTree" in text


@pytest.mark.parametrize("case,case_id,science", CASES, ids=[c[2] for c in CASES])
def test_test_sh_result_json_conforms_to_schema(case, case_id, science) -> None:
    import jsonschema

    schema = json.loads((ROOT / "schemas" / "result.schema.json").read_text(encoding="utf-8"))
    sh_path = case / "verifier" / "test.sh" if (case / "verifier" / "test.sh").is_file() else case / "tests" / "test.sh"
    text = sh_path.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'JSON'\n(.*?)\nJSON", text, re.DOTALL)
    assert blocks, "no JSON heredocs in test.sh"
    validator = jsonschema.Draft202012Validator(schema)
    for block in blocks:
        payload = json.loads(block)
        errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
        assert not errors, errors[0].message
        assert payload["retryable"] is False
