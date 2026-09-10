"""Tests for coverage tags in case manifests (U1).

Coverage tags are optional portfolio-representativeness metadata. Cases
without a [coverage] block are valid; cases with one must have the correct
shape (all string values, no extra keys).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from bench.contracts.case import CaseContractError, CaseSpec, CoverageTags

CASES_DIR = Path(__file__).resolve().parents[2] / "cases"


class TestCoverageTagsDataclass:
    """Unit tests for the CoverageTags dataclass."""

    def test_defaults_empty(self):
        ct = CoverageTags()
        assert ct.scientific_domain == ""
        assert ct.method_family == ""
        assert ct.material_class == ""
        assert ct.computation_type == ""

    def test_frozen(self):
        ct = CoverageTags(scientific_domain="water")
        with pytest.raises(AttributeError):
            ct.scientific_domain = "ice"  # type: ignore[misc]

    def test_equality(self):
        a = CoverageTags(scientific_domain="x", method_family="y")
        b = CoverageTags(scientific_domain="x", method_family="y")
        c = CoverageTags(scientific_domain="x", method_family="z")
        assert a == b
        assert a != c


class TestCoverageInCaseSpec:
    """Integration tests: CaseSpec.load parses [coverage] from case.toml."""

    @pytest.mark.parametrize("case_dir_name", [
        "001-matclaw-cips-active-distillation",
        "002-matclaw-cips-curie-temperature",
        "003-matclaw-cips-domain-wall-search",
        "004-ai2kit-water64-end-to-end-potential",
        "005-go-water-dpmp",
    ])
    def test_existing_cases_have_coverage(self, case_dir_name: str):
        case_dir = CASES_DIR / case_dir_name
        spec = CaseSpec.load(case_dir)
        assert spec.coverage.scientific_domain, f"{case_dir_name}: missing scientific_domain"
        assert spec.coverage.method_family, f"{case_dir_name}: missing method_family"
        assert spec.coverage.material_class, f"{case_dir_name}: missing material_class"
        assert spec.coverage.computation_type, f"{case_dir_name}: missing computation_type"

    def test_case_without_coverage_is_valid(self, tmp_path: Path):
        """A case.toml without [coverage] must still parse successfully."""
        case_dir = tmp_path / "test-case"
        case_dir.mkdir()
        (case_dir / "task.md").write_text("# Test")
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
        )
        spec = CaseSpec.load(case_dir)
        assert spec.coverage == CoverageTags()

    def test_coverage_roundtrip(self, tmp_path: Path):
        """Coverage values survive a write-read roundtrip."""
        case_dir = tmp_path / "roundtrip-case"
        case_dir.mkdir()
        (case_dir / "task.md").write_text("# Test")
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\n'
            'scientific_domain = "test_domain"\n'
            'method_family = "active_learning_potential"\n'
            'material_class = "inorganic_2d"\n'
            'computation_type = "iterative_training"\n'
        )
        spec = CaseSpec.load(case_dir)
        assert spec.coverage.scientific_domain == "test_domain"
        assert spec.coverage.method_family == "active_learning_potential"
        assert spec.coverage.material_class == "inorganic_2d"
        assert spec.coverage.computation_type == "iterative_training"

    def test_distinct_cips_domains(self):
        """CIPS sub-variants must have distinct scientific_domain values."""
        domains = set()
        for name in [
            "001-matclaw-cips-active-distillation",
            "002-matclaw-cips-curie-temperature",
            "003-matclaw-cips-domain-wall-search",
        ]:
            spec = CaseSpec.load(CASES_DIR / name)
            domains.add(spec.coverage.scientific_domain)
        assert len(domains) == 3, f"CIPS sub-variants should have 3 distinct domains, got {domains}"

    def test_water_cases_distinct_domains(self):
        """Water cases (004, 005) should have distinct domains."""
        spec004 = CaseSpec.load(CASES_DIR / "004-ai2kit-water64-end-to-end-potential")
        spec005 = CaseSpec.load(CASES_DIR / "005-go-water-dpmp")
        assert spec004.coverage.scientific_domain != spec005.coverage.scientific_domain


class TestCoverageSchemaValidation:
    """Schema-level validation: [coverage] shape is enforced."""

    def test_coverage_extra_keys_rejected_via_casespec(self, tmp_path: Path):
        """Extra keys in [coverage] must fail CaseSpec.load schema validation."""
        case_dir = tmp_path / "bad-coverage"
        case_dir.mkdir()
        (case_dir / "task.md").write_text("# Test")
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\n'
            'scientific_domain = "test"\n'
            'bogus_field = "should_fail"\n'
        )
        with pytest.raises(CaseContractError, match="schema violation at coverage"):
            CaseSpec.load(case_dir)

    def test_coverage_extension_slug_is_accepted_via_casespec(self, tmp_path: Path):
        """Unknown but normalized paper tags are accepted for future suites."""
        case_dir = tmp_path / "extension-vocab"
        case_dir.mkdir()
        (case_dir / "task.md").write_text("# Test")
        (case_dir / "case.toml").write_text(
            'schema_version = "1.2"\n'
            'case_version = "1.0.0"\n'
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\n'
            'scientific_domain = "test"\n'
            'method_family = "unsupported_method"\n'
        )
        spec = CaseSpec.load(case_dir)
        assert spec.coverage.method_family == "unsupported_method"

    def test_coverage_invalid_extension_slug_rejected_via_casespec(self, tmp_path: Path):
        case_dir = tmp_path / "invalid-extension"
        case_dir.mkdir()
        (case_dir / "task.md").write_text("# Test")
        (case_dir / "case.toml").write_text(
            '[execution]\nclass = "local_sandbox"\n'
            '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
            '[coverage]\nmethod_family = "not/a/path"\n'
        )
        with pytest.raises(CaseContractError, match="lowercase underscore slug"):
            CaseSpec.load(case_dir)

    def test_coverage_extra_keys_rejected(self, tmp_path: Path):
        """Extra keys in [coverage] should fail schema validation."""
        import json
        schema_path = Path(__file__).resolve().parents[2] / "schemas" / "case.schema.json"
        schema = json.loads(schema_path.read_text())
        import jsonschema
        doc = {
            "execution": {"class": "local_sandbox"},
            "candidate": {"instruction": "task.md", "submission_root": "final"},
            "coverage": {"scientific_domain": "test", "bogus_field": "nope"},
        }
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(doc))
        assert any("bogus_field" in str(e) or "additional" in str(e).lower() for e in errors), \
            f"Schema should reject extra coverage keys, got errors: {errors}"
