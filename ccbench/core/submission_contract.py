"""Structural submission contract validation.

A pre-Verifier gate that checks the candidate's submission shape WITHOUT
executing anything: normalized relative paths, regular-file/directory type,
per-file size, and JSON schema where the case explicitly declares one.

This module never imports Python modules from the submission, never unpickles,
and never judges numeric science.  Unsafe filesystem nodes (symlinks, sockets,
absolute or parent-traversing paths) are still rejected by source collection
(``ccbench.core.quarantine``); structural validation runs on the
already-collected raw tree.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

# Paths are normalized to POSIX, relative, no "..", no leading "/" or drive.
_PATH_RE = re.compile(r"^[^/\0][^/\0]*(?:/[^/\0]+)*$")

# A soft cap so a pathological single file cannot evade quota checks.
_MAX_SINGLE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB


@dataclass(frozen=True)
class StructuralError:
    """One structural submission defect, classified with a stable code."""

    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "path": self.path, "message": self.message}


def _normalize(entry_path: str) -> str | None:
    """Normalize a contract path; return None if it is unsafe."""
    # Absolute paths (posix or drive-lettered windows) are never allowed.
    posix = entry_path.replace("\\", "/")
    if posix.startswith("/") or len(posix) >= 2 and posix[1] == ":":
        return None
    cleaned = posix.lstrip("/")
    if not cleaned or cleaned in (".", "..") or cleaned.startswith("../"):
        return None
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if ".." in parts:
        return None
    return "/".join(parts)


def validate_submission(
    root: Path,
    contract: dict[str, Any],
) -> list[StructuralError]:
    """Validate a submission tree against a structural contract.

    ``contract`` shape (mirrored by case.schema.json):

    .. code-block:: json

        {
          "required": [
            {"path": "model.pb", "type": "file"},
            {"path": "out", "type": "directory"}
          ],
          "optional": [
            {"path": "README.md", "type": "file"}
          ],
          "files": {
            "max_single_bytes": 1048576,
            "max_total_bytes": 1073741824
          }
        }

    Returns a list of StructuralError; an empty list means the submission
    passes the structural gate.  Errors are classified with stable codes
    so the harness can map them to a failure taxonomy.
    """
    errors: list[StructuralError] = []
    required = contract.get("required") or []
    optional = contract.get("optional") or []
    file_limits = contract.get("files") or {}

    max_single = int(file_limits.get("max_single_bytes", _MAX_SINGLE_BYTES))
    max_total = int(file_limits.get("max_total_bytes", 0))

    # -- required / optional path shape checks --------------------------------
    for spec in [*required, *optional]:
        raw_path = spec.get("path")
        expected_type = spec.get("type", "file")
        normalized = _normalize(str(raw_path)) if isinstance(raw_path, str) else None
        if normalized is None:
            errors.append(StructuralError(
                "UNSAFE_CONTRACT_PATH",
                str(raw_path),
                f"contract path is not a safe relative path: {raw_path!r}",
            ))
            continue
        target = (root / normalized).resolve(strict=False)
        try:
            target.relative_to(root.resolve())
        except ValueError:
            errors.append(StructuralError(
                "PATH_ESCAPES_ROOT",
                normalized,
                f"path escapes the submission root: {normalized!r}",
            ))
            continue

        if spec in required:
            if not target.exists():
                errors.append(StructuralError(
                    "MISSING_REQUIRED_PATH",
                    normalized,
                    f"required {expected_type} missing: {normalized!r}",
                ))
                continue
        elif not target.exists():
            continue  # optional paths may be absent

        if expected_type == "file" and target.is_dir():
            errors.append(StructuralError(
                "TYPE_MISMATCH",
                normalized,
                f"expected a file but found a directory: {normalized!r}",
            ))
        elif expected_type == "directory" and not target.is_dir():
            errors.append(StructuralError(
                "TYPE_MISMATCH",
                normalized,
                f"expected a directory but found a file: {normalized!r}",
            ))

    # -- non-empty gate: the candidate must have produced at least one artifact
    if contract.get("non_empty") and not any(root.iterdir()):
        errors.append(StructuralError(
            "EMPTY_SUBMISSION",
            ".",
            "submission contains no files or directories",
        ))

    # -- JSON schema checks for declared entries ------------------------------
    for schema_entry in contract.get("json_schema") or []:
        schema_path = _normalize(str(schema_entry.get("path", "")))
        schema_def = schema_entry.get("schema")
        if schema_path is None or schema_def is None:
            continue
        target = root / schema_path
        if not target.is_file():
            continue
        try:
            instance = json.loads(target.read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator(schema_def).validate(instance)
        except json.JSONDecodeError as exc:
            errors.append(StructuralError(
                "JSON_PARSE_ERROR",
                schema_path,
                f"declared JSON file is not parseable: {exc}",
            ))
        except jsonschema.ValidationError as exc:
            errors.append(StructuralError(
                "JSON_SCHEMA_VIOLATION",
                schema_path,
                f"declared JSON violates schema at {list(exc.path)!r}: {exc.message}",
            ))

    # -- size accounting -------------------------------------------------------
    if max_total or max_single < _MAX_SINGLE_BYTES:
        _validate_sizes(root, errors, max_single, max_total)

    return errors


def _validate_sizes(
    root: Path,
    errors: list[StructuralError],
    max_single: int,
    max_total: int,
) -> None:
    """Walk the tree and enforce per-file and aggregate byte limits."""
    total = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > max_single:
            rel = path.relative_to(root)
            errors.append(StructuralError(
                "FILE_SIZE_EXCEEDED",
                str(rel),
                f"file {rel} is {size} bytes; limit is {max_single}",
            ))
        total += size
    if max_total and total > max_total:
        errors.append(StructuralError(
            "TOTAL_SIZE_EXCEEDED",
            ".",
            f"aggregate submission is {total} bytes; limit is {max_total}",
        ))
