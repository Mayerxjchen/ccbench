from __future__ import annotations

import pytest

from bench.agents import build_claude_code_cli_argv, resolve_claude_model_route


def test_custom_model_id_routes_via_supported_cli_alias_without_rewrite() -> None:
    model = "deepseek-v4-pro[1M]"
    route = resolve_claude_model_route(model)
    assert route.requested_model == model
    assert route.cli_model == "claude-sonnet-4-6"
    assert route.upstream_model == model
    assert route.context_selector == "1M"
    argv = build_claude_code_cli_argv(
        route.cli_model,
        instruction="solve",
        tools=("Bash", "Read"),
        effort="medium",
        session_id="run-1",
    )
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-6"
    assert model not in argv
    assert "--session-id" in argv
    assert argv[argv.index("--allowedTools") + 1] == "Bash,Read"
    assert "--disallowedTools" in argv


def test_resume_uses_same_session() -> None:
    argv = build_claude_code_cli_argv(
        "custom/provider-route",
        instruction="continue",
        tools=("Bash", "Read"),
        effort="high",
        session_id="run-1",
        resume_session=True,
    )
    assert argv[argv.index("--resume") + 1] == "run-1"
    assert "--session-id" not in argv


def test_official_claude_model_stays_direct() -> None:
    route = resolve_claude_model_route("claude-sonnet-4-6")
    assert route.cli_model == route.upstream_model == route.requested_model


def test_unknown_tool_is_rejected() -> None:
    from bench.agents import ClaudeCodeAdapter

    with pytest.raises(ValueError, match="frozen container allowlist"):
        ClaudeCodeAdapter(
            model="m",
            threads_root="/tmp",
            task_name="t",
            case_dir=".",
            tools=("WebSearch",),
        )


def test_sidecar_recipe_does_not_double_prefix_python_command() -> None:
    dockerfile = open(
        "runtimes/recipes/model-gateway-sidecar/Dockerfile", encoding="utf-8"
    ).read()
    assert 'ENTRYPOINT ["python3"]' not in dockerfile


def test_candidate_settings_never_mount_into_public_workspace() -> None:
    source = open("bench/agents.py", encoding="utf-8").read()
    assert ":/app/.claude/" not in source
    assert "CLAUDE_CONFIG_DIR=/home/agent/.claude" in source


def test_candidate_output_capture_is_bounded_and_reaps_process() -> None:
    source = open("bench/agents.py", encoding="utf-8").read()
    assert "MAX_CLAUDE_STREAM_BYTES" in source
    assert "asyncio.create_task(capture_stream" in source
    assert "await proc.wait()" in source
