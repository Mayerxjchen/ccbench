"""Unified single-source policy for CompShare instance ownership and zero-orphan states.

Invariants:
- SAFE_DELETED_STATES is strictly {"deleted", "terminated"}. All other states
  (including "stopped", "stopping", "starting", "running", "unknown", "failed")
  require active cleanup and fail zero-orphan gates.
- New ownership markers are always derived from a fixed-width SHA-256 owner
  token:
  name: mlffbench-{token}
  remark: mlffbench:run:{token}
- Empty instance ID on matched managed instance fails closed.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

SAFE_DELETED_STATES: frozenset[str] = frozenset({"deleted", "terminated"})


def instance_requires_cleanup(status: str | None) -> bool:
    """Return True if an instance status indicates active, stopped, or uncleared billing resources."""
    if not status:
        return True
    return status.strip().lower() not in SAFE_DELETED_STATES


_OWNER_TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")
_NEW_NAME_RE = re.compile(r"^mlffbench-[0-9a-f]{16}$")
_NEW_REMARK_RE = re.compile(r"^mlffbench:run:[0-9a-f]{16}$")
# Legacy forms are retained solely so a global recovery sweep can find and
# clean resources created before the fixed-token contract was deployed.
_LEGACY_NAME_RE = re.compile(r"^mlffbench-[A-Za-z0-9_-]{1,32}(?:-worker)?$")
_LEGACY_REMARK_RE = re.compile(r"^mlffbench:[A-Za-z0-9_-]{1,32}:worker$")


def make_ownership_marker(run_id: str) -> tuple[str, str]:
    """Derive the fixed-width marker pair used for every newly created run."""
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty string")
    token = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
    return f"mlffbench-{token}", f"mlffbench:run:{token}"


def matches_ownership_marker(
    instance: Mapping[str, Any],
    run_id: str | None = None,
) -> bool:
    """Check exact ownership markers.

    With ``run_id`` this is a strict match against the current fixed-token
    form.  Without a run it is the recovery/cleanup matcher and additionally
    recognizes exact legacy marker shapes so old resources can be found, but
    never recreated or treated as current ownership.
    """
    name = str(instance.get("name") or "")
    remark = str(instance.get("remark") or "")

    if run_id is not None:
        token = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:16]
        exp_name, exp_remark = make_ownership_marker(run_id)
        return name == exp_name or remark == exp_remark

    return bool(
        _NEW_NAME_RE.fullmatch(name)
        or _NEW_REMARK_RE.fullmatch(remark)
        or _LEGACY_NAME_RE.fullmatch(name)
        or _LEGACY_REMARK_RE.fullmatch(remark)
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
    if len(inst_id) > 128 or any(ord(ch) < 0x20 or ch.isspace() for ch in inst_id):
        raise ValueError(f"Cloud instance has malformed instance_id: {inst_id!r}")
    return inst_id
