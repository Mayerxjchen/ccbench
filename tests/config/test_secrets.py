"""Tests for the secret provider."""

from __future__ import annotations

import json

import pytest

from ccbench.config.secrets import EnvSecretProvider, SecretValue


def test_secret_never_serializes(monkeypatch):
    """SecretValue must not be serializable to JSON."""
    monkeypatch.setenv("DFTWORLD_API_KEY_PRIMARY", "super-secret")
    value = EnvSecretProvider({"primary": "DFTWORLD_API_KEY_PRIMARY"}).get("primary")
    assert value.reveal() == "super-secret"
    assert "super-secret" not in repr(value)
    with pytest.raises(TypeError):
        json.dumps(value)


def test_secret_repr_is_redacted(monkeypatch):
    """SecretValue repr must not contain the secret."""
    monkeypatch.setenv("TEST_SECRET", "my-secret")
    value = SecretValue("my-secret", source="test")
    assert "my-secret" not in repr(value)
    assert "redacted" in repr(value)


def test_secret_str_is_redacted():
    """SecretValue str must not contain the secret."""
    value = SecretValue("my-secret", source="test")
    assert str(value) == "<redacted>"


def test_secret_reveal_returns_value():
    """reveal() must return the actual secret value."""
    value = SecretValue("my-secret", source="test")
    assert value.reveal() == "my-secret"


def test_secret_equality():
    """SecretValues with the same value must be equal."""
    a = SecretValue("secret", source="test")
    b = SecretValue("secret", source="other")
    assert a == b


def test_secret_hash():
    """SecretValues must be hashable."""
    a = SecretValue("secret", source="test")
    b = SecretValue("secret", source="other")
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_env_secret_provider_get(monkeypatch):
    """EnvSecretProvider.get() must resolve from environment."""
    monkeypatch.setenv("MY_API_KEY", "api-key-123")
    provider = EnvSecretProvider({"my_api": "MY_API_KEY"})
    value = provider.get("my_api")
    assert value.reveal() == "api-key-123"


def test_env_secret_provider_raises_on_unknown_profile():
    """EnvSecretProvider.get() must raise KeyError for unknown profile."""
    provider = EnvSecretProvider({})
    with pytest.raises(KeyError, match="unknown secret profile"):
        provider.get("nonexistent")


def test_env_secret_provider_raises_on_missing_env(monkeypatch):
    """EnvSecretProvider.get() must raise ValueError if env var is not set."""
    monkeypatch.delenv("MISSING_VAR", raising=False)
    provider = EnvSecretProvider({"test": "MISSING_VAR"})
    with pytest.raises(ValueError, match="environment variable"):
        provider.get("test")


def test_env_secret_provider_available(monkeypatch):
    """EnvSecretProvider.available() must list available profiles."""
    monkeypatch.setenv("AVAILABLE_KEY", "value")
    monkeypatch.delenv("UNAVAILABLE_KEY", raising=False)
    provider = EnvSecretProvider({
        "available": "AVAILABLE_KEY",
        "unavailable": "UNAVAILABLE_KEY",
    })
    assert provider.available() == ["available"]
