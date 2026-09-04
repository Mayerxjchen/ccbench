"""Pure contracts for Gate A2 CompShare bootstrap evidence.

This module deliberately has no provider client, subprocess runner, config
loader, or environment access.  It only constructs and validates the small
evidence envelope used by the later capture command.  In particular,
``credential_profile_id`` is an opaque *index* recorded in the manifest; it
is never turned into a CLI option or a credential lookup.

The capture boundary is split into two kinds of bytes:

* ``evidence_files`` identify raw provider-response artifacts by relative
  path, size, and SHA-256; and
* each probe carries a normalized ``summary``.  Summaries are intentionally
  unable to carry arbitrary stdout/stderr or secret-looking values.

Older A2 manifests are readable for diagnosis only.  They classify as
``INCOMPLETE_NOT_REPLAYABLE`` and are never rewritten or upgraded in place.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
SCHEMA_ID = (
    "https://mlip-bench.example/schemas/"
    "compshare-bootstrap-evidence.schema.json"
)
KIND = "compshare-bootstrap-evidence/v1"
EXPECTED_CLI_VERSION = "0.4.1"

# Public aliases make the pinned version and status vocabulary discoverable to
# callers without requiring them to duplicate policy strings.
PINNED_COMPSHARE_CLI_VERSION = EXPECTED_CLI_VERSION
EXPECTED_COMPSHARE_CLI_VERSION = EXPECTED_CLI_VERSION
INCOMPLETE_NOT_REPLAYABLE = "INCOMPLETE_NOT_REPLAYABLE"
REPLAYABLE = "REPLAYABLE"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")
_VERSION_RE = re.compile(
    r"^(?:compshare(?:-cli)?\s+)?v?(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$"
)
_SECRET_RE = re.compile(
    r"(?i)(?:secret|private[ _-]?key|api[ _-]?key|password|"
    r"bearer\s|token\s*=|credential\s*=|secret_canary|secret-canary)"
)
_FORBIDDEN_SUMMARY_KEY_RE = re.compile(
    r"(?i)^(?:stdout|stderr|secret|password|token|credential|private[_-]?key|"
    r"api[_-]?key)$"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

DOCTOR_ALLOWLIST = frozenset(
    {"profile", "auth_ok", "api_endpoint", "account_id"}
)

# These are the only command shapes the A2 collector may ever emit.  The
# inventory and price probes are included because the original A2 bootstrap
# notes require them even though the minimal Sol text listed version/doctor.
PROBE_NAMES = (
    "version",
    "doctor",
    "instance_list",
    "zones",
    "families",
    "price_4090",
    "price_5090",
    "search_sh2",
    "search_wlcb",
)


class BootstrapEvidenceError(ValueError):
    """A bootstrap evidence value is unsafe, malformed, or incomplete."""


@dataclass(frozen=True)
class BootstrapProbeSpec:
    """One exact, allowlisted argv shape for a bootstrap probe."""

    name: str
    argv: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "argv": list(self.argv)}


@dataclass(frozen=True)
class EvidenceFile:
    """A relative raw artifact bound by size and SHA-256."""

    path: str
    size_bytes: int
    sha256: str
    role: str = "raw_provider_response"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "role": self.role,
        }


def _is_secret_text(value: str) -> bool:
    return bool(_SECRET_RE.search(value))


def _safe_text(value: Any, *, field: str, max_length: int = 4096) -> str:
    if not isinstance(value, str) or not value:
        raise BootstrapEvidenceError(f"{field} must be a non-empty string")
    if len(value) > max_length:
        raise BootstrapEvidenceError(f"{field} is too long")
    if _CONTROL_RE.search(value):
        raise BootstrapEvidenceError(f"{field} contains control characters")
    if _is_secret_text(value):
        raise BootstrapEvidenceError(f"{field} contains secret-looking material")
    return value


def _safe_token(value: Any, *, field: str) -> str:
    text = _safe_text(value, field=field, max_length=512)
    if not _SAFE_TOKEN_RE.fullmatch(text):
        raise BootstrapEvidenceError(f"{field} contains unsafe argv characters")
    return text


def _sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise BootstrapEvidenceError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def _timestamp(value: Any, *, field: str) -> str:
    text = _safe_text(value, field=field, max_length=80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BootstrapEvidenceError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BootstrapEvidenceError(f"{field} must include a timezone")
    return text


def normalize_cli_version(value: Any) -> str:
    """Normalize a version line without retaining arbitrary stdout."""

    text = _safe_text(value, field="cli_version", max_length=128).strip()
    match = _VERSION_RE.fullmatch(text)
    if match is None:
        raise BootstrapEvidenceError(
            "CLI version output must be exactly a compshare 0.4.1 version line"
        )
    return match.group("version")


def require_pinned_cli_version(value: Any) -> str:
    """Return the normalized version or reject a non-pinned CLI."""

    version = normalize_cli_version(value)
    if version != EXPECTED_CLI_VERSION:
        raise BootstrapEvidenceError(
            f"unsupported CompShare CLI version {version!r}; "
            f"required {EXPECTED_CLI_VERSION}"
        )
    return version


def _validate_cli_bin(cli_bin: Any) -> str:
    if not isinstance(cli_bin, str) or not cli_bin:
        raise BootstrapEvidenceError("cli_bin must be a non-empty string")
    if _CONTROL_RE.search(cli_bin) or _is_secret_text(cli_bin):
        raise BootstrapEvidenceError("cli_bin contains unsafe material")
    # Paths may contain spaces and are still safe argv elements, but shell
    # syntax is rejected to keep the allowlist obvious to human reviewers.
    if any(char in cli_bin for char in (";", "|", "&", "`", "\n", "\r")):
        raise BootstrapEvidenceError("cli_bin contains shell syntax")
    return cli_bin


def _validate_probe_parameter(value: Any, *, field: str) -> str:
    text = _safe_token(value, field=field)
    if text.startswith("-"):
        raise BootstrapEvidenceError(f"{field} cannot be an option")
    return text


def build_allowlisted_probes(
    *,
    cli_bin: str = "compshare",
    region: str = "cn-sh2",
    zone: str = "cn-sh2-02",
    alternate_region: str = "cn-wlcb",
    alternate_zone: str = "cn-wlcb-01",
    _validate: bool = True,
) -> tuple[BootstrapProbeSpec, ...]:
    """Build the finite set of safe A2 command argv arrays.

    No caller-provided command or flag list is accepted.  Region/zone values
    are single validated tokens interpolated into known command templates.
    """

    cli_bin = _validate_cli_bin(cli_bin)
    region = _validate_probe_parameter(region, field="region")
    zone = _validate_probe_parameter(zone, field="zone")
    alternate_region = _validate_probe_parameter(
        alternate_region, field="alternate_region"
    )
    alternate_zone = _validate_probe_parameter(alternate_zone, field="alternate_zone")

    commands: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("version", (cli_bin, "--version")),
        ("doctor", (cli_bin, "--json", "doctor")),
        (
            "instance_list",
            (cli_bin, "--json", "instance", "list", "--all"),
        ),
        ("zones", (cli_bin, "--json", "instance", "zones")),
        ("families", (cli_bin, "--json", "instance", "families")),
        (
            "price_4090",
            (
                cli_bin,
                "--json",
                "instance",
                "price",
                "--region",
                region,
                "--zone",
                zone,
                "--gpu",
                "4090",
                "--cpu",
                "16",
                "--memory",
                "64GiB",
            ),
        ),
        (
            "price_5090",
            (
                cli_bin,
                "--json",
                "instance",
                "price",
                "--region",
                region,
                "--zone",
                zone,
                "--gpu",
                "5090",
                "--cpu",
                "16",
                "--memory",
                "96GiB",
            ),
        ),
        (
            "search_sh2",
            (
                cli_bin,
                "--json",
                "instance",
                "search",
                "--region",
                region,
                "--zone",
                zone,
            ),
        ),
        (
            "search_wlcb",
            (
                cli_bin,
                "--json",
                "instance",
                "search",
                "--region",
                alternate_region,
                "--zone",
                alternate_zone,
            ),
        ),
    )
    if _validate:
        for name, argv in commands:
            validate_allowlisted_argv(
                argv,
                probe_name=name,
                cli_bin=cli_bin,
                region=region,
                zone=zone,
                alternate_region=alternate_region,
                alternate_zone=alternate_zone,
            )
    return tuple(BootstrapProbeSpec(name, argv) for name, argv in commands)


# Natural aliases for callers that use singular terminology.
allowlisted_probes = build_allowlisted_probes
build_bootstrap_probes = build_allowlisted_probes


def _expected_probe_map(
    *,
    cli_bin: str,
    region: str,
    zone: str,
    alternate_region: str,
    alternate_zone: str,
) -> dict[str, tuple[str, ...]]:
    return {
        spec.name: spec.argv
        for spec in build_allowlisted_probes(
            cli_bin=cli_bin,
            region=region,
            zone=zone,
            alternate_region=alternate_region,
            alternate_zone=alternate_zone,
            _validate=False,
        )
    }


def validate_allowlisted_argv(
    argv: Sequence[str],
    *,
    probe_name: str | None = None,
    cli_bin: str = "compshare",
    region: str = "cn-sh2",
    zone: str = "cn-sh2-02",
    alternate_region: str = "cn-wlcb",
    alternate_zone: str = "cn-wlcb-01",
) -> tuple[str, ...]:
    """Validate an argv array against exact, credential-free A2 templates."""

    if not isinstance(argv, (list, tuple)) or not argv:
        raise BootstrapEvidenceError("probe argv must be a non-empty list/tuple")
    if any(not isinstance(token, str) or not token for token in argv):
        raise BootstrapEvidenceError("probe argv tokens must be non-empty strings")
    if any(_is_secret_text(token) for token in argv):
        raise BootstrapEvidenceError("probe argv contains secret-looking material")
    expected = _expected_probe_map(
        cli_bin=_validate_cli_bin(cli_bin),
        region=_validate_probe_parameter(region, field="region"),
        zone=_validate_probe_parameter(zone, field="zone"),
        alternate_region=_validate_probe_parameter(
            alternate_region, field="alternate_region"
        ),
        alternate_zone=_validate_probe_parameter(alternate_zone, field="alternate_zone"),
    )
    actual = tuple(argv)
    if probe_name is not None:
        if probe_name not in expected:
            raise BootstrapEvidenceError(f"unknown bootstrap probe {probe_name!r}")
        if actual != expected[probe_name]:
            raise BootstrapEvidenceError(
                f"argv for probe {probe_name!r} is outside the A2 allowlist"
            )
    elif actual not in expected.values():
        raise BootstrapEvidenceError("argv is outside the A2 allowlist")
    return actual


# Alias used by command-capture code and tests.
assert_allowlisted_argv = validate_allowlisted_argv


def _safe_summary_value(value: Any, *, depth: int = 0) -> Any:
    """Return a JSON-safe summary value with secret channels removed."""

    if depth > 8:
        raise BootstrapEvidenceError("probe summary is nested too deeply")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BootstrapEvidenceError("probe summary contains a non-finite number")
        return value
    if isinstance(value, str):
        if _CONTROL_RE.search(value):
            raise BootstrapEvidenceError("probe summary contains control characters")
        if _is_secret_text(value):
            return "<redacted>"
        if len(value) > 4096:
            raise BootstrapEvidenceError("probe summary string is too long")
        return value
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str) or not key:
                raise BootstrapEvidenceError("probe summary keys must be strings")
            if _FORBIDDEN_SUMMARY_KEY_RE.fullmatch(key):
                continue
            clean[key] = _safe_summary_value(nested, depth=depth + 1)
        return {key: clean[key] for key in sorted(clean)}
    if isinstance(value, (list, tuple)):
        if len(value) > 1024:
            raise BootstrapEvidenceError("probe summary list is too long")
        return [_safe_summary_value(item, depth=depth + 1) for item in value]
    raise BootstrapEvidenceError(
        f"probe summary contains unsupported value type {type(value).__name__}"
    )


def normalize_probe_summary(probe_name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a probe payload without copying stdout/stderr into evidence.

    ``doctor`` is deliberately stricter than other read-only probes: only the
    four fields in :data:`DOCTOR_ALLOWLIST` can appear.  Inventory/price
    responses use the generic JSON-safe projection so the raw artifact keeps
    useful provider data while forbidden channels are dropped/redacted.
    """

    if probe_name not in PROBE_NAMES:
        raise BootstrapEvidenceError(f"unknown bootstrap probe {probe_name!r}")
    if not isinstance(value, Mapping):
        raise BootstrapEvidenceError("probe summary must be a mapping")
    source: Mapping[str, Any] = value
    if probe_name == "doctor" and isinstance(value.get("doctor"), Mapping):
        source = value["doctor"]
    if probe_name == "doctor":
        return {
            key: _safe_summary_value(source[key])
            for key in sorted(DOCTOR_ALLOWLIST)
            if key in source
        }
    clean = _safe_summary_value(source)
    if not isinstance(clean, dict):  # pragma: no cover - mapping input above
        raise BootstrapEvidenceError("normalized probe summary is not an object")
    return clean


def build_probe_record(
    *,
    name: str,
    argv: Sequence[str],
    captured_at: str,
    exit_code: int,
    summary: Mapping[str, Any],
    evidence_path: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    stdout_sha256: str | None = None,
    stderr_sha256: str | None = None,
    cli_bin: str = "compshare",
    region: str = "cn-sh2",
    zone: str = "cn-sh2-02",
    alternate_region: str = "cn-wlcb",
    alternate_zone: str = "cn-wlcb-01",
) -> dict[str, Any]:
    """Construct one validated probe record without invoking a provider."""

    validate_allowlisted_argv(
        argv,
        probe_name=name,
        cli_bin=cli_bin,
        region=region,
        zone=zone,
        alternate_region=alternate_region,
        alternate_zone=alternate_zone,
    )
    _timestamp(captured_at, field="probe.captured_at")
    started = started_at or captured_at
    finished = finished_at or captured_at
    _timestamp(started, field="probe.started_at")
    _timestamp(finished, field="probe.finished_at")
    if (
        isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or exit_code < 0
    ):
        raise BootstrapEvidenceError("probe.exit_code must be a non-negative integer")
    safe_path = validate_relative_path(evidence_path, field="probe.evidence_path")
    normalized = normalize_probe_summary(name, summary)
    result: dict[str, Any] = {
        "name": name,
        "argv": list(argv),
        "captured_at": captured_at,
        "started_at": started,
        "finished_at": finished,
        "exit_code": exit_code,
        "summary": normalized,
        "evidence_path": safe_path,
    }
    if stdout_sha256 is not None:
        result["stdout_sha256"] = _sha256(stdout_sha256, field="probe.stdout_sha256")
    if stderr_sha256 is not None:
        result["stderr_sha256"] = _sha256(stderr_sha256, field="probe.stderr_sha256")
    return result


def validate_relative_path(value: Any, *, field: str = "path") -> str:
    """Validate a portable relative evidence path without following links."""

    text = _safe_text(value, field=field, max_length=512)
    if text.startswith("/") or "\\" in text:
        raise BootstrapEvidenceError(f"{field} must be a portable relative path")
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise BootstrapEvidenceError(f"{field} contains unsafe path components")
    normalized = pure.as_posix()
    if normalized != text:
        raise BootstrapEvidenceError(f"{field} is not normalized")
    return normalized


def _reject_symlink_components(path: Path, root: Path) -> None:
    """Reject symlinks in an evidence path, including the final file."""

    root_lexical = Path(root)
    candidate_lexical = Path(path)
    if not candidate_lexical.is_absolute():
        candidate_lexical = root_lexical / candidate_lexical
    root_resolved = root_lexical.resolve()
    candidate = candidate_lexical.resolve(strict=False)
    try:
        relative = candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise BootstrapEvidenceError("evidence path escapes its root") from exc
    # Check the original lexical path as well as the resolved path.  Only
    # components *below the lexical root* are relevant: macOS commonly
    # aliases /var to /private/var, and rejecting that system alias would make
    # an otherwise safe temporary evidence root unusable.
    try:
        lexical_relative = candidate_lexical.absolute().relative_to(
            root_lexical.absolute()
        )
    except ValueError:
        lexical_relative = None
    if lexical_relative is not None:
        current = root_lexical.absolute()
        for part in lexical_relative.parts:
            current = current / part
            if current.is_symlink():
                raise BootstrapEvidenceError(
                    f"evidence path contains a symlink: {current}"
                )
    return relative


def sha256_file(path: str | Path, *, root: str | Path | None = None) -> str:
    """Hash one local evidence file, rejecting symlink/path escapes."""

    physical = Path(path)
    if root is not None:
        root_path = Path(root)
        if physical.is_absolute():
            candidate = physical
        else:
            candidate = root_path / physical
        _reject_symlink_components(candidate, root_path)
    elif physical.is_symlink():
        raise BootstrapEvidenceError("evidence file must not be a symlink")
    if not physical.is_absolute() and root is not None:
        physical = Path(root) / physical
    if not physical.is_file():
        raise BootstrapEvidenceError(f"evidence file is missing: {physical}")
    digest = hashlib.sha256()
    try:
        with physical.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BootstrapEvidenceError(f"cannot read evidence file {physical}: {exc}") from exc
    return digest.hexdigest()


def build_evidence_file(
    path: str | Path,
    *,
    root: str | Path | None = None,
    relative_path: str | None = None,
    role: str = "raw_provider_response",
) -> dict[str, Any]:
    """Build a path/size/SHA-256 evidence entry from a local file."""

    physical = Path(path)
    root_path = Path(root) if root is not None else None
    if root_path is not None:
        if physical.is_absolute():
            candidate = physical
        else:
            candidate = root_path / physical
        _reject_symlink_components(candidate, root_path)
        physical = candidate
        if relative_path is None:
            try:
                relative_path = physical.resolve().relative_to(root_path.resolve()).as_posix()
            except ValueError as exc:
                raise BootstrapEvidenceError("evidence file is outside its root") from exc
    if physical.is_symlink():
        raise BootstrapEvidenceError("evidence file must not be a symlink")
    if relative_path is None:
        relative_path = physical.name
    safe_path = validate_relative_path(relative_path, field="evidence.path")
    if not isinstance(role, str) or not role or _is_secret_text(role):
        raise BootstrapEvidenceError("evidence.role is unsafe")
    if not physical.is_file():
        raise BootstrapEvidenceError(f"evidence file is missing: {physical}")
    try:
        size = physical.stat().st_size
    except OSError as exc:
        raise BootstrapEvidenceError(f"cannot stat evidence file {physical}: {exc}") from exc
    digest = sha256_file(physical, root=root_path)
    return EvidenceFile(safe_path, size, digest, role).to_dict()


# Compatibility spellings for later capture code.
file_evidence = build_evidence_file
evidence_file_from_path = build_evidence_file


def _canonical_body(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"manifest_digest", "digest"}
    }


def canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    """Return canonical JSON bytes excluding the self-referential digest."""

    if not isinstance(manifest, Mapping):
        raise BootstrapEvidenceError("manifest must be a mapping")
    try:
        return json.dumps(
            _canonical_body(manifest),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BootstrapEvidenceError(f"manifest is not canonical JSON: {exc}") from exc


def canonical_manifest_digest(manifest: Mapping[str, Any]) -> str:
    """Compute the bare SHA-256 digest of a manifest body."""

    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    """Alias for :func:`canonical_manifest_digest`."""

    return canonical_manifest_digest(manifest)


def _validate_environment(environment: Any) -> dict[str, str]:
    if not isinstance(environment, Mapping):
        raise BootstrapEvidenceError("environment must be a mapping")
    required = ("os", "arch", "python", "locale", "timezone")
    if set(environment) != set(required):
        raise BootstrapEvidenceError(
            "environment must contain exactly os, arch, python, locale, timezone"
        )
    return {
        key: _safe_text(environment[key], field=f"environment.{key}", max_length=256)
        for key in required
    }


def _validate_capture_tool(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise BootstrapEvidenceError("capture_tool must be a mapping")
    if set(value) != {"path", "sha256"}:
        raise BootstrapEvidenceError("capture_tool must contain exactly path and sha256")
    path = validate_relative_path(value["path"], field="capture_tool.path")
    return {"path": path, "sha256": _sha256(value["sha256"], field="capture_tool.sha256")}


def _validate_cli(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise BootstrapEvidenceError("cli must be a mapping")
    if set(value) != {"version", "executable_path", "executable_sha256"}:
        raise BootstrapEvidenceError(
            "cli must contain exactly version, executable_path, executable_sha256"
        )
    version = require_pinned_cli_version(value["version"])
    executable_path = _safe_text(
        value["executable_path"], field="cli.executable_path", max_length=1024
    )
    if _CONTROL_RE.search(executable_path):
        raise BootstrapEvidenceError("cli.executable_path contains control characters")
    return {
        "version": version,
        "executable_path": executable_path,
        "executable_sha256": _sha256(
            value["executable_sha256"], field="cli.executable_sha256"
        ),
    }


def _validate_evidence_files(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise BootstrapEvidenceError("evidence_files must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise BootstrapEvidenceError(f"evidence_files[{index}] must be an object")
        required = {"path", "size_bytes", "sha256", "role"}
        if set(entry) != required:
            raise BootstrapEvidenceError(
                f"evidence_files[{index}] must contain exactly {sorted(required)}"
            )
        path = validate_relative_path(entry["path"], field=f"evidence_files[{index}].path")
        if path in seen:
            raise BootstrapEvidenceError(f"duplicate evidence path {path!r}")
        seen.add(path)
        size = entry["size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BootstrapEvidenceError(
                f"evidence_files[{index}].size_bytes must be a non-negative integer"
            )
        role = _safe_token(entry["role"], field=f"evidence_files[{index}].role")
        result.append(
            {
                "path": path,
                "size_bytes": size,
                "sha256": _sha256(
                    entry["sha256"], field=f"evidence_files[{index}].sha256"
                ),
                "role": role,
            }
        )
    if not result:
        raise BootstrapEvidenceError("evidence_files must not be empty")
    return result


def _validate_probes(
    value: Any,
    *,
    cli_bin: str,
    region: str,
    zone: str,
    alternate_region: str,
    alternate_zone: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise BootstrapEvidenceError("probes must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    expected_names = set(PROBE_NAMES)
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise BootstrapEvidenceError(f"probes[{index}] must be an object")
        required = {
            "name",
            "argv",
            "captured_at",
            "started_at",
            "finished_at",
            "exit_code",
            "summary",
            "evidence_path",
        }
        optional = {"stdout_sha256", "stderr_sha256"}
        if set(entry) - required - optional or not required.issubset(entry):
            raise BootstrapEvidenceError(
                f"probes[{index}] has an unsupported or missing field"
            )
        name = entry["name"]
        if not isinstance(name, str) or name not in expected_names:
            raise BootstrapEvidenceError(f"probes[{index}].name is not allowlisted")
        if name in seen:
            raise BootstrapEvidenceError(f"duplicate probe {name!r}")
        seen.add(name)
        argv = validate_allowlisted_argv(
            entry["argv"],
            probe_name=name,
            cli_bin=cli_bin,
            region=region,
            zone=zone,
            alternate_region=alternate_region,
            alternate_zone=alternate_zone,
        )
        captured_at = _timestamp(entry["captured_at"], field=f"probes[{index}].captured_at")
        started_at = _timestamp(entry["started_at"], field=f"probes[{index}].started_at")
        finished_at = _timestamp(entry["finished_at"], field=f"probes[{index}].finished_at")
        exit_code = entry["exit_code"]
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise BootstrapEvidenceError(f"probes[{index}].exit_code must be an integer")
        evidence_path = validate_relative_path(
            entry["evidence_path"], field=f"probes[{index}].evidence_path"
        )
        if not isinstance(entry["summary"], Mapping):
            raise BootstrapEvidenceError(f"probes[{index}].summary must be an object")
        normalized = normalize_probe_summary(name, entry["summary"])
        if normalized != entry["summary"]:
            raise BootstrapEvidenceError(
                f"probes[{index}].summary is not normalized or contains forbidden fields"
            )
        clean: dict[str, Any] = {
            "name": name,
            "argv": list(argv),
            "captured_at": captured_at,
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": exit_code,
            "summary": normalized,
            "evidence_path": evidence_path,
        }
        for field_name in optional:
            if field_name in entry:
                clean[field_name] = _sha256(
                    entry[field_name], field=f"probes[{index}].{field_name}"
                )
        result.append(clean)
    if not result:
        raise BootstrapEvidenceError("probes must not be empty")
    return result


def validate_bootstrap_manifest(
    manifest: Mapping[str, Any],
    *,
    evidence_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a current manifest and optionally verify its local artifacts.

    ``evidence_root`` is an explicit local root.  When supplied, referenced
    files must be regular non-symlink files beneath it and their size/hash are
    recomputed.  No path outside that root is ever opened.
    """

    if not isinstance(manifest, Mapping):
        raise BootstrapEvidenceError("bootstrap manifest must be an object")
    required = {
        "kind",
        "schema_id",
        "schema_version",
        "credential_profile_id",
        "captured_at",
        "source_commit",
        "cli",
        "probes",
        "environment",
        "capture_tool",
        "evidence_files",
        "manifest_digest",
    }
    if set(manifest) != required:
        unknown = sorted(set(manifest) - required)
        missing = sorted(required - set(manifest))
        raise BootstrapEvidenceError(
            f"manifest fields are not exact; missing={missing}, unknown={unknown}"
        )
    if manifest["kind"] != KIND or manifest["schema_id"] != SCHEMA_ID:
        raise BootstrapEvidenceError("manifest kind/schema_id is not the current A2 schema")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise BootstrapEvidenceError("manifest schema_version is unsupported")
    credential_profile_id = _safe_token(
        manifest["credential_profile_id"], field="credential_profile_id"
    )
    captured_at = _timestamp(manifest["captured_at"], field="captured_at")
    source_commit = _safe_text(
        manifest["source_commit"], field="source_commit", max_length=64
    )
    if COMMIT_RE.fullmatch(source_commit) is None:
        raise BootstrapEvidenceError("source_commit must be a hexadecimal commit id")
    cli = _validate_cli(manifest["cli"])
    environment = _validate_environment(manifest["environment"])
    capture_tool = _validate_capture_tool(manifest["capture_tool"])
    evidence_files = _validate_evidence_files(manifest["evidence_files"])
    probes = _validate_probes(
        manifest["probes"],
        cli_bin=cli["executable_path"],
        # The manifest does not carry template parameters separately.  Probe
        # argv validation therefore uses the concrete argv's known defaults;
        # callers constructing alternate regions should use the constructor's
        # explicit template parameters and validate before sealing.
        region="cn-sh2",
        zone="cn-sh2-02",
        alternate_region="cn-wlcb",
        alternate_zone="cn-wlcb-01",
    )
    evidence_by_path = {entry["path"]: entry for entry in evidence_files}
    for probe in probes:
        path = probe["evidence_path"]
        if path not in evidence_by_path:
            raise BootstrapEvidenceError(
                f"probe {probe['name']!r} references unlisted evidence path {path!r}"
            )

    expected_digest = canonical_manifest_digest(manifest)
    if manifest["manifest_digest"] != expected_digest:
        raise BootstrapEvidenceError(
            "manifest_digest does not match the canonical manifest body"
        )

    if evidence_root is not None:
        root = Path(evidence_root)
        if root.is_symlink() or not root.is_dir():
            raise BootstrapEvidenceError("evidence_root must be a regular directory")
        root = root.resolve()
        for entry in evidence_files:
            rel = Path(entry["path"])
            physical = root / rel
            _reject_symlink_components(physical, root)
            if not physical.is_file():
                raise BootstrapEvidenceError(f"evidence artifact is missing: {rel}")
            if physical.stat().st_size != entry["size_bytes"]:
                raise BootstrapEvidenceError(f"evidence artifact size mismatch: {rel}")
            if sha256_file(physical, root=root) != entry["sha256"]:
                raise BootstrapEvidenceError(f"evidence artifact digest mismatch: {rel}")

    return {
        "kind": KIND,
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "credential_profile_id": credential_profile_id,
        "captured_at": captured_at,
        "source_commit": source_commit,
        "cli": cli,
        "probes": probes,
        "environment": environment,
        "capture_tool": capture_tool,
        "evidence_files": evidence_files,
        "manifest_digest": manifest["manifest_digest"],
    }


def build_bootstrap_manifest(
    *,
    credential_profile_id: str,
    captured_at: str,
    source_commit: str,
    cli_version: str,
    executable_sha256: str,
    probes: Sequence[Mapping[str, Any]],
    evidence_files: Sequence[Mapping[str, Any]],
    environment: Mapping[str, Any],
    capture_tool_sha256: str,
    executable_path: str = "compshare",
    capture_tool_path: str = "scripts/qualification/capture_compshare_bootstrap.py",
) -> dict[str, Any]:
    """Construct and self-seal one current A2 bootstrap manifest."""

    version = require_pinned_cli_version(cli_version)
    cli = {
        "version": version,
        "executable_path": _safe_text(
            executable_path, field="cli.executable_path", max_length=1024
        ),
        "executable_sha256": _sha256(
            executable_sha256, field="cli.executable_sha256"
        ),
    }
    # Validate through the same strict path used for replay.  The caller's
    # probe records are not silently modified beyond JSON list normalization.
    manifest: dict[str, Any] = {
        "kind": KIND,
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "credential_profile_id": credential_profile_id,
        "captured_at": captured_at,
        "source_commit": source_commit,
        "cli": cli,
        "probes": [dict(item) for item in probes],
        "environment": dict(environment),
        "capture_tool": {
            "path": capture_tool_path,
            "sha256": capture_tool_sha256,
        },
        "evidence_files": [dict(item) for item in evidence_files],
    }
    # Validation currently expects the canonical default templates.  Probe
    # records are built by ``build_probe_record`` which validates alternate
    # templates before this constructor is called.
    provisional = {
        **manifest,
        "manifest_digest": canonical_manifest_digest(manifest),
    }
    clean = validate_bootstrap_manifest(provisional)
    clean["manifest_digest"] = canonical_manifest_digest(clean)
    # A final validation catches accidental mutation after digest assembly.
    return validate_bootstrap_manifest(clean)


# Construction aliases used by operator-facing code.
make_bootstrap_manifest = build_bootstrap_manifest
construct_bootstrap_manifest = build_bootstrap_manifest


def read_bootstrap_manifest(path: str | Path) -> dict[str, Any]:
    """Read a manifest without writing or upgrading it."""

    manifest_path = Path(path)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BootstrapEvidenceError("manifest path must be a regular file")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapEvidenceError(f"cannot read bootstrap manifest {manifest_path}") from exc
    if not isinstance(value, dict):
        raise BootstrapEvidenceError("bootstrap manifest must contain a JSON object")
    return value


def classify_bootstrap_manifest(
    value: Mapping[str, Any] | str | Path,
    *,
    evidence_root: str | Path | None = None,
) -> dict[str, Any]:
    """Classify current/legacy input without changing the source bytes."""

    manifest = (
        read_bootstrap_manifest(value)
        if isinstance(value, (str, Path))
        else dict(value)
    )
    if manifest.get("kind") != KIND or manifest.get("schema_version") != SCHEMA_VERSION:
        return {
            "status": INCOMPLETE_NOT_REPLAYABLE,
            "manifest": manifest,
            "errors": ["legacy or unsupported bootstrap manifest format"],
        }
    try:
        validated = validate_bootstrap_manifest(manifest, evidence_root=evidence_root)
    except BootstrapEvidenceError as exc:
        return {
            "status": INCOMPLETE_NOT_REPLAYABLE,
            "manifest": manifest,
            "errors": [str(exc)],
        }
    return {"status": REPLAYABLE, "manifest": validated, "errors": []}


def bootstrap_manifest_status(
    value: Mapping[str, Any] | str | Path,
    *,
    evidence_root: str | Path | None = None,
) -> str:
    """Return only the non-mutating replay status."""

    return str(classify_bootstrap_manifest(value, evidence_root=evidence_root)["status"])


# Additional explicit names for tests and later script integration.
validate_manifest = validate_bootstrap_manifest
load_bootstrap_manifest = read_bootstrap_manifest


__all__ = [
    "BootstrapEvidenceError",
    "BootstrapProbeSpec",
    "DOCTOR_ALLOWLIST",
    "EXPECTED_CLI_VERSION",
    "EXPECTED_COMPSHARE_CLI_VERSION",
    "EvidenceFile",
    "INCOMPLETE_NOT_REPLAYABLE",
    "KIND",
    "PINNED_COMPSHARE_CLI_VERSION",
    "PROBE_NAMES",
    "REPLAYABLE",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "allowlisted_probes",
    "assert_allowlisted_argv",
    "bootstrap_manifest_status",
    "build_allowlisted_probes",
    "build_bootstrap_manifest",
    "build_bootstrap_probes",
    "build_evidence_file",
    "build_probe_record",
    "canonical_manifest_bytes",
    "canonical_manifest_digest",
    "classify_bootstrap_manifest",
    "construct_bootstrap_manifest",
    "evidence_file_from_path",
    "file_evidence",
    "load_bootstrap_manifest",
    "make_bootstrap_manifest",
    "manifest_digest",
    "normalize_cli_version",
    "normalize_probe_summary",
    "read_bootstrap_manifest",
    "require_pinned_cli_version",
    "sha256_file",
    "validate_allowlisted_argv",
    "validate_bootstrap_manifest",
    "validate_manifest",
    "validate_relative_path",
]
