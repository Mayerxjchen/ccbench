from __future__ import annotations

from pathlib import Path

import pytest

from ccbench.config.run_config import RunConfigError, load_run_config


ROOT = Path(__file__).resolve().parents[2]


def _yaml(extra: str = "") -> str:
    return f"""\
schema_version: 1
run_config_id: test-v1
mode: pilot
model:
  provider: deepseek
  model_id: deepseek-v4-pro
  deployment_id: default
  identity_strength: alias-only
api:
  endpoint_env: DEEPSEEK_BASE_URL
  credential_env: DEEPSEEK_API_KEY
  max_attempts: 4
  retry_base_delay_sec: 1.0
  retry_max_delay_sec: 30.0
  request_timeout_sec: 180.0
  input_usd_micros_per_million_tokens: 0
  output_usd_micros_per_million_tokens: 0
agent_by_execution_class:
  local_sandbox:
    max_model_turns: 64
    max_total_tokens: 10000000
    active_walltime_sec: 7200
    scheduler_wait_walltime_sec: 0
  hpc_controller:
    max_model_turns: 512
    max_total_tokens: 100000000
    active_walltime_sec: 86400
    scheduler_wait_walltime_sec: 604800
treatments:
  no-skill:
    skills_enabled: false
  with-skill:
    skills_enabled: true
{extra}"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "run.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_real_default_config_routes_budget_by_execution_class() -> None:
    config = load_run_config(ROOT / "experiments/main.toml")
    assert config.budget_for("local_sandbox").max_model_turns == 64
    assert config.budget_for("hpc_controller").max_model_turns == 1024
    assert config.treatment_for(skills_enabled=False).name == "no-skill"
    assert config.treatment_for(skills_enabled=True).name == "with-skill"


def test_digest_is_deterministic_and_secret_free(tmp_path: Path) -> None:
    one = load_run_config(_write(tmp_path, _yaml()))
    two = load_run_config(_write(tmp_path, _yaml()))
    assert one.digest == two.digest
    assert one.digest.startswith("sha256:")
    assert "sk-" not in one.canonical_json()


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RunConfigError, match="unknown_policy"):
        load_run_config(_write(tmp_path, _yaml("unknown_policy: true\n")))


def test_literal_secret_is_rejected(tmp_path: Path) -> None:
    text = _yaml().replace(
        "  credential_env: DEEPSEEK_API_KEY\n",
        "  credential_env: DEEPSEEK_API_KEY\n  credential: sk-forbidden\n",
    )
    with pytest.raises(RunConfigError, match="credential"):
        load_run_config(_write(tmp_path, text))


def test_missing_execution_class_is_rejected(tmp_path: Path) -> None:
    text = _yaml().replace(
        "  hpc_controller:\n    max_model_turns: 512\n"
        "    max_total_tokens: 100000000\n"
        "    active_walltime_sec: 86400\n"
        "    scheduler_wait_walltime_sec: 604800\n",
        "",
    )
    with pytest.raises(RunConfigError, match="hpc_controller"):
        load_run_config(_write(tmp_path, text))


def test_treatments_must_be_exact_skill_pair(tmp_path: Path) -> None:
    text = _yaml().replace("  with-skill:\n    skills_enabled: true\n", "")
    with pytest.raises(RunConfigError, match="with-skill"):
        load_run_config(_write(tmp_path, text))


def test_invalid_budget_is_rejected(tmp_path: Path) -> None:
    text = _yaml().replace("max_model_turns: 512", "max_model_turns: 0")
    with pytest.raises(RunConfigError, match="max_model_turns"):
        load_run_config(_write(tmp_path, text))
