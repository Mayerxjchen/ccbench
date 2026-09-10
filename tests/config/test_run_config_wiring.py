from pathlib import Path
import pytest
from bench.agents import ClaudeCodeAdapter


def test_claude_code_adapter_receives_explicit_trusted_values(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter(
        model="claude-3-7-sonnet-20250219",
        threads_root=tmp_path / "threads",
        task_name="test_wiring",
        case_dir=tmp_path / "case",
        api_endpoint="https://api.anthropic.com",
        api_key="sk-trusted-ant",
    )
    assert adapter.api_endpoint == "https://api.anthropic.com"
    assert adapter._api_key == "sk-trusted-ant"
    assert "ANTHROPIC_API_KEY" not in adapter.container_env

