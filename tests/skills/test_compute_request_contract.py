"""Adversarial contract for the request-only Candidate skill."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.core.digests import sha256_file
from bench.mvp import MvpError, export_case, validate_compute_request

ROOT = Path(__file__).resolve().parents[2]


def _case(tmp_path: Path) -> Path:
    case = tmp_path / "case"
    (case / "input").mkdir(parents=True)
    (case / "input" / "input.json").write_text("{}\n", encoding="utf-8")
    (case / "task.md").write_text("Do the task.\n", encoding="utf-8")
    (case / "case.toml").write_text(
        'schema_version = "1.2"\ncase_version = "1.0"\n'
        '[execution]\nclass = "local_sandbox"\n'
        '[task]\nname = "request-only"\ndescription = "test"\n'
        '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
        'files = [{ destination = ".", source = "input/**", strip_prefix = "input" }]\n'
        '[agent]\nprofile = "claude-mvp"\nskills = ["bench-compute-request"]\n'
        '[verifier]\ntimeout_sec = 30\n'
        '[environment]\ncpus = 1\nmemory_mb = 1024\nstorage_mb = 1024\n'
        'gpus = 0\nallow_internet = false\n',
        encoding="utf-8",
    )
    return case


def _request(bundle: Path, command: list[str]) -> Path:
    path = bundle / "compute-requests" / "request.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "compute_class": "cpu",
                "command": command,
                "resources": {
                    "nodes": 1,
                    "ntasks": 1,
                    "cpus_per_task": 1,
                    "memory_gb_per_node": 1,
                    "walltime_min": 1,
                    "gpus": 0,
                },
                "inputs": [
                    {
                        "path": "input.json",
                        "sha256": sha256_file(bundle / "input.json"),
                        "size": (bundle / "input.json").stat().st_size,
                    }
                ],
                "outputs": ["compute-results/result.json"],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_skill_and_case_export_are_static_request_only(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    export_case(_case(tmp_path), bundle)
    assert (bundle / ".claude" / "skills" / "bench-compute-request" / "SKILL.md").is_file()
    assert not (bundle / "solution").exists()
    assert not (bundle / "reference").exists()
    assert not (bundle / "verifier").exists()


@pytest.mark.parametrize(
    "command",
    [
        ["bash", "-c", "echo unsafe"],
        ["ssh", "host", "run"],
        ["python", "../escape.py"],
        ["python", "$(id)"],
        ["python", "--submit"],
        ["python", "--cancel"],
        ["compshare-cli", "instance", "list"],
    ],
)
def test_request_rejects_operator_shell_and_path_injection(tmp_path: Path, command: list[str]) -> None:
    bundle = tmp_path / "bundle"
    export_case(_case(tmp_path), bundle)
    with pytest.raises(MvpError):
        validate_compute_request(bundle, _request(bundle, command))


def test_request_accepts_only_argv_without_operator_action(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    export_case(_case(tmp_path), bundle)
    request = _request(bundle, ["python", "run.py"])
    assert validate_compute_request(bundle, request)["compute_class"] == "cpu"


def test_no_external_operator_cli_is_discoverable_from_skill_tree() -> None:
    skill_text = (
        ROOT / "runtimes" / "recipes" / "skills" / "bench-compute-request" / "SKILL.md"
    ).read_text(encoding="utf-8").lower()
    assert "dynamic" not in skill_text
    assert "discover" not in skill_text
