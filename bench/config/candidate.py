"""Small, secret-free Candidate run configuration loader.

The file is intentionally a thin TOML convenience layer over the existing
agent profile.  It contains environment variable *names*, never credentials,
and does not create a second runtime/profile registry.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tomllib
from pathlib import Path
from typing import Any


_SECRET_KEY = re.compile(r"(?:secret|password|token|credential|api[_-]?key)", re.I)
_BACKENDS = {"local", "ikkem", "compshare"}
_LIMIT_KEYS = {"max_turns", "max_total_tokens", "agent_timeout_sec", "max_budget_usd"}


class CandidateConfigError(ValueError):
    """Candidate config is malformed or attempts to contain a secret."""


def candidate_config_digest(config: dict[str, Any]) -> str:
    """Digest normalized, secret-free config content (excluding host metadata)."""
    payload = {key: value for key, value in config.items() if key not in {"_path", "digest"}}
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_candidate_config(path: str | Path) -> dict[str, Any]:
    """Load and validate the minimal user TOML config.

    CLI/env values still have precedence in the pilot.  This loader only
    normalizes the optional config values and fails closed on secret-shaped
    keys or values.
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise CandidateConfigError(f"candidate config not found: {p}")
    try:
        raw = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CandidateConfigError(f"invalid candidate config {p}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CandidateConfigError("candidate config must be a TOML table")
    _reject_secrets(raw)

    allowed_top = {"type", "mode", "trust_mode", "agent", "model", "candidate", "environment", "compute", "limits"}
    unknown_top = sorted(set(raw) - allowed_top)
    if unknown_top:
        raise CandidateConfigError(f"unknown candidate config field(s): {', '.join(unknown_top)}")

    agent = raw.get("agent") or {}
    model = raw.get("model") or {}
    candidate = raw.get("candidate") or {}
    environment = raw.get("environment") or {}
    compute = raw.get("compute") or {}
    limits = raw.get("limits") or {}
    if not all(isinstance(section, dict) for section in (agent, model, candidate, environment, compute, limits)):
        raise CandidateConfigError("[agent], [model], [candidate], [environment], [compute], and [limits] must be tables")

    unknown_agent = sorted(set(agent) - {"type", "command", "model", "mode"})
    unknown_model = sorted(set(model) - {"requested_id", "cli_model", "endpoint_env", "credential_env"})
    unknown_candidate = sorted(set(candidate) - {"image", "sidecar_image"})
    unknown_environment = sorted(set(environment) - {"image"})
    unknown_compute = sorted(set(compute) - {"backend", "profile"})
    unknown_limits = sorted(set(limits) - _LIMIT_KEYS)
    if unknown_agent or unknown_model or unknown_candidate or unknown_environment or unknown_compute or unknown_limits:
        names = [f"agent.{x}" for x in unknown_agent]
        names += [f"model.{x}" for x in unknown_model]
        names += [f"candidate.{x}" for x in unknown_candidate]
        names += [f"environment.{x}" for x in unknown_environment]
        names += [f"compute.{x}" for x in unknown_compute]
        names += [f"limits.{x}" for x in unknown_limits]
        raise CandidateConfigError(f"unknown candidate config field(s): {', '.join(names)}")

    out: dict[str, Any] = {"_path": str(p)}
    if "type" in raw and raw["type"] != "claude-code":
        raise CandidateConfigError("type must be 'claude-code'")
    if "mode" in raw:
        if raw["mode"] != "container":
            raise CandidateConfigError("mode must be 'container' (Candidate Docker)")
        out["mode"] = raw["mode"]
    out["type"] = "claude-code"
    trust_mode = raw.get("trust_mode", "local")
    # FORMAL is an admission decision made by a trusted coordinator, never a
    # user-editable TOML switch.  Keep the hyphenated spelling for existing
    # callers and accept the readable underscore alias.
    if trust_mode not in {"local", "untrusted", "external-verifier", "external_submission"}:
        raise CandidateConfigError(
            "trust_mode must be local, untrusted, external-verifier, or external_submission; "
            "formal admission is coordinator-only"
        )
    if "trust_mode" in raw:
        out["trust_mode"] = trust_mode
    if agent:
        agent_type = agent.get("type", "claude-code")
        if agent_type != "claude-code":
            raise CandidateConfigError("agent.type must be 'claude-code'")
        if "command" in agent:
            command = agent["command"]
            if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
                raise CandidateConfigError("agent.command must be a non-empty argv list")
            out["agent"] = {"type": agent_type, "command": list(command)}
        else:
            out["agent"] = {"type": agent_type}
        if "mode" in agent:
            if agent["mode"] != "container":
                raise CandidateConfigError("agent.mode must be 'container' (Candidate Docker)")
            out["mode"] = agent["mode"]
        if "model" in agent and "requested_id" not in model:
            model = {**model, "requested_id": agent["model"]}
    if environment.get("image") is not None:
        if not isinstance(environment["image"], str) or not environment["image"].strip():
            raise CandidateConfigError("environment.image must be a non-empty string")
        out["environment"] = {"image": environment["image"]}
    for key in ("requested_id", "cli_model", "endpoint_env", "credential_env"):
        if key in model:
            value = model[key]
            if not isinstance(value, str) or not value.strip():
                raise CandidateConfigError(f"model.{key} must be a non-empty string")
            if key.endswith("_env") and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise CandidateConfigError(f"model.{key} must be an environment variable name")
            out.setdefault("model", {})[key] = value
    for key in ("image", "sidecar_image"):
        if key in candidate:
            value = candidate[key]
            if not isinstance(value, str) or not value.strip():
                raise CandidateConfigError(f"candidate.{key} must be a non-empty string")
            out.setdefault("candidate", {})[key] = value
    if limits:
        normalized_limits: dict[str, int | float] = {}
        for key, value in limits.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise CandidateConfigError(f"limits.{key} must be a finite number")
            if not math.isfinite(float(value)):
                raise CandidateConfigError(f"limits.{key} must be finite")
            if key in {"max_turns", "max_total_tokens"}:
                if not isinstance(value, int) or value < 1:
                    raise CandidateConfigError(f"limits.{key} must be a positive integer")
                normalized_limits[key] = value
            elif key == "agent_timeout_sec":
                if value <= 0:
                    raise CandidateConfigError("limits.agent_timeout_sec must be positive")
                normalized_limits[key] = float(value)
            elif key == "max_budget_usd":
                if value < 0:
                    raise CandidateConfigError("limits.max_budget_usd must be non-negative")
                normalized_limits[key] = float(value)
        out["limits"] = normalized_limits
    if "backend" in compute:
        backend = compute["backend"]
        if backend not in _BACKENDS:
            raise CandidateConfigError(f"compute.backend must be one of {sorted(_BACKENDS)}")
        out.setdefault("compute", {})["backend"] = backend
    if "profile" in compute:
        profile = compute["profile"]
        if not isinstance(profile, str) or not profile.strip():
            raise CandidateConfigError("compute.profile must be a non-empty path")
        out.setdefault("compute", {})["profile"] = profile
    # The receipt identifies the configuration content, not its host path.
    # This keeps the same config reproducible after moving a run directory.
    out["digest"] = candidate_config_digest(out)
    return out


def _reject_secrets(value: Any, path: str = "") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if (_SECRET_KEY.search(str(key)) and str(key) not in {"credential_env", "endpoint_env"}
                    and not (path == "" and str(key) == "limits")
                    and not (path == "limits" and str(key) in _LIMIT_KEYS)):
                raise CandidateConfigError(
                    f"candidate config must not contain secret material: {key_path}"
                )
            _reject_secrets(child, key_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}[{index}]")
