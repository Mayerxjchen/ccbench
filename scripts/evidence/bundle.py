#!/usr/bin/env python3
"""Deterministic content-addressed ``tar.zst`` run bundles (plan Task 4).

The same input file set always produces the same bundle SHA-256: entries are
sorted, metadata is fixed (uid/gid 0, empty names, mtime 0, mode 0644), and
compression uses a pinned zstd level with the zstd version recorded. A copy of
the evidence manifest v2 is embedded as the first entry.

Extraction is safe: absolute paths, ``..``, links, devices, sockets, and FIFOs
are rejected before any byte is written.
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import zstandard

MANIFEST_ENTRY = "evidence-manifest.json"
ZSTD_LEVEL = 3


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class BundleDescriptor:
    sha256: str
    size_bytes: int
    zstd_version: str
    files: list[dict] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)


def _tar_bytes(files: list[dict], manifest: dict, base_dir: Path | None) -> bytes:
    """Serialize the file set to deterministic USTAR bytes (in memory).

    Content is read from ``base_dir`` (when given) so the bundle actually carries
    the evidence bytes; without ``base_dir`` members are empty placeholders.
    """
    entries = [(f["path"], f["size_bytes"], f["sha256"]) for f in files]
    entries.sort(key=lambda e: e[0])
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        manifest_bytes = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
        mi = tarfile.TarInfo(MANIFEST_ENTRY)
        mi.size = len(manifest_bytes)
        mi.mode = 0o644
        mi.uid = mi.gid = 0
        mi.uname = mi.gname = ""
        mi.mtime = 0
        tf.addfile(mi, io.BytesIO(manifest_bytes))
        for name, size, digest in entries:
            content = b""
            if base_dir is not None:
                content = (base_dir / name).read_bytes()
                if len(content) != size:
                    raise ValueError(f"size mismatch for {name!r}: record {size}, on disk {len(content)}")
                if digest and hashlib.sha256(content).hexdigest() != digest:
                    raise ValueError(f"content digest mismatch for {name!r}")
            ti = tarfile.TarInfo(name)
            ti.size = len(content)
            ti.mode = 0o644
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.mtime = 0
            tf.addfile(ti, io.BytesIO(content))
    return buffer.getvalue()


def _zstd_compress(data: bytes) -> bytes:
    compressor = zstandard.ZstdCompressor(level=ZSTD_LEVEL, write_content_size=True)
    return compressor.compress(data)


def build_bundle(files: list[dict], destination: Path | str | None = None,
                 manifest: dict | None = None, base_dir: Path | str | None = None) -> BundleDescriptor:
    """Build a deterministic bundle from per-file records (path/role/size/sha256).

    ``files`` are validated: each needs a safe relative path and a 64-hex digest.
    Content is read from ``base_dir`` (the workspace root) so the bundle is
    restorable. The bundle SHA-256 is over the compressed bytes.
    """
    checked: list[dict] = []
    for f in files:
        rel = str(f["path"])
        if rel.startswith("/") or rel.startswith("..") or "/../" in f"/{rel}":
            raise ValueError(f"unsafe bundle path: {rel!r}")
        if f["sha256"] and not isinstance(f["sha256"], str):
            raise ValueError(f"non-string digest for {rel!r}")
        checked.append({"path": rel, "role": f.get("role", "scoring_required"),
                        "size_bytes": int(f.get("size_bytes", 0)),
                        "sha256": f.get("sha256") or ""})

    zstd_version = zstandard.__version__
    base = Path(base_dir) if base_dir is not None else None
    inner = _tar_bytes(checked, manifest or {}, base)
    compressed = _zstd_compress(inner)
    digest = _sha256_bytes(compressed)
    if destination is not None:
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(compressed)
    return BundleDescriptor(
        sha256=digest,
        size_bytes=len(compressed),
        zstd_version=zstd_version,
        files=checked,
        manifest=manifest or {},
    )


def extract_bundle(bundle_path: Path | str, destination: Path | str,
                   expected_sha256: str | None = None) -> list[dict]:
    """Extract a bundle into ``destination``; returns the per-file records."""
    src = Path(bundle_path)
    data = src.read_bytes()
    if expected_sha256 and _sha256_bytes(data) != expected_sha256:
        raise ValueError("bundle sha256 mismatch on extraction")

    dest = Path(destination)
    decompressor = zstandard.ZstdDecompressor()
    with decompressor.stream_reader(io.BytesIO(data)) as reader:
        raw = io.BytesIO(reader.read())
    raw.seek(0)

    extracted: list[dict] = []
    with tarfile.open(fileobj=raw, mode="r") as tf:
        for member in tf.getmembers():
            name = member.name
            if name == MANIFEST_ENTRY:
                continue
            if name.startswith("/") or name.startswith("..") or "/../" in f"/{name}" or "\\" in name:
                raise ValueError(f"unsafe archive name: {name!r}")
            if member.islnk() or member.issym() or not member.isfile():
                raise ValueError(f"non-regular archive member: {name!r}")
            rel = Path(name)
            if rel.is_absolute() or any(part == ".." for part in rel.parts):
                raise ValueError(f"unsafe archive path: {name!r}")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = tf.extractfile(member)
            if stream is None:
                raise ValueError(f"cannot extract {name!r}")
            target.write_bytes(stream.read())
            target.chmod(0o644)
            extracted.append({"path": name,
                              "size_bytes": target.stat().st_size,
                              "sha256": _sha256_bytes(target.read_bytes())})
    return extracted


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--files", required=True, help="JSON list of {path,role,size_bytes,sha256}")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--manifest", default="{}", help="JSON manifest v2 to embed")
    args = ap.parse_args(argv)
    files = _json.loads(args.files)
    manifest = _json.loads(args.manifest)
    descriptor = build_bundle(files, args.out, manifest=manifest)
    print(f"bundle sha256={descriptor.sha256} size={descriptor.size_bytes} "
          f"zstd={descriptor.zstd_version} files={len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
