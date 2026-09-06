"""Tests for conditional DOI leakage check (U4A).

The leakage check only applies to research-question paradigm cases
(coverage.paradigm == "research_question").  Standard benchmark cases
are allowed to reference DOIs.  Structural errors (manifest parse failure)
produce exit code 2, never a silent fallback.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.portfolio.leakage_check import (
    LeakageStructuralError,
    check_case_leakage,
    check_file_for_dois,
    check_file_for_paper_identity,
    get_candidate_visible_files,
    is_research_question,
)


def _make_case(tmp_path: Path, name: str, *,
               paradigm: str = "standard",
               instruction: str = "task.md",
               extra_toml: str = "",
               files_block: str = "") -> Path:
    """Helper to create a minimal case directory."""
    case_dir = tmp_path / name
    case_dir.mkdir(exist_ok=True)
    (case_dir / "task.md").write_text("# Test task")
    files_section = ""
    if files_block:
        files_section = f"\n[candidate.files]\n{files_block}\n"
    coverage_paradigm = (
        f'[coverage]\nparadigm = "{paradigm}"\n'
        if paradigm != "standard" else ""
    )
    (case_dir / "case.toml").write_text(
        'schema_version = "1.2"\n'
        'case_version = "1.0.0"\n'
        '[execution]\nclass = "local_sandbox"\n'
        f'[candidate]\ninstruction = "{instruction}"\nsubmission_root = "final"\n'
        f'{coverage_paradigm}'
        f'{extra_toml}'
    )
    return case_dir


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
        f.write_text("Ref 1: 10.1234/first\nRef 2: 10.5678/second\n")
        findings = check_file_for_dois(f)
        assert len(findings) == 2

    def test_binary_file_handled(self, tmp_path: Path):
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
                assert not is_research_question(d), f"{d.name} should not be RQ"

    def test_research_case_with_paradigm(self, tmp_path: Path):
        """A case with coverage.paradigm = research_question is detected."""
        case_dir = _make_case(tmp_path, "rq-case", paradigm="research_question")
        assert is_research_question(case_dir)

    def test_research_case_with_selection_dir(self, tmp_path: Path):
        """A case with selection/state.yaml is detected as research-question."""
        case_dir = _make_case(tmp_path, "rq-sel")
        sel = case_dir / "selection"
        sel.mkdir()
        (sel / "state.yaml").write_text("selection_state: selected\n")
        assert is_research_question(case_dir)

    def test_standard_case_is_false(self, tmp_path: Path):
        case_dir = _make_case(tmp_path, "std-case")
        assert not is_research_question(case_dir)

    def test_broken_manifest_raises_structural_error(self, tmp_path: Path):
        """A case.toml with invalid schema raises LeakageStructuralError."""
        case_dir = tmp_path / "broken"
        case_dir.mkdir()
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\nparadigm = "bogus_value"\n'
        )
        (case_dir / "task.md").write_text("# Test")
        with pytest.raises(LeakageStructuralError):
            is_research_question(case_dir)


class TestCandidateVisibleFiles:
    def test_resolves_instruction_and_input(self, tmp_path: Path):
        """Default case resolves task.md and input/ files."""
        case_dir = _make_case(tmp_path, "basic")
        inp = case_dir / "input"
        inp.mkdir()
        (inp / "data.csv").write_text("x,y\n1,2\n")
        visible = get_candidate_visible_files(case_dir)
        names = {p.name for p in visible}
        assert "task.md" in names
        assert "data.csv" in names

    def test_resolves_custom_candidate_files(self, tmp_path: Path):
        """Custom candidate.files glob patterns are resolved."""
        case_dir = _make_case(
            tmp_path, "custom",
            instruction="custom_doc/guide.md",
            extra_toml=(
                '[[candidate.files]]\n'
                'source = "assets/*.xyz"\n'
                'destination = "."\n'
            ),
        )
        doc = case_dir / "custom_doc"
        doc.mkdir()
        (doc / "guide.md").write_text("# Guide")
        assets = case_dir / "assets"
        assets.mkdir()
        (assets / "struct.xyz").write_text("atoms")
        visible = get_candidate_visible_files(case_dir)
        names = {p.name for p in visible}
        assert "guide.md" in names
        assert "struct.xyz" in names

    def test_broken_manifest_raises_structural_error(self, tmp_path: Path):
        """CaseSpec failure → LeakageStructuralError, no fallback."""
        case_dir = tmp_path / "broken"
        case_dir.mkdir()
        (case_dir / "case.toml").write_text("NOT VALID TOML [[[")
        (case_dir / "task.md").write_text("# Task")
        with pytest.raises(LeakageStructuralError):
            get_candidate_visible_files(case_dir)

    def test_no_silent_fallback_to_input(self, tmp_path: Path):
        """When CaseSpec fails, input/ is NOT silently scanned."""
        case_dir = tmp_path / "nofallback"
        case_dir.mkdir()
        (case_dir / "case.toml").write_text("NOT VALID TOML [[[")
        (case_dir / "task.md").write_text("# Task")
        inp = case_dir / "input"
        inp.mkdir()
        (inp / "secret.csv").write_text("DOI: 10.9999/leaked")
        with pytest.raises(LeakageStructuralError):
            get_candidate_visible_files(case_dir)


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
        case_dir = _make_case(tmp_path, "rq-leaky", paradigm="research_question")
        inp = case_dir / "input"
        inp.mkdir()
        (inp / "data.csv").write_text("x,y\n1,2\nSee 10.1234/leaky\n")
        findings = check_case_leakage(case_dir)
        assert len(findings) >= 1
        assert any("10.1234/leaky" in f.get("doi", "") for f in findings)

    def test_research_case_clean(self, tmp_path: Path):
        """Research-question case with no DOIs is clean."""
        case_dir = _make_case(tmp_path, "rq-clean", paradigm="research_question")
        findings = check_case_leakage(case_dir)
        assert findings == []

    def test_hidden_dirs_not_checked(self, tmp_path: Path):
        """reference/ and solution/ are NOT checked — they're hidden."""
        case_dir = _make_case(tmp_path, "rq-hidden", paradigm="research_question")
        ref = case_dir / "reference"
        ref.mkdir()
        (ref / "paper.pdf").write_text("DOI: 10.9999/not-checked")
        findings = check_case_leakage(case_dir)
        assert findings == []

    def test_custom_candidate_files_allowlist_detected(self, tmp_path: Path):
        """Custom files declared in candidate.files must be scanned dynamically."""
        case_dir = _make_case(
            tmp_path, "rq-custom",
            paradigm="research_question",
            instruction="custom_doc/guidance.md",
            extra_toml=(
                '[[candidate.files]]\n'
                'source = "assets/*.xyz"\n'
                'destination = "."\n'
            ),
        )
        doc_dir = case_dir / "custom_doc"
        doc_dir.mkdir()
        (doc_dir / "guidance.md").write_text("Research: see 10.1038/s41524-020-0001-x")

        assets_dir = case_dir / "assets"
        assets_dir.mkdir()
        (assets_dir / "geom.xyz").write_text("Data with DOI: 10.1103/PhysRevB.99.123456")

        private_dir = case_dir / "internal_secrets"
        private_dir.mkdir()
        (private_dir / "oracle.txt").write_text("Secret: 10.1000/internal-leak")

        findings = check_case_leakage(case_dir)
        found_dois = [f.get("doi", "") for f in findings]
        assert any("10.1038/s41524-020-0001-x" in d for d in found_dois)
        assert any("10.1103/PhysRevB.99.123456" in d for d in found_dois)
        assert not any("10.1000/internal-leak" in d for d in found_dois)

    def test_structural_error_returns_exit_code_2(self, tmp_path: Path):
        """CLI returns 2 when manifest is broken (fail-closed)."""
        case_dir = tmp_path / "broken-rq"
        case_dir.mkdir()
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\nparadigm = "bogus_value"\n'
        )
        (case_dir / "task.md").write_text("# Test")
        from scripts.portfolio.leakage_check import main
        assert main(["--case", str(case_dir)]) == 2
