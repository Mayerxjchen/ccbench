"""Agent-visible and runtime contracts for autonomous PAgent evaluation."""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path


CASE = Path(__file__).resolve().parents[1]
EXPECTED_PUBLIC = {"system.json"}


def test_public_has_spec_but_no_coordinates():
    files = {p.name for p in (CASE / "public").iterdir() if p.is_file()}
    assert files == EXPECTED_PUBLIC
    assert not list((CASE / "public").rglob("*.xyz"))


def test_system_spec_has_no_coordinate_payload():
    system = json.loads((CASE / "public/system.json").read_text())
    assert system == {
        "system": "liquid water",
        "molecules": {"H2O": 64},
        "atoms": 192,
        "cell_A": [12.4, 12.4, 12.4],
        "periodic": [True, True, True],
        "model_family": "DeePMD",
    }
    assert "positions" not in system and "coordinates" not in system


def test_formal_backend_is_real_hpc_controller():
    task = tomllib.loads((CASE / "task.toml").read_text())
    assert task["task"]["execution_backend"] == "real_hpc_controller"


def test_fresh_workspace_contains_only_target_spec(tmp_path):
    workspace = tmp_path / "workspace"
    shutil.copytree(CASE / "public", workspace)
    files = {str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file()}
    assert files == EXPECTED_PUBLIC
    assert not list(workspace.rglob("*.xyz"))
    assert not any(part in {"reference", "solution", "tests"}
                   for path in workspace.rglob("*") for part in path.parts)


def test_instruction_requires_generation_without_connection_recipe():
    text = (CASE / "instruction.md").read_text()
    assert re.search(r"\b(generate|construct)\b", text, re.IGNORECASE)
    forbidden = (
        "water64.xyz", "HPC_ENVIRONMENT.md", "module load", "ssh hpc",
        "/public/home", "10.26.",
    )
    for token in forbidden:
        assert token not in text
