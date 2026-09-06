#!/usr/bin/env python3
"""Contract tests for Task 5 — seeds, provenance, reference failure semantics.

Asserts, statically, that the expert workflow and the reference launcher:
  * use NO bare randomness (no shell ``$RANDOM``, no omb ``add_randint``)
  * derive every sub-seed from a locked ``AI2KIT_ROOT_SEED``
  * treat an NVT/calibration failure as a hard failure, never a warning
  * write success markers only after output validation
  * run the reference with two declared, distinct root seeds and fixed
    resources, gated on the model-compatibility smoke before sync-back
    scoring, with a provenance manifest and no threshold-freeze command.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


CASE = Path(__file__).resolve().parents[1]
EXPERT = CASE / "solution" / "expert"
AL_DIR = EXPERT / "03-active-learning"
G9_COMMON = CASE.parent / "scripts" / "ablation" / "hpc" / "g9_hpc_common.sh"
G9_SUBMIT = CASE.parent / "scripts" / "ablation" / "hpc" / "g9_reference_submit.sh"
G9_FETCH = CASE.parent / "scripts" / "ablation" / "hpc" / "g9_reference_fetch.sh"
G9_TEMPLATE = CASE.parent / "scripts" / "ablation" / "hpc" / "g9_reference.slurm.template"
ENV_SH = EXPERT / "env.sh"
NVT_RUN = EXPERT / "04-validation" / "nvt" / "run.sh"
ITER_WF = AL_DIR / "workflow" / "iter-classic-dp-lammps-cp2k.sh"
PROD_WF = AL_DIR / "workflow" / "prod-dp-lammps.sh"
AL_RUN = AL_DIR / "run.sh"
PROVENANCE = EXPERT / "provenance.sh"


def sh_files_under(d: Path):
    return [p for p in d.rglob("*.sh") if p.is_file()]


class ReferenceContract(unittest.TestCase):
    def text(self, p: Path) -> str:
        self.assertTrue(p.is_file(), f"missing file: {p}")
        return p.read_text()

    # -- 1. no bare randomness in the expert workflow -------------------------
    def test_no_bare_RANDOM_in_expert(self) -> None:
        for f in sh_files_under(EXPERT):
            for lineno, line in enumerate(self.text(f).splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue  # comments may explain *why* randomness is banned
                self.assertNotIn(
                    "RANDOM",
                    line,
                    f"{f.relative_to(CASE)}:{lineno} references RANDOM (bare shell randomness)",
                )

    def test_no_omb_randint_in_active_learning(self) -> None:
        for f in sh_files_under(AL_DIR):
            self.assertNotIn(
                "add_randint",
                self.text(f),
                f"{f.relative_to(CASE)} still uses omb add_randint (non-deterministic)",
            )

    def test_random_sampling_knobs_are_disabled_for_reference(self) -> None:
        t = self.text(AL_RUN)
        self.assertIn("USE_BAD_CONFS=0", t)
        self.assertIn("UPDATE_MD_CONFS=0", t)

    # -- 2. NVT is a hard gate, never a warning -------------------------------
    def test_nvt_uses_derived_seed(self) -> None:
        t = self.text(NVT_RUN)
        self.assertIn("ai2kit_derived_seed", t, "NVT seed must come from the root seed")

    def test_nvt_failure_is_not_soft(self) -> None:
        t = self.text(NVT_RUN)
        self.assertNotIn("set +e", t, "NVT must not disable errexit around wait_job")
        self.assertNotIn("WARNING", t, "NVT failure must not be downgraded to a warning")
        for line in t.splitlines():
            if not line.lstrip().startswith("#"):
                if "wait_job.sh" in line:
                    self.assertNotIn("|| true", line, f"wait_job swallowed: {line.strip()}")
                if "analyze_nvt.py" in line:
                    self.assertNotIn("|| true", line, f"analyze swallowed: {line.strip()}")

    def test_nvt_done_requires_output_validation(self) -> None:
        t = self.text(NVT_RUN)
        self.assertLess(
            t.index("analyze_nvt.py"),
            t.index('touch "$NVT_DIR/nvt.done"'),
            "success marker (nvt.done) must be written only after analysis",
        )

    # -- 3. locked root seed + deterministic derivation in env ----------------
    def test_env_declares_root_seed_and_derivation(self) -> None:
        t = self.text(ENV_SH)
        self.assertIn("AI2KIT_ROOT_SEED", t)
        self.assertIn("ai2kit_derived_seed", t)
        self.assertIn("ai2kit_seed_list", t)

    def test_seed_derivation_is_pure(self) -> None:
        t = self.text(ENV_SH)
        fn = t[t.index("ai2kit_derived_seed()"):]
        self.assertNotIn("RANDOM", fn)
        self.assertNotIn("$$", fn, "seed must not depend on the shell pid")
        self.assertNotIn("date", fn, "seed must not depend on wall clock")

    # -- 4. AL workflows pair deterministic seeds with {i} --------------------
    def test_train_combo_uses_derived_seed_list(self) -> None:
        t = self.text(ITER_WF)
        self.assertIn("ai2kit_seed_list", t, "train seeds must be derived, not add_randint")
        self.assertNotIn("add_randint", t)

    def test_md_combo_uses_derived_seed(self) -> None:
        t = self.text(ITER_WF)
        self.assertIn("set_broadcast SEED", t)
        self.assertIn("ai2kit_derived_seed", t, "MD seed must be derived, not omb RNG")

    def test_prod_utility_uses_derived_seed(self) -> None:
        t = self.text(PROD_WF)
        self.assertNotIn("add_randint", t)
        self.assertIn("ai2kit_derived_seed", t)

    # -- 5. G9 reference: dual seeds, fixed resources, smoke, no freeze --
    def test_launcher_declares_two_distinct_reference_seeds(self) -> None:
        t = self.text(G9_COMMON)
        self.assertIn("g9_ref_seed", t, "G9 common must map REF_RUN_ID -> root seed")
        m = re.search(r"hpc-ref-01\)\s+printf '(\d+)'", t)
        n = re.search(r"hpc-ref-02\)\s+printf '(\d+)'", t)
        self.assertIsNotNone(m, "no declared seed for hpc-ref-01")
        self.assertIsNotNone(n, "no declared seed for hpc-ref-02")
        self.assertNotEqual(m.group(1), n.group(1), "reference-01 and reference-02 must use distinct seeds")
        self.assertIn("AI2KIT_ROOT_SEED", self.text(G9_TEMPLATE), "the reference job must export the root seed")

    def test_launcher_pins_fixed_resources(self) -> None:
        t = self.text(G9_COMMON)
        self.assertIn("G9_SLURM_NTASKS=", t, "NTASKS must be a fixed constant")
        self.assertIn("G9_SLURM_CPUS_PER_TASK=", t, "CPUS_PER_TASK must be a fixed constant")
        self.assertIn("G9_SLURM_TIME=", t)

    def test_launcher_gates_scoring_on_model_compat_smoke(self) -> None:
        t = self.text(G9_FETCH)
        self.assertIn(
            "dp-test.done",
            t,
            "sync-back scoring must be gated on the model-compatibility smoke evidence",
        )

    def test_launcher_has_no_threshold_freeze(self) -> None:
        for path in (G9_SUBMIT, G9_TEMPLATE):
            t = self.text(path)
            self.assertNotIn("thresholds.json", t, "reference must not write the anchor file")
            # G11 freezes thresholds, not the G9 reference — the reference must
            # carry no threshold writing/freezing at all (note: "replay freeze"
            # is the evidence guard, a different concept).
            self.assertNotIn("threshold", t.lower(), "reference must carry no threshold-freeze command")

    def test_launcher_writes_provenance_manifest(self) -> None:
        t = self.text(G9_TEMPLATE)
        self.assertIn("provenance", t.lower())

    def test_provenance_helper_exists_and_records_root_seed(self) -> None:
        self.assertTrue(PROVENANCE.is_file(), "missing solution/expert/provenance.sh")
        self.assertIn("AI2KIT_ROOT_SEED", self.text(PROVENANCE))


if __name__ == "__main__":
    unittest.main()
