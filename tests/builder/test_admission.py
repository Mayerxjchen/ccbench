"""Tests for Builder Admission Gate."""

from __future__ import annotations

import pytest

from ccbench.builder.admission import AdmissionDecision, evaluate_admission


def test_admission_admit_valid_proposal():
    proposal = {
        "scientific_target": {"system": "TiO2", "objective": "Band gap prediction"},
        "runtime": {"candidate_image": "ccbench-agent:v1"},
        "coverage": {
            "scientific_domain": "oxides",
            "method_family": "dft",
            "material_class": "semiconductor",
        },
    }
    report = evaluate_admission(proposal)
    assert report.decision == AdmissionDecision.ADMIT
    assert report.is_admitted()


def test_admission_rejects_missing_target():
    proposal = {
        "scientific_target": {},
        "runtime": {"candidate_image": "ccbench-agent:v1"},
    }
    report = evaluate_admission(proposal)
    assert report.decision == AdmissionDecision.REJECT


def test_admission_refines_duplicate_coverage():
    proposal = {
        "scientific_target": {"system": "Water", "objective": "Density"},
        "runtime": {"candidate_image": "ccbench-agent:v1"},
        "coverage": {
            "scientific_domain": "water_liquid",
            "method_family": "end_to_end_potential",
            "material_class": "molecular_liquid",
        },
    }
    registry = {
        "cases": [
            {
                "coverage": {
                    "scientific_domain": "water_liquid",
                    "method_family": "end_to_end_potential",
                    "material_class": "molecular_liquid",
                }
            }
        ]
    }
    report = evaluate_admission(proposal, portfolio_registry=registry)
    assert report.decision == AdmissionDecision.REFINE
