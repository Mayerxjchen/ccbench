"""Unit tests for ClaudeCodeAdapter and unified agent events."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest

from ccbench.agents import (
    AgentAdapter,
    ClaudeCodeAdapter,
    parse_claude_code_event,
    TextDelta,
    ToolCallBegin,
    ToolResult,
    TurnResult,
)


def test_claude_code_adapter_implements_protocol(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter(
        model="claude-3-7-sonnet-20250219",
        threads_root=tmp_path / "threads",
        task_name="test_task",
        case_dir=tmp_path / "case",
    )
    assert isinstance(adapter, AgentAdapter)
    assert "claude-code" in adapter.version


def test_claude_code_adapter_blocks_forbidden_env(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="candidate env would receive trusted API secrets"):
        ClaudeCodeAdapter(
            model="claude-3-7-sonnet-20250219",
            threads_root=tmp_path / "threads",
            task_name="test_task",
            case_dir=tmp_path / "case",
            container_env={"ANTHROPIC_SECRET_KEY": "sk-ant-test"},
            forbidden_env_names=frozenset({"ANTHROPIC_SECRET_KEY"}),
        )


def test_parse_claude_code_event_stream() -> None:
    # Text delta
    line1 = json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}})
    evt1 = parse_claude_code_event(line1)
    assert isinstance(evt1, TextDelta)
    assert evt1.text == "Hello"

    # Tool use
    line2 = json.dumps({
        "type": "tool_use",
        "name": "hpc-submit",
        "input": {"command": "bench-hpc capabilities"},
        "id": "toolu_123",
    })
    evt2 = parse_claude_code_event(line2)
    assert isinstance(evt2, ToolCallBegin)
    assert evt2.name == "hpc-submit"
    assert evt2.call_id == "toolu_123"

    # Tool result
    line3 = json.dumps({
        "type": "tool_result",
        "name": "hpc-submit",
        "tool_use_id": "toolu_123",
        "content": "output ok",
        "is_error": False,
    })
    evt3 = parse_claude_code_event(line3)
    assert isinstance(evt3, ToolResult)
    assert evt3.ok is True
    assert evt3.content == "output ok"

    # Turn complete with usage
    line4 = json.dumps({
        "type": "result",
        "turn": 1,
        "usage": {"input_tokens": 150, "output_tokens": 50, "total_tokens": 200},
    })
    evt4 = parse_claude_code_event(line4)
    assert isinstance(evt4, TurnResult)
    assert evt4.usage["total_tokens"] == 200
    assert evt4.usage["prompt_tokens"] == 150
    assert evt4.usage["completion_tokens"] == 50


def test_parse_claude_code_event_null_token_handling() -> None:
    # When tokens cannot be parsed, records None rather than forged 0
    line = json.dumps({
        "type": "result",
        "turn": 1,
    })
    evt = parse_claude_code_event(line)
    assert isinstance(evt, TurnResult)
    assert evt.usage["total_tokens"] is None
    assert evt.usage["prompt_tokens"] is None
    assert evt.usage["completion_tokens"] is None


def test_install_skills_to_claude_dir(tmp_path: Path) -> None:
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test Skill", encoding="utf-8")

    threads_root = tmp_path / "threads"
    adapter = ClaudeCodeAdapter(
        model="claude-3-7-sonnet-20250219",
        threads_root=threads_root,
        task_name="sample_case",
        case_dir=tmp_path / "case",
        skill_roots=(skill_dir,),
    )
    # Target directory inside workspace
    ws = Path(adapter.workspace)
    ws.mkdir(parents=True, exist_ok=True)

    asyncio.run(adapter._install_skills_to_claude_dir())

    installed = ws / ".claude" / "skills" / "test-skill" / "SKILL.md"
    assert installed.is_file()
    assert installed.read_text(encoding="utf-8") == "# Test Skill"


def test_adapter_startup_failure_rollback_retains_topology_and_retry_close_succeeds(tmp_path: Path) -> None:
    """When container startup fails and topology rollback fails, Adapter retains
    _topology and resource handles, proxy is closed, and subsequent close() retries topology cleanup."""
    from unittest import mock
    from ccbench.core.sidecar_topology import SidecarTopologyManager, TopologyRollbackError

    async def _test():
        threads_root = tmp_path / "threads"
        case_dir = tmp_path / "case"
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "Dockerfile").write_text("FROM alpine:latest\n", encoding="utf-8")
        (case_dir / "task.toml").write_text('schema_version = "1.2"\n[task]\nname="test"\n', encoding="utf-8")

        mock_proxy = mock.MagicMock()
        mock_proxy.start = mock.AsyncMock(return_value=9999)
        mock_proxy.ephemeral_token = "tok"
        mock_proxy.close = mock.AsyncMock()

        mock_topology = mock.MagicMock(spec=SidecarTopologyManager)
        mock_topology.create_network = mock.AsyncMock(return_value="net-123")
        mock_topology.start_sidecar = mock.AsyncMock(return_value="sidecar-cid-456")
        mock_topology.register_candidate = mock.MagicMock()
        mock_topology.rollback = mock.AsyncMock(side_effect=TopologyRollbackError("simulated rollback failure"))
        mock_topology.candidate_cid = "cand-cid-789"
        mock_topology.sidecar_cid = "sidecar-cid-456"
        mock_topology.network_name = "net-123"
        mock_topology.is_closed = False

        adapter = ClaudeCodeAdapter(
            model="claude-3-7-sonnet-20250219",
            threads_root=threads_root,
            task_name="sample_case",
            case_dir=case_dir,
            api_endpoint="http://fake-upstream",
        )

        with mock.patch("ccbench.agents.SidecarTopologyManager", return_value=mock_topology), \
             mock.patch("ccbench.agents.ModelGatewayProxy", return_value=mock_proxy), \
             mock.patch.object(adapter, "_start_container", new_callable=mock.AsyncMock, side_effect=RuntimeError("container launch error")):

            with pytest.raises(TopologyRollbackError, match="simulated rollback failure"):
                await adapter.start("test instruction")

            # 1. Verify _topology is retained and handles are synchronized
            assert adapter._topology is mock_topology
            assert adapter.container_id == "cand-cid-789"
            assert adapter.sidecar_cid == "sidecar-cid-456"
            assert adapter.internal_net == "net-123"

            # 2. Verify model_proxy was properly closed despite rollback failure
            mock_proxy.close.assert_awaited_once()
            assert adapter._model_proxy is None

        # 3. Subsequent close() retries topology cleanup
        async def _close_success():
            mock_topology.is_closed = True

        mock_topology.close = mock.AsyncMock(side_effect=_close_success)
        await adapter.close()
        mock_topology.close.assert_awaited_once()
        assert adapter._topology is None
        assert adapter.container_id is None
        assert adapter.sidecar_cid is None
        assert adapter.internal_net is None

    asyncio.run(_test())


def test_adapter_fallback_cleanup_retains_handles_on_docker_failure_and_retry_succeeds(tmp_path: Path) -> None:
    """When topology manager is None and fallback direct cleanup encounters
    docker failures, Adapter must retain the failed resource handles (not wipe them),
    and allow a subsequent close() retry to succeed.
    """
    from unittest import mock

    async def _test():
        adapter = ClaudeCodeAdapter(
            model="claude-3-7-sonnet-20250219",
            threads_root=tmp_path / "threads",
            task_name="sample_case",
            case_dir=tmp_path / "case",
        )
        assert adapter._topology is None

        adapter.container_id = "cand-cid-111"
        adapter.sidecar_cid = "sidecar-cid-222"
        adapter.internal_net = "net-333"

        # Mock subprocess exec
        async def fake_subprocess_exec(*cmd, **kwargs):
            m = mock.MagicMock()
            if cmd == ("docker", "stop", "cand-cid-111"):
                m.returncode = 1
                m.communicate = mock.AsyncMock(return_value=(b"", b"Error stopping container cand-cid-111"))
            elif cmd == ("docker", "stop", "sidecar-cid-222"):
                m.returncode = 0
                m.communicate = mock.AsyncMock(return_value=(b"", b""))
            elif cmd == ("docker", "network", "rm", "net-333"):
                m.returncode = 1
                m.communicate = mock.AsyncMock(return_value=(b"", b"network has active endpoints"))
            else:
                m.returncode = 0
                m.communicate = mock.AsyncMock(return_value=(b"", b""))
            return m

        with mock.patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess_exec):
            with pytest.raises(RuntimeError, match="Errors occurred during ClaudeCodeAdapter teardown") as exc_info:
                await adapter.close()

            err_msg = str(exc_info.value)
            assert "docker stop candidate failed" in err_msg
            assert "docker network rm failed" in err_msg

            # Retained handles check
            assert adapter.container_id == "cand-cid-111"
            assert adapter.sidecar_cid is None  # Succeeded, cleared
            assert adapter.internal_net == "net-333"

        # Retry: all commands succeed
        async def fake_subprocess_exec_retry(*cmd, **kwargs):
            m = mock.MagicMock()
            m.returncode = 0
            m.communicate = mock.AsyncMock(return_value=(b"", b""))
            return m

        with mock.patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess_exec_retry):
            await adapter.close()
            assert adapter.container_id is None
            assert adapter.sidecar_cid is None
            assert adapter.internal_net is None

    asyncio.run(_test())


