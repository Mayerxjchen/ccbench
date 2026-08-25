"""Build lightweight, hash-addressed views of the MatClaw demo workspaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark.sources.matclaw.recovery import sha256_file


WORKSPACE_GROUPS = {
    "task1": (
        "workspace_demo1a_distill",
        "workspace_demo1b_distill_pdf",
    ),
    "task2": (
        "workspace_demo2a_curie_no_convergence",
        "workspace_demo2b_curie_with_convergence",
    ),
    "task3": ("workspace_demo3_search",),
}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def workspace_manifest(root: Path, name: str) -> dict[str, object]:
    workspace = root / "repository" / "release" / name
    files = []
    for path in sorted(item for item in workspace.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return {
        "name": name,
        "provenance": "upstream_repository",
        "source_commit": "52557c077f5e3be8444a3f03ea10a647fbd442ca",
        "files": files,
    }


def protocol_record(root: Path) -> dict[str, object]:
    history_relative = (
        "repository/release/workspace_demo3_search/history.jsonl"
    )
    history = root / history_relative
    evidence = []
    required = {
        1: (
            0,
            "system",
            "content",
            [
                "SumCalculator([DeePMD, UniformElectricForce])",
                "F_DP(i) + q_i * E",
                "-sum(q_i * r_i . E)",
                "eV/A/e",
            ],
        ),
        2: (
            1,
            "user",
            "content",
            [
                "Cu = +0.765, In = P = S = -0.085",
                "/pscratch/sd/c/cz2014/cips_distill/frozen_model.pb",
            ],
        ),
        10: (
            4,
            "tool-call",
            "content",
            [
                'charges = {"Cu": 0.765, "In": -0.085, "P": -0.085, "S": -0.085}',
                "job1 = efield_md(",
            ],
        ),
        11: (
            4,
            "tool-response",
            "content",
            [
                "73cee551-d516-4182-8de1-a52c052e0e1f",
                "31332573-8d8a-46d5-80d3-19cdb4f10d66",
            ],
        ),
    }
    with history.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line_number not in required:
                continue
            expected_step, expected_role, field, needles = required[line_number]
            item = json.loads(line)
            if item["step"] != expected_step or item["role"] != expected_role:
                raise RuntimeError(f"unexpected history identity at line {line_number}")
            missing = [needle for needle in needles if needle not in item[field]]
            if missing:
                raise RuntimeError(
                    f"history line {line_number} lacks required evidence: {missing}"
                )
            evidence.append(
                {
                    "jsonl_line": line_number,
                    "step": expected_step,
                    "role": expected_role,
                    "field": field,
                    "matched_text": needles,
                }
            )
    if len(evidence) != len(required):
        raise RuntimeError("required Task 3 history lines were not found")

    implementation_path = (
        root / "repository" / "release" / "remote_jobs" / "_efield_calculator.py"
    )
    implementation_source = implementation_path.read_text(encoding="utf-8")
    implementation_needles = [
        'self.results["forces"] = charges[:, None] * self.field[None, :]',
        "-np.sum(charges * (atoms.positions @ self.field))",
        "return SumCalculator([dp, ext])",
    ]
    missing_implementation = [
        needle for needle in implementation_needles if needle not in implementation_source
    ]
    if missing_implementation:
        raise RuntimeError(
            f"upstream electric-field implementation lacks: {missing_implementation}"
        )

    return {
        "schema_version": 1,
        "provenance": "upstream_repository",
        "calculator_composition": "SumCalculator([DeePMD, UniformElectricForce])",
        "total_force": "F_total(i) = F_DP(i) + q_i * E",
        "external_energy": "-sum_i(q_i * r_i . E)",
        "field_unit": "eV/angstrom/e",
        "equivalent_field_unit": "V/angstrom",
        "charges_e": {"Cu": 0.765, "In": -0.085, "P": -0.085, "S": -0.085},
        "teacher_model_locator": "/pscratch/sd/c/cz2014/cips_distill/frozen_model.pb",
        "history_path": history_relative,
        "history_sha256": sha256_file(history),
        "implementation": {
            "path": implementation_path.relative_to(root).as_posix(),
            "sha256": sha256_file(implementation_path),
            "source_commit": "52557c077f5e3be8444a3f03ea10a647fbd442ca",
            "verified_text": implementation_needles,
        },
        "evidence": evidence,
    }


def build_task_views(root: Path) -> None:
    for task, workspaces in WORKSPACE_GROUPS.items():
        manifest = {
            "schema_version": 1,
            "task": task,
            "workspaces": [workspace_manifest(root, name) for name in workspaces],
        }
        _write_json(root / task / "manifest.json", manifest)
    _write_json(
        root / "task3" / "electric-field-protocol.json",
        protocol_record(root),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    build_task_views(args.root.resolve())


if __name__ == "__main__":
    main()
