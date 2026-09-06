from __future__ import annotations

import json
from pathlib import Path

from ccbench.config.cli import main


import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "experiments/main.toml"


@pytest.fixture(autouse=True)
def hermetic_env(monkeypatch):
    """Ensure tests are hermetic by wiping ambient credential env vars."""
    for var in [
        "CCBENCH_API_KEY",
        "CCBENCH_BASE_URL",
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
    ]:
        monkeypatch.delenv(var, raising=False)


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_resolve_is_secret_free(capsys, monkeypatch) -> None:
    monkeypatch.setenv("CCBENCH_API_KEY", "sk-never-print")
    monkeypatch.setenv("CCBENCH_BASE_URL", "https://api.deepseek.com")
    assert main(["resolve", "--run-config", str(CONFIG), "--execution-class", "hpc_controller"]) == 0
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["budget"]["max_model_turns"] == 1024
    assert payload["model"]["model_id"] == "deepseek-v4-pro"
    assert "sk-never-print" not in raw


def test_doctor_reports_missing_env_without_value(capsys, monkeypatch) -> None:
    monkeypatch.setenv("CCBENCH_BASE_URL", "https://api.deepseek.com")
    assert main(["doctor", "--run-config", str(CONFIG)]) == 2
    payload = _json(capsys)
    assert payload["valid"] is False
    assert any("API_KEY" in err for err in payload["errors"])


def test_doctor_accepts_valid_values_but_redacts_them(capsys, monkeypatch) -> None:
    monkeypatch.setenv("CCBENCH_API_KEY", "sk-never-print")
    monkeypatch.setenv("CCBENCH_BASE_URL", "https://api.deepseek.com")
    assert main(["doctor", "--run-config", str(CONFIG)]) == 0
    raw = capsys.readouterr().out
    assert json.loads(raw)["valid"] is True
    assert "sk-never-print" not in raw
    assert "https://api.deepseek.com" not in raw


def test_doctor_backward_compat_deepseek_env(capsys, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-legacy-secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    assert main(["doctor", "--run-config", str(CONFIG)]) == 0
    raw = capsys.readouterr().out
    assert json.loads(raw)["valid"] is True
    assert "sk-legacy-secret" not in raw


def test_diff_reports_changed_path(tmp_path, capsys) -> None:
    changed = tmp_path / "changed.toml"
    changed.write_text(
        CONFIG.read_text(encoding="utf-8").replace("max_model_turns = 1024", "max_model_turns = 256"),
        encoding="utf-8",
    )
    assert main(["diff", str(CONFIG), str(changed)]) == 1
    payload = _json(capsys)
    assert payload["differences"] == [
        {
            "path": "agent_by_execution_class.hpc_controller.max_model_turns",
            "left": 1024,
            "right": 256,
        }
    ]
