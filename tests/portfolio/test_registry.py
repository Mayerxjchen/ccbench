"""Tests for portfolio registry (U2).

Verifies case scanning, distribution computation, concentration flags,
gap detection, and diversity scoring against the 5 real cases.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.portfolio.registry import (
    build_report,
    compute_distribution,
    compute_diversity_score,
    find_concentration,
    find_gaps,
    scan_cases,
)

CASES_DIR = Path(__file__).resolve().parents[2] / "cases"


@pytest.fixture
def report() -> dict:
    return build_report(CASES_DIR)


class TestScanCases:
    def test_finds_all_five(self):
        cases, invalid = scan_cases(CASES_DIR)
        assert len(cases) == 5
        assert invalid == []

    def test_all_have_coverage(self):
        cases, invalid = scan_cases(CASES_DIR)
        assert invalid == []
        for c in cases:
            assert c["has_coverage"], f"{c['case_id']} missing coverage"

    def test_case_ids_unique(self, report: dict):
        ids = [c["case_id"] for c in report["cases"]]
        assert len(ids) == len(set(ids))


class TestDistribution:
    def test_five_distinct_domains(self, report: dict):
        domains = report["distribution"]["scientific_domain"]
        assert len(domains) == 5, f"Expected 5 distinct domains, got {domains}"

    def test_inorganic_2d_has_three(self, report: dict):
        mat = report["distribution"]["material_class"]
        assert mat.get("inorganic_2d") == 3

    def test_molecular_liquid_and_oxide(self, report: dict):
        mat = report["distribution"]["material_class"]
        assert mat.get("molecular_liquid") == 1
        assert mat.get("oxide_interface") == 1


class TestConcentration:
    def test_inorganic_2d_flagged(self, report: dict):
        """inorganic_2d has 3/5 = 60% share, should be flagged."""
        flags = report["concentration_flags"]
        mat_flags = [f for f in flags if f["dimension"] == "material_class"]
        assert any(f["value"] == "inorganic_2d" for f in mat_flags)

    def test_end_to_end_pipeline_flagged(self, report: dict):
        """end_to_end_pipeline has 2/5 = 40%, should NOT be flagged."""
        flags = report["concentration_flags"]
        comp_flags = [f for f in flags
                      if f["dimension"] == "computation_type"
                      and f["value"] == "end_to_end_pipeline"]
        # 40% is below 50% threshold
        assert not comp_flags


class TestGaps:
    def test_no_gaps_in_real_cases(self, report: dict):
        """All 5 real cases should have complete coverage."""
        assert report["gaps"] == []

    def test_detects_untagged_case(self, tmp_path: Path):
        """A case without [coverage] should appear as a gap."""
        case_dir = tmp_path / "untagged"
        case_dir.mkdir()
        (case_dir / "task.md").write_text("# Test")
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
        )
        # Use tmp_path as cases dir
        cases, invalid = scan_cases(tmp_path)
        assert invalid == []
        gaps = find_gaps(cases)
        assert len(gaps) == 1
        assert gaps[0]["case_id"] == "untagged"


class TestDiversity:
    def test_overall_score_positive(self, report: dict):
        assert report["diversity"]["overall"] > 0

    def test_per_dimension_all_present(self, report: dict):
        for dim in ("scientific_domain", "method_family", "material_class", "computation_type"):
            assert dim in report["diversity"]["per_dimension"]

    def test_domain_diversity_is_one(self, report: dict):
        """5 unique domains / 5 cases = 1.0 normalized."""
        domain_info = report["diversity"]["per_dimension"]["scientific_domain"]
        assert domain_info["unique_values"] == 5
        assert domain_info["normalized"] == 1.0

    def test_empty_cases_zero_diversity(self):
        dist = compute_distribution([], )
        score = compute_diversity_score(dist, 0)
        assert score["overall"] == 0.0


class TestBuildReport:
    def test_has_required_keys(self, report: dict):
        for key in ("schema_version", "status", "summary", "distribution",
                     "concentration_flags", "gaps", "uncovered_vocabularies",
                     "diversity", "cases", "invalid_cases"):
            assert key in report
        assert report["status"] == "VALID"
        assert report["invalid_cases"] == []

    def test_summary_counts(self, report: dict):
        s = report["summary"]
        assert s["total_cases"] == 5
        assert s["tagged_cases"] == 5
        assert s["untagged_cases"] == 0
        assert s["invalid_cases"] == 0

    def test_json_serializable(self, report: dict):
        """Report must be JSON-serializable (no Path objects, etc.)."""
        payload = json.dumps(report)
        restored = json.loads(payload)
        assert restored["summary"]["total_cases"] == 5

    def test_corrupted_case_fails_closed(self, tmp_path: Path):
        """A corrupted case must NOT be skipped quietly; report must mark INVALID and CLI return 1."""
        from scripts.portfolio.registry import main

        # Create one valid case and one corrupted case (invalid schema with bogus_field)
        valid_dir = tmp_path / "case-valid"
        valid_dir.mkdir()
        (valid_dir / "task.md").write_text("# Valid")
        (valid_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\nscientific_domain = "valid_domain"\n'
        )

        broken_dir = tmp_path / "case-broken"
        broken_dir.mkdir()
        (broken_dir / "task.md").write_text("# Broken")
        (broken_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\nbogus_field = "illegal"\n'
        )

        # build_report should record invalid_cases and status INVALID
        rep = build_report(tmp_path)
        assert rep["status"] == "INVALID"
        assert len(rep["invalid_cases"]) == 1
        assert rep["invalid_cases"][0]["case_id"] == "case-broken"
        assert rep["summary"]["invalid_cases"] == 1

        # CLI main must exit with code 1
        exit_code = main(["--cases-dir", str(tmp_path)])
        assert exit_code == 1
