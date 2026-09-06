#!/usr/bin/env python3
"""Documentation contract tests for Task 6 — truthful docs, no leaked strategy.

Asserts, statically, that the 034 documentation:
  * never claims the hidden set is HPC-generated (it is derived from the expert
    AIMD mother set — reference/hidden-validation/README.md says so verbatim),
    and the agent-visible tree never references the hidden set at all;
  * keeps the scientific thresholds DRAFT (reference/thresholds.json has
    draft:true and a freeze_note; nothing claims they are frozen) and pins the
    freeze to the HPC dual reference (hpc-ref-01 / hpc-ref-02);
  * requires the pinned scoring-compatibility contract: the same package
    versions the reference runs on are recorded in env-hpc.sh, the launcher
    gates sync-back scoring on the model-compat smoke (dp-test.done), and the
    internal design documents record that contract as an anchoring precondition.

These are regression guards: they protect truths that are easy to break with a
stale edit (e.g. a line claiming "the hidden set was generated on the cluster"
or "thresholds are frozen").
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


CASE = Path(__file__).resolve().parents[1]
PUBLIC = CASE / "public"
REF = CASE / "reference"
EXPERT = CASE / "solution" / "expert"
ABLATION = CASE / "ablation"

# Files the agent actually sees: the COPY public/ -> /app surface plus the
# instruction prompt. CONTRACT.md is deliberately NOT here — it is the
# repo-internal builder contract that legitimately names the hidden evaluator
# and DeePMD "frozen" models (frozen_model.pb/compress.pb) and is never copied
# into the agent's /app. None of the agent-facing files may reference the hidden
# set or claim thresholds are frozen.
AGENT_FACING = [
    PUBLIC / "system.json",
    CASE / "instruction.md",
]

# Tokens that must never appear in a current-release (non-historical, non-test)
# file: the deleted public coordinate (both the public/ and the container /app
# alias) and the retired launcher.
FORBIDDEN = ("public/water64.xyz", "/app/water64.xyz", "run_reference_hpc.sh")

# Path substrings excluded from the release-tree scan: historical plans/specs,
# evidence dirs, test fixtures, and the hidden expert lineage / runtime evidence.
SCAN_EXCLUDE = (
    "/docs/",                # docs/superpowers/{plans,specs} are historical
    "/evidence/",            # evidence dirs are frozen run records
    "/fixtures/",            # test fixtures (good/ reference outputs)
    "/expert-trajectory/",   # hidden expert lineage (historical data)
    "/runtime-evidence/",    # historical runtime evidence
)

SCAN_SUFFIXES = (".py", ".json", ".md", ".sh", ".toml", ".txt", ".yml", ".yaml", ".slurm")


def text(p: Path) -> str:
    return p.read_text()


class DocsContract(unittest.TestCase):
    # -- 1. hidden-set provenance: expert AIMD, never HPC --------------------
    def test_hidden_validation_readme_says_no_hpc(self) -> None:
        t = text(REF / "hidden-validation" / "README.md")
        self.assertIn("no HPC", t, "hidden-validation README must state the hidden set needs no HPC run")

    def test_reference_json_attributes_hidden_to_expert_aimd(self) -> None:
        d = json.loads(text(REF / "reference.json"))
        role = d["aimd"]["hidden_validation_role"].lower()
        self.assertIn("mother set", role, "reference.json must call the hidden set the expert AIMD mother set")
        self.assertNotIn("hpc", role, "reference.json must not attribute the hidden set to HPC generation")

    def test_agent_facing_files_never_reference_hidden_set(self) -> None:
        for f in AGENT_FACING:
            self.assertNotIn(
                "hidden",
                text(f).lower(),
                f"{f.relative_to(CASE)} must not reference the hidden validation set",
            )

    # -- 1b. verifier env stays verifier-only (user decision 2026-08-12) ----
    def test_agent_runtime_env_excludes_verifier_env(self) -> None:
        """task.toml [verifier.env] belongs to the hidden verifier only: the
        agent container receives only /app (public/) + the prompt, so the four
        verifier variables must never be baked into the agent image ENV (task or
        base Dockerfile) nor be named in any agent-facing file.  User decision:
        B1-B4 (hidden paths / NVT params) are kept for verifier reproducibility;
        this regression locks the current delivery model against regressions."""
        import tomllib
        with open(CASE / "task.toml", "rb") as f:
            ver_env = tomllib.load(f)["verifier"]["env"]
        self.assertEqual(
            set(ver_env),
            {"AI2KIT_HIDDEN_VALIDATION", "AI2KIT_HIDDEN_RDF_REFERENCE",
             "AI2KIT_NVT_STEPS", "AI2KIT_NVT_TEMPERATURE"},
            "verifier env keys changed — update this regression",
        )
        # Image-defined env: the task Dockerfile + every base-image Dockerfile
        # under runtimes/recipes/ (superset guard: none may bake the vars in).
        dockerfiles = []
        if (CASE / "Dockerfile").exists():
            dockerfiles.append(CASE / "Dockerfile")
        recipes_dir = CASE.parents[1] / "runtimes" / "recipes"
        if recipes_dir.exists():
            dockerfiles += sorted(recipes_dir.rglob("Dockerfile*"))
        env_sources = "\n".join(
            f"# {df}\n" + df.read_text(errors="replace") for df in dockerfiles)
        for name in ver_env:
            self.assertNotIn(f"ENV {name}=", env_sources,
                             f"{name} must not be baked into the agent image ENV")
            for f in AGENT_FACING:
                self.assertNotIn(
                    name, text(f),
                    f"{f.relative_to(CASE)} must not name the verifier-only var {name}")

    # -- 2. thresholds stay DRAFT until the HPC dual reference ----------------
    def test_thresholds_file_is_draft(self) -> None:
        d = json.loads(text(REF / "thresholds.json"))
        self.assertTrue(d["draft"], "thresholds.json must remain draft:true (no freeze yet)")
        self.assertIn("draft", d["status"], "status must say draft while the reference is pending")
        self.assertIn("freeze_note", d, "thresholds.json must still document its freeze conditions")

    def test_thresholds_freeze_note_pins_hpc_dual_reference(self) -> None:
        n = text(REF / "thresholds.json")
        self.assertIn("hpc-ref-01", n, "freeze conditions must name the HPC dual reference (hpc-ref-01)")
        self.assertIn("hpc-ref-02", n, "freeze conditions must name the HPC dual reference (hpc-ref-02)")
        self.assertNotIn('"draft": false', n, "thresholds must not be flipped to frozen")

    def test_nothing_claims_thresholds_are_frozen(self) -> None:
        for f in AGENT_FACING:
            if f.suffix == ".md":
                self.assertNotIn(
                    "frozen",
                    text(f).lower(),
                    f"{f.relative_to(CASE)} must not claim thresholds are frozen",
                )

    # -- 3. pinned scoring-compatibility contract -----------------------------
    def test_pinned_versions_recorded_in_env_hpc(self) -> None:
        t = text(EXPERT / "env-hpc.sh")
        self.assertIn("AI2KIT_REQUIRED_DEEPMD=", t, "env-hpc.sh must pin deepmd-kit==2.2.11")
        self.assertIn("deepmd-kit==2.2.11", t)
        self.assertIn("ai2-kit==1.1.0", t, "env-hpc.sh must pin ai2-kit==1.1.0")
        self.assertIn("oh-my-batch==0.7.6", t, "env-hpc.sh must pin omb==0.7.6")

    def test_ablation_readme_names_hpc_dual_anchor(self) -> None:
        t = text(ABLATION / "README.md")
        self.assertIn("hpc-ref-01", t, "ablation README must document the HPC dual reference as the anchor")
        self.assertIn("hpc-ref-02", t)

    def test_design_documents_scoring_compat_gate(self) -> None:
        t = text(ABLATION / "hpc-transport" / "DESIGN.md")
        self.assertIn(
            "dp-test",
            t,
            "HPC transport DESIGN must document the model-compat (dp-test) gate as an anchoring precondition",
        )
        self.assertIn(
            "2.2.11",
            t,
            "DESIGN must record the pinned deepmd version the scoring surface runs on",
        )

    def test_public_surface_contains_no_environment_recipe(self) -> None:
        self.assertFalse((PUBLIC / "HPC_ENVIRONMENT.md").exists())
        joined = "\n".join(text(f) for f in AGENT_FACING)
        for forbidden in ("module load", "ssh hpc", "/public/home", "10.26."):
            self.assertNotIn(forbidden, joined)

    def test_release_tree_is_coordinate_free_and_launcher_free(self) -> None:
        """Directory-walk scan: every current-release (non-historical, non-test)
        file in the case tree + harness must not reference the deleted public
        coordinate (public/water64.xyz or /app/water64.xyz) or the retired
        launcher (run_reference_hpc.sh).  Historical plans/specs, evidence,
        fixtures, the hidden expert lineage, and test/mutation modules are
        excluded by path/name."""
        roots = [CASE, CASE.parent / "scripts" / "ablation"]
        offenders: list[str] = []
        for root in roots:
            for p in sorted(root.rglob("*")):
                if not p.is_file() or p.suffix not in SCAN_SUFFIXES:
                    continue
                rel = p.as_posix()
                if any(s in rel for s in SCAN_EXCLUDE):
                    continue
                if p.name.startswith("test_") or p.name.startswith("mutate_"):
                    continue
                content = p.read_text(errors="replace")
                for tok in FORBIDDEN:
                    if tok in content:
                        offenders.append(f"{p.relative_to(CASE.parent)}: {tok}")
        self.assertFalse(
            offenders,
            "release tree references a deleted coordinate/launcher:\n" + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
