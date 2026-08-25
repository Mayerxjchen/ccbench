"""Artifact quarantine: hostile-filesystem rejection + deterministic sealing."""

from __future__ import annotations

import json
import os
import socket
import stat as stat_mod
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dftworld_bench.core.quarantine import (
    QuarantineError,
    QuarantineLimits,
    collect_raw_submission,
    quarantine_submission,
)

NOW = datetime(2026, 8, 18, tzinfo=timezone.utc)
LIMITS = QuarantineLimits(
    max_files=10,
    max_single_bytes=1024 * 1024,
    max_total_bytes=2 * 1024 * 1024,
    allow_archives=False,
)


def _normal_raw(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "out.txt").write_text("result\n", encoding="utf-8")
    (raw / "sub").mkdir()
    (raw / "sub" / "data.npy").write_bytes(b"\x00\x01\x02")
    return raw


def test_quarantine_seals_normal_tree(tmp_path):
    raw = _normal_raw(tmp_path)
    seal = quarantine_submission(raw, tmp_path / "clean", LIMITS)
    clean = tmp_path / "clean"
    assert (clean / "out.txt").read_text() == "result\n"
    assert (clean / "sub" / "data.npy").read_bytes() == b"\x00\x01\x02"
    assert seal.file_count == 2
    assert seal.total_bytes == 7 + 3
    manifest = json.loads((clean / "manifest.json").read_text(encoding="utf-8"))
    assert {f["path"] for f in manifest["files"]} == {"out.txt", "sub/data.npy"}


def test_quarantine_rejects_fifo(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    os.mkfifo(raw / "pipe")
    with pytest.raises(QuarantineError, match="FIFO"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


def test_quarantine_rejects_symlink(tmp_path):
    raw = _normal_raw(tmp_path)
    (raw / "link").symlink_to("../secret")
    with pytest.raises(QuarantineError, match="symlink"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


def test_quarantine_rejects_hardlink(tmp_path):
    raw = _normal_raw(tmp_path)
    os.link(raw / "out.txt", raw / "hardcopy")
    with pytest.raises(QuarantineError, match="hardlink"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


def test_quarantine_rejects_socket(tmp_path):
    raw = _normal_raw(tmp_path)
    # macOS AF_UNIX paths cap at ~104 chars; bind at a short path, then rename
    # the socket node into the raw submission.
    short = f"/tmp/q_sock_{os.getpid()}"
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(short)
    finally:
        sock.close()
    os.rename(short, raw / "sock")
    with pytest.raises(QuarantineError, match="socket"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


def test_quarantine_rejects_device(tmp_path):
    raw = _normal_raw(tmp_path)
    try:
        os.mknod(raw / "dev", 0o600, stat_mod.S_IFCHR)
    except PermissionError:
        pytest.skip("cannot create device node without privileges")
    with pytest.raises(QuarantineError, match="device"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


def test_quarantine_rejects_setuid(tmp_path):
    raw = _normal_raw(tmp_path)
    os.chmod(raw / "out.txt", 0o4755)
    with pytest.raises(QuarantineError, match="setuid"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


def test_quarantine_rejects_parent_traversal_in_manifest(tmp_path):
    raw = _normal_raw(tmp_path)
    (raw / "manifest.json").write_text(
        json.dumps({"files": [{"path": "../../etc/passwd", "size": 0, "sha256": "0"}]}),
        encoding="utf-8",
    )
    with pytest.raises(QuarantineError, match="manifest"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


def test_quarantine_rejects_excessive_file_count(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for i in range(LIMITS.max_files + 1):
        (raw / f"f{i}.txt").write_text("x")
    with pytest.raises(QuarantineError, match="file count"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


def test_quarantine_rejects_single_file_limit(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "big.bin").write_bytes(b"\x00" * (LIMITS.max_single_bytes + 1))
    with pytest.raises(QuarantineError, match="single-file"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


def test_quarantine_rejects_total_limit(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for i in range(3):
        (raw / f"chunk{i}.bin").write_bytes(b"\x00" * (LIMITS.max_total_bytes // 2))
    with pytest.raises(QuarantineError, match="total"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


@pytest.mark.parametrize(
    "name",
    ["a.zip", "b.tar", "c.gz", "d.tgz", "e.tar.gz", "f.bz2", "g.tar.bz2",
     "h.xz", "i.tar.xz", "j.7z", "k.rar", "l.zst"],
)
def test_quarantine_rejects_every_archive_when_disallowed(tmp_path, name):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / name).write_bytes(b"PK\x03\x04")
    with pytest.raises(QuarantineError, match="archive"):
        quarantine_submission(raw, tmp_path / "clean", LIMITS)


def test_quarantine_normalizes_permissions(tmp_path):
    raw = _normal_raw(tmp_path)
    os.chmod(raw / "out.txt", 0o777)
    os.chmod(raw / "sub", 0o700)
    seal = quarantine_submission(raw, tmp_path / "clean", LIMITS)
    manifest = json.loads((tmp_path / "clean" / "manifest.json").read_text(encoding="utf-8"))
    modes = {f["path"]: f["mode"] for f in manifest["files"]}
    assert modes["out.txt"] == 0o644
    clean_sub_mode = os.stat(tmp_path / "clean" / "sub").st_mode & 0o777
    assert clean_sub_mode == 0o755


def test_seal_is_deterministic_and_atomic(tmp_path):
    raw = _normal_raw(tmp_path)
    first = quarantine_submission(raw, tmp_path / "clean-a", LIMITS)
    second = quarantine_submission(raw, tmp_path / "clean-b", LIMITS)
    assert first.manifest_digest == second.manifest_digest
    # clean path must not exist before sealing (no partial writes at final path)
    assert not (tmp_path / "clean-a" / "manifest.json").is_symlink()


def test_collect_canonical_final_only(tmp_path):
    ws = tmp_path / "ws"
    (ws / "final").mkdir(parents=True)
    (ws / "final" / "out.txt").write_text("ok")
    (ws / "stray.bin").write_text("leak")
    raw = tmp_path / "raw"
    collect_raw_submission(ws, "final", raw, legacy_layout=False)
    assert (raw / "out.txt").read_text() == "ok"
    assert not (raw / "stray.bin").exists()


def test_collect_legacy_excludes_runtime_names(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "result.txt").write_text("ok")
    for name in (".venv", ".skills", "_dftworld_tests", "tests", "reference",
                 "solution", ".pytest_cache", "logs"):
        (ws / name).mkdir(parents=True, exist_ok=True)
        (ws / name / "x").write_text("runtime")
    raw = tmp_path / "raw"
    collect_raw_submission(ws, ".", raw, legacy_layout=True)
    assert (raw / "result.txt").read_text() == "ok"
    for name in (".venv", ".skills", "_dftworld_tests", "tests", "reference",
                 "solution", ".pytest_cache", "logs"):
        assert not (raw / name).exists(), name
    collector = json.loads((raw / "_collector.json").read_text(encoding="utf-8"))
    assert collector["legacy_layout"] is True
    assert "tests" in collector["exclusions"]


# --------------------------------------------------------------------------- #
# source-safe collection (Task 13): unsafe nodes rejected at lstat, before any
# byte copy — a symlink in the frozen workspace is Candidate-origin escape
# material and must never be copied into the raw host tree.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("broken", [False, True])
def test_collector_rejects_file_symlink(tmp_path, broken):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target"
    if not broken:
        target.write_text("bytes")
    (workspace / "alias").symlink_to(target.name)
    with pytest.raises(QuarantineError, match="symlink"):
        collect_raw_submission(workspace, ".", tmp_path / "raw", True)


def test_collector_rejects_symlinked_directory(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "real").mkdir(parents=True)
    (workspace / "dirlink").symlink_to("real", target_is_directory=True)
    with pytest.raises(QuarantineError, match="symlink"):
        collect_raw_submission(workspace, ".", tmp_path / "raw", True)


def test_collector_rejects_031_checkpoint_symlinks(tmp_path):
    """031 checkpoint family (model.ckpt.{meta,index,data}) broke collection
    before lstat-safe copying: the symlinks are a Candidate-origin escape risk
    and must be rejected at collection — never byte-copied into the raw tree."""
    ws = tmp_path / "ws"
    (ws / "final").mkdir(parents=True)
    (ws / "final" / "checkpoint").write_text('model_checkpoint_path: "model.ckpt"\n')
    for suffix in ("meta", "index", "data"):
        (ws / "final" / f"model.ckpt.{suffix}").symlink_to("checkpoint")
    with pytest.raises(QuarantineError, match="symlink"):
        collect_raw_submission(ws, "final", tmp_path / "raw", legacy_layout=False)
    raw = tmp_path / "raw"
    assert not list(raw.rglob("*")) if raw.exists() else True
