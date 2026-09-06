"""Allowlist Case Packager.

Stages the Candidate input bundle from an explicit public-file allowlist only.
Never copies a whole case and then deletes private paths: ``reference/``,
``solution/``, ``tests/`` etc. are never resolvable sources. The returned
``BundleManifest`` lists the complete input set plus a deterministic root
digest, so any later drift (extra file, changed byte) is detectable.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import jsonschema

from ccbench.contracts.case import CaseSpec, PublicFileRule
from ccbench.core.digests import digest_bytes, sha256_file

# Defense-in-depth leak scan: a normalized destination matching any of these
# path segments aborts packaging, even though the allowlist should never
# resolve them in the first place.
FORBIDDEN_NAMES = (
    "reference",
    "solution",
    "tests",
    "verifier",
    "thresholds",
    "fixtures",
    ".git",
    "expert_notes",
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "bundle-manifest.schema.json"
MANIFEST_NAME = "bundle-manifest.json"


class PackageError(Exception):
    """Raised when a candidate bundle cannot be packaged safely."""


@dataclass(frozen=True)
class BundleFile:
    path: str  # normalized candidate-relative path (forward slashes)
    size: int
    sha256: str


@dataclass(frozen=True)
class BundleManifest:
    case_id: str
    case_version: str
    schema_version: str
    files: tuple[BundleFile, ...]
    public_digest: str
    leak_scan: tuple[str, ...]  # forbidden names that were scanned; empty = clean

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_version": self.case_version,
            "schema_version": self.schema_version,
            "files": [
                {"path": f.path, "size": f.size, "sha256": f.sha256} for f in self.files
            ],
            "public_digest": self.public_digest,
            "leak_scan": list(self.leak_scan),
        }


def _has_glob(source: str) -> bool:
    return any(ch in source for ch in "*?[")


def _strip_prefix(rel: str, rule: PublicFileRule) -> str:
    prefix = rule.strip_prefix
    if not prefix:
        return rel
    base = prefix.rstrip("/")
    if rel == base:
        return ""
    if not rel.startswith(base + "/"):
        raise PackageError(
            f"strip_prefix {prefix!r} does not match resolved source {rel!r}"
        )
    return rel[len(base) + 1 :]


def _normalized_dest(rel: str, rule: PublicFileRule, single_file: bool) -> str:
    """Map a resolved case-relative source to a candidate-relative destination.

    A non-glob source that resolves to a single file maps to the rule's
    ``destination`` as the final path (legacy ``/app/<file>`` stays stable).
    A glob (or directory) source keeps relative structure under the
    ``destination`` directory after ``strip_prefix``.
    """
    stripped = _strip_prefix(rel, rule)
    if single_file:
        return PurePosixPath(rule.destination).as_posix()
    dest = (PurePosixPath(rule.destination) / PurePosixPath(stripped)).as_posix()
    return _verify_safe_dest(dest, rel)


def _verify_safe_dest(dest: str, rel: str) -> str:
    posix = PurePosixPath(dest)
    if posix.is_absolute() or ".." in posix.parts:
        raise PackageError(f"unsafe destination {dest!r} for source {rel!r}")
    return posix.as_posix()


def _reject_non_regular(src: Path) -> None:
    if src.is_symlink():
        raise PackageError(f"symlink not allowed in public inputs: {src.name}")
    if not src.is_file():
        raise PackageError(f"non-regular file not allowed in public inputs: {src.name}")


def _leak_scan(paths: list[str]) -> None:
    for path in paths:
        for segment in PurePosixPath(path).parts:
            if segment in FORBIDDEN_NAMES:
                raise PackageError(
                    f"leak-scan forbidden name {segment!r} in bundle path {path!r}"
                )


def package_candidate(spec: CaseSpec, destination: Path) -> BundleManifest:
    """Copy the allowlist inputs into ``destination`` and return the manifest.

    Resolves every public-file rule under the case root, rejects symlinks and
    non-regular files, applies ``strip_prefix``/``destination``, rejects
    collisions, copies the instruction to ``instruction.md``, and writes
    ``bundle-manifest.json`` last.
    """
    case_root = spec.path
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    staged: list[tuple[Path, str]] = []  # (source abs path, normalized dest)

    for rule in spec.public_files:
        globbed = _has_glob(rule.source)
        if globbed:
            for match in case_root.glob(rule.source):
                if match.is_dir() and not match.is_symlink():
                    continue
                _reject_non_regular(match)
                rel = match.relative_to(case_root).as_posix()
                staged.append((match, _normalized_dest(rel, rule, single_file=False)))
        else:
            src = case_root / rule.source
            if not src.exists():
                raise PackageError(f"public source not found: {rule.source}")
            _reject_non_regular(src)
            if src.is_dir():
                for match in src.rglob("*"):
                    _reject_non_regular(match)
                    rel = match.relative_to(case_root).as_posix()
                    staged.append((match, _normalized_dest(rel, rule, single_file=False)))
            else:
                rel = src.relative_to(case_root).as_posix()
                staged.append((src, _normalized_dest(rel, rule, single_file=True)))

    # Instruction is always mapped to instruction.md at the candidate root.
    instruction = case_root / spec.instruction_path
    if not instruction.is_file() or instruction.is_symlink():
        raise PackageError(
            f"instruction file {spec.instruction_path!r} missing or not a regular file"
        )
    staged.append((instruction, "instruction.md"))

    # Destination collisions are rejected (fail closed).
    seen: dict[str, Path] = {}
    for src, dest in staged:
        if dest in seen:
            raise PackageError(
                f"destination collision: {dest!r} from {seen[dest]!r} and {src!r}"
            )
        seen[dest] = src

    # Leak-scan the allowlist results, then copy deterministically.
    ordered = sorted(staged, key=lambda item: item[1])
    _leak_scan([dest for _, dest in ordered])

    for src, dest in ordered:
        target = destination / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)

    files = tuple(
        BundleFile(path=dest, size=src.stat().st_size, sha256=sha256_file(src))
        for src, dest in ordered
    )
    public_digest = digest_bytes(
        "".join(
            f"{record.sha256}  {record.size}  {record.path}\n" for record in files
        ).encode("utf-8")
    )
    manifest = BundleManifest(
        case_id=spec.case_id,
        case_version=spec.case_version,
        schema_version=spec.schema_version,
        files=files,
        public_digest=public_digest,
        leak_scan=FORBIDDEN_NAMES,
    )

    payload = json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n"
    _validate_manifest(payload)
    (destination / MANIFEST_NAME).write_text(payload, encoding="utf-8")
    return manifest


def _validate_manifest(payload: str) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(json.loads(payload)), key=lambda e: list(e.path)
    )
    if errors:
        raise PackageError(
            f"bundle manifest violates bundle-manifest.schema.json: {errors[0].message}"
        )
