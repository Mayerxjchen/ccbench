"""Tests for source locking, bidirectional verification, and transitive taint lineage propagation."""

from __future__ import annotations

from pathlib import Path
import pytest

from bench.builder.source_lock import (
    SourceTier,
    build_sources_lock,
    check_gold_leakage,
    get_transitive_ancestors,
    verify_sources_lock_bidirectional,
)


def test_build_sources_lock_hashes_and_tiers(tmp_path: Path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "paper.pdf").write_bytes(b"pdf-content")
    (source_dir / "solution.xyz").write_bytes(b"gold-solution")

    manifest = {
        "paper.pdf": SourceTier.PUBLIC_SOURCE,
        "solution.xyz": SourceTier.GOLD_SOURCE,
    }
    lock = build_sources_lock(source_dir, manifest)
    assert len(lock["sources"]) == 2
    assert (source_dir / "sources.lock.json").is_file()

    # Verify bidirectional check passes
    valid, errors = verify_sources_lock_bidirectional(source_dir)
    assert valid is True
    assert len(errors) == 0


def test_empty_sources_lock_rejected(tmp_path: Path):
    """Empty sources lock must fail bidirectional verification."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    build_sources_lock(source_dir, {})

    valid, errors = verify_sources_lock_bidirectional(source_dir, require_non_empty=True)
    assert valid is False
    assert any("0 sources" in e for e in errors)


def test_untracked_source_added_after_lock_detected(tmp_path: Path):
    """Adding an un-locked source file on disk breaks bidirectional integrity."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "locked.xyz").write_text("locked", encoding="utf-8")
    build_sources_lock(source_dir, {"locked.xyz": SourceTier.PUBLIC_SOURCE})

    # Add untracked source file afterwards
    (source_dir / "untracked.xyz").write_text("sneaky", encoding="utf-8")

    valid, errors = verify_sources_lock_bidirectional(source_dir)
    assert valid is False
    assert any("Untracked/unlocked source artifact on disk: 'untracked.xyz'" in e for e in errors)


def test_direct_gold_leakage_detected():
    manifest = {
        "candidate_input.xyz": "GOLD_SOURCE",
        "doc.md": "PUBLIC_SOURCE",
    }
    violations = check_gold_leakage(["candidate_input.xyz"], manifest)
    assert len(violations) == 1
    assert "directly classified as GOLD_SOURCE" in violations[0]


def test_transitive_gold_taint_lineage_detected():
    manifest = {
        "raw_gold_ground_truth.xyz": "GOLD_SOURCE",
        "intermediate_copy.json": "MAINTAINER_SOURCE",
        "public_candidate_input.json": "PUBLIC_SOURCE",
    }
    lineage = {
        "intermediate_copy.json": ["raw_gold_ground_truth.xyz"],
        "public_candidate_input.json": ["intermediate_copy.json"],
    }

    # Transitive ancestors of public_candidate_input
    ancestors = get_transitive_ancestors("public_candidate_input.json", lineage)
    assert "intermediate_copy.json" in ancestors
    assert "raw_gold_ground_truth.xyz" in ancestors

    # Taint check
    violations = check_gold_leakage(["public_candidate_input.json"], manifest, lineage=lineage)
    assert len(violations) == 1
    assert "is tainted: ancestor 'raw_gold_ground_truth.xyz' is classified as GOLD_SOURCE" in violations[0]
