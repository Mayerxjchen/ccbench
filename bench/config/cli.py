"""Secret-free inspection commands for DFTWorld Run Config."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bench.config.run_config import EXECUTION_CLASSES, RunConfigError, load_run_config


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "experiments/main.toml"


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True))


def _resolved(path: Path, execution_class: str) -> dict[str, Any]:
    config = load_run_config(path)
    return {
        "run_config_id": config.run_config_id,
        "run_config_digest": config.digest,
        "mode": config.mode,
        "model": config.model.model_dump(mode="json"),
        "api": config.api.model_dump(mode="json"),
        "execution_class": execution_class,
        "budget": config.budget_for(execution_class).model_dump(mode="json"),
        "treatments": config.treatments.keys(),
    }


def _doctor(path: Path) -> tuple[int, dict[str, Any]]:
    try:
        config = load_run_config(path)
    except RunConfigError as exc:
        return 2, {"valid": False, "errors": [str(exc)]}
    errors: list[str] = []
    for env_name in (config.api.endpoint_env, config.api.credential_env):
        if not os.environ.get(env_name):
            errors.append(f"required environment variable is not set: {env_name}")
    endpoint = os.environ.get(config.api.endpoint_env)
    if endpoint:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"endpoint environment variable is not a valid HTTP URL: {config.api.endpoint_env}")
    return (0 if not errors else 2), {
        "valid": not errors,
        "run_config_id": config.run_config_id,
        "run_config_digest": config.digest,
        "checked_env_names": [config.api.endpoint_env, config.api.credential_env],
        "errors": errors,
    }


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    out: dict[str, Any] = {}
    for key in sorted(value):
        path = f"{prefix}.{key}" if prefix else key
        out.update(_flatten(value[key], path))
    return out


def _diff(left: Path, right: Path) -> list[dict[str, Any]]:
    a = _flatten(load_run_config(left).model_dump(mode="json"))
    b = _flatten(load_run_config(right).model_dump(mode="json"))
    missing = object()
    rows: list[dict[str, Any]] = []
    for path in sorted(set(a) | set(b)):
        av, bv = a.get(path, missing), b.get(path, missing)
        if av != bv:
            rows.append({
                "path": path,
                "left": None if av is missing else av,
                "right": None if bv is missing else bv,
            })
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dftworld-config")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("resolve", "doctor"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--run-config", type=Path, default=DEFAULT_CONFIG)
        if name == "resolve":
            cmd.add_argument(
                "--execution-class", choices=sorted(EXECUTION_CLASSES), default="local_sandbox"
            )
    diff = sub.add_parser("diff")
    diff.add_argument("left", type=Path)
    diff.add_argument("right", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "resolve":
            payload = _resolved(args.run_config, args.execution_class)
            payload["treatments"] = sorted(payload["treatments"])
            _emit(payload)
            return 0
        if args.command == "doctor":
            code, payload = _doctor(args.run_config)
            _emit(payload)
            return code
        differences = _diff(args.left, args.right)
        _emit({"equal": not differences, "differences": differences})
        return 0 if not differences else 1
    except RunConfigError as exc:
        _emit({"valid": False, "errors": [str(exc)]})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
