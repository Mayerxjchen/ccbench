#!/usr/bin/env python3
"""Content-addressed evidence store with dual (primary + replica) backends (plan Task 4).

Objects are addressed by the SHA-256 of their bytes under ``sha256/<first-two>/<full-sha256>.tar.zst``.
Writes are atomic: temp object in the destination dir, fsync, then rename. Objects are immutable:
identical bytes are reused; different bytes under an existing digest raise ``ValueError``.

A formal manifest is only emitted after both primary and replica stores independently return
the expected digest and size (the caller checks ``verify`` before emitting the manifest).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, unquote

from scripts.evidence.bundle import extract_bundle

OBJECT_SUFFIX = ".tar.zst"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class StoredObject:
    sha256: str
    size_bytes: int
    primary_uri: str
    replica_uri: str
    primary_version: str
    replica_version: str


class FileBackend:
    """``cas+file://`` backend: a directory addressed by content digest."""

    def __init__(self, uri: str) -> None:
        parsed = urlparse(uri)
        if parsed.scheme != "cas+file":
            raise ValueError(f"unsupported store scheme: {parsed.scheme!r} (want cas+file)")
        path = unquote(parsed.path)
        if path.endswith(OBJECT_SUFFIX):
            # Full object URI (.../sha256/<first-two>/<digest>.tar.zst): derive the
            # store base so a manifest's bundle URIs can construct the store directly.
            path = str(Path(path).parents[2])
        self.root = Path(path)

    def _object_path(self, digest: str) -> Path:
        return self.root / "sha256" / digest[:2] / f"{digest}{OBJECT_SUFFIX}"

    def exists(self, digest: str) -> bool:
        return self._object_path(digest).is_file()

    def put(self, data: bytes, digest: str) -> tuple[str, int]:
        """Write bytes under digest, atomically. Returns (uri, size_bytes).

        Raises ``ValueError`` if the digest does not match the data or the slot
        is already occupied by different bytes.
        """
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"invalid digest: {digest!r}")
        if _sha256_bytes(data) != digest:
            raise ValueError(f"digest mismatch: bytes hash to {_sha256_bytes(data)}, expected {digest}")
        target = self._object_path(digest)
        if target.exists():
            existing = target.read_bytes()
            if existing != data:
                raise ValueError(f"object exists with different bytes under digest {digest}")
            return self.uri_for(digest), len(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / f".tmp-{uuid.uuid4().hex}"
        try:
            with tmp.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()
        return self.uri_for(digest), len(data)

    def get(self, digest: str, destination: Path) -> list[dict]:
        """Restore the object into ``destination``; recomputes per-file hashes."""
        target = self._object_path(digest)
        if not target.exists():
            raise FileNotFoundError(f"evidence object unavailable: {digest}")
        destination.mkdir(parents=True, exist_ok=True)
        return extract_bundle(target, destination, expected_sha256=digest)

    def verify(self, digest: str) -> tuple[str, int] | None:
        target = self._object_path(digest)
        if not target.exists():
            return None
        size = target.stat().st_size
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError(f"object corrupt: {target} hashes to {actual}, expected {digest}")
        return self.uri_for(digest), size

    def uri_for(self, digest: str) -> str:
        target = self._object_path(digest)
        return f"cas+file://{target}"


class EvidenceStore:
    """Dual-location content-addressed store. Both writes must succeed."""

    def __init__(self, primary_uri: str, replica_uri: str) -> None:
        self.primary = FileBackend(primary_uri)
        self.replica = FileBackend(replica_uri)

    def put(self, data: bytes, digest: str) -> StoredObject:
        pri = self.primary.put(data, digest)
        rep = self.replica.put(data, digest)
        return self._object(pri, rep, digest, len(data))

    def put_primary(self, data: bytes, digest: str) -> StoredObject:
        """Write + read-back verify primary only (replica still pending)."""
        pri = self.primary.put(data, digest)
        verified = self.primary.verify(digest)
        if verified is None or verified[1] != len(data):
            raise ValueError(f"primary read-back failed for {digest}")
        return self._object(pri, None, digest, len(data))

    def put_replica(self, data: bytes, digest: str) -> StoredObject:
        """Write + read-back verify replica only (primary assumed stored)."""
        rep = self.replica.put(data, digest)
        verified = self.replica.verify(digest)
        if verified is None or verified[1] != len(data):
            raise ValueError(f"replica read-back failed for {digest}")
        return self._object(None, rep, digest, len(data))

    def _object(self, pri: tuple[str, int] | None, rep: tuple[str, int] | None,
                digest: str, size: int) -> StoredObject:
        return StoredObject(
            sha256=digest,
            size_bytes=size,
            primary_uri=pri[0] if pri else "",
            replica_uri=rep[0] if rep else "",
            primary_version=pri[1] if pri else "",
            replica_version=rep[1] if rep else "",
        )

    def get(self, digest: str, destination: Path | str) -> list[dict]:
        """Restore from primary; falls back to replica. Fails closed if neither has it."""
        dest = Path(destination)
        for backend in (self.primary, self.replica):
            if backend.exists(digest):
                return backend.get(digest, dest)
        raise FileNotFoundError(f"evidence object unavailable in primary and replica: {digest}")

    def verify(self, digest: str) -> StoredObject:
        """Return a StoredObject only if both stores independently return digest + size."""
        pri = self.primary.verify(digest)
        if pri is None:
            raise FileNotFoundError(f"primary store missing object: {digest}")
        rep = self.replica.verify(digest)
        if rep is None:
            raise FileNotFoundError(f"replica store missing object: {digest}")
        if pri[1] != rep[1]:
            raise ValueError(f"size divergence between stores for {digest}: {pri[1]} != {rep[1]}")
        return StoredObject(
            sha256=digest,
            size_bytes=pri[1],
            primary_uri=pri[0],
            replica_uri=rep[0],
            primary_version=pri[1],
            replica_version=rep[1],
        )


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    put = sub.add_parser("put")
    put.add_argument("--primary", required=True)
    put.add_argument("--replica", required=True)
    put.add_argument("--digest", required=True)
    put.add_argument("bundle", type=Path)

    get = sub.add_parser("get")
    get.add_argument("--primary", required=True)
    get.add_argument("--replica", required=True)
    get.add_argument("--digest", required=True)
    get.add_argument("--dest", required=True, type=Path)

    verify = sub.add_parser("verify")
    verify.add_argument("--primary", required=True)
    verify.add_argument("--replica", required=True)
    verify.add_argument("--digest", required=True)

    args = ap.parse_args(argv)
    store = EvidenceStore(args.primary, args.replica)
    if args.cmd == "put":
        data = args.bundle.read_bytes()
        obj = store.put(data, args.digest)
        print(f"put ok sha256={obj.sha256} size={obj.size_bytes}")
        print(f"  primary: {obj.primary_uri}")
        print(f"  replica: {obj.replica_uri}")
    elif args.cmd == "get":
        files = store.get(args.digest, args.dest)
        print(f"restored {len(files)} files into {args.dest}")
    elif args.cmd == "verify":
        obj = store.verify(args.digest)
        print(f"verify ok sha256={obj.sha256} size={obj.size_bytes}")
        print(f"  primary: {obj.primary_uri}")
        print(f"  replica: {obj.replica_uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
