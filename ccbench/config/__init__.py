"""Centralized configuration for dftworld infrastructure.

This package provides profile loading, secret management, and experiment
resolution. Profiles are loaded from TOML files in infra/config/; secrets
are resolved from environment variables and never committed.
"""

from ccbench.config.profiles import ProfileRegistry
from ccbench.config.secrets import EnvSecretProvider, SecretValue

__all__ = ["ProfileRegistry", "EnvSecretProvider", "SecretValue"]
