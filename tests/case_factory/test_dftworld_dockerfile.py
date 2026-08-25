"""Task 4 — Safe Dockerfile and .dockerignore generation.

The generated Dockerfile is exactly ``FROM <image>`` + ``COPY public/ /app/``.
Image references are rejected if they carry whitespace, directives, shell
metacharacters, paths, credentials or build arguments.  The generated
.dockerignore excludes every private / build-irrelevant path so hidden assets
never enter the build context.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dftworld_bench.case_factory.dftworld_target import (
    DftworldTargetAdapter,
    DOCKERIGNORE_ENTRIES,
    RenderError,
)

ADAPTER = DftworldTargetAdapter()

BASE = {
    "identity": {
        "case_id": "042-example",
        "task_name": "benchmark/042-example",
        "title": "Example",
        "description": "Example case",
        "case_version": "1.0.0",
    },
    "execution": {"class": "local_sandbox"},
    "runtime": {
        "candidate_image": "dftworld-base-mace",
        "build_timeout_sec": 1800,
        "agent_timeout_sec": 7200,
        "verifier_timeout_sec": 3600,
        "cpus": 4,
        "memory_mb": 8192,
        "storage_mb": 20480,
        "gpus": 0,
        "allow_internet": False,
    },
    "submission": {"root": "final"},
    "candidate_files": [
        {"source": "public/**", "destination": ".", "strip_prefix": "public"}
    ],
}


def _dockerfile(design: dict) -> bytes:
    files = ADAPTER.render(Path("/tmp/x"), design)
    return next(f.content for f in files if f.path.as_posix() == "Dockerfile")


def test_exact_dockerfile_shape():
    assert _dockerfile(BASE) == b"FROM dftworld-base-mace\nCOPY public/ /app/\n"


def test_image_with_whitespace_rejected():
    d = {**BASE}
    d["runtime"] = {**d["runtime"], "candidate_image": "dftworld base"}
    assert _design_invalid(d)


def test_image_with_directive_or_shell_meta_rejected():
    for image in (
        "FROM evil",
        "dftworld;rm-rf",
        "dftworld|sh",
        "dftworld$(whoami)",
        "dftworld`id`",
        "dftworld && echo hi",
        "dftworld\\x",
    ):
        d = {**BASE}
        d["runtime"] = {**d["runtime"], "candidate_image": image}
        assert _design_invalid(d), f"image {image!r} accepted"


def test_image_with_credentials_or_path_rejected():
    for image in (
        "user@host:22/image",
        "registry.example:5000/image",
        "/home/user/image",
        "dftworld-base.sif",
        "https://example.com/image",
    ):
        d = {**BASE}
        d["runtime"] = {**d["runtime"], "candidate_image": image}
        assert _design_invalid(d), f"image {image!r} accepted"


def test_image_with_build_arg_rejected():
    d = {**BASE}
    d["runtime"] = {**d["runtime"], "candidate_image": "dftworld:${TAG}"}
    assert _design_invalid(d)


def test_plain_tagged_image_allowed():
    d = {**BASE}
    d["runtime"] = {**d["runtime"], "candidate_image": "dftworld-base-mace:1.2.3"}
    assert _dockerfile(d) == b"FROM dftworld-base-mace:1.2.3\nCOPY public/ /app/\n"


def _design_invalid(design: dict) -> bool:
    verdict = ADAPTER.validate_design(Path("/tmp/x"), design)
    return not verdict.valid


def test_dockerignore_excludes_private_paths():
    files = ADAPTER.render(Path("/tmp/x"), BASE)
    di = next(f.content for f in files if f.path.as_posix() == ".dockerignore")
    text = di.decode("utf-8")
    for entry in ("reference", "solution", "tests", "profiles", "evidence",
                  "source", ".git", "jobs", "**/*.pem", "**/*.key",
                  "**/credentials", "**/.env"):
        assert entry in text, f"dockerignore missing {entry}"
    # Dockerignore is a superset of the mandated exclude list.
    for entry in DOCKERIGNORE_ENTRIES:
        assert entry in text


def test_dockerignore_is_deterministic():
    a = ADAPTER.render(Path("/tmp/a"), BASE)
    b = ADAPTER.render(Path("/tmp/b"), BASE)
    da = next(f.content for f in a if f.path.as_posix() == ".dockerignore")
    db = next(f.content for f in b if f.path.as_posix() == ".dockerignore")
    assert da == db


def test_dockerfile_rejects_control_characters():
    import tomllib

    # A dockerfile produced by the adapter is always safe; ensure no stray
    # control characters slip through the renderer for a pathological image.
    d = {**BASE}
    d["runtime"] = {**d["runtime"], "candidate_image": "dftworld\nRUN ls"}
    verdict = ADAPTER.validate_design(Path("/tmp/x"), d)
    assert not verdict.valid
