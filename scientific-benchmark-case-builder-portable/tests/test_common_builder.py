"""Common Core registry and lifecycle contract tests."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

PACKAGE = Path(__file__).resolve().parents[1]
BUILDER = PACKAGE / "skills" / "build-scientific-benchmark-case"
REGISTRY = BUILDER / "references" / "category-registry.yaml"
VALIDATE_CATEGORY = BUILDER / "scripts" / "categories" / "mlp" / "validate_category.py"
VALIDATE_CASE = BUILDER / "scripts" / "common" / "validate_case.py"


def run_validate_category(category: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATE_CATEGORY), "--root", str(BUILDER), "--category", category],
        text=True,
        capture_output=True,
        check=False,
    )

COMMON_REQUIRED_DIRS = (
    "public", "source", "reference", "solution/expert",
    "tests/fixtures/positive", "tests/fixtures/negative",
    "tests/fixtures/alternative-valid", "tools",
)
COMMON_REQUIRED_FILES = (
    "CONTRACT.md", "instruction.md", "task.toml", "case-design.yaml",
    "profiles/smoke.yaml", "profiles/formal.yaml",
    "evidence/retention-policy.yaml", "evidence/manifest.json",
    "evaluator-manifest.json", "VALIDATION.json", "benchmark_valid.json",
)


def make_case(
    root: Path,
    *,
    execution: str = "local_sandbox",
    status: str = "draft",
    benchmark_valid: bool = False,
    open_gates: list[str] | None = None,
    evidence_pointers: dict | None = None,
    hpc_assets: bool = True,
    site_field: bool = False,
) -> Path:
    case = root / "case"
    for rel in COMMON_REQUIRED_DIRS:
        (case / rel).mkdir(parents=True)
    (case / "CONTRACT.md").write_text("# contract\n", encoding="utf-8")
    (case / "instruction.md").write_text("# instruction\n", encoding="utf-8")
    (case / "task.toml").write_text("task = 'demo'\n", encoding="utf-8")
    design = {"schema_version": 1, "execution": {"class": execution}}
    (case / "case-design.yaml").write_text(yaml.safe_dump(design), encoding="utf-8")
    for rel in ("profiles/smoke.yaml", "profiles/formal.yaml",
                "evidence/retention-policy.yaml"):
        (case / rel).parent.mkdir(parents=True, exist_ok=True)
        (case / rel).write_text("draft: {}\n", encoding="utf-8")
    for rel in ("evidence/manifest.json", "evaluator-manifest.json"):
        (case / rel).parent.mkdir(parents=True, exist_ok=True)
        (case / rel).write_text("{}\n", encoding="utf-8")
    validation = {
        "case_status": status,
        "benchmark_valid": False,
        "open_gates": open_gates if open_gates is not None else ["G0"],
        "evidence_pointers": evidence_pointers if evidence_pointers is not None else {},
    }
    (case / "VALIDATION.json").write_text(json.dumps(validation), encoding="utf-8")
    (case / "benchmark_valid.json").write_text(
        json.dumps({"benchmark_valid": benchmark_valid}), encoding="utf-8"
    )
    if execution == "hpc_controller" and hpc_assets:
        for rel in ("profiles/resource.yaml", "profiles/platform.yaml",
                    "reference/compute-runtime.lock.json"):
            path = case / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("draft: {}\n", encoding="utf-8")
    if site_field:
        (case / "profiles/runtime.yaml").write_text("hostname: hpc-01\n", encoding="utf-8")
    return case


def run_validate_case(case_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATE_CASE), str(case_dir), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )


class RegistryContractTests(unittest.TestCase):
    def test_registry_lists_only_mlp_as_supported(self):
        registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
        cats = registry["categories"]
        self.assertEqual(list(cats), ["mlp"])
        self.assertEqual(cats["mlp"]["state"], "supported")

    def test_mlp_roots_point_into_category_layout(self):
        registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
        mlp = registry["categories"]["mlp"]
        self.assertEqual(mlp["references_root"], "references/categories/mlp")
        self.assertEqual(mlp["scripts_root"], "scripts/categories/mlp")
        self.assertEqual(mlp["template_root"], "assets/case-template/categories/mlp")

    def test_mlp_supports_expected_case_kinds(self):
        registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
        kinds = set(registry["categories"]["mlp"]["case_kinds"])
        for expected in (
            "final_model_retraining",
            "end_to_end_model_development",
            "published_model_execution",
            "model_evaluation",
            "active_learning_workflow",
        ):
            self.assertIn(expected, kinds)

    def test_validate_category_accepts_mlp(self) -> None:
        proc = run_validate_category("mlp")
        verdict = json.loads(proc.stdout)
        template_root = BUILDER / "assets/case-template/categories/mlp"
        if not template_root.is_dir():
            # Template overlay lands in Task 4; refs/scripts contract must hold now.
            self.assertFalse(verdict["valid"])
            self.assertTrue(all("template_root" in err for err in verdict["errors"]), verdict["errors"])
        else:
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertTrue(verdict["valid"])
            self.assertEqual([], verdict["errors"])

    def test_validate_category_rejects_unknown(self) -> None:
        proc = run_validate_category("dft")
        self.assertNotEqual(0, proc.returncode)
        verdict = json.loads(proc.stdout)
        self.assertFalse(verdict["valid"])
        self.assertIn("mlp", verdict["errors"][0])


class LifecycleContractTests(unittest.TestCase):
    def run_verdict(self, case_dir: Path) -> dict:
        proc = run_validate_case(case_dir)
        return json.loads(proc.stdout)

    def test_validate_case_accepts_valid_local_draft(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            verdict = self.run_verdict(make_case(Path(td)))
        self.assertTrue(verdict["valid"], verdict["errors"])

    def test_validate_case_accepts_valid_hpc_draft(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            verdict = self.run_verdict(make_case(Path(td), execution="hpc_controller"))
        self.assertTrue(verdict["valid"], verdict["errors"])

    def test_validate_case_rejects_unknown_execution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            verdict = self.run_verdict(make_case(Path(td), execution="magic"))
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("execution class" in e for e in verdict["errors"]))

    def test_validate_case_rejects_hpc_without_resource_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            verdict = self.run_verdict(
                make_case(Path(td), execution="hpc_controller", hpc_assets=False)
            )
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("resource" in e for e in verdict["errors"]))

    def test_validate_case_rejects_local_with_site_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            verdict = self.run_verdict(make_case(Path(td), site_field=True))
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("site field" in e for e in verdict["errors"]))

    def test_validate_case_rejects_local_with_hpc_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = make_case(Path(td), execution="local_sandbox")
            (case / "profiles/resource.yaml").write_text("draft: {}\n", encoding="utf-8")
            verdict = self.run_verdict(case)
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("local_sandbox must not contain" in e for e in verdict["errors"]))

    def test_validate_case_rejects_self_asserted_validity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            verdict = self.run_verdict(
                make_case(Path(td), status="draft", benchmark_valid=True)
            )
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("benchmark_valid=true" in e for e in verdict["errors"]))

    def test_validate_case_rejects_open_gate_with_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            verdict = self.run_verdict(
                make_case(
                    Path(td), status="benchmark_valid", benchmark_valid=True,
                    open_gates=["G0"], evidence_pointers={"g0": "e"},
                )
            )
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("open release gates" in e for e in verdict["errors"]))

    def test_validate_case_rejects_unknown_status(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            verdict = self.run_verdict(make_case(Path(td), status="magic"))
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("unknown case_status" in e for e in verdict["errors"]))

    def test_validate_case_rejects_missing_evidence_pointers_at_advanced_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            verdict = self.run_verdict(make_case(Path(td), status="reference_validated"))
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("evidence_pointers" in e for e in verdict["errors"]))


INIT_CASE = BUILDER / "scripts" / "common" / "init_case.py"
CASE_FIXTURES = PACKAGE / "tests" / "fixtures"

COMMON_TREE = (
    "CONTRACT.md", "instruction.md", "task.toml", "case-design.yaml",
    "public", "source", "reference", "solution/expert",
    "tests/fixtures/positive", "tests/fixtures/negative",
    "tests/fixtures/alternative-valid",
    "profiles/smoke.yaml", "profiles/formal.yaml", "tools",
    "evidence/retention-policy.yaml", "evidence/manifest.json",
    "evaluator-manifest.json", "VALIDATION.json", "benchmark_valid.json",
)
HPC_TREE = (
    "profiles/resource.yaml", "profiles/platform.yaml",
    "reference/compute-runtime.lock.json",
)


def run_init_case(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INIT_CASE), *args],
        text=True,
        capture_output=True,
        check=check,
    )


class ScaffoldTests(unittest.TestCase):
    def scaffold(
        self, design_name: str, out: Path, kind: str = "final_model_retraining",
        *extra: str, check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return run_init_case(
            "--category", "mlp", "--kind", kind,
            "--design", str(CASE_FIXTURES / design_name / "case-design.yaml"),
            "--output", str(out), *extra, check=check,
        )

    def test_scaffold_creates_full_common_tree_local(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = self.scaffold("simple-mlp-local", out)
            self.assertEqual(0, proc.returncode, proc.stderr)
            for rel in COMMON_TREE:
                self.assertTrue((out / rel).exists(), rel)
            for rel in HPC_TREE:
                self.assertFalse((out / rel).exists(), rel)

    def test_scaffold_hpc_adds_execution_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = self.scaffold(
                "water64-like-hpc", out, kind="end_to_end_model_development"
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            for rel in HPC_TREE:
                path = out / rel
                self.assertTrue(path.is_file(), rel)
                self.assertTrue(path.read_text(encoding="utf-8").strip(), rel)
            design = yaml.safe_load((out / "case-design.yaml").read_text(encoding="utf-8"))
            self.assertEqual("hpc_controller", design["execution"]["class"])

    def test_scaffold_reference_starts_planned_with_empty_lineage(self) -> None:
        REFERENCE_STATES = (
            "planned", "inputs_frozen", "smoke_executed", "formal_executed",
            "independently_verified", "reproducible",
        )
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            self.assertEqual(0, self.scaffold("simple-mlp-local", out).returncode)
            ref = json.loads((out / "reference" / "reference.json").read_text(encoding="utf-8"))
            self.assertIn(ref["state"], REFERENCE_STATES)
            self.assertEqual("planned", ref["state"])
            self.assertEqual([], ref["lineage"])
            self.assertIsNone(ref["independent_parser"])
            # v2.3: the unconsumed inputs.lock.json stub was deleted from the
            # scaffold; reference.json stays the single planned-state record.
            self.assertFalse((out / "reference" / "inputs.lock.json").exists())

    def test_scaffold_has_no_experiment_or_ablation_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            self.assertEqual(0, self.scaffold("simple-mlp-local", out).returncode)
            self.assertFalse((out / "ablation").exists())
            self.assertFalse((out / "experiments").exists())
            self.assertFalse((out / "pilot").exists())

    def test_experiment_handoff_policy_exists(self) -> None:
        policy = BUILDER / "references/common/experiment-handoff.md"
        self.assertTrue(policy.is_file())
        text = policy.read_text(encoding="utf-8")
        self.assertIn("benchmark_valid: true", text)
        self.assertIn("ablation", text)

    def test_scaffold_refuses_unknown_category(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = run_init_case(
                "--category", "dft", "--kind", "final_model_retraining",
                "--design", str(CASE_FIXTURES / "simple-mlp-local" / "case-design.yaml"),
                "--output", str(out), check=False,
            )
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("supported", proc.stderr)

    def test_scaffold_is_deterministic(self) -> None:
        def manifest(root: Path) -> list[str]:
            return sorted(
                str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
            )
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a"
            b = Path(td) / "b"
            self.assertEqual(0, self.scaffold("simple-mlp-local", a).returncode)
            self.assertEqual(0, self.scaffold("simple-mlp-local", b).returncode)
            self.assertEqual(manifest(a), manifest(b))

    def test_scaffold_refuses_nonempty_without_force_and_preserves_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            out.mkdir()
            marker = out / "keep.txt"
            marker.write_text("precious", encoding="utf-8")
            proc = self.scaffold("simple-mlp-local", out, check=False)
            self.assertNotEqual(0, proc.returncode)
            self.assertEqual("precious", marker.read_text(encoding="utf-8"))
            self.assertEqual(["keep.txt"], [p.name for p in out.iterdir()])

    def test_scaffold_force_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            out.mkdir()
            (out / "stale.txt").write_text("x", encoding="utf-8")
            proc = self.scaffold("simple-mlp-local", out, "final_model_retraining", "--force")
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertTrue((out / "case-design.yaml").exists())
            self.assertFalse((out / "stale.txt").exists())

    def test_scaffold_refuses_blocked_design(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = self.scaffold("blocked-mlp", out, check=False)
            self.assertNotEqual(0, proc.returncode)
            self.assertFalse((out / "benchmark_valid.json").exists())

    def test_scaffold_allow_blocked_writes_draft_with_false_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = self.scaffold("blocked-mlp", out, "final_model_retraining", "--allow-blocked")
            self.assertEqual(0, proc.returncode, proc.stderr)
            bv = json.loads((out / "benchmark_valid.json").read_text(encoding="utf-8"))
            self.assertIs(False, bv["benchmark_valid"])
            validation = json.loads((out / "VALIDATION.json").read_text(encoding="utf-8"))
            self.assertEqual("draft", validation["case_status"])
            design = yaml.safe_load((out / "case-design.yaml").read_text(encoding="utf-8"))
            self.assertTrue(design["blockers"])


CHECK_RELEASE = BUILDER / "scripts" / "common" / "check_release.py"
FREEZE_EVALUATOR = BUILDER / "scripts" / "common" / "freeze_evaluator_manifest.py"
DERIVE_STATE = BUILDER / "scripts" / "common" / "derive_validation_state.py"
CHECK_THRESHOLD_FREEZE = BUILDER / "scripts" / "common" / "check_threshold_freeze.py"
AUDIT_CASE = BUILDER / "scripts" / "common" / "audit_candidate_bundle.py"
GAMING_FIXTURE = CASE_FIXTURES / "verifier-gaming"


def run_check_thresholds(
    thresholds: Path, reference: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(CHECK_THRESHOLD_FREEZE), str(thresholds), "--json"]
    if reference is not None:
        cmd += ["--reference", str(reference)]
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def valid_frozen(key: str = "mae_energy", **over: object) -> dict:
    value = {
        "schema_version": 1,
        "status": "frozen",
        "units": {key: "eV/atom"},
        "metric_definitions": {key: "mean absolute error over held-out energies"},
        "thresholds": {key: {
            "pass_bound": 0.0321,
            "stochastic": True,
            "statistics": {"mean": 0.024, "std": 0.004, "n_runs": 5},
            "reference_run_ids": ["run-001"],
            "rationale": "5-run calibration; bound = mean + 2*std",
            "calibrated_at": "2026-08-18T09:00:00Z",
        }},
        "produced_by": "threshold_calibration",
        "agent_results_inspected_at": None,
        "case_version": "1.0.0",
        "verifier_version": "1.0.0",
        "hidden_set": {
            "generation_provenance": "held-out DFT structures",
            "candidate_inaccessible": True,
            "submission_overlap_checked": True,
        },
    }
    value.update(over)
    return value


@unittest.skipUnless(CHECK_THRESHOLD_FREEZE.exists(), "check_threshold_freeze.py lands in Task 8")
class ThresholdFreezeTests(unittest.TestCase):
    def verdict(self, data: dict, reference: dict | None = None) -> dict:
        with tempfile.TemporaryDirectory() as td:
            thresholds = Path(td) / "thresholds.json"
            thresholds.write_text(json.dumps(data), encoding="utf-8")
            ref_path = None
            if reference is not None:
                ref_path = Path(td) / "reference.json"
                ref_path.write_text(json.dumps(reference), encoding="utf-8")
            proc = run_check_thresholds(thresholds, ref_path)
        return json.loads(proc.stdout)

    def test_draft_template_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(CASE_FIXTURES / "simple-mlp-local" / "case-design.yaml"),
                "--output", str(out),
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            verdict = self.verdict(json.loads(
                (out / "reference" / "thresholds.json").read_text(encoding="utf-8")
            ))
        self.assertTrue(verdict["valid"], verdict["errors"])
        self.assertEqual("draft", verdict["status"])

    def test_frozen_requires_full_record(self) -> None:
        verdict = self.verdict(valid_frozen(status="frozen", thresholds={}))
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("empty" in e for e in verdict["errors"]), verdict["errors"])

    def test_rejects_one_run_stochastic_calibration(self) -> None:
        entry = dict(valid_frozen()["thresholds"]["mae_energy"])
        entry["statistics"]["n_runs"] = 1
        verdict = self.verdict(valid_frozen(thresholds={"mae_energy": entry}))
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("one-run stochastic" in e for e in verdict["errors"]), verdict["errors"])

    def test_rejects_post_agent_freeze(self) -> None:
        verdict = self.verdict(valid_frozen(
            agent_results_inspected_at="2026-08-18T12:00:00Z"
        ))
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("precede Agent" in e for e in verdict["errors"]), verdict["errors"])

    def test_rejects_expert_threshold_writer(self) -> None:
        verdict = self.verdict(valid_frozen(produced_by="expert_runner"))
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("threshold_calibration" in e for e in verdict["errors"]), verdict["errors"])

    def test_rejects_missing_units(self) -> None:
        verdict = self.verdict(valid_frozen(units={}))
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("units" in e for e in verdict["errors"]), verdict["errors"])

    def test_rejects_tight_pass_bound_from_expert(self) -> None:
        ref = {"metrics": {"mae_energy": 0.0321}}
        verdict = self.verdict(valid_frozen(), reference=ref)
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("equals expert value" in e for e in verdict["errors"]), verdict["errors"])

    def test_accepts_valid_frozen_record(self) -> None:
        ref = {"metrics": {"mae_energy": 0.0210}}
        verdict = self.verdict(valid_frozen(), reference=ref)
        self.assertTrue(verdict["valid"], verdict["errors"])
        self.assertEqual("frozen", verdict["status"])

    def test_rejects_unknown_status(self) -> None:
        verdict = self.verdict(valid_frozen(status="sealed"))
        self.assertFalse(verdict["valid"])
        self.assertTrue(any("unknown threshold status" in e for e in verdict["errors"]), verdict["errors"])


def run_common_script(script: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args], text=True, capture_output=True, check=check,
    )


DUMMY_CATEGORY = CASE_FIXTURES / "dummy-future-category"


@unittest.skipUnless(CASE_FIXTURES.joinpath("dummy-future-category/category.yaml").exists(),
                     "dummy-future-category fixture lands in Task 10")
class ExtensibilityTests(unittest.TestCase):
    def temp_skill_with_dummy(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        td = tempfile.TemporaryDirectory()
        skill = Path(td.name) / "skill"
        shutil.copytree(BUILDER, skill)
        registry_path = skill / "references/category-registry.yaml"
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        dummy = yaml.safe_load((DUMMY_CATEGORY / "category.yaml").read_text(encoding="utf-8"))
        registry["categories"]["dummy"] = dummy
        registry_path.write_text(yaml.safe_dump(registry, sort_keys=False), encoding="utf-8")
        overlay = skill / "assets/case-template/categories/dummy"
        overlay.mkdir(parents=True)
        (overlay / "case-requirements.yaml").write_text(
            "schema_version: 1\ncategory: dummy\nworkflow_capabilities: []\n",
            encoding="utf-8",
        )
        return td, skill

    def test_dummy_category_scaffolds_without_mlp_fields(self) -> None:
        td, skill = self.temp_skill_with_dummy()
        with td:
            out = Path(td.name) / "case"
            proc = run_init_case(
                "--category", "dummy", "--kind", "dummy_kind",
                "--design", str(DUMMY_CATEGORY / "case-design.yaml"),
                "--output", str(out),
                "--skill-root", str(skill),
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            for rel in COMMON_TREE:
                self.assertTrue((out / rel).exists(), rel)
            design_text = (out / "case-design.yaml").read_text(encoding="utf-8")
            design = yaml.safe_load(design_text)
            self.assertEqual("dummy", design["category"])
            self.assertEqual("dummy_kind", design["case_kind"])
            self.assertNotIn("mlp", design_text.lower())
            self.assertNotIn("workflow_capabilities", design_text)
            self.assertFalse((out / "verifier-plan.yaml").exists())

    def test_shipped_registry_remains_mlp_only(self) -> None:
        registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(list(registry["categories"]), ["mlp"])
        self.assertNotIn("dummy", registry["categories"])


def build_release_case(root: Path) -> Path:
    return subprocess.run(
        [sys.executable, str(script), *args], text=True, capture_output=True, check=check,
    )


def build_release_case(root: Path) -> Path:
    case = make_case(root, status="benchmark_valid", open_gates=[])
    (case / "reference/reference.json").write_text(json.dumps({
        "state": "reproducible", "raw_evidence": [], "metrics": {},
        "lineage": [{"capability": "hidden_static_accuracy", "inputs": [], "outputs": [],
                     "commands": [], "runtime_identity": {}, "digest": ""}],
        "independent_parser": "parser",
    }), encoding="utf-8")
    (case / "reference/thresholds.json").write_text(
        json.dumps(valid_frozen()), encoding="utf-8"
    )
    record = case / "evidence/record.json"
    record.write_text(json.dumps({"gate_evidence": True}), encoding="utf-8")
    blob = record.read_bytes()
    manifest = {
        "schema_version": 1,
        "objects": {
            "record": {
                "rel_path": "evidence/record.json",
                "size": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        },
        "gates": {f"G{i}": "record" for i in range(13)},
    }
    (case / "evidence/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    run_common_script(FREEZE_EVALUATOR, str(case))
    return case


@unittest.skipUnless(CHECK_RELEASE.exists(), "check_release.py lands in Task 9")
class ReleaseDerivationTests(unittest.TestCase):
    def verdict(self, case_dir: Path, write: bool = False) -> dict:
        cmd = ["--json"] + (["--write"] if write else [])
        proc = run_common_script(CHECK_RELEASE, str(case_dir), *cmd)
        return json.loads(proc.stdout)

    def test_release_valid_writes_benchmark_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = build_release_case(Path(td))
            report = self.verdict(case, write=True)
            self.assertTrue(report["valid"], report["errors"])
            bv = json.loads((case / "benchmark_valid.json").read_text(encoding="utf-8"))
            self.assertIs(True, bv["benchmark_valid"])
            self.assertEqual("check_release.py", bv["derived_by"])
            validation = json.loads((case / "VALIDATION.json").read_text(encoding="utf-8"))
            self.assertEqual("benchmark_valid", validation["case_status"])
            self.assertEqual([], validation["open_gates"])

    def test_release_missing_byte_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = build_release_case(Path(td))
            (case / "evidence/record.json").unlink()
            report = self.verdict(case)
        self.assertFalse(report["valid"])
        self.assertTrue(any("missing restorable" in e for e in report["errors"]), report["errors"])

    def test_release_hash_mismatch_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = build_release_case(Path(td))
            (case / "evidence/record.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
            report = self.verdict(case)
        self.assertFalse(report["valid"])
        self.assertTrue(any("hash mismatch" in e for e in report["errors"]), report["errors"])

    def test_release_open_gate_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = build_release_case(Path(td))
            validation = json.loads((case / "VALIDATION.json").read_text(encoding="utf-8"))
            validation["open_gates"] = ["G5"]
            (case / "VALIDATION.json").write_text(json.dumps(validation), encoding="utf-8")
            report = self.verdict(case)
        self.assertFalse(report["valid"])
        self.assertTrue(any("open release gates" in e for e in report["errors"]), report["errors"])

    def test_release_draft_threshold_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = build_release_case(Path(td))
            thresholds = json.loads((case / "reference/thresholds.json").read_text(encoding="utf-8"))
            thresholds["status"] = "calibrating"
            (case / "reference/thresholds.json").write_text(json.dumps(thresholds), encoding="utf-8")
            report = self.verdict(case)
        self.assertFalse(report["valid"])
        self.assertTrue(any("not frozen" in e for e in report["errors"]), report["errors"])

    def test_release_evaluator_drift_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = build_release_case(Path(td))
            (case / "tests/drift.txt").write_text("new hidden test\n", encoding="utf-8")
            report = self.verdict(case)
        self.assertFalse(report["valid"])
        self.assertTrue(any("unfrozen file" in e for e in report["errors"]), report["errors"])

    def test_release_candidate_leak_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = build_release_case(Path(td))
            (case / "public/reference").mkdir(parents=True)
            (case / "public/reference/leak.txt").write_text("x", encoding="utf-8")
            report = self.verdict(case)
        self.assertFalse(report["valid"])
        self.assertTrue(any("candidate leak" in e for e in report["errors"]), report["errors"])

    def test_release_insufficient_references_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = build_release_case(Path(td))
            reference = json.loads((case / "reference/reference.json").read_text(encoding="utf-8"))
            reference["state"] = "formal_executed"
            (case / "reference/reference.json").write_text(json.dumps(reference), encoding="utf-8")
            report = self.verdict(case)
        self.assertFalse(report["valid"])
        self.assertTrue(any("insufficient references" in e for e in report["errors"]), report["errors"])

    def test_release_never_writes_on_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = build_release_case(Path(td))
            (case / "evidence/record.json").unlink()
            self.verdict(case, write=True)
            bv = json.loads((case / "benchmark_valid.json").read_text(encoding="utf-8"))
            self.assertIs(False, bv["benchmark_valid"])


@unittest.skipUnless(DERIVE_STATE.exists(), "derive_validation_state.py lands in Task 9")
class ValidationStateDerivationTests(unittest.TestCase):
    def derive(self, case_dir: Path) -> dict:
        return json.loads(run_common_script(DERIVE_STATE, str(case_dir)).stdout)

    def test_scaffold_derives_draft_never_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(CASE_FIXTURES / "simple-mlp-local" / "case-design.yaml"),
                "--output", str(out),
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            state = self.derive(out)
        self.assertEqual("draft", state["case_status"])
        self.assertIs(False, state["benchmark_valid"])

    def test_reproducible_reference_derives_verifier_validated_not_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(CASE_FIXTURES / "simple-mlp-local" / "case-design.yaml"),
                "--output", str(out),
            )
            ref_path = out / "reference/reference.json"
            reference = json.loads(ref_path.read_text(encoding="utf-8"))
            reference["state"] = "reproducible"
            ref_path.write_text(json.dumps(reference), encoding="utf-8")
            state = self.derive(out)
        self.assertEqual("verifier_validated", state["case_status"])
        self.assertIs(False, state["benchmark_valid"])


def run_audit(case_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(AUDIT_CASE), str(case_dir), "--json"],
        text=True, capture_output=True, check=False,
    )


@unittest.skipUnless(AUDIT_CASE.exists(), "audit_candidate_bundle.py lands in Task 5")
class CandidateBoundaryTests(unittest.TestCase):
    def verdict(self, case_dir: Path) -> dict:
        proc = run_audit(case_dir)
        return json.loads(proc.stdout)

    def assert_invalid(self, case_dir: Path, fragment: str) -> dict:
        verdict = self.verdict(case_dir)
        self.assertFalse(verdict["valid"], verdict["errors"])
        self.assertTrue(
            any(fragment in e for e in verdict["errors"]), verdict["errors"]
        )
        return verdict

    def test_audit_accepts_clean_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(GAMING_FIXTURE / "case-design.yaml"),
                "--output", str(out),
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            verdict = self.verdict(out)
            self.assertTrue(verdict["valid"], verdict["errors"])

    def test_audit_rejects_reference_smuggled_into_public(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(GAMING_FIXTURE / "case-design.yaml"),
                "--output", str(out),
            )
            (out / "public").mkdir(exist_ok=True)
            src = out / "reference" / "thresholds.json"
            if src.is_file():
                (out / "public" / "thresholds.json").write_bytes(src.read_bytes())
            self.assert_invalid(out, "thresholds")

    def test_audit_rejects_solution_dir_in_public(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(GAMING_FIXTURE / "case-design.yaml"),
                "--output", str(out),
            )
            shutil.copytree(out / "solution", out / "public" / "solution")
            self.assert_invalid(out, "solution")

    def test_audit_rejects_symlink_to_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(GAMING_FIXTURE / "case-design.yaml"),
                "--output", str(out),
            )
            link = out / "public" / "leak"
            link.symlink_to(out / "solution", target_is_directory=True)
            self.assert_invalid(out, "symlink")

    def test_audit_rejects_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(GAMING_FIXTURE / "case-design.yaml"),
                "--output", str(out),
            )
            src = out / "reference" / "reference.json"
            if src.is_file():
                os.link(src, out / "public" / "copy.json")
                self.assert_invalid(out, "hardlink")

    def test_audit_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(GAMING_FIXTURE / "case-design.yaml"),
                "--output", str(out),
            )
            link = out / "public" / "escape"
            link.symlink_to(out / "..", target_is_directory=True)
            verdict = self.verdict(out)
            self.assertFalse(verdict["valid"], verdict["errors"])

    def test_audit_rejects_hash_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(GAMING_FIXTURE / "case-design.yaml"),
                "--output", str(out),
            )
            secret = "a" * 64
            manifest = out / "evidence" / "manifest.json"
            manifest.write_text(json.dumps({"sha256": secret}), encoding="utf-8")
            (out / "public" / "answer.txt").write_text(secret, encoding="utf-8")
            self.assert_invalid(out, "semantic leakage")

    def test_audit_rejects_expert_filename_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(GAMING_FIXTURE / "case-design.yaml"),
                "--output", str(out),
            )
            expert = out / "solution" / "expert" / "expert_analysis.py"
            expert.write_text("# expert\n", encoding="utf-8")
            (out / "public" / "hint.txt").write_text(
                "see expert_analysis.py", encoding="utf-8"
            )
            self.assert_invalid(out, "semantic leakage")

    def test_audit_rejects_override_importing_hidden_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(GAMING_FIXTURE / "case-design.yaml"),
                "--output", str(out),
            )
            design_path = out / "case-design.yaml"
            design = yaml.safe_load(design_path.read_text(encoding="utf-8"))
            design["candidate_visible"] = ["public", "reference"]
            design_path.write_text(yaml.safe_dump(design), encoding="utf-8")
            self.assert_invalid(out, "reference")

    def test_audit_rejects_git_state_under_hidden_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(GAMING_FIXTURE / "case-design.yaml"),
                "--output", str(out),
            )
            git = out / "reference" / ".git"
            git.mkdir()
            (git / "config").write_text("[core]\n", encoding="utf-8")
            self.assert_invalid(out, ".git")


class DftworldTargetTests(unittest.TestCase):
    """Task 7 — optional --target dftworld bridge.

    Generic mode stays byte-compatible.  Target mode renders the executable
    draft through the repository Case Factory CLI; outside the repository, or
    when the adapter fails, it fails clearly and preserves the generic scaffold.
    """

    DFTWORLD_DESIGN = {
        "schema_version": 1,
        "category": "mlp",
        "case_kind": "final_model_retraining",
        "execution": {"class": "local_sandbox"},
        "objective": "Hidden static accuracy for a released MACE model",
        "identity": {
            "case_id": "042-example",
            "task_name": "benchmark/042-example",
            "title": "Example",
            "description": "Example case",
            "case_version": "1.0.0",
        },
        "runtime": {
            "candidate_image": "dftworld-base-mace",
            "build_timeout_sec": 1800,
            "agent_timeout_sec": 7200,
            "verifier_timeout_sec": 3600,
            "cpus": 4,
            "memory_mb": 8192,
            "storage_mb": 20480,
            "gpus": 0,
            "allow_internet": False,
        },
        "submission": {"root": "final"},
        "candidate_files": [
            {"source": "public/**", "destination": ".", "strip_prefix": "public"}
        ],
    }

    def _write_design(self, td: Path) -> Path:
        path = td / "case-design.yaml"
        path.write_text(yaml.safe_dump(self.DFTWORLD_DESIGN, sort_keys=False), encoding="utf-8")
        return path

    def _run_target(self, design: Path, out: Path) -> subprocess.CompletedProcess[str]:
        return run_init_case(
            "--category", "mlp", "--kind", "final_model_retraining",
            "--design", str(design), "--output", str(out),
            "--target", "dftworld", check=False,
        )

    def test_generic_mode_unchanged_without_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = run_init_case(
                "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(CASE_FIXTURES / "simple-mlp-local" / "case-design.yaml"),
                "--output", str(out),
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            # Template task.toml (schema_version 1) is preserved in generic mode.
            toml = (out / "task.toml").read_text(encoding="utf-8")
            self.assertIn("schema_version", toml)
            self.assertFalse((out / "Dockerfile").exists())
            self.assertFalse((out / "source" / "dftworld-target.lock.json").exists())

    def test_target_outside_repo_fails_cleanly_and_preserves_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = self._run_target(self._write_design(Path(td)), out)
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("dftworld repository", proc.stderr)
            # Generic scaffold preserved.
            self.assertTrue((out / "task.toml").is_file())
            self.assertTrue((out / "case-design.yaml").is_file())
            self.assertFalse((out / "Dockerfile").exists())

    def test_target_adapter_failure_preserves_scaffold(self) -> None:
        # Fake repository root whose dftworld_bench is empty: the adapter CLI
        # cannot run, so the target render fails after the scaffold is written.
        with tempfile.TemporaryDirectory() as td:
            fake_root = Path(td) / "repo"
            (fake_root / "dftworld_bench").mkdir(parents=True)
            out = fake_root / "case"
            proc = self._run_target(self._write_design(Path(td)), out)
            self.assertNotEqual(0, proc.returncode)
            self.assertIn("dftworld target render failed", proc.stderr)
            self.assertTrue((out / "task.toml").is_file())
            self.assertTrue((out / "case-design.yaml").is_file())

    @unittest.skipUnless(
        (Path(__file__).resolve().parents[2] / "dftworld_bench").is_dir(),
        "dftworld repository not present",
    )
    def test_target_inside_repo_renders_executable_draft(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=repo_root) as td:
            out = Path(td) / "case"
            proc = self._run_target(self._write_design(Path(td)), out)
            self.assertEqual(0, proc.returncode, proc.stderr)
            summary = json.loads(proc.stdout)
            self.assertEqual("dftworld", summary["target"])
            self.assertIs(False, summary["benchmark_valid"])
            toml = (out / "task.toml").read_text(encoding="utf-8")
            self.assertIn('schema_version = "1.2"', toml)
            self.assertTrue((out / "Dockerfile").is_file())
            self.assertTrue((out / ".dockerignore").is_file())
            lock = out / "source" / "dftworld-target.lock.json"
            self.assertTrue(lock.is_file())
            bv = json.loads((out / "benchmark_valid.json").read_text(encoding="utf-8"))
            self.assertIs(False, bv["benchmark_valid"])
