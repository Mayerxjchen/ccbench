"""Inspect and validate an external case suite without copying it.

This module deliberately treats ``case.toml`` as the only source of case
identity.  A directory name is used only to discover likely case directories
and to report a diagnostic when its numeric prefix disagrees with the manifest.
"""

from __future__ import annotations

import re
import hashlib
import os
import stat
import tomllib
from pathlib import Path
from typing import Any

from bench.contracts.case import CaseContractError, CaseSpec, load_coverage_vocabularies

_CASE_DIR_RE = re.compile(r"^\d{3}(?:[-_ ]|$)")
_IGNORED_NAMES = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache"}


def _discover(root: Path) -> tuple[Path, list[Path], dict[str, Any], str | None]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"suite root is not a directory: {root}")
    cases_root = root
    suite_doc: dict[str, Any] = {}
    suite_digest: str | None = None
    suite_manifest = root / "suite.toml"
    try:
        suite_info = os.lstat(suite_manifest)
    except FileNotFoundError:
        suite_info = None
    if suite_info is not None and (
        stat.S_ISLNK(suite_info.st_mode)
        or not stat.S_ISREG(suite_info.st_mode)
        or suite_info.st_nlink != 1
    ):
        raise ValueError("suite.toml must be a non-symlink, single-link regular file")
    if suite_info is not None:
        try:
            suite_bytes = suite_manifest.read_bytes()
            suite_doc = tomllib.loads(suite_bytes.decode("utf-8"))
            suite_digest = "sha256:" + hashlib.sha256(suite_bytes).hexdigest()
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"invalid suite.toml: {exc}") from exc
        configured_root = suite_doc.get("cases_root", ".") if isinstance(suite_doc, dict) else "."
        if not isinstance(configured_root, str) or not configured_root:
            raise ValueError("suite.toml cases_root must be a non-empty relative path")
        configured_path = root / configured_root
        if configured_path.is_symlink():
            raise ValueError("suite.toml cases_root must not be a symlink")
        cases_root = configured_path.resolve()
        if root not in cases_root.parents and cases_root != root:
            raise ValueError("suite.toml cases_root escapes suite root")
        if not cases_root.is_dir():
            raise ValueError(f"suite cases_root is not a directory: {configured_root}")
    found: list[Path] = []
    for child in sorted(cases_root.iterdir(), key=lambda p: p.name):
        if child.name in _IGNORED_NAMES or child.name.startswith("."):
            continue
        if child.is_symlink():
            # Include a would-be case so inspect/validate reports the unsafe
            # topology instead of silently following it or hiding it.
            if _CASE_DIR_RE.match(child.name) or (child / "case.toml").exists():
                found.append(child)
            continue
        if not child.is_dir():
            continue
        if (child / "case.toml").is_file() or _CASE_DIR_RE.match(child.name):
            found.append(child)
    return cases_root, found, suite_doc, suite_digest


def _numeric_prefix(name: str) -> str | None:
    match = re.match(r"^(\d{3})(?:[-_ ]|$)", name)
    return match.group(1) if match else None


def _manifest_identity(raw: dict[str, Any]) -> str | None:
    task = raw.get("task")
    if isinstance(task, dict) and isinstance(task.get("name"), str):
        return task["name"]
    case = raw.get("case")
    if isinstance(case, dict) and isinstance(case.get("name"), str):
        return case["name"]
    if isinstance(raw.get("case_id"), str):
        return raw["case_id"]
    return None


def _public_safety_diagnostics(case_dir: Path, spec: CaseSpec) -> list[dict[str, Any]]:
    """Check the manifest's public sources without following symlinks."""
    root = case_dir.resolve()
    diagnostics: list[dict[str, Any]] = []
    candidates: list[Path] = [case_dir / spec.instruction_path]
    for rule in spec.public_files:
        # Check literal directory prefixes before glob expansion; pathlib's
        # glob can otherwise hide a symlinked directory with no matches.
        prefix = case_dir
        for part in rule.source.split("/"):
            if any(ch in part for ch in "*?["):
                break
            prefix = prefix / part
            try:
                if stat.S_ISLNK(os.lstat(prefix).st_mode):
                    candidates.append(prefix)
                    break
            except OSError:
                break
        matches = case_dir.glob(rule.source) if any(ch in rule.source for ch in "*?[") else [case_dir / rule.source]
        candidates.extend(matches)
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            info = os.lstat(path)
        except OSError:
            continue
        rel = path.relative_to(case_dir).as_posix()
        if stat.S_ISLNK(info.st_mode):
            diagnostics.append({"code": "PUBLIC_SYMLINK", "severity": "error", "path": rel})
            continue
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            diagnostics.append({"code": "PUBLIC_PATH_ESCAPE", "severity": "error", "path": rel})
    return diagnostics


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _public_digest(case_dir: Path, spec: CaseSpec) -> str:
    """Digest the case manifest and exactly the bytes in its public allowlist."""
    files: list[dict[str, Any]] = []
    sources: list[Path] = [case_dir / spec.instruction_path]
    for rule in spec.public_files:
        matches = case_dir.glob(rule.source) if any(ch in rule.source for ch in "*?[") else [case_dir / rule.source]
        sources.extend(match for match in matches if match.is_file())
    unique = sorted({path for path in sources}, key=lambda path: path.relative_to(case_dir).as_posix())
    for path in unique:
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"public source is not a safe regular file: {path.relative_to(case_dir)}")
        data = path.read_bytes()
        files.append({
            "path": path.relative_to(case_dir).as_posix(),
            "size": len(data),
            "sha256": _sha256_bytes(data),
        })
    manifest = case_dir / "case.toml"
    manifest_digest = _sha256_bytes(manifest.read_bytes())
    payload = {"case_manifest_digest": manifest_digest, "files": files}
    return _sha256_bytes(
        json_bytes(payload)
    )


def json_bytes(value: Any) -> bytes:
    # Kept local to this module to make the digest independent of formatting
    # or platform path conventions.
    import json
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _row(case_dir: Path) -> dict[str, Any]:
    manifest = case_dir / "case.toml"
    row: dict[str, Any] = {
        "directory": case_dir.name,
        "manifest": "case.toml" if manifest.is_file() else None,
        "case_id": None,
        "case_version": None,
        "case_manifest_digest": None,
        "public_digest": None,
        "execution_class": None,
        "difficulty": None,
        "compute_route": None,
        "runtime_status": None,
        "readiness": "CASE_NOT_READY",
        "diagnostics": [],
    }
    if case_dir.is_symlink():
        row["diagnostics"] = [{"code": "SYMLINK_CASE_DIRECTORY", "severity": "error"}]
        return row
    try:
        manifest_info = os.lstat(manifest)
    except FileNotFoundError:
        manifest_info = None
    if manifest_info is not None and stat.S_ISLNK(manifest_info.st_mode):
        row["diagnostics"] = [{"code": "CASE_MANIFEST_SYMLINK", "severity": "error"}]
        return row
    if manifest_info is not None and (
        not stat.S_ISREG(manifest_info.st_mode) or manifest_info.st_nlink != 1
    ):
        row["diagnostics"] = [{"code": "CASE_MANIFEST_UNSAFE", "severity": "error"}]
        return row
    if manifest_info is None:
        row["diagnostics"] = [{"code": "MISSING_CASE_TOML", "severity": "error"}]
        return row
    try:
        row["case_manifest_digest"] = _sha256_bytes(manifest.read_bytes())
    except OSError:
        row["diagnostics"] = [{"code": "UNREADABLE_CASE_TOML", "severity": "error"}]
        return row
    try:
        raw = tomllib.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("case.toml must contain a table")
        manifest_id = _manifest_identity(raw)
        # Load through the canonical contract parser; never synthesize an ID
        # from the external directory name.
        # External suites opt into the active canonical contract explicitly;
        # never fall back to the permissive legacy parser just because a
        # directory happens to be outside the repository.
        spec = CaseSpec.load(case_dir, strict_external=True)
        row.update({
            "case_id": spec.case_id,
            "case_version": spec.case_version,
            "execution_class": spec.execution_class,
            "difficulty": raw.get("difficulty"),
        })
        compute = raw.get("compute")
        if isinstance(compute, dict):
            classes = compute.get("classes")
            row["compute_route"] = classes if isinstance(classes, list) else classes
        elif spec.execution_class == "local_sandbox":
            row["compute_route"] = ["local"]
        runtime = raw.get("runtime")
        if isinstance(runtime, dict) and isinstance(runtime.get("status"), str):
            row["runtime_status"] = runtime["status"]
            normalized_status = runtime["status"].strip().lower().replace("-", "_")
            if any(token in normalized_status for token in ("unbuilt", "unqualified", "not_qualified")):
                row["diagnostics"].append({
                    "code": "RUNTIME_NOT_READY",
                    "severity": "error",
                    "status": runtime["status"],
                })
        prefix = _numeric_prefix(case_dir.name)
        id_prefix = _numeric_prefix(manifest_id.rsplit("/", 1)[-1]) if manifest_id else None
        if prefix and id_prefix and prefix != id_prefix:
            row["diagnostics"].append({
                "code": "CASE_ID_DIRECTORY_MISMATCH",
                "severity": "error",
                "directory_prefix": prefix,
                "manifest_id": manifest_id,
            })
        vocab = load_coverage_vocabularies()
        coverage = raw.get("coverage")
        if isinstance(coverage, dict):
            for dimension in ("method_family", "material_class", "computation_type"):
                value = coverage.get(dimension)
                if isinstance(value, str) and value and value not in vocab.get(dimension, set()):
                    row["diagnostics"].append({
                        "code": "COVERAGE_EXTENSION",
                        "severity": "warning",
                        "dimension": dimension,
                        "value": value,
                    })
        instruction = case_dir / spec.instruction_path
        if not instruction.is_file() or instruction.is_symlink():
            row["diagnostics"].append({
                "code": "MISSING_INSTRUCTION",
                "severity": "error",
                "path": spec.instruction_path,
            })
        row["diagnostics"].extend(_public_safety_diagnostics(case_dir, spec))
        if not any(item.get("severity") == "error" for item in row["diagnostics"]):
            row["public_digest"] = _public_digest(case_dir, spec)
        if raw.get("readiness") == "CASE_NOT_READY":
            row["readiness"] = "CASE_NOT_READY"
            row["diagnostics"].append({"code": "EXPLICIT_NOT_READY", "severity": "error"})
        elif "draft" in spec.case_version.lower():
            row["readiness"] = "DRAFT_NOT_READY"
            row["diagnostics"].append({"code": "DRAFT_CASE", "severity": "warning"})
        elif any(item.get("severity") == "error" for item in row["diagnostics"]):
            row["readiness"] = "CASE_NEEDS_REVIEW"
        else:
            row["readiness"] = "READY"
    except (OSError, ValueError, CaseContractError, tomllib.TOMLDecodeError) as exc:
        row["diagnostics"] = [{"code": "INVALID_CASE_TOML", "severity": "error", "message": str(exc)}]
    return row


def inspect_suite(root: str | Path) -> dict[str, Any]:
    """Return a stable, JSON-serializable readiness report for ``root``."""
    resolved = Path(root).expanduser().resolve()
    cases_root, discovered, suite_doc, suite_digest = _discover(resolved)
    rows = [_row(path) for path in discovered]
    rows.sort(key=lambda row: row["directory"])
    import json
    bindings = [
        {
            "directory": row["directory"],
            "case_manifest_digest": row["case_manifest_digest"],
            "public_digest": row["public_digest"],
        }
        for row in rows
    ]
    manifest_digest = _sha256_bytes(
        json.dumps(
            {"suite_manifest_digest": suite_digest, "cases": bindings},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return {
        "schema_version": "bench-suite/v1",
        "root": str(resolved),
        "cases_root": str(cases_root.relative_to(resolved) or "."),
        "suite_id": suite_doc.get("suite_id") or suite_doc.get("name") or resolved.name,
        "paper_id": suite_doc.get("paper_id"),
        "suite_manifest_digest": suite_digest,
        "manifest_digest": manifest_digest,
        "case_count": len(rows),
        "cases": rows,
        "ready": bool(rows) and all(row["readiness"] == "READY" for row in rows),
    }


def validate_suite(root: str | Path) -> dict[str, Any]:
    """Inspect a suite and mark whether every discovered case is publish-ready."""
    report = inspect_suite(root)
    report["validation"] = "PASS" if report["ready"] else "FAIL"
    return report
