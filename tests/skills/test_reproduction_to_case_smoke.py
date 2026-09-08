"""Case builder smoke from a paper-reproduction fixture (two-skill plan, TS7 item 4).

Chain under test:

    reproduction fixture (contract frozen, run bound)  --  paper-reproduction skill
        -> benchmark-export.md mapping (observable -> Case IR)  --  case-authoring.md
        -> ccbench case intake/design/build
        -> RUNNABLE_DRAFT

Proves a reproduced claim can become a runnable case draft through
``ccbench.builder`` without invoking any deleted Skill.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from ccbench.builder.state import CaseLifecycleState, derive_state
from ccbench.cli import main as cli_main

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
# Reproduction observable -> Case IR mapping under test (case-authoring.md).
OBSERVABLE = "formation_energy"
TOLERANCE_EV = 0.05


def _freeze_reproduction_contract(tmp_path: Path) -> Path:
    """Run the paper-reproduction validator end-to-end: complete draft -> freeze."""
    spec = importlib.util.spec_from_file_location("paper_validate", VALIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    c = tmp_path / "reproduction-contract.v1.yaml"
    c.write_text(
        'schema_version: "1.0"\n'
        "contract_version: 1\n"
        "paper_identity:\n"
        '  doi: "10.1000/x"\n'
        '  citation: "Li et al. 2024"\n'
        "evidence_sources:\n"
        '  - id: "paper"\n'
        '    description: "main article"\n'
        '    classification: "PUBLIC_SOURCE"\n'
        '    sha256: "' + "a" * 64 + '"\n'
        'information_level: "L1_full_reproduction"\n'
        'reproduction_scope: ["formation energy"]\n'
        'non_goals: ["dynamics"]\n'
        "target_claim:\n"
        f'  observable: "{OBSERVABLE} [eV]"\n'
        '  system: "Cu-Au Alloy"\n'
        '  conditions: "0 K"\n'
        '  reported_value: "0.05"\n'
        '  evidence_anchor: "paper Fig 2"\n'
        "method_fingerprint:\n"
        '  engine: "vasp"\n'
        "assumptions:\n"
        '  - "PBE as in paper"\n'
        "comparison_rule:\n"
        '  description: "parse OUTCAR energy difference"\n'
        'acceptance_criterion: "within tolerance"\n'
        "tolerance:\n"
        f"  value: {TOLERANCE_EV}\n"
        '  unit: "eV"\n'
        "input_files:\n"
        '  - path: "input/POSCAR"\n'
        '    role: "structure"\n'
        '    sha256: "' + "b" * 64 + '"\n'
        'freeze_hash: ""\n',
        encoding="utf-8",
    )
    args = mod.build_parser().parse_args(["contract", str(c), "--freeze"])
    assert args.func(args) == 0, "fixture contract must freeze (VALID FROZEN)"
    return c


def _build_case_ir(reproduction_contract: Path) -> dict:
    """case-authoring.md mapping: contract tolerance/observable -> Case IR."""
    doc = yaml.safe_load(reproduction_contract.read_text(encoding="utf-8"))
    tolerance = doc["tolerance"]["value"]
    return {
        "schema_version": 1,
        "identity": {
            "title": "Cu-Au Alloy Potential",
            "category": "mlp",
            "case_id": "006-cu-au-potential",
            "version": "1.0.0",
        },
        "scientific_target": {
            "system": "Cu-Au Alloy",
            "objective": "Predict formation energy",
            "observable": doc["target_claim"]["observable"],
        },
        "selection": {"paradigm": "standard"},
        "candidate": {
            "instruction": "Train neural potential on training structures.",
            "inputs": [{"path": "train.xyz", "description": "Training structures"}],
            "allowed_tools": ["bash"],
        },
        "submission": {
            "root": "final",
            "artifacts": [
                {"path": "model.pt", "kind": "checkpoint", "required": True},
                {"path": "metrics.json", "kind": "metrics", "required": True},
            ],
        },
        "runtime": {
            "execution_class": "local_sandbox",
            "candidate_image": "ccbench-agent:v1",
            "timeout_sec": 1200.0,
            "gpus": 0,
        },
        "coverage": {
            "scientific_domain": "metal_alloys",
            "method_family": "end_to_end_potential",
            "material_class": "metallic_alloy",
            "computation_type": "iterative_training",
            "paradigm": "standard",
        },
        "verification": {
            "layers": ["V0", "V1", "V2", "V4"],
            "primitives": [
                {
                    "primitive": "mlp.energy_rmse",
                    "target": "final/metrics.json",
                    "threshold_ref": "energy_rmse_max",
                    "params": {"metric": "energy_rmse"},
                }
            ],
            # Thresholds carried unchanged from the frozen contract tolerance
            # (Threshold Independence: frozen before any candidate results).
            "thresholds": {"energy_rmse_max": tolerance},
        },
    }


def test_reproduction_fixture_flows_to_runnable_case_draft(tmp_path: Path):
    contract = _freeze_reproduction_contract(tmp_path)

    ir = _build_case_ir(contract)
    assert ir["verification"]["thresholds"]["energy_rmse_max"] == TOLERANCE_EV

    run_dir = tmp_path / "runs" / "006-repro-smoke"
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True)
    maintainer_dir = tmp_path / "maintainer"
    maintainer_dir.mkdir(parents=True)

    # 1. Intake
    rc = cli_main(
        [
            "case", "intake",
            "--run-dir", str(run_dir),
            "--category", "mlp",
            "--title", "Cu-Au Alloy Potential",
            "--system", "Cu-Au Alloy",
            "--objective", "Predict formation energy",
        ]
    )
    assert rc == 0
    assert (run_dir / "source" / "intake.json").is_file()

    # Real scientific source file, locked as PUBLIC_SOURCE.
    from ccbench.builder.source_lock import SourceTier, build_sources_lock

    (run_dir / "source" / "train.xyz").write_text("dummy-xyz-data", encoding="utf-8")
    build_sources_lock(run_dir / "source", {"train.xyz": SourceTier.PUBLIC_SOURCE})

    # 2. Design from the reproduction-derived Case IR.
    ir_file = tmp_path / "case.ir.yaml"
    ir_file.write_text(yaml.safe_dump(ir), encoding="utf-8")
    rc = cli_main(
        ["case", "design", "--ir", str(ir_file), "--run-dir", str(run_dir)]
    )
    assert rc == 0
    assert (run_dir / "design" / "case.ir.yaml").is_file()
    assert derive_state(run_dir).current_state == CaseLifecycleState.DESIGN_VALID

    # 3. Build -> RUNNABLE_DRAFT (the runnable-validation gate).
    rc = cli_main(["case", "build", "--run-dir", str(run_dir)])
    assert rc == 0
    assert (run_dir / "draft" / "task.md").is_file()
    assert (run_dir / "verifier" / "verify.py").is_file()
    assert derive_state(run_dir).current_state == CaseLifecycleState.RUNNABLE_DRAFT
