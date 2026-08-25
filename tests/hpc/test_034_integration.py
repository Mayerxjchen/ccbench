"""Case 034 HPC scientific reference — frozen profiles + compute runtime lock.

Task 12 migration contract.  The case declares, per profile, the resource
ceiling, the required HPC contract, and the DFT label / MD step limits that
separate smoke from formal.  Every scientific data asset carries a full sha256;
the formal verifier timeout is 10800s and matches ``task.toml``; smoke success
is never labeled scientific success.  The compute-runtime lock pins the frozen
runtime without any ``:latest`` or undigested reference.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

CASE = Path(__file__).resolve().parents[2] / "034-ai2kit-water64-end-to-end-potential"
PROFILES = CASE / "profiles"
HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Recorded verbatim from the reconstruction plan Task 12 Step 3.
CP2K_DATA_HASHES = {
    "GTH_BASIS_SETS": "76d1ccd5204390abfcadeb5c0d5e609ff3d151c1c1a583a02349af348ef919f7",
    "GTH_POTENTIALS": "a4307115bacdaa253e0faa24b3f9f7ba7c1dc60585cbd83691c65ff51d0db9fd",
    "dftd3.dat": "1f5041914fb3a7fa6c97602d82da6cb9129c1c1dba675db23522de9c2837dafd",
}

SOFTWARE = {"ai2_kit", "deepmd_kit", "lammps", "cp2k", "python", "oh_my_batch"}


def _load(name: str) -> dict:
    path = PROFILES / name
    assert path.is_file(), f"missing profile {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _task_toml() -> str:
    return (CASE / "task.toml").read_text(encoding="utf-8")


def test_profiles_exist() -> None:
    for name in ("resource.yaml", "platform.yaml", "smoke.yaml", "formal.yaml"):
        assert (PROFILES / name).is_file(), name


def test_resource_profile_pins_gpus_and_cpus() -> None:
    res = _load("resource.yaml")
    assert res["gpus"] == 1
    # match the sandbox resource policy in task.toml / ablation lock
    assert res["cpus"] == 16
    assert res["memory_mb"] == 32768


def test_resource_profile_records_cp2k_data_hashes_verbatim() -> None:
    assets = {a["path"]: a["sha256"] for a in _load("resource.yaml")["data_assets"]}
    for path, digest in CP2K_DATA_HASHES.items():
        assert assets.get(path) == digest, path


def test_no_undigested_runtime_artifact() -> None:
    res = _load("resource.yaml")
    for asset in res["data_assets"]:
        assert HEX64.match(asset["sha256"]), asset
    assert "dft" in res
    assert res["dft"]["method"]  # e.g. CP2K BLYP-D3 / TZV2P-GTH
    blob = json.dumps(res)
    assert ":latest" not in blob
    assert "/tmp/hehr/" not in blob  # broken compile-time cp2k data path never referenced


def test_platform_profile_declares_hpc_contract() -> None:
    plat = _load("platform.yaml")
    assert plat["contract_version"] == "hpc-execution/v1"
    assert plat["default_queue"] == "gpu"
    assert set(plat["required_capabilities"]) == {"batch_jobs", "gpu", "artifact_fetch"}
    assert plat["gpus"] == 1
    # matches the HPC contract declared in task.toml
    toml = _task_toml()
    for cap in plat["required_capabilities"]:
        assert cap in toml


def test_smoke_profile_is_looser_than_formal() -> None:
    smoke = _load("smoke.yaml")
    formal = _load("formal.yaml")
    assert smoke["nvt_steps"] < formal["nvt_steps"]
    assert smoke["dft_label_runs"] < formal["dft_label_runs"]
    assert smoke["verifier_timeout_sec"] < formal["verifier_timeout_sec"]


def test_formal_profile_matches_task_toml() -> None:
    formal = _load("formal.yaml")
    toml = _task_toml()
    assert formal["verifier_timeout_sec"] == 10800
    assert "timeout_sec = 10800.0" in toml
    assert formal["nvt_steps"] == 5000
    assert 'AI2KIT_NVT_STEPS = "5000"' in toml
    assert formal["temperature_k"] == 300
    assert 'AI2KIT_NVT_TEMPERATURE = "300"' in toml


def test_smoke_is_never_scientific_success() -> None:
    assert _load("smoke.yaml")["scientific"] is False
    assert _load("formal.yaml")["scientific"] is True


def test_compute_runtime_lock_pins_frozen_runtime() -> None:
    lock = json.loads((CASE / "reference" / "compute-runtime.lock.json").read_text())
    assert lock["benchmark_id"] == "034-ai2kit-water64-end-to-end-potential"
    assert lock["schema"].startswith("034-compute-runtime-lock/")
    assert lock["gpus"] == 1
    # software identities from the provenance lock (source.lock.json)
    for key in SOFTWARE:
        assert lock["software"][key], key
    # every data asset is digest-pinned
    assets = {a["path"]: a["sha256"] for a in lock["data_assets"]}
    assert set(assets) == set(CP2K_DATA_HASHES)
    for path, digest in CP2K_DATA_HASHES.items():
        assert assets[path] == digest, path
    # the runtime image is pinned by tag + recorded image id, never :latest
    image = lock["runtime_image"]
    assert image["tag"] == "dftworld-base-ai2kit:0.1.0-cpu-controller"
    assert image["image_id"]
    assert ":latest" not in json.dumps(lock)


def test_compute_runtime_lock_consistent_with_provenance() -> None:
    prov = json.loads((CASE / "reference" / "source.lock.json").read_text())
    lock = json.loads((CASE / "reference" / "compute-runtime.lock.json").read_text())
    soft = prov["software"]
    for key in ("ai2_kit", "deepmd_kit", "oh_my_batch"):
        assert lock["software"][key] == soft[key], key


def test_test_sh_emits_common_result_json() -> None:
    text = (CASE / "tests" / "test.sh").read_text(encoding="utf-8")
    # common result.json per result.schema.json, not only legacy reward.txt
    assert "result.json" in text
    assert "result_class" in text
    assert "VALID_RESULT" in text
    assert "AGENT_FAILURE" in text
    assert "retryable" in text
    # L1-L9 scientific evidence still runs
    assert "test_outputs.py" in text


def test_test_sh_result_json_conforms_to_schema() -> None:
    import jsonschema

    schema = json.loads(
        (CASE.parents[0] / "schemas" / "result.schema.json").read_text(encoding="utf-8")
    )
    text = (CASE / "tests" / "test.sh").read_text(encoding="utf-8")
    blocks = re.findall(r"<<'JSON'\n(.*?)\nJSON", text, re.DOTALL)
    assert blocks, "no JSON heredocs in test.sh"
    validator = jsonschema.Draft202012Validator(schema)
    for block in blocks:
        payload = json.loads(block)
        errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)
        assert not errors, errors[0].message
        assert payload["result_class"] in ("VALID_RESULT", "AGENT_FAILURE")
        assert payload["retryable"] is False
        assert payload["is_counted_scientifically"] is True
