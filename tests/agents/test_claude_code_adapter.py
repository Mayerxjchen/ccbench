"""Unit tests for ClaudeCodeAdapter and unified agent events."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest

from dftworld_bench.agents import (
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
