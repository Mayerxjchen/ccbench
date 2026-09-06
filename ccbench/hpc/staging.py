"""Input staging: seal an immutable snapshot before any scheduler contact.

``seal_inputs`` reopens every declared input with ``O_NOFOLLOW``, rechecks
type, size and digest against the request, copies bytes into a
content-addressed staging area, and fsyncs before returning the manifest. A
Candidate edit between validation and staging fails closed instead of
changing submitted bytes.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ccbench.hpc.request import ExecutionRequestV2

_CHUNK = 1 << 20


class StagingError(Exception):
    """A declared input could not be sealed as declared."""


@dataclass(frozen=True)
class StagedInput:
    """One sealed input: declared identity plus staged content path."""

    path: str
    sha256: str
    size_bytes: int
    staged: Path


@dataclass(frozen=True)
class InputManifest:
    """Immutable record of the sealed input snapshot for one attempt."""

    operation_id: str
    attempt: int
    entries: tuple[StagedInput, ...]

    def staged_path(self, path: str) -> Path:
        for entry in self.entries:
            if entry.path == path:
                return entry.staged
        raise StagingError(f"path not in manifest: {path!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "attempt": self.attempt,
            "entries": [
                {
                    "path": e.path,
                    "sha256": e.sha256,
                    "size_bytes": e.size_bytes,
                    "staged": str(e.staged),
                }
                for e in self.entries
            ],
        }


def seal_inputs(
    request: ExecutionRequestV2,
    workspace: Path,
    staging_root: Path,
) -> InputManifest:
    """Copy every declared input into the staging area, verifying identity."""
    workspace = Path(workspace)
    staging_root = Path(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)
    sealed: list[StagedInput] = []
    for entry in request.input_entries:
        source = workspace / entry.path
        digest, size = _copy_verified(source, entry, staging_root)
        sealed.append(
            StagedInput(
                path=entry.path,
                sha256=digest,
                size_bytes=size,
                staged=staging_root / entry.sha256,
            )
        )
    return InputManifest(
        operation_id=request.operation_id,
        attempt=request.attempt,
        entries=tuple(sealed),
    )


def _copy_verified(
    source: Path,
    entry: Any,
    staging_root: Path,
) -> tuple[str, int]:
    """Open no-follow, require a regular file, verify size+digest, stage bytes."""
    try:
        # O_NONBLOCK so opening a FIFO never blocks waiting for a writer; for
        # regular files the flag is a no-op.
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise StagingError(f"input is a symlink: {entry.path!r}") from exc
        raise StagingError(f"cannot open input {entry.path!r}: {exc}") from exc
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode):
            kind = "fifo" if stat.S_ISFIFO(meta.st_mode) else "special file"
            raise StagingError(
                f"input {entry.path!r} is a {kind}, not a regular file"
            )
        if meta.st_size != entry.size_bytes:
            raise StagingError(
                f"input {entry.path!r} size mismatch: declared "
                f"{entry.size_bytes}, found {meta.st_size}"
            )
        hasher = hashlib.sha256()
        staged = staging_root / entry.sha256
        staged_tmp = staging_root / (entry.sha256 + ".tmp")
        with open(fd, "rb", closefd=False) as reader, open(staged_tmp, "wb") as out:
            while chunk := reader.read(_CHUNK):
                hasher.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        found = hasher.hexdigest()
        if found != entry.sha256:
            staged_tmp.unlink(missing_ok=True)
            raise StagingError(
                f"input {entry.path!r} digest mismatch: declared "
                f"{entry.sha256}, found {found}"
            )
        os.replace(staged_tmp, staged)
        return found, meta.st_size
    finally:
        os.close(fd)
