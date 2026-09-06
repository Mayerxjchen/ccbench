"""Tests for marginal coverage scoring (U3).

Verifies that the scoring function correctly identifies new-vs-existing
dimension values and produces expected rankings.
"""
from __future__ import annotations

import pytest

from dftworld_bench.contracts.case import CoverageTags
from scripts.portfolio.marginal_coverage import (
    coverage_gaps,
    marginal_coverage_value,
    rank_by_marginal_coverage,
)


class TestMarginalCoverageValue:
    def test_all_new_scores_one(self):
        """Candidate with all-new values on every dimension scores 1.0."""
        existing = [CoverageTags(scientific_domain="water", method_family="md")]
        candidate = CoverageTags(
            scientific_domain="ice", method_family="ml", material_class="crystal"
        )
        score = marginal_coverage_value(candidate, existing)
        assert score == 1.0

    def test_all_existing_scores_zero(self):
        """Candidate that duplicates every existing value scores 0.0."""
        existing = [
            CoverageTags(scientific_domain="water", method_family="md",
                         material_class="liquid", computation_type="single_shot"),
        ]
        candidate = CoverageTags(
            scientific_domain="water", method_family="md",
            material_class="liquid", computation_type="single_shot",
        )
        score = marginal_coverage_value(candidate, existing)
        assert score == 0.0

    def test_partial_new(self):
        """Candidate with 2 of 4 new dimensions scores 0.5."""
        existing = [
            CoverageTags(scientific_domain="water", method_family="md",
                         material_class="liquid", computation_type="single_shot"),
        ]
        candidate = CoverageTags(
            scientific_domain="ice",  # new
            method_family="md",       # existing
            material_class="crystal", # new
            computation_type="single_shot",  # existing
        )
        score = marginal_coverage_value(candidate, existing)
        assert score == 0.5

    def test_empty_candidate_scores_zero(self):
        """Candidate with no tags at all scores 0.0."""
        existing = [CoverageTags(scientific_domain="water")]
        score = marginal_coverage_value(CoverageTags(), existing)
        assert score == 0.0

    def test_empty_existing(self):
        """With no existing cases, any non-empty candidate is all-new."""
        candidate = CoverageTags(scientific_domain="water")
        score = marginal_coverage_value(candidate, [])
        assert score == 1.0

    def test_ignores_empty_candidate_dims(self):
        """Empty dimensions on the candidate don't count toward denominator."""
        existing = [CoverageTags(scientific_domain="water")]
        candidate = CoverageTags(scientific_domain="ice")  # only 1 dim
        score = marginal_coverage_value(candidate, existing)
        assert score == 1.0  # 1 new out of 1 scored

    def test_multiple_existing(self):
        """Existing portfolio with multiple cases unions their values."""
        existing = [
            CoverageTags(scientific_domain="water"),
            CoverageTags(scientific_domain="ice"),
        ]
        candidate = CoverageTags(scientific_domain="water")  # duplicate
        score = marginal_coverage_value(candidate, existing)
        assert score == 0.0

    def test_rounding(self):
        """Score is rounded to 4 decimal places."""
        existing = [
            CoverageTags(scientific_domain="a", method_family="b", material_class="c"),
        ]
        candidate = CoverageTags(
            scientific_domain="x", method_family="y", material_class="c"
        )
        score = marginal_coverage_value(candidate, existing)
        # 2 new / 3 scored = 0.6667
        assert score == pytest.approx(0.6667, abs=0.001)


class TestCoverageGaps:
    def test_returns_union(self):
        existing = [
            CoverageTags(scientific_domain="water", method_family="md"),
            CoverageTags(scientific_domain="ice", material_class="crystal"),
        ]
        gaps = coverage_gaps(existing)
        assert gaps["scientific_domain"] == {"water", "ice"}
        assert gaps["method_family"] == {"md"}
        assert gaps["material_class"] == {"crystal"}
        assert gaps["computation_type"] == set()

    def test_empty_existing(self):
        gaps = coverage_gaps([])
        for dim in gaps:
            assert gaps[dim] == set()


class TestRankByMarginalCoverage:
    def test_highest_marginal_first(self):
        existing = [
            CoverageTags(scientific_domain="water", method_family="md",
                         material_class="liquid", computation_type="single_shot"),
        ]
        candidates = [
            {"id": "dup", "coverage": {
                "scientific_domain": "water", "method_family": "md",
                "material_class": "liquid", "computation_type": "single_shot",
            }},
            {"id": "all_new", "coverage": {
                "scientific_domain": "ice", "method_family": "ml",
                "material_class": "crystal", "computation_type": "training",
            }},
            {"id": "partial", "coverage": {
                "scientific_domain": "ice", "method_family": "md",
                "material_class": "crystal", "computation_type": "single_shot",
            }},
        ]
        ranked = rank_by_marginal_coverage(candidates, existing)
        assert ranked[0]["id"] == "all_new"
        assert ranked[1]["id"] == "partial"
        assert ranked[2]["id"] == "dup"

    def test_score_attached(self):
        existing = [CoverageTags(scientific_domain="water")]
        candidates = [{"id": "x", "coverage": {"scientific_domain": "ice"}}]
        ranked = rank_by_marginal_coverage(candidates, existing)
        assert "marginal_coverage_score" in ranked[0]
        assert ranked[0]["marginal_coverage_score"] == 1.0

    def test_missing_coverage_key(self):
        """Candidates without a coverage key get score 0.0."""
        existing = [CoverageTags(scientific_domain="water")]
        candidates = [{"id": "no_cov"}]
        ranked = rank_by_marginal_coverage(candidates, existing)
        assert ranked[0]["marginal_coverage_score"] == 0.0
