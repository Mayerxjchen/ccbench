"""Paper-reproduction Contract Freeze behaviour matrix (two-skill plan, TS7).

Pins, via the skill's own deterministic validator, the exact behaviours:

- blank draft                  -> exit 0 "VALID DRAFT" + warnings
- complete draft               -> exit 0 "VALID DRAFT"
- freeze while missing a key   -> nonzero "INVALID"
- complete freeze              -> "VALID FROZEN"
- content change after freeze  -> hash mismatch "INVALID"
- run-record contract hash mismatch -> "INVALID"
- unknown information/verdict/run reason -> "INVALID"
- benchmark export leaking private/gold ground truth -> "INVALID"
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = (
    ROOT
    / "runtimes"
    / "recipes"
    / "skills"
    / "paper-reproduction"
    / "scripts"
    / "validate_reproduction.py"
)
TEMPLATE = (
    ROOT
    / "runtimes"
    / "recipes"
    / "skills"
    / "paper-reproduction"
    / "templates"
    / "reproduction-contract.yaml"
)
EXPORT_TEMPLATE = (
    ROOT
    / "runtimes"
    / "recipes"
    / "skills"
    / "paper-reproduction"
    / "templates"
    / "benchmark-export.md"
)


def _load_validator():
    spec = importlib.util.spec_from_file_location("paper_validate", VALIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(mod, argv: list[str], capsys) -> tuple[int, str]:
    args = mod.build_parser().parse_args(argv)
    rc = args.func(args)
    return rc, capsys.readouterr().out


def _frozen_hash(path: Path) -> str:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["freeze_hash"]


def _complete_contract(path: Path) -> None:
    """A complete, freeze-ready reproduction contract."""
    path.write_text(
        'schema_version: "1.0"\n'
        "contract_version: 1\n"
        "paper_identity:\n"
        '  doi: "10.1000/x"\n'
        '  citation: "Li et al. J. Test 2024"\n'
        "evidence_sources:\n"
        '  - id: "paper"\n'
        '    description: "main article"\n'
        '    classification: "PUBLIC_SOURCE"\n'
        '    sha256: "' + "a" * 64 + '"\n'
        'information_level: "L1_full_reproduction"\n'
        'reproduction_scope: ["adsorption energy"]\n'
        'non_goals: ["dynamics"]\n'
        "target_claim:\n"
        '  observable: "DeltaE_ads [eV]"\n'
        '  system: "Cu(111)"\n'
        '  conditions: "0 K"\n'
        '  reported_value: "0.40"\n'
        '  evidence_anchor: "paper Fig 3"\n'
        "method_fingerprint:\n"
        '  engine: "vasp"\n'
        "assumptions:\n"
        '  - "PBE as in paper"\n'
        "comparison_rule:\n"
        '  description: "parse OUTCAR energy difference"\n'
        'acceptance_criterion: "within tolerance"\n'
        "tolerance:\n"
        "  value: 0.05\n"
        '  unit: "eV"\n'
        "input_files:\n"
        '  - path: "input/POSCAR"\n'
        '    role: "structure"\n'
        '    sha256: "' + "b" * 64 + '"\n'
        'freeze_hash: ""\n',
        encoding="utf-8",
    )


@pytest.fixture()
def mod():
    return _load_validator()


def test_blank_draft_is_valid_with_warnings(mod, tmp_path, capsys):
    p = tmp_path / "blank.yaml"
    p.write_text("# nothing yet\n", encoding="utf-8")
    rc, out = _run(mod, ["contract", str(p)], capsys)
    assert rc == 0
    assert "VALID DRAFT" in out
    assert "warning" in out.lower()


def test_complete_draft_is_valid(mod, tmp_path, capsys):
    p = tmp_path / "complete.yaml"
    _complete_contract(p)
    rc, out = _run(mod, ["contract", str(p)], capsys)
    assert rc == 0
    assert "VALID DRAFT" in out


def test_freeze_on_incomplete_contract_is_invalid(mod, tmp_path, capsys):
    p = tmp_path / "incomplete.yaml"
    _complete_contract(p)
    text = p.read_text(encoding="utf-8").replace(
        'information_level: "L1_full_reproduction"', 'information_level: ""'
    )
    p.write_text(text, encoding="utf-8")
    rc, out = _run(mod, ["contract", str(p), "--freeze"], capsys)
    assert rc != 0
    assert "INVALID" in out


def test_freeze_complete_then_revalidate_is_valid_frozen(mod, tmp_path, capsys):
    p = tmp_path / "contract.yaml"
    _complete_contract(p)
    rc, out = _run(mod, ["contract", str(p), "--freeze"], capsys)
    assert rc == 0
    assert "VALID FROZEN" in out
    rc2, out2 = _run(mod, ["contract", str(p)], capsys)
    assert rc2 == 0
    assert "VALID FROZEN" in out2


def test_tamper_after_freeze_is_invalid(mod, tmp_path, capsys):
    p = tmp_path / "contract.yaml"
    _complete_contract(p)
    assert _run(mod, ["contract", str(p), "--freeze"], capsys)[0] == 0
    p.write_text(
        p.read_text(encoding="utf-8").replace(
            'reported_value: "0.40"', 'reported_value: "0.99"'
        ),
        encoding="utf-8",
    )
    rc, out = _run(mod, ["contract", str(p)], capsys)
    assert rc != 0
    assert "INVALID" in out


def test_run_record_with_mismatched_contract_hash_is_invalid(mod, tmp_path, capsys):
    c = tmp_path / "contract.yaml"
    _complete_contract(c)
    assert _run(mod, ["contract", str(c), "--freeze"], capsys)[0] == 0
    good_hash = _frozen_hash(c)
    r = tmp_path / "run.yaml"
    r.write_text(
        "run_id: run-001\n"
        "contract_version: 1\n"
        f'freeze_hash: "{good_hash}"\n'
        'dag_stage: "ads"\n'
        'command: "vasp_std"\n'
        'run_reason: "initial"\n'
        "outcome:\n"
        '  status: "completed"\n'
        "observable:\n"
        "  value: 0.41\n"
        '  unit: "eV"\n',
        encoding="utf-8",
    )
    rc, _ = _run(mod, ["run", str(r), "--contract", str(c)], capsys)
    assert rc == 0  # matching hash binds fine
    bad = tmp_path / "run-bad.yaml"
    bad.write_text(
        r.read_text(encoding="utf-8").replace(good_hash, "0" * 64),
        encoding="utf-8",
    )
    rc2, out2 = _run(mod, ["run", str(bad), "--contract", str(c)], capsys)
    assert rc2 != 0
    assert "INVALID" in out2 and "mismatch" in out2.lower()


def test_unknown_enum_values_are_invalid(mod, tmp_path, capsys):
    for field, value, rest in (
        ("information_level", "L9_fake", ""),
        ("final_verdict", "definitely_wrong", ""),
    ):
        p = tmp_path / f"enum-{field}.yaml"
        p.write_text(
            'schema_version: "1.0"\n' f"{field}: {value!r}\n" + rest,
            encoding="utf-8",
        )
        rc, out = _run(mod, ["contract", str(p)], capsys)
        assert rc != 0
        assert "INVALID" in out


def test_unknown_run_reason_is_invalid(mod, tmp_path, capsys):
    c = tmp_path / "contract.yaml"
    _complete_contract(c)
    assert _run(mod, ["contract", str(c), "--freeze"], capsys)[0] == 0
    good_hash = _frozen_hash(c)
    r = tmp_path / "run.yaml"
    r.write_text(
        "run_id: run-001\n"
        "contract_version: 1\n"
        f'freeze_hash: "{good_hash}"\n'
        'run_reason: "tune_to_target"\n'
        "outcome:\n"
        '  status: "completed"\n',
        encoding="utf-8",
    )
    rc, out = _run(mod, ["run", str(r), "--contract", str(c)], capsys)
    assert rc != 0
    assert "INVALID" in out
    assert "tune_to_target" in out


def test_export_leaking_private_ground_truth_is_invalid(mod, tmp_path, capsys):
    ok = tmp_path / "export.md"
    ok.write_text(EXPORT_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    rc, _ = _run(mod, ["export", str(ok)], capsys)
    assert rc == 0  # the clean export template is VALID EXPORT

    leak = tmp_path / "export-leak.md"
    text = EXPORT_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace(
        "- case title / category / version:",
        "- case title / category / version:\n"
        '  - leaked: "gold ground-truth energy 0.40 eV appears here"',
    )
    assert "gold ground-truth" in text  # injection landed
    leak.write_text(text, encoding="utf-8")
    rc2, out2 = _run(mod, ["export", str(leak)], capsys)
    assert rc2 != 0
    assert "INVALID" in out2


def test_export_with_gold_source_row_marked_candidate_visible_is_invalid(
    mod, tmp_path, capsys
):
    leak = tmp_path / "export-leak-table.md"
    text = EXPORT_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace(
        "|  | PUBLIC_SOURCE / MAINTAINER_SOURCE / GOLD_SOURCE / REFERENCE_SOURCE | yes/no |  |",
        "| answer key | GOLD_SOURCE | yes | " + "c" * 64 + " |",
    )
    assert "answer key" in text  # table injection landed
    leak.write_text(text, encoding="utf-8")
    rc, out = _run(mod, ["export", str(leak)], capsys)
    assert rc != 0
    assert "INVALID" in out
