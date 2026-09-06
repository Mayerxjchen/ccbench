"""Layout enforcement tests for cases directory (R3-20).

Enforces the canonical Case v2 layout:
Every preserved benchmark case under `cases/<case_id>` must contain strictly
the 4 canonical objects and zero stray artifacts:
- Manifest file: case.toml (or task.toml)
- Instruction sheet: instruction.md (or task.md)
- Public input directory: input (or environment)
- Verifier directory: verifier

All developer artifacts (solutions, reference trajectories, profiles,
developer documentation) belong in `maintainer/cases/`.
Container smoke and scientific regression tests belong in `tests/cases/`.
"""

from __future__ import annotations

import re
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "cases"

PRESERVED_CASE_IDS = (
    "001-matclaw-cips-active-distillation",
    "002-matclaw-cips-curie-temperature",
    "003-matclaw-cips-domain-wall-search",
    "004-ai2kit-water64-end-to-end-potential",
    "005-go-water-dpmp",
)

ALLOWED_MANIFEST_NAMES = {"case.toml", "task.toml"}
ALLOWED_INSTRUCTION_NAMES = {"instruction.md", "task.md"}
ALLOWED_INPUT_NAMES = {"input", "environment"}
ALLOWED_VERIFIER_NAMES = {"verifier"}

FORBIDDEN_NAMES = {
    "Dockerfile",
    "benchmark_valid.json",
    "VALIDATION.json",
    "evaluator-manifest.json",
    "solution",
    "reference",
    "profiles",
    "tests",
    "ablation",
    "source",
    "tools",
}


def test_cases_directory_exists_and_contains_only_five_preserved_cases():
    """cases/ must exist and contain strictly the 5 canonical preserved cases."""
    assert CASES_DIR.is_dir(), f"cases directory not found at {CASES_DIR}"
    actual_cases = sorted(
        p.name for p in CASES_DIR.iterdir() if p.is_dir() and re.fullmatch(r"\d{3}-.+", p.name)
    )
    assert actual_cases == list(PRESERVED_CASE_IDS), (
        f"cases directory must contain exactly the 5 preserved cases, found: {actual_cases}"
    )


@pytest.mark.parametrize("case_id", PRESERVED_CASE_IDS)
def test_case_top_level_contains_strictly_four_canonical_objects(case_id: str):
    """Each case directory must contain strictly 4 top-level entries, no more, no less."""
    case_path = CASES_DIR / case_id
    assert case_path.is_dir(), f"Case directory not found: {case_path}"

    entries = {p.name: p for p in case_path.iterdir() if not p.name.startswith(".")}
    entry_names = set(entries.keys())

    # Ensure no forbidden developer artifacts are present
    forbidden_present = entry_names & FORBIDDEN_NAMES
    assert not forbidden_present, (
        f"Case {case_id} contains forbidden legacy/maintainer objects: {sorted(forbidden_present)}. "
        f"These must be in maintainer/cases/{case_id[:3]}/ or tests/cases/{case_id[:3]}/."
    )

    # Validate strictly 4 entries
    assert len(entry_names) == 4, (
        f"Case {case_id} must have exactly 4 entries, found {len(entry_names)}: {sorted(entry_names)}"
    )

    # 1. Manifest file
    manifests = entry_names & ALLOWED_MANIFEST_NAMES
    assert len(manifests) == 1, f"Case {case_id} must have exactly 1 manifest file ({ALLOWED_MANIFEST_NAMES})"
    manifest_file = entries[list(manifests)[0]]
    assert manifest_file.is_file(), f"{manifest_file} must be a file"

    # 2. Instruction sheet
    instructions = entry_names & ALLOWED_INSTRUCTION_NAMES
    assert len(instructions) == 1, f"Case {case_id} must have exactly 1 instruction sheet ({ALLOWED_INSTRUCTION_NAMES})"
    instruction_file = entries[list(instructions)[0]]
    assert instruction_file.is_file(), f"{instruction_file} must be a file"

    # 3. Public input directory
    inputs = entry_names & ALLOWED_INPUT_NAMES
    assert len(inputs) == 1, f"Case {case_id} must have exactly 1 input directory ({ALLOWED_INPUT_NAMES})"
    input_dir = entries[list(inputs)[0]]
    assert input_dir.is_dir(), f"{input_dir} must be a directory"

    # 4. Verifier directory
    verifiers = entry_names & ALLOWED_VERIFIER_NAMES
    assert len(verifiers) == 1, f"Case {case_id} must have exactly 1 verifier directory ({ALLOWED_VERIFIER_NAMES})"
    verifier_dir = entries[list(verifiers)[0]]
    assert verifier_dir.is_dir(), f"{verifier_dir} must be a directory"
