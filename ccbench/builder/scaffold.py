"""Scaffold compiler — Compiles Case IR into canonical case artifacts."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def _hash_file(path: Path) -> str:
    """Compute sha256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def compile_case_ir_to_draft(
    case_ir: dict[str, Any],
    draft_dir: Path,
    *,
    source_dir: Path | None = None,
) -> dict[str, Path]:
    """Compile Case IR into task.md, case.toml, and input/ directory."""
    import shutil
    draft_dir = Path(draft_dir)
    draft_dir.mkdir(parents=True, exist_ok=True)
    input_dir = draft_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, Path] = {}

    # 1. Compile task.md
    cand = case_ir["candidate"]
    target = case_ir["scientific_target"]

    # Materialize candidate inputs from source_dir if provided
    # and generate candidate-inputs.lock.json for provenance binding
    input_lock_entries = []
    if source_dir:
        source_dir = Path(source_dir)
        for inp in cand.get("inputs", []):
            rel_cand_path = inp["path"].removeprefix("input/")
            source_ref = inp.get("source_ref") or inp["path"].removeprefix("source/").removeprefix("input/")
            src_candidate = source_dir / source_ref
            if not src_candidate.is_file():
                src_candidate = source_dir / Path(inp["path"]).name
            if src_candidate.is_file():
                dest_input = input_dir / rel_cand_path
                dest_input.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_candidate, dest_input)
                # Record source→candidate hash binding
                source_hash = _hash_file(src_candidate)
                candidate_hash = _hash_file(dest_input)
                input_lock_entries.append({
                    "source_path": str(source_ref),
                    "source_sha256": source_hash,
                    "candidate_path": f"input/{rel_cand_path}",
                    "candidate_sha256": candidate_hash,
                })

    # Write candidate-inputs.lock.json
    if input_lock_entries:
        import json as _json
        lock_doc = {"schema_version": 1, "inputs": input_lock_entries}
        lock_path = draft_dir / "candidate-inputs.lock.json"
        lock_path.write_text(_json.dumps(lock_doc, indent=2), encoding="utf-8")
        artifacts["candidate_inputs_lock"] = lock_path
    task_content = f"""# {case_ir['identity']['title']}

## Scientific Objective
Target System: {target['system']}
Objective: {target['objective']}

## Task Instructions
{cand['instruction']}

## Submission Requirements
Submit your final solution under `{case_ir['submission']['root']}/`.
"""
    task_file = draft_dir / "task.md"
    task_file.write_text(task_content, encoding="utf-8")
    artifacts["task_md"] = task_file

    # 2. Compile case.toml
    runtime = case_ir["runtime"]
    coverage = case_ir["coverage"]
    identity = case_ir["identity"]
    case_id = identity.get("case_id", "draft-case")

    case_toml_lines = [
        'schema_version = "1.2"',
        f'case_version = "{identity.get("version", "1.0.0")}"',
        "",
        "[execution]",
        f'class = "{runtime["execution_class"]}"',
        "",
        "[task]",
        f'name = "{case_id}"',
        "",
        "[candidate]",
        'instruction = "task.md"',
        f'submission_root = "{case_ir["submission"]["root"]}"',
        f'image = "{runtime["candidate_image"]}"',
        f'max_agent_seconds = {float(runtime.get("timeout_sec", 1800.0))}',
        "",
        "[selection]",
        f'paradigm = "{case_ir["selection"]["paradigm"]}"',
        "",
        "[coverage]",
        f'scientific_domain = "{coverage["scientific_domain"]}"',
        f'method_family = "{coverage["method_family"]}"',
        f'material_class = "{coverage["material_class"]}"',
        f'computation_type = "{coverage["computation_type"]}"',
        f'paradigm = "{case_ir["selection"]["paradigm"]}"',
        "",
    ]
    toml_file = draft_dir / "case.toml"
    toml_file.write_text("\n".join(case_toml_lines), encoding="utf-8")
    artifacts["case_toml"] = toml_file

    # 3. Create submission-contract
    sub_contract = {
        "root": case_ir["submission"]["root"],
        "artifacts": case_ir["submission"]["artifacts"],
    }
    sub_file = draft_dir / "submission-contract.json"
    sub_file.write_text(json.dumps(sub_contract, indent=2), encoding="utf-8")
    artifacts["submission_contract"] = sub_file

    return artifacts
