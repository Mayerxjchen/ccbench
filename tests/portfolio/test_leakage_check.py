"""Tests for conditional DOI leakage check (U4A).

The leakage check only applies to research-question paradigm cases.
Standard benchmark cases are allowed to reference DOIs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.portfolio.leakage_check import (
    _is_research_question,
    check_case_leakage,
    check_file_for_dois,
    check_file_for_paper_identity,
)


class TestDoiDetection:
    def test_detects_doi(self, tmp_path: Path):
        f = tmp_path / "test.md"
        f.write_text("See https://doi.org/10.1234/abcd-5678 for details.")
        findings = check_file_for_dois(f)
        assert len(findings) == 1
        assert "10.1234/abcd-5678" in findings[0]["doi"]

    def test_no_doi(self, tmp_path: Path):
        f = tmp_path / "test.md"
        f.write_text("# Clean instruction\nRun the calculation.")
        findings = check_file_for_dois(f)
        assert len(findings) == 0

    def test_multiple_dois(self, tmp_path: Path):
        f = tmp_path / "test.md"
        f.write_text(
            "Ref 1: 10.1234/first\nRef 2: 10.5678/second\n"
        )
        findings = check_file_for_dois(f)
        assert len(findings) == 2

    def test_binary_file_handled(self, tmp_path: Path):
        """Binary files should not crash the checker."""
        f = tmp_path / "model.pb"
        f.write_bytes(b"\x00\x01\x02\x03")
        findings = check_file_for_dois(f)
        assert findings == []


class TestPaperIdentityDetection:
    def test_detects_doi_prefix(self, tmp_path: Path):
        f = tmp_path / "test.md"
        f.write_text("DOI: 10.1234/something")
        findings = check_file_for_paper_identity(f)
        assert len(findings) >= 1

    def test_detects_arxiv(self, tmp_path: Path):
        f = tmp_path / "test.md"
        f.write_text("See arXiv:2301.12345 for details.")
        findings = check_file_for_paper_identity(f)
        assert len(findings) >= 1

    def test_clean_file(self, tmp_path: Path):
        f = tmp_path / "test.md"
        f.write_text("# Task\nCalculate the energy of the system.")
        findings = check_file_for_paper_identity(f)
        assert len(findings) == 0


class TestResearchQuestionDetection:
    def test_standard_case_not_research(self):
        """The 5 real cases are not research-question paradigm."""
        cases_dir = Path(__file__).resolve().parents[2] / "cases"
        for d in sorted(cases_dir.iterdir()):
            if d.is_dir():
                assert not _is_research_question(d), f"{d.name} should not be RQ"

    def test_research_case_with_paradigm(self, tmp_path: Path):
        """A case with coverage.paradigm = research_question is detected."""
        case_dir = tmp_path / "rq-case"
        case_dir.mkdir()
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\nparadigm = "research_question"\n'
        )
        (case_dir / "task.md").write_text("# Test")
        assert _is_research_question(case_dir)

    def test_research_case_with_selection_dir(self, tmp_path: Path):
        """A case with selection/state.yaml is detected as research-question."""
        case_dir = tmp_path / "rq-case"
        case_dir.mkdir()
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
        )
        (case_dir / "task.md").write_text("# Test")
        sel = case_dir / "selection"
        sel.mkdir()
        (sel / "state.yaml").write_text("selection_state: selected\n")
        assert _is_research_question(case_dir)


class TestCaseLeakage:
    def test_standard_case_clean(self):
        """Standard cases return no findings (not research-question)."""
        cases_dir = Path(__file__).resolve().parents[2] / "cases"
        for d in sorted(cases_dir.iterdir()):
            if d.is_dir():
                findings = check_case_leakage(d)
                assert findings == [], f"{d.name} should have no findings"

    def test_research_case_with_doi_in_input(self, tmp_path: Path):
        """Research-question case with DOI in input/ should flag it."""
        case_dir = tmp_path / "rq-leaky"
        case_dir.mkdir()
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\nparadigm = "research_question"\n'
        )
        (case_dir / "task.md").write_text("# Task\nDo the science.")
        inp = case_dir / "input"
        inp.mkdir()
        (inp / "data.csv").write_text("x,y\n1,2\nSee 10.1234/leaky\n")
        findings = check_case_leakage(case_dir)
        assert len(findings) >= 1
        assert any("10.1234/leaky" in f.get("doi", "") for f in findings)

    def test_research_case_clean(self, tmp_path: Path):
        """Research-question case with no DOIs is clean."""
        case_dir = tmp_path / "rq-clean"
        case_dir.mkdir()
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\nparadigm = "research_question"\n'
        )
        (case_dir / "task.md").write_text("# Task\nCalculate energy.")
        findings = check_case_leakage(case_dir)
        assert findings == []

    def test_hidden_dirs_not_checked(self, tmp_path: Path):
        """reference/ and solution/ are NOT checked — they're hidden."""
        case_dir = tmp_path / "rq-hidden"
        case_dir.mkdir()
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            'paradigm = "research_question"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\n'
            'scientific_domain = "ferroelectric_cips"\n'
            'method_family = "active_learning_potential"\n'
            'material_class = "inorganic_2d"\n'
            'computation_type = "iterative_training"\n'
        )
        (case_dir / "task.md").write_text("# Task")
        ref = case_dir / "reference"
        ref.mkdir()
        (ref / "paper.pdf").write_text("DOI: 10.9999/not-checked")
        findings = check_case_leakage(case_dir)
        assert findings == []

    def test_custom_candidate_files_allowlist_detected(self, tmp_path: Path):
        """Custom files declared in candidate.files must be scanned dynamically."""
        case_dir = tmp_path / "rq-custom-files"
        case_dir.mkdir()
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            'paradigm = "research_question"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\n'
            'instruction = "custom_doc/guidance.md"\n'
            'submission_root = "final"\n'
            'files = [\n'
            '    { source = "assets/*.xyz", destination = "." },\n'
            '    { source = "extra/config.json", destination = "config.json" },\n'
            ']\n'
            '[coverage]\n'
            'scientific_domain = "ferroelectric_cips"\n'
            'method_family = "active_learning_potential"\n'
            'material_class = "inorganic_2d"\n'
            'computation_type = "iterative_training"\n'
        )
        # Custom instruction file with DOI
        doc_dir = case_dir / "custom_doc"
        doc_dir.mkdir()
        (doc_dir / "guidance.md").write_text("Research problem: see 10.1038/s41524-020-0001-x")

        # Custom assets matching glob pattern
        assets_dir = case_dir / "assets"
        assets_dir.mkdir()
        (assets_dir / "geom.xyz").write_text("Structure data with DOI: 10.1103/PhysRevB.99.123456")

        # Extra file without leak
        extra_dir = case_dir / "extra"
        extra_dir.mkdir()
        (extra_dir / "config.json").write_text('{"cutoff": 5.0}')

        # Private dir not in candidate.files allowlist with DOI
        private_dir = case_dir / "internal_secrets"
        private_dir.mkdir()
        (private_dir / "oracle.txt").write_text("Secret source: 10.1000/internal-leak")

        findings = check_case_leakage(case_dir)

        # Must detect leaks in guidance.md and geom.xyz
        found_dois = [f.get("doi", "") for f in findings]
        assert any("10.1038/s41524-020-0001-x" in d for d in found_dois)
        assert any("10.1103/PhysRevB.99.123456" in d for d in found_dois)

        # Must NOT detect the unexposed internal_secrets directory
        assert not any("10.1000/internal-leak" in d for d in found_dois)
