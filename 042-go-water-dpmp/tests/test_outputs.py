"""Case 042 verifier suite (verifier contract v2 — V0..V6 levels).

Structural positives + negatives run everywhere (host dev + container). The
scientific layers (V2 probe, V4-V6) need deepmd-jax AND a real trained model;
the synthetic fixture ships a 1024 B placeholder, so those tests are gated on
both. The containerized grading path is `test_submission`, which verifies the
real agent workspace pointed at by $AI2KIT_042_SUBMISSION.

Contract (tests/verifier.py):
    verify(submission, profile) -> {valid, levels:{V0..V6:{ok,errors,diagnostics}},
                                    errors, diagnostics, reward}
    thresholds live at tests/hidden/thresholds.json (levels.Vx) unless overridden
    by env; hidden frames / density reference likewise default to tests/hidden/.
"""
import os
from pathlib import Path

import pytest

import verifier

FIXTURES = Path(__file__).resolve().parents[0] / "fixtures"
GOOD = FIXTURES / "positive" / "good"
HAS_DEEPMD_JAX = verifier._has_deepmd_jax()
_ENV_SUB = verifier.ENV_SUBMISSION
_ENV_PROFILE = verifier.ENV_PROFILE


def _v(root: Path) -> dict:
    return verifier.verify(root, profile="smoke")


def _ok(r: dict, level: str) -> bool:
    return r["levels"][level]["ok"]


def _real_model_installed() -> bool:
    m = GOOD / "final" / "models" / "model.pkl"
    return m.is_file() and m.stat().st_size > 10_000  # placeholder is 1024 B


REAL_MODEL = HAS_DEEPMD_JAX and _real_model_installed()


# --------------------------------------------------------------------------- #
# structural positives (V0/V1/V3 must pass on every environment)
# --------------------------------------------------------------------------- #
def test_good_fixture_structural_passes():
    r = _v(GOOD)
    for level in ("V0", "V1", "V3"):
        assert _ok(r, level), f"{level}: {r['levels'][level]['errors']}"


@pytest.mark.skipif(not REAL_MODEL, reason="deepmd-jax + real oracle model required")
def test_good_fixture_full_reward():
    r = _v(GOOD)
    assert r["reward"] == 1.0, r["errors"]


# --------------------------------------------------------------------------- #
# structural negatives
# --------------------------------------------------------------------------- #
def test_manifest_only_no_model_fails():
    r = _v(FIXTURES / "negative" / "manifest-only")
    assert not _ok(r, "V0"), r["levels"]["V0"]["errors"]


def test_corrupt_empty_model_fails():
    r = _v(FIXTURES / "negative" / "corrupt-model")
    assert not _ok(r, "V2"), r["levels"]["V2"]["errors"]


def test_duplicate_model_copies_fail():
    r = _v(FIXTURES / "negative" / "duplicate-models")
    assert not _ok(r, "V2"), r["levels"]["V2"]["errors"]


def test_forged_labels_fail():
    r = _v(FIXTURES / "negative" / "forged-labels")
    assert not _ok(r, "V1"), r["levels"]["V1"]["errors"]


def test_nonphysical_energy_fails():
    r = _v(FIXTURES / "negative" / "nonphysical-energy")
    assert not _ok(r, "V1"), r["levels"]["V1"]["errors"]


def test_no_iterative_loop_fails():
    r = _v(FIXTURES / "negative" / "no-loop")
    assert not _ok(r, "V3"), r["levels"]["V3"]["errors"]


def test_new_labels_unused_fails():
    r = _v(FIXTURES / "negative" / "new-labels-unused")
    assert not _ok(r, "V3"), r["levels"]["V3"]["errors"]


# --------------------------------------------------------------------------- #
# placeholder model with deepmd-jax: scientific layers must fail (not skip)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not HAS_DEEPMD_JAX, reason="deepmd-jax not in verifier env")
def test_placeholder_model_fails_scientific_layers():
    """The 1024 B placeholder is not a real model: V2 probe and V4-V6 must fail."""
    r = _v(GOOD)
    for level in ("V2", "V4", "V5", "V6"):
        assert not _ok(r, level), f"{level} should fail on placeholder: {r['levels'][level]}"


# --------------------------------------------------------------------------- #
# containerized grading: the real agent workspace
# --------------------------------------------------------------------------- #
def _submission_path():
    raw = os.environ.get(_ENV_SUB)
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


@pytest.mark.skipif(_submission_path() is None, reason="no AI2KIT_042_SUBMISSION set")
def test_submission_passes_all_gates():
    """The graded workspace must satisfy V0-V6 under the active profile."""
    root = _submission_path()
    profile = os.environ.get(_ENV_PROFILE, "formal")
    r = verifier.verify(root, profile=profile)
    assert r["valid"], f"submission failed: {r['errors']}"
    assert r["reward"] == 1.0
