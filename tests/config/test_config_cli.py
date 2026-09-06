from __future__ import annotations

import json
from pathlib import Path

from dftworld_bench.config.cli import main


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "infra/runs/skill-ablation-v2.yaml"


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_resolve_is_secret_free(capsys, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-never-print")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    assert main(["resolve", "--run-config", str(CONFIG), "--execution-class", "hpc_controller"]) == 0
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["budget"]["max_model_turns"] == 1024
    assert payload["model"]["model_id"] == "deepseek-v4-pro"
    assert "sk-never-print" not in raw


def test_doctor_reports_missing_env_without_value(capsys, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    assert main(["doctor", "--run-config", str(CONFIG)]) == 2
    payload = _json(capsys)
    assert payload["valid"] is False
    assert "DEEPSEEK_API_KEY" in payload["errors"][0]


def test_doctor_accepts_valid_values_but_redacts_them(capsys, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-never-print")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    assert main(["doctor", "--run-config", str(CONFIG)]) == 0
    raw = capsys.readouterr().out
    assert json.loads(raw)["valid"] is True
    assert "sk-never-print" not in raw
    assert "https://api.deepseek.com" not in raw


def test_diff_reports_changed_path(tmp_path, capsys) -> None:
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        CONFIG.read_text(encoding="utf-8").replace("max_model_turns: 1024", "max_model_turns: 256"),
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
