"""MLP category regression tests for build-scientific-benchmark-case.

Adapted from portable v1.1 literature-to-mlp-spec tests. The v1.1 draft-envelope
test is replaced by the scaffold-refuses-blocked-design gate (Task 4), and the
installer atomicity test is kept for the new install.sh (Task 11); both are
forward-gated via skipUnless so the suite stays green as tasks land.
"""
from __future__ import annotations

import copy
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
SKILL = PACKAGE / "skills" / "build-scientific-benchmark-case"
MLP_SCRIPTS = SKILL / "scripts/categories/mlp"
MLP_REFS = SKILL / "references/categories/mlp"
EXAMPLE = PACKAGE / "tests/fixtures" / "minimal-production-model"
FIXTURES = PACKAGE / "tests/fixtures"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_helper(name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MLP_SCRIPTS / name), *args],
        text=True,
        capture_output=True,
        check=check,
    )


def run_common(name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "scripts/common" / name), *args],
        text=True,
        capture_output=True,
        check=check,
    )


class MlpCategoryTests(unittest.TestCase):
    def test_behavior_fixture_contracts_exist(self) -> None:
        expected = {
            "dpmp-multi-role", "conflicting-config",
            "non-deepmd-model", "blocked-reproduction",
        }
        for name in expected:
            contract = load_yaml(FIXTURES / name / "behavior.yaml")
            self.assertIn("stop_before_execution", contract["must"])
            self.assertIn("auto_execute_training", contract["must_not"])

    def run_readiness(self, spec: dict, evidence: dict | None = None) -> dict:
        evidence = evidence or load_yaml(EXAMPLE / "source-evidence-map.yaml")
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            spec_path = temp / "spec.yaml"
            evidence_path = temp / "evidence.yaml"
            output = temp / "assessment.yaml"
            spec_path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
            evidence_path.write_text(yaml.safe_dump(evidence, sort_keys=False), encoding="utf-8")
            run_helper(
                "check_readiness.py", str(spec_path),
                "--evidence", str(evidence_path), "--output", str(output),
            )
            return load_yaml(output)

    def test_commands_use_skill_directory_variable(self) -> None:
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for script in ("validate_spec.py", "check_readiness.py", "hash_sources.py"):
            self.assertIn(
                f"${{CLAUDE_SKILL_DIR}}/scripts/categories/mlp/{script}",
                skill,
            )
        self.assertNotIn("python scripts/", skill)

    def test_shipped_assessment_is_exact_script_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "assessment.yaml"
            run_helper(
                "check_readiness.py",
                str(EXAMPLE / "mlp-reproduction-spec.yaml"),
                "--evidence", str(EXAMPLE / "source-evidence-map.yaml"),
                "--output", str(output),
            )
            actual = load_yaml(output)
        expected = load_yaml(EXAMPLE / "reproducibility-assessment.yaml")
        self.assertEqual(expected, actual)

    def test_final_retraining_requires_architecture_and_data_binding(self) -> None:
        spec = load_yaml(EXAMPLE / "mlp-reproduction-spec.yaml")
        spec["model"]["architecture"] = {"cutoff": None, "parameters": {}}
        spec["training"]["data_binding"] = {
            "train_systems": [], "validation_systems": [], "test_systems": []
        }
        result = self.run_readiness(spec)
        self.assertNotEqual("ready", result["readiness"]["execution_readiness"])
        fields = {row["field"] for row in result["missing"]}
        self.assertIn("model.architecture", fields)
        self.assertIn("training.data_binding.train_systems", fields)

    def test_model_level_mapping_must_contain_split_metric_and_procedure(self) -> None:
        spec = load_yaml(EXAMPLE / "mlp-reproduction-spec.yaml")
        spec["validation"]["model_level"] = {
            "dataset_or_split": None,
            "metrics": [],
            "reference_values": [],
            "procedure": None,
        }
        result = self.run_readiness(spec)
        self.assertNotEqual("ready", result["readiness"]["verification_readiness"])

    def test_generic_schema_accepts_mace_architecture_without_deepmd_fields(self) -> None:
        spec = load_yaml(EXAMPLE / "mlp-reproduction-spec.yaml")
        spec["target"]["primary"].update({
            "reported_name": "MACE-medium", "model_family": "MACE", "variant": "medium",
        })
        spec["model"] = {
            "family": "MACE",
            "variant": "medium",
            "architecture": {
                "cutoff": {"value": 5.0, "unit": "angstrom", "claim_status": "observed", "evidence": ["ev-cutoff"]},
                "parameters": {"num_interactions": 2, "hidden_irreps": "128x0e+128x1o", "correlation": 3},
            },
            "precision": "float64",
            "initialization": None,
            "evidence": ["ev-cutoff"],
        }
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "mace.yaml"
            path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
            proc = run_helper(
                "validate_spec.py", str(path),
                "--evidence", str(EXAMPLE / "source-evidence-map.yaml"), check=False,
            )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertNotIn("embedding_network", spec["model"]["architecture"]["parameters"])

    def test_license_conflict_only_blocks_benchmark_dimension(self) -> None:
        spec = load_yaml(EXAMPLE / "mlp-reproduction-spec.yaml")
        evidence = load_yaml(EXAMPLE / "source-evidence-map.yaml")
        evidence["evidence"]["ev-license-a"] = {
            "source_id": "paper-001", "source_kind": "paper",
            "locator": {"section": "Data availability"}, "claim": "licenses.model",
        }
        evidence["evidence"]["ev-license-b"] = {
            "source_id": "repo-001", "source_kind": "repository_config",
            "locator": {"path": "LICENSE"}, "claim": "licenses.model",
        }
        evidence["conflicts"] = [{
            "conflict_id": "license-conflict",
            "field": "licenses.model",
            "severity": "critical",
            "evidence": ["ev-license-a", "ev-license-b"],
            "resolution": {"status": "unresolved", "selected_evidence": None, "rationale": None},
        }]
        result = self.run_readiness(spec, evidence)
        self.assertEqual("ready", result["readiness"]["execution_readiness"])
        self.assertEqual("ready", result["readiness"]["verification_readiness"])
        self.assertEqual("blocked", result["readiness"]["benchmark_case_readiness"])

    def test_noncritical_unknowns_remain_visible(self) -> None:
        result = self.run_readiness(load_yaml(EXAMPLE / "mlp-reproduction-spec.yaml"))
        missing = {row["field"]: row for row in result["missing"]}
        self.assertIn("training.seed", missing)
        self.assertEqual([], missing["training.seed"]["critical_for"])

    def test_validator_rejects_invalid_scope_role_and_access(self) -> None:
        spec = load_yaml(EXAMPLE / "mlp-reproduction-spec.yaml")
        spec["reproduction_scope"]["target"] = "magic_scope"
        spec["target"]["primary"]["role"] = "favorite"
        spec["access"]["dataset"]["status"] = "sometimes"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "spec.yaml"
            path.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
            proc = run_helper(
                "validate_spec.py", str(path),
                "--evidence", str(EXAMPLE / "source-evidence-map.yaml"), check=False,
            )
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("reproduction_scope.target", proc.stderr)
        self.assertIn("target.primary.role", proc.stderr)
        self.assertIn("access.dataset.status", proc.stderr)

    def test_hash_sources_rejects_symlink_and_marks_dirty_repo_partial(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            source = temp / "source"
            source.mkdir()
            outside = temp / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (source / "escape").symlink_to(outside)
            proc = run_helper(
                "hash_sources.py", "--dir", f"data={source}",
                "--output", str(temp / "bad.json"), check=False,
            )
            self.assertNotEqual(0, proc.returncode)

            repo = temp / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            (repo / "config.yaml").write_text("value: 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "config.yaml"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
            (repo / "config.yaml").write_text("value: 2\n", encoding="utf-8")
            output = temp / "repo.json"
            run_helper("hash_sources.py", "--repo", f"repo={repo}", "--output", str(output))
            lock = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual("partial", lock["sources"]["repo"]["identity_status"])
        self.assertTrue(lock["sources"]["repo"]["worktree_dirty"])

    @unittest.skipUnless(
        (SKILL / "scripts/common/init_case.py").exists(),
        "init_case.py not built yet (Task 4)",
    )
    def test_scaffold_refuses_blocked_design_and_never_sets_valid(self) -> None:
        blocked_design = FIXTURES / "blocked-mlp" / "case-design.yaml"
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = run_common(
                "init_case.py",
                "--category", "mlp",
                "--kind", "final_model_retraining",
                "--design", str(blocked_design),
                "--output", str(out),
                check=False,
            )
        self.assertNotEqual(0, proc.returncode, proc.stderr)
        self.assertFalse((out / "benchmark_valid.json").exists())

    @unittest.skipUnless(
        (PACKAGE / "install.sh").exists(),
        "install.sh not migrated yet (Task 11)",
    )
    def test_installer_preserves_existing_skill_if_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            existing = temp / ".claude/skills/build-scientific-benchmark-case"
            existing.mkdir(parents=True)
            marker = existing / "keep.txt"
            marker.write_text("old", encoding="utf-8")
            fake_bin = temp / "bin"
            fake_bin.mkdir()
            fake_cp = fake_bin / "cp"
            fake_cp.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
            fake_cp.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            proc = subprocess.run(
                ["bash", str(PACKAGE / "install.sh"), "--host", "claude-code", "--scope", "project"],
                cwd=temp, env=env, text=True, capture_output=True,
            )
            self.assertEqual(42, proc.returncode, proc.stderr)
            self.assertEqual("old", marker.read_text(encoding="utf-8"))


class MlpAcceptanceTests(unittest.TestCase):
    """Deterministic end-to-end MLP flows (Task 12 Step 1)."""

    def run_common_json(self, name: str, *args: str, check: bool = True) -> dict:
        return json.loads(run_common(name, *args, check=check).stdout)

    def test_flow_simple_mlp_local_constructed_pending_reference(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_common(
                "init_case.py", "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(FIXTURES / "simple-mlp-local" / "case-design.yaml"),
                "--output", str(out),
            )
            self.assertTrue(self.run_common_json("validate_case.py", str(out), "--json")["valid"])
            self.assertTrue(self.run_common_json("audit_candidate_bundle.py", str(out), "--json")["valid"])
            state = self.run_common_json("derive_validation_state.py", str(out))
            self.assertEqual("draft", state["case_status"])
            # run the reference: state advances, but validity stays fail-closed
            ref_path = out / "reference/reference.json"
            reference = json.loads(ref_path.read_text(encoding="utf-8"))
            reference["state"] = "formal_executed"
            ref_path.write_text(json.dumps(reference), encoding="utf-8")
            state = self.run_common_json("derive_validation_state.py", str(out))
            self.assertEqual("constructed", state["case_status"])
            self.assertIs(False, state["benchmark_valid"])

    def test_flow_water64_hpc_full_capability_plan_invalid_pending_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_common(
                "init_case.py", "--category", "mlp", "--kind", "end_to_end_model_development",
                "--design", str(FIXTURES / "water64-like-hpc" / "case-design.yaml"),
                "--output", str(out),
            )
            self.assertTrue(self.run_common_json("validate_case.py", str(out), "--json")["valid"])
            plan_out = out / "verifier-plan.yaml"
            run_helper(
                "derive_verifier_plan.py", "--design", str(out / "case-design.yaml"),
                "--output", str(plan_out),
            )
            ids = [layer["id"] for layer in load_yaml(plan_out)["layers"]]
            for expected in ("MLP-V3", "MLP-V4", "MLP-V5", "MLP-V6", "C-V7", "C-V8"):
                self.assertIn(expected, ids)
            plan = load_yaml(plan_out)
            self.assertEqual(4, plan["fixtures"]["negative"])
            verdict = self.run_common_json("generate_fixture_matrix.py", str(out), "--json")
            # v2.3 template ships eight executable negatives (four standard +
            # four C-V8 integrity probes), so the matrix is closed at scaffold
            # time for the default and 4-count plans
            self.assertTrue(verdict["valid"], verdict["errors"])
            # count-based closure is fail-closed: losing more than the surplus
            # over the plan's hard-outcome count must break the matrix
            for lost in ("broken-lineage", "empty", "missing-model",
                         "forged-manifest", "missing-artifact"):
                shutil.rmtree(out / "tests/fixtures/negative" / lost)
            verdict = self.run_common_json(
                "generate_fixture_matrix.py", str(out), "--json", check=False
            )
            self.assertFalse(verdict["valid"])  # 3 < required 4
            # 034-like complexity must not leak into Common Core
            leak = []
            for root in (SKILL / "references/common", SKILL / "assets/case-template/common"):
                for path in sorted(root.rglob("*")):
                    if not path.is_file():
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace").lower()
                    if "water64" in text or "ai2-kit" in text:
                        leak.append(str(path.relative_to(SKILL)))
            self.assertEqual([], leak)

    def test_flow_blocked_sources_draft_with_blockers_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            proc = run_common(
                "init_case.py", "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(FIXTURES / "blocked-mlp" / "case-design.yaml"),
                "--output", str(out), check=False,
            )
            self.assertNotEqual(0, proc.returncode)
            self.assertFalse((out / "benchmark_valid.json").exists())
            run_common(
                "init_case.py", "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(FIXTURES / "blocked-mlp" / "case-design.yaml"),
                "--output", str(out), "--allow-blocked",
            )
            design = load_yaml(out / "case-design.yaml")
            self.assertTrue(design["blockers"])
            validation = json.loads((out / "VALIDATION.json").read_text(encoding="utf-8"))
            self.assertEqual("draft", validation["case_status"])
            self.assertIs(False, validation["benchmark_valid"])

    def test_flow_alternative_valid_layout_outcome_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_common(
                "init_case.py", "--category", "mlp", "--kind", "end_to_end_model_development",
                "--design", str(FIXTURES / "alternative-valid-layout" / "case-design.yaml"),
                "--output", str(out),
            )
            plan_out = out / "verifier-plan.yaml"
            run_helper(
                "derive_verifier_plan.py", "--design", str(out / "case-design.yaml"),
                "--output", str(plan_out),
            )
            ids = [layer["id"] for layer in load_yaml(plan_out)["layers"]]
            self.assertIn("MLP-V5", ids)  # alternative dynamic layout still outcome-checked
            for cls, count in (("positive", 1), ("negative", 3), ("alternative_valid", 1)):
                base = out / "tests/fixtures" / {"positive": "positive",
                                                 "negative": "negative",
                                                 "alternative_valid": "alternative-valid"}[cls]
                for i in range(count):
                    (base / f"{cls}_{i}").mkdir(parents=True)
            verdict = self.run_common_json("generate_fixture_matrix.py", str(out), "--json")
            self.assertTrue(verdict["valid"], verdict["errors"])

    def test_flow_gaming_fixture_boundary_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "case"
            run_common(
                "init_case.py", "--category", "mlp", "--kind", "final_model_retraining",
                "--design", str(FIXTURES / "verifier-gaming" / "case-design.yaml"),
                "--output", str(out),
            )
            (out / "public/reference").mkdir(parents=True)
            (out / "public/reference/leak.txt").write_text("x", encoding="utf-8")
            verdict = self.run_common_json(
                "audit_candidate_bundle.py", str(out), "--json", check=False
            )
            self.assertFalse(verdict["valid"])
            self.assertTrue(any("forbidden path" in e for e in verdict["errors"]), verdict["errors"])


class DeriveVerifierPlanTests(unittest.TestCase):
    DERIVE = "derive_verifier_plan.py"
    MATRIX = "generate_fixture_matrix.py"

    def plan_ids(self, design_name: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "verifier-plan.yaml"
            proc = run_helper(
                self.DERIVE,
                "--design", str(FIXTURES / design_name / "case-design.yaml"),
                "--output", str(out),
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            return [layer["id"] for layer in load_yaml(out)["layers"]]

    def test_derive_base_layers_always_on(self) -> None:
        ids = self.plan_ids("simple-mlp-local")
        for expected in ("MLP-V0", "MLP-V1", "MLP-V2", "MLP-V4", "C-V7", "C-V8"):
            self.assertIn(expected, ids)
        for absent in ("MLP-V3", "MLP-V5", "MLP-V6"):
            self.assertNotIn(absent, ids)

    def test_derive_conditional_layers_require_capability(self) -> None:
        ids = self.plan_ids("alternative-valid-layout")
        for expected in ("MLP-V3", "MLP-V4", "MLP-V5", "C-V7", "C-V8"):
            self.assertIn(expected, ids)
        self.assertNotIn("MLP-V6", ids)

    def test_derive_unknown_capability_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            design = load_yaml(FIXTURES / "simple-mlp-local" / "case-design.yaml")
            design["workflow_capabilities"] = ["hidden_static_accuracy", "time_travel"]
            design_path = Path(td) / "case-design.yaml"
            design_path.write_text(yaml.safe_dump(design), encoding="utf-8")
            out = Path(td) / "verifier-plan.yaml"
            proc = run_helper(
                self.DERIVE, "--design", str(design_path), "--output", str(out),
                check=False,
            )
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("unknown capability", proc.stderr)

    def test_derive_negative_closure_scales_with_hard_layers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "verifier-plan.yaml"
            run_helper(
                self.DERIVE,
                "--design", str(FIXTURES / "alternative-valid-layout" / "case-design.yaml"),
                "--output", str(out),
            )
            plan = load_yaml(out)
        hard = [l for l in plan["layers"] if l["hard_outcome"]]
        self.assertEqual(3, len(hard))
        self.assertEqual(3, plan["fixtures"]["negative"])

    def test_matrix_closure_fail_closed_then_valid(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            case = Path(td) / "case"
            proc = run_common(
                "init_case.py", "--category", "mlp", "--kind", "end_to_end_model_development",
                "--design", str(FIXTURES / "alternative-valid-layout" / "case-design.yaml"),
                "--output", str(case),
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            plan_out = case / "verifier-plan.yaml"
            run_helper(
                self.DERIVE, "--design", str(case / "case-design.yaml"),
                "--output", str(plan_out),
            )
            verdict = json.loads(
                run_common(self.MATRIX, str(case), "--json").stdout
            )
            self.assertTrue(verdict["valid"], verdict["errors"])
            shutil.rmtree(case / "tests/fixtures/negative")
            verdict = json.loads(
                run_common(self.MATRIX, str(case), "--json", check=False).stdout
            )
            self.assertFalse(verdict["valid"])
            self.assertTrue(any("negative" in e for e in verdict["errors"]), verdict["errors"])
            for cls, count in (("positive", 1), ("negative", 3), ("alternative_valid", 1)):
                base = case / "tests/fixtures" / {"positive": "positive",
                                                  "negative": "negative",
                                                  "alternative_valid": "alternative-valid"}[cls]
                base.mkdir(parents=True, exist_ok=True)
                for i in range(count):
                    (base / f"{cls}_{i}").mkdir(parents=True)
            verdict = json.loads(run_common(self.MATRIX, str(case), "--json").stdout)
            self.assertTrue(verdict["valid"], verdict["errors"])


if __name__ == "__main__":
    unittest.main()
