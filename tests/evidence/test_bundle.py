"""Deterministic content-addressed bundles (plan Task 4).

The same input bytes produce the same tar.zst SHA-256 regardless of mtime,
ownership, or source order. Extraction is safe: no absolute paths, no parent
traversal, no links, no special files.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from pathlib import Path

import pytest
import zstandard

from scripts.evidence.bundle import build_bundle, extract_bundle

ROOT = Path(__file__).resolve().parents[2]
SHA256 = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731


def _open_tar_zst(path: Path) -> tarfile.TarFile:
    """tarfile cannot read zstd directly; decompress first."""
    dctx = zstandard.ZstdDecompressor()
    data = path.read_bytes()
    with dctx.stream_reader(io.BytesIO(data)) as reader:
        return tarfile.open(fileobj=io.BytesIO(reader.read()), mode="r")


def _files(ws: Path) -> list[dict]:
    # deterministic per-file records (path, role, size_bytes, sha256)
    out = []
    for path in sorted(ws.rglob("*")):
        if path.is_file():
            out.append({"path": path.relative_to(ws).as_posix(),
                        "role": "scoring_required",
                        "size_bytes": path.stat().st_size,
                        "sha256": SHA256(path.read_bytes())})
    return out


def test_bundle_digest_is_independent_of_mtime_and_order(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "md").mkdir(parents=True)
    (ws / "md" / "production_100K.traj").write_text("traj-a")
    (ws / "md" / "production_200K.traj").write_text("traj-b")
    (ws / "result.json").write_text('{"profile":"paper"}')

    first = build_bundle(_files(ws), tmp_path / "bundle1.tar.zst", base_dir=ws)
    os.utime(ws / "md" / "production_100K.traj", (1, 1))
    os.utime(ws / "result.json", (2, 2))
    files_reversed = list(reversed(_files(ws)))
    second = build_bundle(files_reversed, tmp_path / "bundle2.tar.zst", base_dir=ws)

    assert first.sha256 == second.sha256
    assert first.size_bytes == second.size_bytes


def test_bundle_changes_when_content_changes(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a").write_text("one")
    first = build_bundle(_files(ws), tmp_path / "b1.tar.zst", base_dir=ws)
    (ws / "a").write_text("two")
    second = build_bundle(_files(ws), tmp_path / "b2.tar.zst", base_dir=ws)
    assert first.sha256 != second.sha256


def test_bundle_embeds_manifest_copy(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "result.json").write_text('{"x":1}')
    descriptor = build_bundle(_files(ws), tmp_path / "b.tar.zst",
                              manifest={"schema_version": "2.0", "case": "032"}, base_dir=ws)
    assert descriptor.manifest["case"] == "032"
    with _open_tar_zst(tmp_path / "b.tar.zst") as tf:
        names = tf.getnames()
        assert "evidence-manifest.json" in names
        m = tf.extractfile("evidence-manifest.json")
        import json
        assert json.loads(m.read())["case"] == "032"


def test_extraction_matches_per_file_digests(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    (ws / "md").mkdir(parents=True)
    (ws / "md" / "a.traj").write_text("aaaa")
    (ws / "result.json").write_text('{"x":1}')
    descriptor = build_bundle(_files(ws), tmp_path / "b.tar.zst", base_dir=ws)

    dest = tmp_path / "restored"
    extracted = extract_bundle(tmp_path / "b.tar.zst", dest)
    assert descriptor.sha256 == SHA256((tmp_path / "b.tar.zst").read_bytes())
    by_path = {f["path"]: f["sha256"] for f in extracted}
    for f in _files(ws):
        assert by_path[f["path"]] == f["sha256"]
    assert (dest / "md" / "a.traj").read_text() == "aaaa"
    assert (dest / "result.json").is_file()


def test_extracted_entries_have_fixed_metadata(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "result.json").write_text("{}")
    build_bundle(_files(ws), tmp_path / "b.tar.zst", base_dir=ws)
    with _open_tar_zst(tmp_path / "b.tar.zst") as tf:
        for member in tf.getmembers():
            if member.name == "evidence-manifest.json":
                continue
            assert member.uid == 0
            assert member.gid == 0
            assert member.mtime == 0
            assert member.mode & 0o777 == 0o644


def test_unsafe_archive_names_are_rejected(tmp_path: Path) -> None:
    # a hand-crafted tar with absolute + traversal + symlink members
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as tf:
        ti = tarfile.TarInfo("/etc/passwd")
        ti.size = 4
        payload.seek(tf.offset)
        tf.addfile(ti, io.BytesIO(b"root"))
        ti2 = tarfile.TarInfo("../escape.txt")
        ti2.size = 4
        tf.addfile(ti2, io.BytesIO(b"data"))
        ti3 = tarfile.TarInfo("link")
        ti3.type = tarfile.SYMTYPE
        ti3.linkname = "/etc/passwd"
        tf.addfile(ti3)
    evil = tmp_path / "evil.tar.zst"
    evil.write_bytes(zstandard.ZstdCompressor().compress(payload.getvalue()))
    dest = tmp_path / "out"
    dest.mkdir()
    with pytest.raises(Exception):
        extract_bundle(evil, dest)
    assert not (dest / "etc").exists()
