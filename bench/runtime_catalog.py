"""Small, read-only runtime catalog for the public ``bench`` UX.

The catalog is deliberately descriptive rather than a second lock system.  A
case names a capability/runtime family; an operator resolves that family to a
local image or a site profile and then performs the existing qualification
checks.  Empty digests and ``UNQUALIFIED`` states are intentional and are
never promoted by this module.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


_DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "infra" / "config" / "runtime-catalog.toml"


def _catalog_path(path: str | Path | None) -> Path:
    result = Path(path).expanduser().resolve() if path is not None else _DEFAULT_CATALOG
    if not result.is_file():
        raise ValueError(f"runtime catalog not found: {result}")
    return result


def load_catalog(path: str | Path | None = None) -> dict[str, Any]:
    """Load and minimally validate the operator-facing catalog."""
    catalog_path = _catalog_path(path)
    try:
        document = tomllib.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"invalid runtime catalog {catalog_path}: {exc}") from exc
    entries = document.get("runtimes")
    if not isinstance(entries, list):
        raise ValueError("runtime catalog must contain [[runtimes]] entries")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = {"runtime_id", "capabilities", "version", "qualification_status"}
    for entry in entries:
        if not isinstance(entry, dict) or not required.issubset(entry):
            raise ValueError("each runtime catalog entry requires runtime_id, capabilities, version, qualification_status")
        runtime_id = entry["runtime_id"]
        capabilities = entry["capabilities"]
        if not isinstance(runtime_id, str) or not runtime_id or runtime_id in seen:
            raise ValueError(f"invalid or duplicate runtime_id: {runtime_id!r}")
        if not isinstance(capabilities, list) or not capabilities or not all(
            isinstance(value, str) and value for value in capabilities
        ):
            raise ValueError(f"runtime {runtime_id!r} has invalid capabilities")
        seen.add(runtime_id)
        normalized.append(dict(entry))
    normalized.sort(key=lambda entry: entry["runtime_id"])
    return {
        "schema_version": document.get("schema_version", "bench-runtime-catalog/v1"),
        "catalog": document.get("catalog", "default"),
        "source": str(catalog_path),
        "runtimes": normalized,
    }


def inspect_runtime(runtime_id: str, path: str | Path | None = None) -> dict[str, Any]:
    catalog = load_catalog(path)
    for entry in catalog["runtimes"]:
        if entry["runtime_id"] == runtime_id:
            return {**catalog, "runtime": entry}
    raise ValueError(f"runtime not found: {runtime_id}")
