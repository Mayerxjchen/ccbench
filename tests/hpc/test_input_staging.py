"""Input staging seals an immutable snapshot before any scheduler contact.

``seal_inputs`` reopens every declared input without following links, rechecks
type, size and digest, copies bytes into a content-addressed staging area, and
returns a manifest. A Candidate edit between validation and staging must fail
rather than change submitted bytes.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from dftworld_bench.hpc.request import ExecutionRequestV2
from dftworld_bench.hpc.staging import InputManifest, StagingError, seal_inputs

DIGEST = "img@sha256:" + "a" * 64


def _make_request(tmp_path, body: bytes):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    source = workspace / "input.inp"
    source.write_bytes(body)
    request = (
        ExecutionRequestV2.from_dict(
            {
                "schema_version": 2,
                "operation_id": "cp2k-round-01",
                "attempt": 1,
                "runtime": DIGEST,
                "command": ["/usr/local/bin/cp2k", "-i", "input.inp"],
                "resources": {
                    "cpus": 1,
                    "memory_gb": 1,
                    "gpus": 0,
                    "walltime_minutes": 5,
                },
                "inputs": [
                    {
                        "path": "input.inp",
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "size_bytes": len(body),
                    }
                ],
                "outputs": ["out/"],
            }
        )
        .with_input(
            "input.inp",
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
        )
        .with_attempt(1)
    )
    return request, workspace


def test_seal_copies_content_addressed_snapshot(tmp_path):
    body = b"&GLOBAL\n  RUN_TYPE ENERGY\n&END GLOBAL\n"
    request, workspace = _make_request(tmp_path, body)
    manifest = seal_inputs(request, workspace, tmp_path / "sealed")
    assert isinstance(manifest, InputManifest)
    staged = manifest.staged_path("input.inp")
    assert staged.read_bytes() == body
    assert staged.parent != workspace


def test_input_swap_after_validation_is_rejected(tmp_path):
    body = b"&GLOBAL\n  RUN_TYPE ENERGY\n&END GLOBAL\n"
    request, workspace = _make_request(tmp_path, body)
    (workspace / "input.inp").write_bytes(b"changed after validation")
    # A swapped file trips whichever identity check sees it first: size or
    # digest. Both are fail-closed rejections of the same TOCTOU attempt.
    with pytest.raises(StagingError, match="digest|size"):
        seal_inputs(request, workspace, tmp_path / "sealed")


def test_symlinked_input_is_rejected_without_following(tmp_path):
    body = b"real bytes"
    request, workspace = _make_request(tmp_path, body)
    # The link target has exactly the declared bytes: digest checks alone
    # would pass, so only no-follow traversal can reject this input.
    outside = tmp_path / "outside.txt"
    outside.write_bytes(body)
    link = workspace / "input.inp"
    link.unlink()
    os.symlink(outside, link)
    with pytest.raises(StagingError, match="symlink"):
        seal_inputs(request, workspace, tmp_path / "sealed")


def test_size_mismatch_is_rejected(tmp_path):
    body = b"exact size"
    request, workspace = _make_request(tmp_path, body)
    bumped = request.with_input(
        "input.inp",
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body) + 5,
    )
    with pytest.raises(StagingError, match="size"):
        seal_inputs(bumped, workspace, tmp_path / "sealed")


def test_non_regular_file_input_is_rejected(tmp_path):
    body = b"payload"
    request, workspace = _make_request(tmp_path, body)
    fifo = workspace / "input.inp"
    fifo.unlink()
    os.mkfifo(fifo)
    with pytest.raises(StagingError):
        seal_inputs(request, workspace, tmp_path / "sealed")


def test_undeclared_workspace_file_is_not_staged(tmp_path):
    body = b"declared only"
    request, workspace = _make_request(tmp_path, body)
    (workspace / "stowaway.txt").write_bytes(b"not declared")
    manifest = seal_inputs(request, workspace, tmp_path / "sealed")
    all_paths = {entry.path for entry in manifest.entries}
    assert all_paths == {"input.inp"}
