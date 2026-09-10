#!/usr/bin/env python3
"""Fast, case-independent runtime qualification for the CPU DPMP image."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import platform

EXPECTED = {
    "ase": "3.29.0",
    "deepmd-jax": "0.2",
    "flax": "0.10.6",
    "h5py": "3.16.0",
    "jax": "0.5.3",
    "jax-md": "0.2.29",
    "jaxlib": "0.5.3",
    "optax": "0.2.8",
}


def main() -> int:
    errors: list[str] = []
    versions: dict[str, str] = {}
    for distribution, expected in EXPECTED.items():
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "missing"
        if versions[distribution] != expected:
            errors.append(f"{distribution}={versions[distribution]!r}, expected {expected!r}")

    for module in ("ase", "flax", "h5py", "jax", "jax_md", "optax", "deepmd_jax"):
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors.append(f"import {module} failed: {exc}")

    devices: list[str] = []
    x64_enabled = False
    try:
        import jax
        from deepmd_jax.md import Simulation  # noqa: F401
        from deepmd_jax.train import evaluate  # noqa: F401

        devices = [str(device) for device in jax.devices()]
        if not devices or any(device.platform != "cpu" for device in jax.devices()):
            errors.append(f"CPU-only device contract failed: {devices}")
        x64_enabled = bool(jax.config.jax_enable_x64)
        if not x64_enabled:
            errors.append("JAX x64 is disabled")
    except Exception as exc:
        errors.append(f"DPMP API probe failed: {exc}")

    report = {
        "schema_version": "bench-deepmd-jax-smoke/v1",
        "status": "PASS" if not errors else "FAIL",
        "python": platform.python_version(),
        "versions": versions,
        "devices": devices,
        "x64_enabled": x64_enabled,
        "apis": ["deepmd_jax.train.evaluate", "deepmd_jax.md.Simulation"],
        "errors": errors,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
