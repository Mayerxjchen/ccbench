from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.infra import qualify_candidate_runtimes as qualification


def test_candidate_probe_executes_recipe_smoke_with_formal_hardening(
    monkeypatch, tmp_path: Path,
) -> None:
    smoke = tmp_path / "smoke.sh"
    smoke.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(argv: list[str], *, timeout: float = 120.0):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "CANDIDATE_BASE_SMOKE_OK\n", "")

    monkeypatch.setattr(qualification, "CANDIDATE_SMOKE", smoke)
    monkeypatch.setattr(qualification, "_run", fake_run)

    qualification._probe("candidate", "candidate:v2")

    assert len(commands) == 1
    command = commands[0]
    assert command[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == command[command.index("--network"):command.index("--network") + 2]
    assert "--read-only" in command
    assert ["--cap-drop", "ALL"] == command[command.index("--cap-drop"):command.index("--cap-drop") + 2]
    assert ["--security-opt", "no-new-privileges"] == command[command.index("--security-opt"):command.index("--security-opt") + 2]
    assert ["--user", "10001:10001"] == command[command.index("--user"):command.index("--user") + 2]
    assert command[-3:] == ["bash", "candidate:v2", "/opt/bench-candidate-smoke.sh"]
    assert f"{smoke}:/opt/bench-candidate-smoke.sh:ro" in command


def test_candidate_probe_fails_when_recipe_smoke_is_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(qualification, "CANDIDATE_SMOKE", tmp_path / "missing.sh")

    try:
        qualification._probe("candidate", "candidate:v2")
    except RuntimeError as exc:
        assert "smoke script is missing" in str(exc)
    else:
        raise AssertionError("missing smoke script must fail qualification")
