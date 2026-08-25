import ast
import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASES = (
    "027-name2opt-so2",
    "028-name2vib-water",
    "029-name2gibbs-co2",
    "030-name2file-so2",
    "039-react2enthalpy-methane",
    "040-react2gibbs-ammonia",
)
MACE_CASES = CASES[:2] + (CASES[3],)
XTB_CASES = (CASES[2],) + CASES[4:]
FORCE_CONTRACT = "max_i ||F_i|| < 0.01 eV/Å"


def text(case, relative):
    return (ROOT / case / relative).read_text()


def test_public_instructions_expose_the_force_and_runtime_contracts():
    for case in CASES:
        instruction = text(case, "instruction.md")
        assert FORCE_CONTRACT in instruction, case
        assert "/app/.venv/bin/python" in instruction, case
        assert "source /app/.venv/bin/activate" not in instruction, case


def test_cases_are_offline():
    for case in CASES:
        task = tomllib.loads(text(case, "task.toml"))
        assert task["environment"]["allow_internet"] is False, case


def test_evaluators_require_canonical_method_identity():
    for case in CASES:
        evaluator = text(case, "tests/test_outputs.py")
        assert "CANONICAL_METHOD" in evaluator, case
        assert "normalize_method" in evaluator, case

        tree = ast.parse(evaluator)
        identity_tests = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_l3")
        ]
        comparisons = [
            node for function in identity_tests for node in ast.walk(function)
            if isinstance(node, ast.Compare)
        ]

        def is_normalized_call(node, argument):
            return (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "normalize_method"
                and len(node.args) == 1
                and ast.dump(node.args[0], include_attributes=False) == ast.dump(
                    argument, include_attributes=False
                )
            )

        submitted_method = ast.parse('data["method"]', mode="eval").body
        canonical_method = ast.Name(id="CANONICAL_METHOD", ctx=ast.Load())
        assert any(
            len(compare.ops) == 1
            and isinstance(compare.ops[0], ast.Eq)
            and len(compare.comparators) == 1
            and is_normalized_call(compare.left, submitted_method)
            and is_normalized_call(compare.comparators[0], canonical_method)
            for compare in comparisons
        ), f"{case}: L3 must compare the normalized submitted method to CANONICAL_METHOD"


def test_mace_force_checks_use_atom_wise_norms():
    for case in MACE_CASES:
        evaluator = text(case, "tests/test_outputs.py")
        assert "np.linalg.norm(forces, axis=1).max()" in evaluator, case
        assert "np.abs(forces).max()" not in evaluator, case


def test_force_terminology_is_atom_wise_and_unambiguous():
    for case in CASES:
        for relative in ("instruction.md", "tests/test_outputs.py", "benchmark_valid.json"):
            artifact = text(case, relative)
            assert "max|F|" not in artifact, f"{case}/{relative}"


def test_xtb_oracles_emit_standard_xyz():
    for case in XTB_CASES:
        solution = text(case, "solution/solve.sh")
        assert 'write(out_xyz, atoms, format="xyz")' in solution, case


def test_039_locks_spin_but_not_optimizer_identity():
    instruction = text(CASES[4], "instruction.md")
    assert "spin = 0" in instruction
    assert "BFGS is the pinned reference workflow, not a required optimizer" in instruction
    assert "use ASE `BFGS`" not in instruction


def test_smoke_metadata_agrees_per_case():
    for case in CASES:
        validation = json.loads(text(case, "VALIDATION.json"))
        summary = json.loads(text(case, "benchmark_valid.json"))
        assert validation["agent_smoke_tested"] == summary["agent_smoke_tested"], case
        if validation["agent_smoke_tested"]:
            validation_manifest = validation["agent_smoke_detail"]["evidence_manifest"]
            summary_manifest = summary["agent_smoke_detail"]["evidence_manifest"]
            assert validation_manifest == summary_manifest, case
            manifest_path = ROOT / validation_manifest
            manifest = json.loads(manifest_path.read_text())
            assert manifest["run"]["reward"] == 1.0, case
            assert manifest["run"]["ok"] is True, case
            assert all(len(value) == 64 for value in manifest["sha256"].values()), case
