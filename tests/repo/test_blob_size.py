"""File blob size gate (R3-20).

Enforces Git repository blob hygiene:
- Prevents accidental check-in of giant binary blobs, datasets, or raw checkpoint dumps (> 5MB).
- Strictly whitelists the legacy/established benchmark weights and reference documents.
- Sets an absolute ceiling (50MB) even for whitelisted scientific assets.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MAX_BLOB_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
HARD_CEILING_BYTES = 50 * 1024 * 1024  # 50 MB

IGNORED_TOP_LEVEL_DIRS = {
    ".git",
    ".venv",
    ".pytest_cache",
    "jobs",
    "runs",
    ".worktrees",
    ".pagent",
}

# Explicit whitelist of historical / scientific baseline model weights and references
KNOWN_LARGE_BLOBS = {
    "cases/001-matclaw-cips-active-distillation/input/He_paper.pdf",
    "cases/001-matclaw-cips-active-distillation/input/teacher_model.pb",
    "cases/002-matclaw-cips-curie-temperature/input/teacher_model.pb",
    "cases/003-matclaw-cips-domain-wall-search/input/teacher_model.pb",
    "tests/cases/004/fixtures/good/work/geopt/output/output",
    "maintainer/cases/004/reference/expert-trajectory/geopt/output/output",
    "benchmark/sources/matclaw/paper/he-physrevb-108-024305.pdf",
    "benchmark/sources/matclaw/common/teacher-model/CIPS_data.zip",
    "benchmark/sources/matclaw/common/teacher-model/frozen_model.pb",
    "benchmark/sources/matclaw/repository/release/workspace_demo1b_distill_pdf/He_paper.pdf",
    "runtimes/recipes/jax-gpu/assets/jax_md-0.2.29.tar.gz",
}


def test_no_unapproved_blobs_exceed_five_megabytes():
    """Ensure no new files exceed 5MB unless explicitly authorized in the whitelist."""
    unapproved_large_files = []

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in IGNORED_TOP_LEVEL_DIRS for part in rel.parts):
            continue
        rel_posix = rel.as_posix()
        size = path.stat().st_size

        if size > MAX_BLOB_SIZE_BYTES:
            if rel_posix not in KNOWN_LARGE_BLOBS:
                unapproved_large_files.append((rel_posix, size))

    assert not unapproved_large_files, (
        f"Detected {len(unapproved_large_files)} unapproved file(s) exceeding 5MB: "
        + ", ".join(f"{f} ({sz / (1024*1024):.2f}MB)" for f, sz in unapproved_large_files)
    )


def test_whitelisted_blobs_respect_absolute_hard_ceiling():
    """Whitelisted large assets must still be strictly below 50MB."""
    for rel_posix in sorted(KNOWN_LARGE_BLOBS):
        p = ROOT / rel_posix
        if p.is_file():
            size = p.stat().st_size
            assert size <= HARD_CEILING_BYTES, (
                f"Whitelisted file {rel_posix} exceeds absolute hard ceiling 50MB ({size} bytes)"
            )
