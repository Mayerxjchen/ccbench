#!/usr/bin/env python3
"""Cluster profile for the MatClaw HPC controller (Case 031).

The controller used to carry ~15 hardcoded site facts (SSH alias, Slurm
account/partition/QoS/gres, apptainer path, remote root, node arch).  A
third-party deployment must not edit source to point at its own cluster — it
supplies a TOML profile describing that cluster, and the controller reads every
site-specific value from it.  The SIF path/SHA are NOT here: F3 makes the
runtime lock the single truth for SIF identity.

Format: TOML via the stdlib ``tomllib`` (Python 3.11+), so no new dependency
and consistent with the repo's existing ``task.toml`` / ``pyproject.toml``.

Every schema key is REQUIRED: a missing or wrong-typed key fails fast with a
clear message instead of silently falling back to a "generic default" that
would not match the site.  The in-repo template is
``scripts/hpc/cluster_profile.toml``; ``DEFAULT_PROFILE`` mirrors the current
the HPC site site values so the controller keeps working with no profile file.

python3.11-compatible, standard library only.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Dotted key -> acceptable type(s).  A tuple means "any of these".
PROFILE_SCHEMA: Dict[str, Union[type, Tuple[type, ...]]] = {
    "ssh.host": str,
    "ssh.user": str,
    "ssh.port": int,
    "ssh.options.BatchMode": str,
    "ssh.options.ConnectTimeout": str,
    "ssh.options.IdentitiesOnly": str,
    "ssh.options.ControlMaster": str,
    "ssh.options.StrictHostKeyChecking": str,
    "slurm.account": str,
    "slurm.partition": str,
    "slurm.qos": str,
    "slurm.gres": str,
    "slurm.nodes": str,
    "slurm.cpus_per_task": (int, str),
    "slurm.mem": str,
    "slurm.time_paper": str,
    "slurm.time_smoke": str,
    "slurm.time_probe": str,
    "paths.remote_root": str,
    "paths.apptainer": str,
    "paths.run_suffix": str,
    "runtime.expected_node_arch": str,
    "runtime.sync_strategy": str,
}


class ProfileError(ValueError):
    """Raised when a cluster profile is missing, ungrammatical, or invalid."""


def _flatten(profile: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Flatten a nested dict to dotted keys for schema checks.

    ``{"ssh": {"host": "x"}}`` -> ``{"ssh.host": "x"}``.
    """
    out: Dict[str, Any] = {}
    for key, value in profile.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, dotted))
        else:
            out[dotted] = value
    return out


def validate_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Require every schema key, with an acceptable type; return the profile.

    Raises ``ProfileError`` (a ``ValueError``) on any missing/invalid key.
    """
    flat = _flatten(profile)
    missing = [key for key in PROFILE_SCHEMA if key not in flat]
    if missing:
        raise ProfileError(
            "cluster profile missing required key(s): "
            + ", ".join(sorted(missing))
        )
    bad: List[str] = []
    for key, expected in PROFILE_SCHEMA.items():
        value = flat[key]
        types = expected if isinstance(expected, tuple) else (expected,)
        if not isinstance(value, types):
            bad.append(
                f"{key}: expected "
                + "|".join(t.__name__ for t in types)
                + f", got {type(value).__name__} ({value!r})"
            )
    if bad:
        raise ProfileError(
            "cluster profile has invalid values:\n  " + "\n  ".join(bad)
        )
    return profile


def load_profile(profile_path: Union[str, Path]) -> Dict[str, Any]:
    """Load and validate a cluster profile TOML file."""
    path = Path(profile_path)
    try:
        with open(path, "rb") as fh:
            profile = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ProfileError(f"cluster profile not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(
            f"cluster profile is not valid TOML ({path}): {exc}"
        ) from exc
    if not isinstance(profile, dict):
        raise ProfileError(
            f"cluster profile root must be a table ({path})"
        )
    return validate_profile(profile)


# Default profile mirrors the current the HPC site site so the controller works
# with no profile file (backward compatible).  A third-party deployment should
# copy scripts/hpc/cluster_profile.toml and edit it, then pass the path in.
DEFAULT_PROFILE: Dict[str, Any] = {
    "ssh": {
        "host": "<site-alias>",
        "user": "",
        "port": 22,
        "options": {
            "BatchMode": "yes",
            "ConnectTimeout": "10",
            "IdentitiesOnly": "no",   # use the forwarded agent, not a key path
            "ControlMaster": "no",    # no control-socket dir inside container
            "StrictHostKeyChecking": "accept-new",
        },
    },
    "slurm": {
        "account": "acct-blocked",
        "partition": "gpu",
        "qos": "normal",
        "gres": "gpu:1",
        "nodes": "1",
        "cpus_per_task": "8",
        "mem": "64G",
        "time_paper": "08:00:00",
        "time_smoke": "00:30:00",
        "time_probe": "00:30:00",
    },
    "paths": {
        "remote_root": "/public/home/<site-user>/dftworld2-runs/matclaw-031",
        "apptainer": "/public/software/apptainer/bin/apptainer",
        "run_suffix": "runs",
    },
    "runtime": {
        "expected_node_arch": "x86_64",
        "sync_strategy": "sync_back",
    },
}
