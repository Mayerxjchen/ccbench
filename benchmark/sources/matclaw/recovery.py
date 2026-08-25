"""Validation and gate derivation for the MatClaw CIPS source package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ALLOWED_PROVENANCE = {
    "upstream_repository",
    "upstream_paper",
    "upstream_author_dataset",
    "reconstructed_from_paper",
    "locally_validated",
    "missing_upstream",
}

GATE_ARTIFACTS = {
    "structure": "structure_recovered",
    "task3_structure": "task3_structure_recovered",
    "teacher_model": "teacher_model_recovered",
    "task1_workspace": "task1_workspace_recovered",
    "task2_workspace": "task2_workspace_recovered",
    "task3_workspace": "task3_workspace_recovered",
    "task3_electric_field_protocol": "task3_electric_field_protocol_verified",
    "task2_raw_trajectories": "task2_raw_trajectories_recovered",
    "task3_raw_trajectories": "task3_raw_trajectories_recovered",
    "repository_commit": "repository_commit_pinned",
}


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of *path* without normalizing its bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def validate_inventory(root: Path, inventory: dict[str, Any]) -> list[str]:
    """Return all structural and on-disk validation errors in an inventory."""
    errors: list[str] = []
    source_root = root.resolve()
    gate_id_counts: dict[str, int] = {}

    for index, artifact in enumerate(inventory.get("artifacts", [])):
        artifact_id = artifact.get("id", f"artifact[{index}]")
        if artifact_id in GATE_ARTIFACTS:
            gate_id_counts[artifact_id] = gate_id_counts.get(artifact_id, 0) + 1
        provenance = artifact.get("provenance")
        status = artifact.get("status")

        if provenance not in ALLOWED_PROVENANCE:
            errors.append(f"{artifact_id}: unknown provenance {provenance!r}")

        if status not in {"recovered", "missing"}:
            errors.append(f"{artifact_id}: unknown status {status!r}")
            continue

        if status == "missing":
            if provenance != "missing_upstream":
                errors.append(
                    f"{artifact_id}: missing artifact must use missing_upstream provenance"
                )
            continue

        if provenance == "missing_upstream":
            errors.append(
                f"{artifact_id}: recovered artifact cannot use missing_upstream provenance"
            )

        relative_path = artifact.get("path")
        if not relative_path:
            errors.append(f"{artifact_id}: recovered artifact has no path")
            continue

        candidate = (source_root / relative_path).resolve()
        if not _is_within(source_root, candidate):
            errors.append(f"{artifact_id}: path is outside source root")
            continue
        if not candidate.is_file():
            errors.append(f"{artifact_id}: recovered file is absent: {relative_path}")
            continue

        expected_hash = artifact.get("sha256")
        if not expected_hash:
            errors.append(f"{artifact_id}: recovered file has no sha256")
        elif sha256_file(candidate) != expected_hash:
            errors.append(f"{artifact_id}: sha256 mismatch")

        expected_size = artifact.get("size")
        if expected_size is None:
            errors.append(f"{artifact_id}: recovered file has no size")
        elif candidate.stat().st_size != expected_size:
            errors.append(f"{artifact_id}: size mismatch")

        if not artifact.get("source_locator"):
            errors.append(f"{artifact_id}: recovered file has no source locator")

    for artifact_id, count in gate_id_counts.items():
        if count != 1:
            errors.append(f"{artifact_id}: expected one gate artifact, found {count}")

    if inventory.get("project") == "MatClaw":
        errors.extend(_validate_matclaw_evidence(source_root, inventory))

    return errors


def _validate_manifest(
    root: Path, relative_path: str, expected_workspaces: set[str]
) -> list[str]:
    errors: list[str] = []
    manifest = json.loads((root / relative_path).read_text(encoding="utf-8"))
    actual_workspaces = {
        workspace.get("name") for workspace in manifest.get("workspaces", [])
    }
    if actual_workspaces != expected_workspaces:
        errors.append(
            f"{relative_path}: workspace set {actual_workspaces} does not match "
            f"{expected_workspaces}"
        )
    for workspace in manifest.get("workspaces", []):
        if not workspace.get("files"):
            errors.append(f"{relative_path}: empty workspace {workspace.get('name')}")
        for item in workspace.get("files", []):
            path = (root / item["path"]).resolve()
            if not _is_within(root, path) or not path.is_file():
                errors.append(f"{relative_path}: missing manifest file {item['path']}")
                continue
            if sha256_file(path) != item.get("sha256"):
                errors.append(f"{relative_path}: hash mismatch for {item['path']}")
            if path.stat().st_size != item.get("size"):
                errors.append(f"{relative_path}: size mismatch for {item['path']}")
    return errors


def _validate_matclaw_evidence(
    root: Path, inventory: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    lock = json.loads((root / "source.lock.json").read_text(encoding="utf-8"))
    repository = lock.get("repository", {})
    paper = lock.get("paper", {})
    if repository.get("release_commit") != "52557c077f5e3be8444a3f03ea10a647fbd442ca":
        errors.append("repository_commit: unexpected release commit")
    if repository.get("public_commit") != "cebcf2be839af87663c0e5b64efaf1afe99f2e39":
        errors.append("repository_commit: unexpected public commit")
    required_paths = {
        "remote_jobs/_efield_calculator.py",
        "workspace_demo1a_distill",
        "workspace_demo1b_distill_pdf",
        "workspace_demo2a_curie_no_convergence",
        "workspace_demo2b_curie_with_convergence",
        "workspace_demo3_search",
    }
    if not required_paths <= set(repository.get("selected_paths", [])):
        errors.append("repository_commit: selected source paths are incomplete")
    if paper.get("sha256") != sha256_file(root / "paper" / "matclaw-2604.02688v3.pdf"):
        errors.append("paper: lock hash does not match recovered PDF")

    expected_manifests = {
        "task1/manifest.json": {
            "workspace_demo1a_distill",
            "workspace_demo1b_distill_pdf",
        },
        "task2/manifest.json": {
            "workspace_demo2a_curie_no_convergence",
            "workspace_demo2b_curie_with_convergence",
        },
        "task3/manifest.json": {"workspace_demo3_search"},
    }
    for manifest, workspaces in expected_manifests.items():
        errors.extend(_validate_manifest(root, manifest, workspaces))

    protocol = json.loads(
        (root / "task3" / "electric-field-protocol.json").read_text(
            encoding="utf-8"
        )
    )
    implementation = protocol.get("implementation", {})
    expected_implementation_path = (
        "repository/release/remote_jobs/_efield_calculator.py"
    )
    if implementation.get("path") != expected_implementation_path:
        errors.append("task3_electric_field_protocol: unexpected implementation path")
    if implementation.get("source_commit") != "52557c077f5e3be8444a3f03ea10a647fbd442ca":
        errors.append("task3_electric_field_protocol: implementation commit is unpinned")
    implementation_path = (root / implementation.get("path", "")).resolve()
    if (
        not _is_within(root, implementation_path)
        or not implementation_path.is_file()
        or sha256_file(implementation_path) != implementation.get("sha256")
    ):
        errors.append("task3_electric_field_protocol: implementation hash invalid")
    else:
        source = implementation_path.read_text(encoding="utf-8")
        required_snippets = {
            'self.results["forces"] = charges[:, None] * self.field[None, :]',
            "-np.sum(charges * (atoms.positions @ self.field))",
            "return SumCalculator([dp, ext])",
        }
        if set(implementation.get("verified_text", [])) != required_snippets:
            errors.append(
                "task3_electric_field_protocol: implementation checks are incomplete"
            )
        for snippet in required_snippets:
            if snippet not in source:
                errors.append(
                    "task3_electric_field_protocol: verified implementation text absent"
                )
    history = (root / protocol.get("history_path", "")).resolve()
    if not history.is_file() or sha256_file(history) != protocol.get("history_sha256"):
        errors.append("task3_electric_field_protocol: history hash invalid")
    else:
        history_lines = history.read_text(encoding="utf-8").splitlines()
        required_evidence = {
            1: (0, "system", "content", {"SumCalculator([DeePMD, UniformElectricForce])"}),
            2: (1, "user", "content", {"Cu = +0.765, In = P = S = -0.085"}),
            10: (4, "tool-call", "content", {"job1 = efield_md("}),
            11: (
                4,
                "tool-response",
                "content",
                {"73cee551-d516-4182-8de1-a52c052e0e1f"},
            ),
        }
        evidence_by_line = {
            item.get("jsonl_line"): item for item in protocol.get("evidence", [])
        }
        if not required_evidence.keys() <= evidence_by_line.keys():
            errors.append("task3_electric_field_protocol: required call evidence absent")
        for line_number, (step, role, field, needles) in required_evidence.items():
            evidence = evidence_by_line.get(line_number, {})
            record = json.loads(history_lines[line_number - 1])
            if (
                evidence.get("step") != step
                or evidence.get("role") != role
                or evidence.get("field") != field
                or record.get("step") != step
                or record.get("role") != role
            ):
                errors.append(
                    f"task3_electric_field_protocol: invalid evidence identity at line {line_number}"
                )
                continue
            for needle in needles:
                if (
                    needle not in evidence.get("matched_text", [])
                    or needle not in record.get(field, "")
                ):
                    errors.append(
                        f"task3_electric_field_protocol: call evidence mismatch at line {line_number}"
                    )

    teacher = json.loads(
        (root / "common" / "teacher-model" / "recovery.json").read_text(
            encoding="utf-8"
        )
    )
    teacher_artifact = next(
        (
            item
            for item in inventory.get("artifacts", [])
            if item.get("id") == "teacher_model"
        ),
        {},
    )
    model = root / "common" / "teacher-model" / "frozen_model.pb"
    structure = root / "common" / "CuInP2S6.cif"
    type_map = root / "common" / "teacher-model" / "type_map.raw"
    load_test = teacher.get("load_test", {})
    bindings = {
        "model_sha256": sha256_file(model),
        "structure_sha256": sha256_file(structure),
        "type_map_sha256": sha256_file(type_map),
    }
    for key, expected in bindings.items():
        if load_test.get(key) != expected:
            errors.append(f"teacher_model: invalid load-test {key}")
    extracted_type_map = type_map.read_text(encoding="utf-8").split()
    if load_test.get("model_type_map") != extracted_type_map:
        errors.append("teacher_model: embedded type map was not verified")
    if load_test.get("passed") is not True or load_test.get("outputs", {}).get("all_finite") is not True:
        errors.append("teacher_model: bound finite-value load test did not pass")
    if teacher_artifact.get("load_test_passed") is not True:
        errors.append("teacher_model: inventory load-test flag is false")
    if teacher_artifact.get("type_map_verified") is not True:
        errors.append("teacher_model: inventory type-map flag is false")

    runtime_lock = json.loads(
        (root / "teacher-runtime.lock.json").read_text(encoding="utf-8")
    )
    requirements = root / runtime_lock.get("requirements", {}).get("path", "")
    if runtime_lock.get("python", {}).get("version") != "3.11.15":
        errors.append("teacher_runtime_lock: Python patch version is not pinned")
    if (
        not requirements.is_file()
        or sha256_file(requirements)
        != runtime_lock.get("requirements", {}).get("sha256")
    ):
        errors.append("teacher_runtime_lock: requirements hash is invalid")
    elif len(requirements.read_text(encoding="utf-8").splitlines()) != runtime_lock.get(
        "requirements", {}
    ).get("distribution_count"):
        errors.append("teacher_runtime_lock: distribution count is invalid")
    return errors


def derive_gate(
    inventory: dict[str, Any], root: Path | None = None
) -> dict[str, bool]:
    """Derive source-recovery gates solely from inventory evidence."""
    gate = {gate_name: False for gate_name in GATE_ARTIFACTS.values()}
    artifacts = inventory.get("artifacts", [])
    if root is None or validate_inventory(root, inventory):
        gate["teacher_model_load_test_passed"] = False
        gate["teacher_model_type_map_verified"] = False
        gate["source_recovery_complete"] = False
        gate["benchmark_construction_unblocked"] = False
        return gate

    for artifact in artifacts:
        gate_name = GATE_ARTIFACTS.get(artifact.get("id"))
        if gate_name and artifact.get("status") == "recovered":
            gate[gate_name] = True

    teacher = next(
        (artifact for artifact in artifacts if artifact.get("id") == "teacher_model"),
        {},
    )
    gate["teacher_model_load_test_passed"] = bool(
        gate["teacher_model_recovered"] and teacher.get("load_test_passed") is True
    )
    gate["teacher_model_type_map_verified"] = bool(
        gate["teacher_model_recovered"] and teacher.get("type_map_verified") is True
    )

    core_keys = (
        "structure_recovered",
        "task3_structure_recovered",
        "teacher_model_recovered",
        "task1_workspace_recovered",
        "task2_workspace_recovered",
        "task3_workspace_recovered",
        "task3_electric_field_protocol_verified",
        "repository_commit_pinned",
        "teacher_model_load_test_passed",
        "teacher_model_type_map_verified",
    )
    gate["source_recovery_complete"] = all(gate[key] for key in core_keys)
    gate["benchmark_construction_unblocked"] = gate["source_recovery_complete"]
    return gate


def _recovered_artifact(
    root: Path,
    artifact_id: str,
    relative_path: str,
    provenance: str,
    source_locator: str,
    **evidence: Any,
) -> dict[str, Any]:
    path = root / relative_path
    return {
        "id": artifact_id,
        "status": "recovered",
        "provenance": provenance,
        "path": relative_path,
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "source_locator": source_locator,
        **evidence,
    }


def build_inventory(root: Path) -> dict[str, Any]:
    """Build the canonical inventory from the checked source package."""
    teacher = json.loads(
        (root / "common" / "teacher-model" / "recovery.json").read_text(
            encoding="utf-8"
        )
    )
    release_commit = "52557c077f5e3be8444a3f03ea10a647fbd442ca"
    artifacts = [
        _recovered_artifact(
            root,
            "repository_commit",
            "source.lock.json",
            "upstream_repository",
            f"https://github.com/cz2014/MatClaw/tree/{release_commit}",
        ),
        _recovered_artifact(
            root,
            "paper",
            "paper/matclaw-2604.02688v3.pdf",
            "upstream_paper",
            "https://arxiv.org/pdf/2604.02688v3",
        ),
        _recovered_artifact(
            root,
            "paper",
            "paper/he-physrevb-108-024305.pdf",
            "upstream_repository",
            (
                "repository/release/"
                "workspace_demo1b_distill_pdf/He_paper.pdf"
            ),
        ),
        _recovered_artifact(
            root,
            "structure",
            "common/CuInP2S6.cif",
            "upstream_repository",
            "repository/release/.ref/CuInP2S6.cif",
        ),
        _recovered_artifact(
            root,
            "task3_structure",
            "common/cips_monolayer.cif",
            "upstream_repository",
            "repository/release/.ref/cips_monolayer.cif",
        ),
        _recovered_artifact(
            root,
            "teacher_archive",
            "common/teacher-model/CIPS_data.zip",
            "upstream_author_dataset",
            teacher["source"]["download_url"],
        ),
        _recovered_artifact(
            root,
            "teacher_model",
            "common/teacher-model/frozen_model.pb",
            "upstream_author_dataset",
            f"AIS Square record {teacher['source']['record_id']}:{teacher['archive_member']}",
            type_map_verified=teacher["type_map_verified"],
            load_test_passed=teacher["load_test"]["passed"],
        ),
        _recovered_artifact(
            root,
            "teacher_type_map",
            "common/teacher-model/type_map.raw",
            "upstream_author_dataset",
            f"AIS Square record {teacher['source']['record_id']}:{teacher['type_map_member']}",
        ),
        _recovered_artifact(
            root,
            "teacher_runtime_lock",
            "teacher-runtime.lock.json",
            "locally_validated",
            "runtime versions proven by bound teacher-model load test",
        ),
        _recovered_artifact(
            root,
            "teacher_runtime_requirements",
            "teacher-runtime-requirements.txt",
            "locally_validated",
            "full resolved environment used by bound teacher-model load test",
        ),
        _recovered_artifact(
            root,
            "task1_workspace",
            "task1/manifest.json",
            "upstream_repository",
            "repository/release/workspace_demo1a_distill + workspace_demo1b_distill_pdf",
        ),
        _recovered_artifact(
            root,
            "task2_workspace",
            "task2/manifest.json",
            "upstream_repository",
            (
                "repository/release/workspace_demo2a_curie_no_convergence + "
                "workspace_demo2b_curie_with_convergence"
            ),
        ),
        _recovered_artifact(
            root,
            "task3_workspace",
            "task3/manifest.json",
            "upstream_repository",
            "repository/release/workspace_demo3_search",
        ),
        _recovered_artifact(
            root,
            "task3_electric_field_protocol",
            "task3/electric-field-protocol.json",
            "upstream_repository",
            (
                "repository/release/remote_jobs/_efield_calculator.py + "
                "workspace_demo3_search/history.jsonl lines 1, 2, 10, and 11"
            ),
        ),
        {
            "id": "task2_raw_trajectories",
            "status": "missing",
            "provenance": "missing_upstream",
            "source_locator": (
                "repository/release/workspace_demo2b_curie_with_convergence/"
                "sweep/sweep_manifest.csv contains job UUIDs and trajectory names, "
                "but no trajectory files"
            ),
        },
        {
            "id": "task3_raw_trajectories",
            "status": "missing",
            "provenance": "missing_upstream",
            "source_locator": (
                "repository/release/workspace_demo3_search/search_state.json "
                "contains 14 UUIDs and /pscratch paths, but no trajectory files"
            ),
        },
    ]
    return {
        "schema_version": 1,
        "project": "MatClaw",
        "system": "CuInP2S6",
        "generated_at": "2026-08-10",
        "artifacts": artifacts,
    }


def write_metadata(root: Path) -> tuple[Path, Path]:
    inventory = build_inventory(root)
    errors = validate_inventory(root, inventory)
    if errors:
        raise RuntimeError("invalid generated inventory:\n" + "\n".join(errors))
    gate = derive_gate(inventory, root)
    inventory_path = root / "inventory.json"
    gate_path = root / "recovery_gate.json"
    inventory_path.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    gate_path.write_text(
        json.dumps(gate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return inventory_path, gate_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.write:
        for path in write_metadata(root):
            print(path)
        return
    inventory = build_inventory(root)
    print(
        json.dumps(
            {
                "errors": validate_inventory(root, inventory),
                "gate": derive_gate(inventory, root),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
