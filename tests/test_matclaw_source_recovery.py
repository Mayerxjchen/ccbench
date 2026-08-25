import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmark.sources.matclaw.recovery import (
    GATE_ARTIFACTS,
    derive_gate,
    sha256_file,
    validate_inventory,
)
from benchmark.sources.matclaw.recover_sources import (
    PAPER_SHA256,
    RELEASE_COMMIT,
    SELECTED_REPOSITORY_PATHS,
    SOURCE_SPEC,
    copy_runtime_locks,
    recover_repository,
)
from benchmark.sources.matclaw.recover_teacher import validate_load_test_bindings


def test_sha256_file_hashes_exact_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"matclaw\x00source")

    assert sha256_file(artifact) == hashlib.sha256(b"matclaw\x00source").hexdigest()


def test_remote_path_does_not_recover_teacher() -> None:
    inventory = {
        "artifacts": [
            {
                "id": "teacher_model",
                "status": "missing",
                "provenance": "missing_upstream",
                "source_locator": "/pscratch/example/frozen_model.pb",
            }
        ]
    }

    gate = derive_gate(inventory)

    assert gate["teacher_model_recovered"] is False
    assert gate["benchmark_construction_unblocked"] is False


def test_gate_requires_load_test_before_unblocking(tmp_path: Path) -> None:
    model = tmp_path / "common" / "teacher-model" / "frozen_model.pb"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    inventory = {
        "artifacts": [
            {
                "id": "teacher_model",
                "status": "recovered",
                "provenance": "upstream_author_dataset",
                "path": "common/teacher-model/frozen_model.pb",
                "sha256": sha256_file(model),
                "size": model.stat().st_size,
                "source_locator": "author record",
                "type_map_verified": True,
                "load_test_passed": False,
            }
        ]
    }

    gate = derive_gate(inventory, tmp_path)

    assert gate["teacher_model_recovered"] is True
    assert gate["teacher_model_load_test_passed"] is False
    assert gate["benchmark_construction_unblocked"] is False


def test_gate_fails_closed_without_on_disk_validation() -> None:
    inventory = {
        "artifacts": [
            {
                "id": artifact_id,
                "status": "recovered",
                "provenance": "upstream_repository",
                "path": "fabricated",
                "sha256": "0" * 64,
                "size": 1,
                "source_locator": "fabricated",
                "type_map_verified": True,
                "load_test_passed": True,
            }
            for artifact_id in GATE_ARTIFACTS
        ]
    }

    gate = derive_gate(inventory)

    assert gate["source_recovery_complete"] is False
    assert gate["benchmark_construction_unblocked"] is False


def test_validate_inventory_accepts_hashed_recovered_file(tmp_path: Path) -> None:
    artifact = tmp_path / "common" / "CuInP2S6.cif"
    artifact.parent.mkdir()
    artifact.write_text("CIPS\n", encoding="utf-8")
    inventory = {
        "artifacts": [
            {
                "id": "structure",
                "status": "recovered",
                "provenance": "upstream_repository",
                "path": "common/CuInP2S6.cif",
                "sha256": sha256_file(artifact),
                "size": artifact.stat().st_size,
                "source_locator": ".ref/CuInP2S6.cif",
            }
        ]
    }

    assert validate_inventory(tmp_path, inventory) == []


def test_validate_inventory_reports_unknown_provenance_and_hash_mismatch(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"real bytes")
    inventory = {
        "artifacts": [
            {
                "id": "structure",
                "status": "recovered",
                "provenance": "copied_somewhere",
                "path": "artifact.bin",
                "sha256": "0" * 64,
                "size": artifact.stat().st_size,
                "source_locator": "unknown",
            }
        ]
    }

    errors = validate_inventory(tmp_path, inventory)

    assert any("unknown provenance" in error for error in errors)
    assert any("sha256 mismatch" in error for error in errors)


def test_validate_inventory_rejects_path_outside_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"outside")
    inventory = {
        "artifacts": [
            {
                "id": "structure",
                "status": "recovered",
                "provenance": "upstream_repository",
                "path": "../outside.bin",
                "sha256": sha256_file(outside),
                "size": outside.stat().st_size,
                "source_locator": ".ref/CuInP2S6.cif",
            }
        ]
    }

    errors = validate_inventory(tmp_path, inventory)

    assert any("outside source root" in error for error in errors)


def test_validate_inventory_rejects_duplicate_gate_artifacts(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"source")
    entry = {
        "id": "teacher_model",
        "status": "recovered",
        "provenance": "upstream_author_dataset",
        "path": "artifact.bin",
        "sha256": sha256_file(artifact),
        "size": artifact.stat().st_size,
        "source_locator": "author record",
    }

    errors = validate_inventory(tmp_path, {"artifacts": [entry, dict(entry)]})

    assert any("expected one gate artifact, found 2" in error for error in errors)


def test_source_spec_pins_paper_and_repository_commits() -> None:
    assert SOURCE_SPEC["paper"]["arxiv"] == "2604.02688v3"
    assert SOURCE_SPEC["repository"]["url"] == "https://github.com/cz2014/MatClaw.git"
    assert SOURCE_SPEC["repository"]["public_commit"] == (
        "cebcf2be839af87663c0e5b64efaf1afe99f2e39"
    )
    assert RELEASE_COMMIT == "52557c077f5e3be8444a3f03ea10a647fbd442ca"
    assert PAPER_SHA256 == (
        "28349bc2cf74a93d12a505c99864d1b4bdb2754f19dbee7848fe00288df33ce6"
    )
    assert SOURCE_SPEC["paper"]["sha256"] == PAPER_SHA256


def test_selected_repository_paths_cover_only_cips_sources() -> None:
    expected_workspaces = {
        "workspace_demo1a_distill",
        "workspace_demo1b_distill_pdf",
        "workspace_demo2a_curie_no_convergence",
        "workspace_demo2b_curie_with_convergence",
        "workspace_demo3_search",
    }

    assert expected_workspaces < set(SELECTED_REPOSITORY_PATHS)
    assert ".ref/CuInP2S6.cif" in SELECTED_REPOSITORY_PATHS
    assert ".ref/cips_monolayer.cif" in SELECTED_REPOSITORY_PATHS
    assert ".ref/cips_monolayer.data" in SELECTED_REPOSITORY_PATHS
    assert "remote_jobs/_efield_calculator.py" in SELECTED_REPOSITORY_PATHS
    assert all(not path.startswith("corpus") for path in SELECTED_REPOSITORY_PATHS)
    assert all(not path.startswith("benchmark") for path in SELECTED_REPOSITORY_PATHS)
    assert all(not path.startswith("code") for path in SELECTED_REPOSITORY_PATHS)


def test_recover_repository_copies_selected_paths_only(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    expected_files = set()
    for selected in SELECTED_REPOSITORY_PATHS:
        selected_path = source / selected
        if selected.startswith(".ref/") or selected.endswith(".py"):
            selected_path.parent.mkdir(parents=True, exist_ok=True)
            selected_path.write_text(selected, encoding="utf-8")
            expected_files.add(selected)
        else:
            selected_path.mkdir(parents=True)
            (selected_path / "evidence.txt").write_text(selected, encoding="utf-8")
            expected_files.add(f"{selected}/evidence.txt")
    unrelated = source / "corpus" / "large"
    unrelated.mkdir(parents=True)
    (unrelated / "unrelated.txt").write_text("do not copy", encoding="utf-8")

    copied = recover_repository(source, destination)

    assert {path.relative_to(destination).as_posix() for path in copied} == expected_files
    assert not (destination / "corpus").exists()


def test_runtime_locks_are_copied_into_fresh_recovery_output(tmp_path: Path) -> None:
    copied = copy_runtime_locks(tmp_path)

    assert {path.name for path in copied} == {
        "teacher-runtime.lock.json",
        "teacher-runtime-requirements.txt",
    }
    lock = json.loads(
        (tmp_path / "teacher-runtime.lock.json").read_text(encoding="utf-8")
    )
    requirements = tmp_path / lock["requirements"]["path"]
    assert lock["python"]["version"] == "3.11.15"
    assert lock["requirements"]["sha256"] == sha256_file(requirements)
    assert lock["requirements"]["distribution_count"] == len(
        requirements.read_text(encoding="utf-8").splitlines()
    )


def test_common_structures_preserve_upstream_bytes() -> None:
    source_root = ROOT / "benchmark" / "sources" / "matclaw"
    pairs = (
        ("CuInP2S6.cif", ".ref/CuInP2S6.cif"),
        ("cips_monolayer.cif", ".ref/cips_monolayer.cif"),
        ("cips_monolayer.data", ".ref/cips_monolayer.data"),
    )

    for common_name, upstream_name in pairs:
        common = source_root / "common" / common_name
        upstream = source_root / "repository" / "release" / upstream_name
        assert common.read_bytes() == upstream.read_bytes()


def test_structure_audit_records_cross_parser_agreement() -> None:
    audit_path = (
        ROOT
        / "benchmark"
        / "sources"
        / "matclaw"
        / "common"
        / "structure-audit.json"
    )
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    structures = {entry["filename"]: entry for entry in audit["structures"]}

    primitive = structures["CuInP2S6.cif"]
    assert primitive["sha256"] == sha256_file(audit_path.parent / primitive["filename"])
    assert primitive["pymatgen"]["num_sites"] == 10
    assert primitive["canonical_reduced_formula"] == "CuInP2S6"
    assert primitive["pymatgen"]["element_counts"] == {
        "Cu": 1,
        "In": 1,
        "P": 2,
        "S": 6,
    }
    assert primitive["ase"]["num_atoms"] == 10
    assert primitive["parser_agreement"]["atom_count"] is True
    assert primitive["parser_agreement"]["formula"] is True

    monolayer = structures["cips_monolayer.cif"]
    assert monolayer["sha256"] == sha256_file(audit_path.parent / monolayer["filename"])
    assert monolayer["pymatgen"]["num_sites"] == 20
    assert monolayer["canonical_reduced_formula"] == "CuInP2S6"
    assert monolayer["pymatgen"]["element_counts"] == {
        "Cu": 2,
        "In": 2,
        "P": 4,
        "S": 12,
    }
    assert monolayer["ase"]["num_atoms"] == 20
    assert monolayer["parser_agreement"]["atom_count"] is True
    assert monolayer["parser_agreement"]["formula"] is True


def test_task_manifests_cover_all_five_upstream_workspaces() -> None:
    source_root = ROOT / "benchmark" / "sources" / "matclaw"
    expected = {
        "task1": {
            "workspace_demo1a_distill",
            "workspace_demo1b_distill_pdf",
        },
        "task2": {
            "workspace_demo2a_curie_no_convergence",
            "workspace_demo2b_curie_with_convergence",
        },
        "task3": {"workspace_demo3_search"},
    }

    for task, workspace_names in expected.items():
        manifest_path = source_root / task / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert {entry["name"] for entry in manifest["workspaces"]} == workspace_names
        for workspace in manifest["workspaces"]:
            assert workspace["files"]
            for artifact in workspace["files"]:
                raw = source_root / artifact["path"]
                assert artifact["sha256"] == sha256_file(raw)
                assert artifact["size"] == raw.stat().st_size


def test_task3_protocol_records_equations_charges_units_and_history_evidence() -> None:
    source_root = ROOT / "benchmark" / "sources" / "matclaw"
    protocol_path = source_root / "task3" / "electric-field-protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))

    assert protocol["calculator_composition"] == (
        "SumCalculator([DeePMD, UniformElectricForce])"
    )
    assert protocol["total_force"] == "F_total(i) = F_DP(i) + q_i * E"
    assert protocol["external_energy"] == "-sum_i(q_i * r_i . E)"
    assert protocol["field_unit"] == "eV/angstrom/e"
    assert protocol["equivalent_field_unit"] == "V/angstrom"
    assert protocol["charges_e"] == {
        "Cu": 0.765,
        "In": -0.085,
        "P": -0.085,
        "S": -0.085,
    }
    evidence_identities = {
        (item["jsonl_line"], item["step"], item["role"])
        for item in protocol["evidence"]
    }
    assert evidence_identities >= {
        (1, 0, "system"),
        (2, 1, "user"),
        (10, 4, "tool-call"),
        (11, 4, "tool-response"),
    }
    history = source_root / protocol["history_path"]
    assert protocol["history_sha256"] == sha256_file(history)
    implementation = source_root / protocol["implementation"]["path"]
    assert protocol["implementation"]["sha256"] == sha256_file(implementation)
    source = implementation.read_text(encoding="utf-8")
    assert 'self.results["forces"] = charges[:, None] * self.field[None, :]' in source
    assert "return SumCalculator([dp, ext])" in source


def test_teacher_model_is_authoritative_hashed_and_load_tested() -> None:
    source_root = ROOT / "benchmark" / "sources" / "matclaw"
    recovery_path = source_root / "common" / "teacher-model" / "recovery.json"
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))

    assert recovery["status"] == "recovered"
    assert recovery["provenance"] == "upstream_author_dataset"
    assert recovery["source"]["provider"] == "AIS Square"
    assert recovery["source"]["record_id"] == 109
    assert recovery["source"]["record_name"] == "vdW_CuInP2S6_optB86b"
    assert recovery["type_map"] == ["Cu", "In", "P", "S"]
    assert recovery["type_map_verified"] is True
    assert recovery["load_test"]["passed"] is True
    assert recovery["load_test"]["num_atoms"] == 10
    model = source_root / "common" / "teacher-model" / "frozen_model.pb"
    structure = source_root / "common" / "CuInP2S6.cif"
    type_map = source_root / "common" / "teacher-model" / "type_map.raw"
    assert recovery["load_test"]["model_sha256"] == sha256_file(model)
    assert recovery["load_test"]["structure_sha256"] == sha256_file(structure)
    assert recovery["load_test"]["type_map_sha256"] == sha256_file(type_map)
    assert recovery["load_test"]["model_type_map"] == ["Cu", "In", "P", "S"]
    assert recovery["load_test"]["model_type_map"] == recovery["type_map"]

    for artifact in recovery["artifacts"]:
        path = source_root / artifact["path"]
        assert artifact["sha256"] == sha256_file(path)
        assert artifact["size"] == path.stat().st_size


def test_teacher_archive_and_model_match_author_source_bytes() -> None:
    source_root = ROOT / "benchmark" / "sources" / "matclaw"
    teacher = source_root / "common" / "teacher-model"
    assert sha256_file(teacher / "CIPS_data.zip") == (
        "1a8ebdd410fc6d6f417c5c0cd5a3fafce430530e57882bf9fcb03563886c42de"
    )
    assert sha256_file(teacher / "frozen_model.pb") == (
        "a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d"
    )
    assert (teacher / "type_map.raw").read_text(encoding="utf-8").split() == [
        "Cu",
        "In",
        "P",
        "S",
    ]
    assert (
        source_root / "paper" / "he-physrevb-108-024305.pdf"
    ).read_bytes() == (
        source_root
        / "repository"
        / "release"
        / "workspace_demo1b_distill_pdf"
        / "He_paper.pdf"
    ).read_bytes()
    runtime_lock = json.loads(
        (source_root / "teacher-runtime.lock.json").read_text(encoding="utf-8")
    )
    assert runtime_lock["python"]["version"] == "3.11.15"
    assert runtime_lock["platform"] == "macOS arm64"
    requirements = source_root / runtime_lock["requirements"]["path"]
    assert runtime_lock["requirements"]["sha256"] == sha256_file(requirements)
    assert runtime_lock["requirements"]["distribution_count"] == 55


def test_teacher_recovery_rejects_unbound_load_test_evidence() -> None:
    source_root = ROOT / "benchmark" / "sources" / "matclaw"
    recovery = json.loads(
        (
            source_root / "common" / "teacher-model" / "recovery.json"
        ).read_text(encoding="utf-8")
    )
    fabricated = dict(recovery["load_test"])
    fabricated["model_sha256"] = "0" * 64

    with pytest.raises(RuntimeError, match="does not bind"):
        validate_load_test_bindings(source_root, fabricated)


def test_checked_in_inventory_and_gate_are_self_consistent() -> None:
    source_root = ROOT / "benchmark" / "sources" / "matclaw"
    inventory = json.loads(
        (source_root / "inventory.json").read_text(encoding="utf-8")
    )
    checked_gate = json.loads(
        (source_root / "recovery_gate.json").read_text(encoding="utf-8")
    )

    assert validate_inventory(source_root, inventory) == []
    assert checked_gate == derive_gate(inventory, source_root)
    assert checked_gate["source_recovery_complete"] is True
    assert checked_gate["benchmark_construction_unblocked"] is True
    assert checked_gate["task2_raw_trajectories_recovered"] is False
    assert checked_gate["task3_raw_trajectories_recovered"] is False


def test_readme_reports_complete_core_recovery_and_missing_raw_trajectories() -> None:
    source_root = ROOT / "benchmark" / "sources" / "matclaw"
    readme = (source_root / "README.md").read_text(encoding="utf-8")

    assert "Core source recovery: COMPLETE" in readme
    assert "Benchmark construction: UNBLOCKED" in readme
    assert "Task 2 raw trajectories | not recovered" in readme
    assert "Task 3 raw trajectories | not recovered" in readme


def test_source_recovery_package_contains_no_case_dockerfile() -> None:
    source_root = ROOT / "benchmark" / "sources" / "matclaw"
    assert not list(source_root.rglob("Dockerfile*"))
