"""Tests for the profile registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from dftworld_bench.config.profiles import ProfileRegistry, digest_bytes, canonical_json


# Test data: same profile with different key ordering
PROFILES_A = {
    "agents": {
        "formal-long": {
            "max_model_turns": 512,
            "max_total_tokens": 100000000,
            "agent_active_walltime_sec": 86400,
            "scheduler_wait_walltime_sec": 604800,
        }
    }
}

PROFILES_B_REORDERED = {
    "agents": {
        "formal-long": {
            "scheduler_wait_walltime_sec": 604800,
            "agent_active_walltime_sec": 86400,
            "max_total_tokens": 100000000,
            "max_model_turns": 512,
        }
    }
}


def test_profile_digest_ignores_toml_key_order():
    """Digest must be identical regardless of TOML key ordering."""
    a = ProfileRegistry.from_mapping(PROFILES_A)
    b = ProfileRegistry.from_mapping(PROFILES_B_REORDERED)
    assert a.digest("agents", "formal-long") == b.digest("agents", "formal-long")


def test_canonical_json_is_deterministic():
    """Canonical JSON must be deterministic across Python dict ordering."""
    obj = {"b": 2, "a": 1, "c": {"z": 26, "y": 25}}
    result = canonical_json(obj)
    assert result == '{"a":1,"b":2,"c":{"y":25,"z":26}}'


def test_digest_bytes_is_sha256():
    """Digest must be SHA-256 with sha256: prefix."""
    result = digest_bytes("test")
    assert result.startswith("sha256:")
    assert len(result) == 71  # sha256: + 64 hex chars


def test_profile_registry_loads_from_directory(tmp_path):
    """ProfileRegistry.load() must load all *-profiles.toml files."""
    (tmp_path / "agent-profiles.toml").write_text(
        "[agents.pilot]\n"
        "max_model_turns = 128\n"
        "max_total_tokens = 100000\n"
        "agent_active_walltime_sec = 3600\n"
        "scheduler_wait_walltime_sec = 1800\n",
        encoding="utf-8",
    )
    (tmp_path / "model-profiles.toml").write_text(
        '[models.default]\nprovider = "openai"\n',
        encoding="utf-8",
    )
    registry = ProfileRegistry.load(tmp_path)
    assert registry.require("agents", "pilot")["max_model_turns"] == 128
    assert registry.require("models", "default")["provider"] == "openai"


def test_profile_registry_raises_on_missing_kind():
    """require() must raise KeyError for unknown kind."""
    registry = ProfileRegistry.from_mapping({})
    with pytest.raises(KeyError, match="unknown profile kind"):
        registry.require("nonexistent", "profile")


def test_profile_registry_raises_on_missing_name():
    """require() must raise KeyError for unknown profile name."""
    registry = ProfileRegistry.from_mapping(PROFILES_A)
    with pytest.raises(KeyError, match="unknown profile"):
        registry.require("agents", "nonexistent")


def test_profile_registry_kinds_and_names():
    """kinds() and names() must return sorted lists."""
    registry = ProfileRegistry.from_mapping(PROFILES_A)
    assert registry.kinds() == ["agents"]
    assert registry.names("agents") == ["formal-long"]
    assert registry.names("nonexistent") == []


def test_profile_registry_from_mapping():
    """from_mapping() must create a registry from in-memory data."""
    registry = ProfileRegistry.from_mapping(PROFILES_A)
    profile = registry.require("agents", "formal-long")
    assert profile["max_model_turns"] == 512
