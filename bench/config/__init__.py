"""Configuration for the reusable Bench Candidate/HPC infrastructure.

Profiles are loaded from ``infra/config/``; secrets are resolved from
environment variables and never committed.  Legacy experiment files are not
part of the active run entry point.
"""

from bench.config.profiles import ProfileRegistry

__all__ = ["ProfileRegistry"]
