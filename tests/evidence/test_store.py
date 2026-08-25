"""Content-addressed evidence store (plan Task 4).

Immutability: identical bytes reuse the existing object; different bytes under
an existing digest fail. Retrieval into a fresh directory recomputes object and
per-file hashes. A formal manifest is only emitted after both primary and
replica stores independently return the expected digest and size.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.evidence.bundle import build_bundle
from scripts.evidence.store import EvidenceStore

ROOT = Path(__file__).resolve().parents[2]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_bundle(tmp_path: Path) -> tuple[Path, str, int]:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "result.json").write_text('{"profile":"paper"}')
    files = [{"path": "result.json", "role": "scoring_required",
              "size_bytes": (ws / "result.json").stat().st_size,
              "sha256": _sha((ws / "result.json").read_bytes())}]
    bundle_path = tmp_path / "b.tar.zst"
    descriptor = build_bundle(files, bundle_path, base_dir=ws)
    return bundle_path, descriptor.sha256, descriptor.size_bytes


def test_put_then_get_roundtrip(tmp_path: Path) -> None:
    primary = tmp_path / "store" / "primary"
    replica = tmp_path / "store" / "replica"
    store = EvidenceStore(f"cas+file://{primary}", f"cas+file://{replica}")
    bundle_path, digest, size = _make_bundle(tmp_path)
    data = bundle_path.read_bytes()

    stored = store.put(data, digest)
    assert stored.primary_uri
    assert stored.replica_uri
    assert stored.sha256 == digest
    assert stored.size_bytes == size
    assert (primary / "sha256" / digest[:2] / f"{digest}.tar.zst").is_file()
    assert (replica / "sha256" / digest[:2] / f"{digest}.tar.zst").is_file()

    dest = tmp_path / "fresh"
    files = store.get(digest, dest)
    assert (dest / "result.json").read_text() == '{"profile":"paper"}'
    assert files[0]["sha256"] == _sha((dest / "result.json").read_bytes())


def test_put_identical_bytes_is_idempotent(tmp_path: Path) -> None:
    primary = tmp_path / "p"
    replica = tmp_path / "r"
    store = EvidenceStore(f"cas+file://{primary}", f"cas+file://{replica}")
    bundle_path, digest, _ = _make_bundle(tmp_path)
    data = bundle_path.read_bytes()

    first = store.put(data, digest)
    before = sorted(p.relative_to(primary).as_posix() for p in primary.rglob("*") if p.is_file())
    second = store.put(data, digest)
    after = sorted(p.relative_to(primary).as_posix() for p in primary.rglob("*") if p.is_file())
    assert before == after
    assert first.sha256 == second.sha256


def test_put_different_bytes_under_existing_digest_fails(tmp_path: Path) -> None:
    primary = tmp_path / "p"
    replica = tmp_path / "r"
    store = EvidenceStore(f"cas+file://{primary}", f"cas+file://{replica}")
    bundle_path, digest, _ = _make_bundle(tmp_path)
    store.put(bundle_path.read_bytes(), digest)

    with pytest.raises(ValueError, match="digest"):
        store.put(b"different-bytes", digest)


def test_verify_reports_both_locations(tmp_path: Path) -> None:
    primary = tmp_path / "p"
    replica = tmp_path / "r"
    store = EvidenceStore(f"cas+file://{primary}", f"cas+file://{replica}")
    bundle_path, digest, size = _make_bundle(tmp_path)
    store.put(bundle_path.read_bytes(), digest)
    obj = store.verify(digest)
    assert obj.sha256 == digest
    assert obj.size_bytes == size


def test_missing_object_reports_unavailable(tmp_path: Path) -> None:
    store = EvidenceStore(f"cas+file://{tmp_path / 'p'}", f"cas+file://{tmp_path / 'r'}")
    with pytest.raises(FileNotFoundError):
        store.get("11" * 32, tmp_path / "out")
