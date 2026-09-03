"""Package-level architecture tests for build-scientific-benchmark-case."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

PACKAGE = Path(__file__).resolve().parents[1]
SKILLS = PACKAGE / "skills"
BUILDER = SKILLS / "build-scientific-benchmark-case"


def load_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, raw, _ = text.split("---", 2)
    return yaml.safe_load(raw)


class PackageArchitectureTests(unittest.TestCase):
    def test_package_exposes_exactly_one_skill(self):
        skill_dirs = [p for p in SKILLS.iterdir() if (p / "SKILL.md").is_file()]
        self.assertEqual(skill_dirs, [BUILDER])

    def test_builder_is_explicit_only(self):
        frontmatter = load_frontmatter(BUILDER / "SKILL.md")
        self.assertEqual(frontmatter["name"], "build-scientific-benchmark-case")
        self.assertIs(frontmatter["disable-model-invocation"], True)


def package_files() -> list[str]:
    return sorted(
        str(p.relative_to(PACKAGE))
        for p in PACKAGE.rglob("*")
        if p.is_file() and p.name != "SHA256SUMS"
        and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )


class PackageContractTests(unittest.TestCase):
    def test_manifest_version_is_2_3_0(self) -> None:
        manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("2.3.0", manifest["version"])
        self.assertEqual(["build-scientific-benchmark-case"], manifest["skills"])
        self.assertEqual("literature-to-mlp-spec", manifest["legacy"]["name"])

    def test_sha256sums_lists_every_file_exactly_once(self) -> None:
        lines = (PACKAGE / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        listed: list[str] = []
        for line in lines:
            self.assertRegex(line, r"^[0-9a-f]{64}  (.+)$")
            listed.append(line.split("  ", 1)[1])
        self.assertEqual(listed, package_files(), "SHA256SUMS must list every file exactly once")

    def test_sha256sums_hashes_match(self) -> None:
        for line in (PACKAGE / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            expected, file = line.split("  ", 1)
            actual = hashlib.sha256((PACKAGE / file).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, file)

    def test_install_sh_presence_and_syntax(self) -> None:
        install = PACKAGE / "install.sh"
        self.assertTrue(install.is_file())
        self.assertTrue(install.stat().st_mode & 0o111, "install.sh must be executable")
        proc = subprocess.run(["bash", "-n", str(install)], text=True, capture_output=True)
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_install_sh_has_migration_notice_and_remove_legacy(self) -> None:
        text = (PACKAGE / "install.sh").read_text(encoding="utf-8")
        self.assertIn("--remove-legacy", text)
        self.assertIn("literature-to-mlp-spec", text)
        self.assertIn("mode=extract-spec category=mlp", text)

    def test_template_ships_runnable_verifier_chain(self) -> None:
        tests = BUILDER / "assets/case-template/common/tests"
        self.assertTrue((tests / "test.sh").is_file())
        self.assertTrue((tests / "test.sh").stat().st_mode & 0o111, "test.sh must be executable")
        self.assertTrue((tests / "verifier.py").is_file())
        self.assertTrue((tests / "test_verifier_contract.py").is_file())
        self.assertTrue(
            (BUILDER / "assets/case-template/common/public/submission-schema.json").is_file()
        )
        for name in ("forged-manifest", "missing-model", "broken-lineage",
                     "missing-artifact", "multi-hash-mismatch",
                     "missing-plus-mismatch", "type-garbage-manifest"):
            fixture = tests / "fixtures/negative" / name
            self.assertTrue(fixture.is_dir(), f"negative fixture {name} missing")
            self.assertTrue(
                any(p.is_file() and p.name not in ("README.md", ".gitkeep")
                    for p in fixture.rglob("*")),
                f"negative fixture {name} has no executable submission content",
            )
        empty = tests / "fixtures/negative/empty"
        self.assertTrue(empty.is_dir(), "negative fixture empty missing")
        self.assertFalse(
            any(p.is_file() and p.name not in ("README.md", ".gitkeep")
                for p in empty.rglob("*")),
            "the empty fixture must stay submission-empty to prove NO_SUBMISSION",
        )
        structural = tests / "fixtures/positive/structural-minimal"
        self.assertTrue((structural / "manifest.json").is_file())

    def test_registry_closure(self) -> None:
        registry = yaml.safe_load((BUILDER / "references/category-registry.yaml").read_text(encoding="utf-8"))
        mlp = registry["categories"]["mlp"]
        for field in ("references_root", "scripts_root", "template_root"):
            root = BUILDER / mlp[field]
            self.assertTrue(root.exists(), f"{field} {root} missing")

    def test_skill_internal_reference_closure(self) -> None:
        text = (BUILDER / "SKILL.md").read_text(encoding="utf-8")
        for ref in ("references/category-registry.yaml", "references/common/case-standard.md",
                    "references/common/lifecycle-and-gates.md",
                    "references/categories/mlp/workflow-capabilities.md",
                    "references/categories/mlp/verifier-policy.md",
                    "scripts/categories/mlp/validate_spec.py",
                    "scripts/categories/mlp/check_readiness.py",
                    "scripts/categories/mlp/hash_sources.py",
                    "scripts/common/init_case.py",
                    "scripts/common/validate_case.py",
                    "scripts/common/classify_failure.py",
                    "scripts/categories/mlp/check_draft_consistency.py",
                    "scripts/common/check_discovery_runnable.py",
                    "references/common/mvp-runnable-draft.md",
                    "references/common/discovery-and-refinement.md",
                    "references/common/cross-layer-consistency.md",
                    "references/categories/mlp/prompt-contract.md"):
            self.assertTrue((BUILDER / ref).exists(), f"SKILL.md references missing {ref}")

    def test_python_scripts_compile(self) -> None:
        scripts = sorted((BUILDER / "scripts").rglob("*.py"))
        self.assertGreaterEqual(len(scripts), 8)
        for script in scripts:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(script)],
                text=True, capture_output=True,
            )
            self.assertEqual(0, proc.returncode, f"{script}: {proc.stderr}")

    def test_no_unfinished_markers_in_builder_logic(self) -> None:
        markers = ("TODO", "TBD", "FIXME", "XXX", "not implemented")
        for root in ("references", "scripts"):
            for path in sorted((BUILDER / root).rglob("*")):
                if not path.is_file():
                    continue
                for marker in markers:
                    self.assertNotIn(
                        marker, path.read_text(encoding="utf-8", errors="replace"),
                        f"{path.relative_to(BUILDER)} contains {marker}",
                    )
