from pathlib import Path

import pytest

from bench.config.candidate import CandidateConfigError, load_candidate_config


def test_candidate_config_is_secret_free_and_normalized(tmp_path: Path) -> None:
    path = tmp_path / "candidate.toml"
    path.write_text(
        '[model]\nrequested_id="gemini-3.8-flash"\ncli_model="claude-sonnet-4-6"\n'
        'endpoint_env="BENCH_BASE_URL"\ncredential_env="BENCH_API_KEY"\n'
        '[candidate]\nimage="user/candidate:v2"\nsidecar_image="gateway:v1"\n'
        '[compute]\nbackend="compshare"\nprofile="/secure/profile.json"\n',
        encoding="utf-8",
    )
    cfg = load_candidate_config(path)
    assert cfg["model"]["requested_id"] == "gemini-3.8-flash"
    assert cfg["model"]["credential_env"] == "BENCH_API_KEY"
    assert cfg["compute"]["backend"] == "compshare"
    assert cfg["digest"].startswith("sha256:")


@pytest.mark.parametrize("key", ["api_key", "secret", "password", "token"])
def test_candidate_config_rejects_plaintext_secret(tmp_path: Path, key: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(f"[model]\n{key}=\"do-not-store\"\n", encoding="utf-8")
    with pytest.raises(CandidateConfigError, match="secret"):
        load_candidate_config(path)


def test_candidate_config_rejects_unknown_compute_backend(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('[compute]\nbackend="slurm"\n', encoding="utf-8")
    with pytest.raises(CandidateConfigError, match="compute.backend"):
        load_candidate_config(path)


def test_candidate_config_rejects_unknown_field(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text('[candidate]\nimage="x"\nextra="ignored"\n', encoding="utf-8")
    with pytest.raises(CandidateConfigError, match="unknown"):
        load_candidate_config(path)


def test_candidate_config_digest_does_not_depend_on_host_path(tmp_path: Path) -> None:
    first = tmp_path / "a" / "candidate.toml"
    second = tmp_path / "b" / "candidate.toml"
    first.parent.mkdir()
    second.parent.mkdir()
    content = '[model]\nrequested_id="deepseek-v4-pro[1M]"\n'
    first.write_text(content, encoding="utf-8")
    second.write_text(content, encoding="utf-8")
    assert load_candidate_config(first)["digest"] == load_candidate_config(second)["digest"]


def test_candidate_limits_are_strict_and_secret_regex_safe(tmp_path: Path) -> None:
    path = tmp_path / "limits.toml"
    path.write_text(
        "[limits]\nmax_turns=1024\nmax_total_tokens=5000000\n"
        "agent_timeout_sec=14400\nmax_budget_usd=0.0\n", encoding="utf-8"
    )
    assert load_candidate_config(path)["limits"] == {
        "max_turns": 1024, "max_total_tokens": 5000000,
        "agent_timeout_sec": 14400.0, "max_budget_usd": 0.0,
    }


@pytest.mark.parametrize("body", [
    "[limits]\nmax_turns=true\n",
    "[limits]\nmax_total_tokens=0\n",
    "[limits]\nagent_timeout_sec=-1\n",
    "[limits]\nmax_budget_usd=-0.1\n",
    "[limits]\nmax_total_tokens=nan\n",
    "[limits]\nunknown=1\n",
])
def test_candidate_limits_reject_invalid_values(tmp_path: Path, body: str) -> None:
    path = tmp_path / "bad-limits.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(CandidateConfigError):
        load_candidate_config(path)
