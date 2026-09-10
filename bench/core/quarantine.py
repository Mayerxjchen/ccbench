"""Artifact quarantine and submission sealing.

The raw submission is copied to a private host directory (only ``final/`` for
canonical cases; the workspace minus known runtime names for legacy root-layout
cases), then quarantined with lstat-based validation: symlinks, hardlinks with
count > 1, FIFOs, sockets, devices, and setuid/setgid bits are rejected;
archive payloads are rejected unless allowed; file-count, single-file, and
aggregate limits are enforced. The clean submission is normalized (dirs 0755,
files 0644), its manifest is fsynced, and the directory is atomically renamed
into place. Candidate Python is never imported; nothing is pickled.
"""

from __future__ import annotations

import json
import os
import shutil
import stat as stat_mod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import jsonschema

from bench.core.digests import digest_bytes, sha256_file

# Runtime-only names never accepted from a legacy root-layout workspace.
EXCLUDED_LEGACY_NAMES = (
    ".venv",
    ".skills",
    "_dftworld_tests",
    "tests",
    "reference",
    "solution",
    ".pytest_cache",
    "logs",
)

COLLECTOR_FILE = "_collector.json"
SEAL_MANIFEST = "manifest.json"

ARCHIVE_SUFFIXES = (
    ".zip", ".tar", ".gz", ".tgz", ".tar.gz",
    ".bz2", ".tar.bz2", ".xz", ".tar.xz",
    ".7z", ".rar", ".zst", ".lz4",
)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "submission.schema.json"


class QuarantineError(Exception):
    """Raised when a raw submission is unsafe or exceeds limits."""


@dataclass(frozen=True)
class QuarantineLimits:
    max_files: int
    max_single_bytes: int
    max_total_bytes: int
    allow_archives: bool = False


@dataclass(frozen=True)
class SubmissionFile:
    path: str  # normalized relative path (forward slashes)
    size: int
    sha256: str
    mode: int  # normalized permission bits


@dataclass(frozen=True)
class SubmissionSeal:
    manifest_digest: str
    file_count: int
    total_bytes: int
    sealed_at: datetime
    legacy_layout: bool
    exclusions: tuple[str, ...]


# --------------------------------------------------------------------------- #
# collection (frozen Candidate -> private raw host dir)
# --------------------------------------------------------------------------- #

def _copy_regular(src: Path, dst: Path, max_bytes: int) -> int:
    """Byte-copy one already-lstat'd regular file without ever following a
    symlink.  ``O_NOFOLLOW`` guarantees the opened inode is the file we
    validated even if the source is swapped for a symlink between lstat and
    open (TOCTOU); ``fstat`` re-verifies regularity before any byte is
    written.  An ``OSError`` here propagates as a harness-side failure — the
    source node already passed validation, so a vanished/swapped source is
    infrastructure, not Candidate unsafety."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    with os.fdopen(os.open(src, flags), "rb") as handle:
        st = os.fstat(handle.fileno())
        if not stat_mod.S_ISREG(st.st_mode):
            raise QuarantineError(
                f"non-regular node after open rejected in submission: {src}"
            )
        copied = 0
        try:
            with open(dst, "wb") as out:
                while True:
                    chunk = handle.read(min(1024 * 1024, max_bytes - copied + 1))
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > max_bytes:
                        raise QuarantineError(f"file exceeds collection limit: {src}")
                    out.write(chunk)
        except Exception:
            try:
                dst.unlink()
            except FileNotFoundError:
                pass
            raise
        return copied


def _copy_tree(
    src: Path, dst: Path, skip: set[str], *,
    max_files: int, max_single_bytes: int, max_total_bytes: int,
    usage: dict[str, int],
) -> None:
    """lstat-safe recursive copy.

    Every node is lstat'd before bytes are copied: symlinks (broken or not),
    special files and setuid/setgid are rejected as Candidate-origin unsafety
    while directories are pruned; regular files go through ``_copy_regular``
    (O_NOFOLLOW + fstat).  Excluded legacy names are pruned before any stat,
    as before.  Rejection is a ``QuarantineError`` (AGENT_FAILURE /
    INVALID_SUBMISSION in the harness); OS errors after validation stay
    ``OSError`` (INFRA_INVALID / HARNESS_FAILURE).
    """
    src = Path(src)
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    # The walk root itself is followed by os.walk regardless of followlinks;
    # lstat it so a symlinked submission root cannot walk outside the tree.
    _reject_unsafe_node(os.lstat(src), ".")
    for root, dirnames, filenames in os.walk(src, topdown=True, followlinks=False):
        keep_dirs: list[str] = []
        for name in dirnames:
            if name in skip:
                continue
            entry = Path(root) / name
            rel = os.path.relpath(entry, src).replace(os.sep, "/")
            _reject_unsafe_node(os.lstat(entry), rel)
            keep_dirs.append(name)
        dirnames[:] = keep_dirs
        rel = os.path.relpath(root, src)
        target_dir = dst if rel == "." else dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            if name in skip:
                continue
            entry = Path(root) / name
            rel = os.path.relpath(entry, src).replace(os.sep, "/")
            info = os.lstat(entry)
            _reject_unsafe_node(info, rel)
            if usage["files"] >= max_files:
                raise QuarantineError(f"submission file count exceeds limit {max_files}")
            if info.st_size > max_single_bytes:
                raise QuarantineError(f"submission file {rel} exceeds single-file limit")
            if usage["bytes"] + info.st_size > max_total_bytes:
                raise QuarantineError("submission aggregate size exceeds collection limit")
            copy_cap = min(max_single_bytes, max_total_bytes - usage["bytes"])
            copied = _copy_regular(entry, target_dir / name, copy_cap)
            if copied != info.st_size:
                raise QuarantineError(f"submission file changed while collecting: {rel}")
            if usage["bytes"] + copied > max_total_bytes:
                raise QuarantineError("submission aggregate size exceeds collection limit")
            usage["files"] += 1
            usage["bytes"] += copied


def collect_raw_submission(
    workspace: Path,
    submission_root: str,
    raw: Path,
    legacy_layout: bool,
    max_size_mb: int = 100,
    max_files: int = 10_000,
    max_single_bytes: int | None = None,
    max_total_bytes: int | None = None,
) -> None:
    """Copy the frozen submission into the private raw directory.

    Canonical v2 cases contribute only ``final/``. Legacy root-layout cases
    contribute the whole workspace minus ``EXCLUDED_LEGACY_NAMES``. The
    collection mode and exclusions are recorded for the seal evidence.

    Raises QuarantineError before copying bytes that would exceed the
    aggregate, per-file, or file-count limits.  The raw directory is removed
    on every collection failure, so a rejected attempt cannot leave a partial
    submission for a later seal.
    """
    workspace = Path(workspace)
    raw = Path(raw)
    posix_root = PurePosixPath(submission_root)
    if not submission_root or posix_root.is_absolute() or ".." in posix_root.parts:
        raise QuarantineError("submission_root must be a non-empty relative path without '..'")
    resolved_root = (workspace / Path(*posix_root.parts)).resolve()
    try:
        resolved_root.relative_to(workspace.resolve())
    except ValueError as exc:
        raise QuarantineError("submission_root escapes Candidate workspace") from exc
    max_bytes = max_size_mb * 1024 * 1024
    if max_size_mb <= 0 or max_files <= 0:
        raise QuarantineError("submission collection limits must be positive")
    if max_single_bytes is None:
        max_single_bytes = max_bytes
    if max_total_bytes is None:
        max_total_bytes = max_bytes
    if max_total_bytes <= 0 or max_total_bytes > max_bytes:
        raise QuarantineError("aggregate collection limit is invalid")
    if max_single_bytes <= 0 or max_single_bytes > max_total_bytes:
        raise QuarantineError("single-file collection limit is invalid")
    # Clean raw dir before collecting to prevent stale bytes from a crashed
    # previous collection merging with fresh bytes on resume.
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True, exist_ok=True)
    try:
        usage = {"files": 0, "bytes": 0}
        if legacy_layout:
            _copy_tree(
                workspace, raw, set(EXCLUDED_LEGACY_NAMES),
                max_files=max_files, max_single_bytes=max_single_bytes,
                max_total_bytes=max_total_bytes, usage=usage,
            )
            exclusions: tuple[str, ...] = EXCLUDED_LEGACY_NAMES
        else:
            final = workspace / submission_root
            if not final.is_dir():
                raise QuarantineError(
                    f"canonical submission root {final} is not a directory"
                )
            _copy_tree(
                final, raw, set(), max_files=max_files,
                max_single_bytes=max_single_bytes,
                max_total_bytes=max_total_bytes, usage=usage,
            )
            exclusions = ()
        (raw / COLLECTOR_FILE).write_text(
            json.dumps({
                "legacy_layout": legacy_layout,
                "exclusions": list(exclusions),
                "file_count": usage["files"],
                "total_bytes": usage["bytes"],
            }),
            encoding="utf-8",
        )
    except Exception:
        shutil.rmtree(raw, ignore_errors=True)
        raise


# --------------------------------------------------------------------------- #
# quarantine + seal (raw -> clean)
# --------------------------------------------------------------------------- #

def _reject_unsafe_node(st: os.stat_result, rel: str) -> None:
    mode = st.st_mode
    if stat_mod.S_ISLNK(mode):
        raise QuarantineError(f"symlink rejected in submission: {rel}")
    if stat_mod.S_ISFIFO(mode):
        raise QuarantineError(f"FIFO rejected in submission: {rel}")
    if stat_mod.S_ISSOCK(mode):
        raise QuarantineError(f"socket rejected in submission: {rel}")
    if stat_mod.S_ISCHR(mode) or stat_mod.S_ISBLK(mode):
        raise QuarantineError(f"device node rejected in submission: {rel}")
    if stat_mod.S_ISREG(mode):
        if st.st_nlink > 1:
            raise QuarantineError(f"hardlink (nlink > 1) rejected in submission: {rel}")
        if mode & (stat_mod.S_ISUID | stat_mod.S_ISGID):
            raise QuarantineError(f"setuid/setgid rejected in submission: {rel}")


def _validate_declared_manifest_paths(path: Path) -> None:
    """A candidate-provided manifest.json is discarded, but never trusted: if it
    declares parent-traversing or absolute paths, the submission fails closed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return
    entries = data.get("files") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        declared = entry.get("path")
        if not isinstance(declared, str):
            continue
        posix = PurePosixPath(declared)
        if posix.is_absolute() or ".." in posix.parts:
            raise QuarantineError(
                f"manifest.json declares unsafe path {declared!r}"
            )


def _is_archive(rel: str) -> bool:
    low = rel.lower()
    return any(low.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def quarantine_submission(
    raw: Path,
    clean: Path,
    limits: QuarantineLimits,
) -> SubmissionSeal:
    """Validate and normalize ``raw`` into the sealed ``clean`` submission.

    Returns a ``SubmissionSeal``; the manifest digest is computed over the
    collected file records only (not the wall-clock timestamp), so identical
    inputs seal identically.
    """
    raw = Path(raw)
    clean = Path(clean)
    if clean.exists():
        raise QuarantineError(f"sealed destination already exists: {clean}")

    collector: dict[str, Any] = {}
    if (raw / COLLECTOR_FILE).is_file():
        collector = json.loads((raw / COLLECTOR_FILE).read_text(encoding="utf-8"))

    dirs_to_create: list[str] = []
    records: list[SubmissionFile] = []
    total_bytes = 0
    count = 0

    for root, dirnames, filenames in os.walk(raw, topdown=True, followlinks=False):
        rel_dir = os.path.relpath(root, raw)
        if rel_dir != ".":
            st = os.lstat(root)
            if stat_mod.S_ISLNK(st.st_mode):
                raise QuarantineError(f"symlink rejected in submission: {rel_dir}")
            dirs_to_create.append(PurePosixPath(rel_dir).as_posix())
        for name in dirnames:
            entry = Path(root) / name
            st = os.lstat(entry)
            if stat_mod.S_ISLNK(st.st_mode):
                raise QuarantineError(f"symlink rejected in submission: {name}")
        for name in filenames:
            if name == COLLECTOR_FILE:
                continue
            path = Path(root) / name
            rel = path.relative_to(raw).as_posix()
            st = os.lstat(path)
            _reject_unsafe_node(st, rel)

            if name == SEAL_MANIFEST:
                _validate_declared_manifest_paths(path)
                continue  # replaced by the trusted seal manifest
            if _is_archive(rel) and not limits.allow_archives:
                raise QuarantineError(f"archive rejected in submission: {rel}")

            size = st.st_size
            if size > limits.max_single_bytes:
                raise QuarantineError(
                    f"single-file limit exceeded: {rel} ({size} bytes)"
                )
            total_bytes += size
            if total_bytes > limits.max_total_bytes:
                raise QuarantineError(
                    f"total submission limit exceeded ({total_bytes} bytes)"
                )
            count += 1
            if count > limits.max_files:
                raise QuarantineError(
                    f"file count exceeds limit ({limits.max_files})"
                )

            records.append(
                SubmissionFile(
                    path=rel,
                    size=size,
                    sha256=sha256_file(path),
                    mode=0o644,
                )
            )

    records.sort(key=lambda record: record.path)

    # Write into a staging dir, then atomically rename into place.
    staging = clean.parent / (clean.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    for dir_rel in sorted(dirs_to_create):
        target_dir = staging / dir_rel
        target_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(target_dir, 0o755)

    for record in records:
        target = staging / record.path
        target.parent.mkdir(parents=True, exist_ok=True)
        # Re-open without following symlinks and verify the bytes after the
        # copy.  This closes the lstat/hash -> copy race at the seal boundary.
        copied = _copy_regular(raw / record.path, target, limits.max_single_bytes)
        if copied != record.size or target.stat().st_size != record.size or sha256_file(target) != record.sha256:
            raise QuarantineError(f"submission changed during sealing: {record.path}")
        os.chmod(target, 0o644)

    sealed_at = datetime.now(timezone.utc)
    manifest_payload = _build_manifest(
        records=records,
        total_bytes=total_bytes,
        sealed_at=sealed_at,
        legacy_layout=bool(collector.get("legacy_layout", False)),
        exclusions=tuple(collector.get("exclusions", []) or []),
    )
    _validate_manifest(manifest_payload)

    manifest_file = staging / SEAL_MANIFEST
    manifest_file.write_text(manifest_payload, encoding="utf-8")
    with manifest_file.open("ab") as handle:
        os.fsync(handle.fileno())

    os.rename(staging, clean)

    manifest_digest = "sha256:" + digest_bytes(
        "".join(
            f"{record.sha256}  {record.size}  {record.path}\n" for record in records
        ).encode("utf-8")
    )
    return SubmissionSeal(
        manifest_digest=manifest_digest,
        file_count=len(records),
        total_bytes=total_bytes,
        sealed_at=sealed_at,
        legacy_layout=bool(collector.get("legacy_layout", False)),
        exclusions=tuple(collector.get("exclusions", []) or []),
    )


def _build_manifest(
    records: list[SubmissionFile],
    total_bytes: int,
    sealed_at: datetime,
    legacy_layout: bool,
    exclusions: tuple[str, ...],
) -> str:
    payload = {
        "schema_version": "1.0",
        "legacy_layout": legacy_layout,
        "exclusions": list(exclusions),
        "files": [
            {"path": record.path, "size": record.size, "sha256": record.sha256, "mode": record.mode}
            for record in records
        ],
        "total_bytes": total_bytes,
        "sealed_at": sealed_at.isoformat(),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _validate_manifest(payload: str) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(json.loads(payload)),
        key=lambda e: list(e.path),
    )
    if errors:
        raise QuarantineError(
            f"sealed manifest violates submission.schema.json: {errors[0].message}"
        )
