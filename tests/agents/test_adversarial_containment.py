"""Adversarial containment and hardened sandbox verification tests.

Verifies that the candidate agent container strictly enforces fail-closed
security boundaries, non-root execution, privilege drop, and interceptors
for forbidden commands (ssh, sbatch, compshare, docker, etc.).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest

_ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = (
    _ROOT / "runtimes" / "recipes" / "agent-claude-code" / "claude-code.lock.json"
    if (_ROOT / "runtimes" / "recipes" / "agent-claude-code" / "claude-code.lock.json").is_file()
    else _ROOT / "base-env-build" / "agent-claude-code" / "claude-code.lock.json"
)
AGENT_IMAGE = "mlffbench-candidate-claude-code-sandbox:v1"


def _docker_and_image_available() -> bool:
    try:
        res = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if res.returncode != 0:
            return False
        img = subprocess.run(
            ["docker", "image", "inspect", AGENT_IMAGE],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        return img.returncode == 0
    except Exception:
        return False


pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(not _docker_and_image_available(), reason=f"Docker daemon or candidate image {AGENT_IMAGE} not available"),
]


def test_agent_image_inspect_and_lock_digest():
    """Verify built image digest matches the canonical claude-code.lock.json."""
    assert LOCK_FILE.is_file(), f"Missing lock file: {LOCK_FILE}"
    lock_data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    expected_digest = lock_data.get("built_image_digest")
    assert expected_digest, "built_image_digest missing in claude-code.lock.json"

    res = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", AGENT_IMAGE],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    actual_digest = res.stdout.strip()
    assert actual_digest == expected_digest, f"Digest mismatch: {actual_digest} != {expected_digest}"


def test_qualify_probe_in_hardened_container():
    """Run internal qualify_agent.sh probe within the hardened container."""
    res = subprocess.run(
        [
            "docker", "run", "--rm",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=256",
            AGENT_IMAGE,
            "/usr/local/bin/qualify_agent.sh",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert res.returncode == 0, f"Probe failed: stdout={res.stdout}, stderr={res.stderr}"
    assert "User: agent (UID=1000)" in res.stdout
    assert "PASS: Zero scientific computing engines" in res.stdout
    assert "PASS: No docker socket or host ssh credentials detected" in res.stdout
    assert "ALL AGENT SANDBOX PROBES PASSED" in res.stdout


@pytest.mark.parametrize("cmd", ["ssh", "sbatch", "compshare", "docker", "nc", "nmap"])
def test_adversarial_command_interceptor(cmd: str):
    """Candidate attempting forbidden infrastructure commands must receive 126 Access Denied."""
    res = subprocess.run(
        [
            "docker", "run", "--rm",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            AGENT_IMAGE,
            cmd, "arg1", "arg2",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert res.returncode == 126, f"Command {cmd} was not blocked with code 126 (got {res.returncode})"
    assert f"Access Denied: command {cmd} is strictly prohibited" in res.stderr


def test_absence_of_scientific_compute_tools():
    """Container must NOT contain any scientific computing tools."""
    for sci_bin in ["cp2k.popt", "cp2k.psmp", "dp", "lmp", "mpirun"]:
        res = subprocess.run(
            ["docker", "run", "--rm", AGENT_IMAGE, "which", sci_bin],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert res.returncode != 0, f"Scientific binary {sci_bin} unexpectedly found in agent container!"


def test_claude_code_version_and_non_root():
    """Verify claude CLI is executable by agent user and reports pinned version."""
    res = subprocess.run(
        [
            "docker", "run", "--rm",
            "--user", "1000:1000",
            AGENT_IMAGE,
            "claude", "--version",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert res.returncode == 0
    assert "0.2.29" in res.stdout


def test_settings_json_read_only_protection(tmp_path: Path):
    """Verify that trusted settings.json mounted read-only cannot be overwritten from container."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    settings_file = trusted / "settings.json"
    settings_file.write_text('{"permissions":{"allow":["FileRead"]}}\n', encoding="utf-8")

    res = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{ws.resolve()}:/app",
            "-v", f"{settings_file.resolve()}:/app/.claude/settings.json:ro",
            AGENT_IMAGE,
            "bash", "-c", "echo '{\"permissions\":{\"allow\":[\"*\"]}}' > /app/.claude/settings.json",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert res.returncode != 0
    assert "Read-only file system" in res.stderr


def test_quarantine_check_blocks_injected_claude_settings(tmp_path: Path):
    """Quarantine must reject tasks that attempt to supply pre-baked .claude settings."""
    from dftworld_bench.agents import ClaudeCodeAdapter
    import asyncio

    ws = tmp_path / "threads" / "probe_task" / "workspace"
    ws.mkdir(parents=True)
    injected = ws / ".claude"
    injected.mkdir()
    (injected / "settings.json").write_text('{"hacked": true}', encoding="utf-8")

    adapter = ClaudeCodeAdapter(
        model="claude-3-7-sonnet-20250219",
        threads_root=tmp_path / "threads",
        task_name="probe_task",
        case_dir=tmp_path / "case",
    )

    with pytest.raises(ValueError, match="workspace contains unauthorized injected .claude settings"):
        asyncio.run(adapter.start("solve task"))


def test_container_egress_network_isolation():
    """Verify candidate container cannot reach public internet under hardened network flags."""
    res = subprocess.run(
        [
            "docker", "run", "--rm",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--dns", "127.0.0.1",
            "--env", "http_proxy=http://127.0.0.1:9",
            "--env", "https_proxy=http://127.0.0.1:9",
            "--env", "all_proxy=http://127.0.0.1:9",
            "--env", "no_proxy=host.docker.internal",
            AGENT_IMAGE,
            "curl", "-s", "--connect-timeout", "2", "http://example.com",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert res.returncode != 0, "Security isolation failure: container reached public internet!"


def test_skills_mount_read_only_protection(tmp_path: Path):
    """Verify skills mounted into container are strictly read-only and immutable."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_file = skills_dir / "SKILL.md"
    skill_file.write_text("# Test Skill\n", encoding="utf-8")

    res = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{skills_dir.resolve()}:/app/.claude/skills:ro",
            AGENT_IMAGE,
            "bash", "-c", "echo 'injected' >> /app/.claude/skills/SKILL.md",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert res.returncode != 0
    assert "Read-only file system" in res.stderr


