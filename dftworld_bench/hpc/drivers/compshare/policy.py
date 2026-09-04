"""Unified single-source policy for CompShare instance ownership and zero-orphan states.

Invariants:
- SAFE_DELETED_STATES is strictly {"deleted", "terminated"}. All other states
  (including "stopped", "stopping", "starting", "running", "unknown", "failed")
  require active cleanup and fail zero-orphan gates.
- Ownership marker is deterministically derived:
  name: mlffbench-{run_id}-worker
  remark: mlffbench:{run_id}:worker
- Empty instance ID on matched managed instance fails closed.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

SAFE_DELETED_STATES: frozenset[str] = frozenset({"deleted", "terminated"})


def instance_requires_cleanup(status: str | None) -> bool:
    """Return True if an instance status indicates active, stopped, or uncleared billing resources."""
    if not status:
        return True
    return status.strip().lower() not in SAFE_DELETED_STATES


import re

_SAFE_RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


def make_ownership_marker(run_id: str) -> tuple[str, str]:
    """Derive deterministic (name, remark) ownership marker pair for a given run_id."""
    if _SAFE_RUN_ID_PATTERN.match(run_id):
        name = f"mlffbench-{run_id}-worker"
        remark = f"mlffbench:{run_id}:worker"
    else:
        token = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
        name = f"mlffbench-{token}"
        remark = f"mlffbench:run:{token}"
    return name, remark


def matches_ownership_marker(
    instance: Mapping[str, Any],
    run_id: str | None = None,
) -> bool:
    """Check whether a cloud instance record belongs to MLFFBench and optionally a specific run_id."""
    name = str(instance.get("name") or "")
    remark = str(instance.get("remark") or "")

    if run_id is not None:
        token = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
        exp_name, exp_remark = make_ownership_marker(run_id)
        return (
            name == exp_name
            or remark == exp_remark
            or name == f"mlffbench-{token}"
            or remark == f"mlffbench:run:{token}"
            or name == f"mlffbench-{token}-worker"
            or remark == f"mlffbench:{token}:worker"
            or name.startswith(f"mlffbench-{run_id}")
            or remark.startswith(f"mlffbench:{run_id}")
            or name.startswith(f"mlffbench-{token}")
            or remark.startswith(f"mlffbench:run:{token}")
            or remark.startswith(f"mlffbench:{token}")
        )

    return (
        name.startswith("mlffbench-")
        or remark.startswith("mlffbench:run:")
        or remark.startswith("mlffbench:")
    )


def extract_verified_instance_id(instance: Mapping[str, Any]) -> str:
    """Extract and validate instance ID from a cloud instance record.

    Fails closed if the instance matches the MLFFBench ownership marker but
    lacks a valid non-empty instance ID.
    """
    inst_id = str(instance.get("instance_id") or instance.get("id") or "").strip()
    if not inst_id:
        if matches_ownership_marker(instance):
            raise ValueError(
                f"Cloud instance matched MLFFBench ownership marker but has empty instance_id: {instance}"
            )
        return ""
    return inst_id
