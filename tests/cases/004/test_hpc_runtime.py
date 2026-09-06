#!/usr/bin/env python3
"""Contract tests for the native-HPC runtime adapter."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


CASE = Path(__file__).resolve().parents[1]
EXPERT = CASE / "solution" / "expert"
HPC_ENV = EXPERT / "env-hpc.sh"
RUNTIME = EXPERT / "hpc-runtime.sh"


class HpcRuntimeContract(unittest.TestCase):
    def text(self, path: Path) -> str:
        self.assertTrue(path.is_file(), f"missing runtime file: {path}")
        return path.read_text()

    def test_private_environment_is_required_and_base_is_not_modified(self) -> None:
        text = self.text(HPC_ENV)
        self.assertIn("AI2KIT_CONDA_ENV", text)
        self.assertIn("deepmd-kit==2.2.11", text)
        self.assertIn("ai2-kit==1.1.0", text)
        self.assertIn("oh-my-batch==0.7.6", text)
        self.assertNotIn("conda install", text)
        self.assertNotIn("pip install", text)

    def test_booking_and_absolute_logs_use_sbatch_environment(self) -> None:
        text = self.text(HPC_ENV)
        for token in ("SBATCH_PARTITION", "SBATCH_ACCOUNT", "SBATCH_QOS", "SBATCH_OUTPUT", "%j"):
            self.assertIn(token, text)
        self.assertIn("mkdir -p", text)

    def test_cp2k_layout_and_launcher_are_explicit(self) -> None:
        env_text = self.text(HPC_ENV)
        runtime_text = self.text(RUNTIME)
        self.assertIn('AI2KIT_CP2K_NP="${AI2KIT_CP2K_NP:-4}"', env_text)
        self.assertIn('AI2KIT_CP2K_OMP="${AI2KIT_CP2K_OMP:-2}"', env_text)
        self.assertIn("AI2KIT_CP2K_LAUNCHER", runtime_text)
        self.assertIn("AI2KIT_CP2K_BIN", runtime_text)
        self.assertIn("ai2kit_run_cp2k", runtime_text)
        self.assertIn("srun", runtime_text)
        self.assertIn("mpirun", runtime_text)

    def test_lammps_mode_is_explicit_and_missing_plugin_is_fatal(self) -> None:
        text = self.text(RUNTIME)
        for mode in ("builtin", "plugin", "matched-lmp"):
            self.assertIn(mode, text)
        self.assertIn("ai2kit_lammps_plugin_command", text)
        self.assertRegex(text, r'plugin.+\[ ! -f.+DEEPMD_PLUGIN')
        self.assertIn("return 1", text)

    def test_stage_scripts_select_environment_and_have_no_hardcoded_cpu_partition(self) -> None:
        scripts = list(EXPERT.rglob("*.sh"))
        self.assertGreater(len(scripts), 5)
        combined = "\n".join(path.read_text() for path in scripts)
        self.assertNotIn("#SBATCH --partition=cpu", combined)
        for rel in ("01-geopt/run.sh", "02-aimd/run.sh", "03-active-learning/run.sh", "04-validation/nvt/run.sh"):
            text = self.text(EXPERT / rel)
            self.assertIn("ai2kit_source_env", text, rel)

    def test_runtime_plugin_command_behaviour(self) -> None:
        runtime = str(RUNTIME)
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp) / "libdeepmd_lmpplugin.so"
            plugin.write_bytes(b"probe")
            command = f'source "{runtime}"; AI2KIT_LAMMPS_MODE=plugin; DEEPMD_PLUGIN="{plugin}"; ai2kit_lammps_plugin_command'
            ok = subprocess.run(["bash", "-c", command], text=True, capture_output=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(ok.stdout.strip(), f"plugin load {plugin}")
            missing = subprocess.run(
                ["bash", "-c", f'source "{runtime}"; AI2KIT_LAMMPS_MODE=plugin; DEEPMD_PLUGIN="{tmp}/missing.so"; ai2kit_lammps_plugin_command'],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(missing.returncode, 0)

    def test_runtime_exports_ai2kit_lmp_and_plugin_ld_library_path_helper(self) -> None:
        """Fix 2 (job 3537330): the native-HPC LAMMPS has no lmp-wrapper, so
        libdeepmd_lmpplugin.so cannot reach libtensorflow_cc.so.2.  The runtime
        must expose a prepend helper + an ai2kit_lmp launcher that applies it."""
        text = self.text(RUNTIME)
        self.assertIn("ai2kit_lammps_prepend_ld_library_path", text)
        self.assertIn("ai2kit_lmp", text)
        self.assertIn("libtensorflow_cc.so.2", text)
        self.assertIn("libdeepmd_lmpplugin.so", text)
        self.assertIn("ai2kit_lammps_prepend_ld_library_path\n  exec \"${AI2KIT_LAMMPS_BIN:-lmp}\" \"$@\"",
                      text, "ai2kit_lmp must prepend then exec the G7.6-pinned binary")
        # the prepend must be idempotent (no LD_LIBRARY_PATH duplication)
        self.assertIn('case ":${LD_LIBRARY_PATH:-}:"', text)

    def test_lammps_subjobs_use_ai2kit_lmp_and_pinned_binary(self) -> None:
        """Fix 2 reaches the GENERATED sub-jobs, not just the runtime: bare
        `lmp` does not resolve on the cluster and the ABI loader path must be
        injected where the plugin is actually loaded."""
        # AL exploration run.sh runs as `bash ./run.sh` (NEW process) so it must
        # source the runtime itself, exactly like config/cp2k/run.sh, and use
        # ai2kit_lmp rather than bare lmp.
        al_run = self.text(EXPERT / "03-active-learning" / "config" / "lammps" / "run.sh")
        self.assertIn('source "${EXPERT_DIR:?}/hpc-runtime.sh"', al_run)
        self.assertIn("ai2kit_lmp -i lammps.in", al_run)
        self.assertNotIn("\nlmp -i lammps.in", al_run)
        # AL lammps header must probe the PINNED binary, never `command -v lmp`.
        al_hdr = self.text(EXPERT / "03-active-learning" / "config" / "lammps" / "slurm-header.sh")
        self.assertNotIn("command -v lmp", al_hdr)
        self.assertIn('"${AI2KIT_LAMMPS_BIN:-lmp}" -h >/dev/null', al_hdr)
        self.assertIn('echo "LMP=${AI2KIT_LAMMPS_BIN:-lmp}"', al_hdr)
        # NVT slurm heredoc runs in the same process that sources the runtime,
        # so it calls ai2kit_lmp directly (the ABI fix applies there too).
        nvt = self.text(EXPERT / "04-validation" / "nvt" / "run.sh")
        self.assertIn('ai2kit_lmp -i nvt.in', nvt)
        self.assertNotIn('"${AI2KIT_LAMMPS_BIN:-lmp}" -i nvt.in', nvt)

    def test_lammps_header_routes_engine_modules_through_helper(self) -> None:
        """Engine-scoped isolation reaches the AL LAMMPS header: its `-h` probe
        must load ONLY the LAMMPS stack via ai2kit_load_engine_modules — never a
        raw `module load`, never the CP2K stack (oneAPI 2023.2 collides with the
        LAMMPS oneAPI 2021.1 runtime)."""
        hdr = self.text(EXPERT / "03-active-learning" / "config" / "lammps" / "slurm-header.sh")
        self.assertIn('source "${EXPERT_DIR:?}/hpc-runtime.sh"', hdr)
        self.assertIn('ai2kit_load_engine_modules "${AI2KIT_LAMMPS_MODULES:-}"', hdr)
        self.assertNotIn("AI2KIT_CP2K_MODULES", hdr)
        self.assertNotIn("module load ${AI2KIT_LAMMPS_MODULES}", hdr)

    def test_engine_module_purge_failure_is_fail_closed(self) -> None:
        """A failed `module purge` must abort the engine-module load (no mixing).

        In `if ai2kit_run_cp2k ...` bash suppresses errexit inside the function,
        so the purge failure must be an explicit `|| return 1` — a bare `module
        purge` followed by a succeeding `module load` would mask it and re-mix
        the oneAPI stacks."""
        script = (
            f'source "{RUNTIME}"\n'
            "module() {\n"
            "  case \"$1\" in\n"
            "    purge) echo 'purge_failed' >&2; return 1 ;;\n"
            "    load)  echo \"LOADED $2\" ;;\n"
            "    *)     return 0 ;;\n"
            "  esac\n"
            "}\n"
            "if ai2kit_load_engine_modules 'cp2k/2024.3'; then\n"
            "  echo 'RESULT=UNEXPECTED_SUCCESS'\n"
            "else\n"
            "  echo 'RESULT=FAIL_CLOSED'\n"
            "fi\n"
        )
        out = subprocess.run(["bash", "-c", script], text=True, capture_output=True)
        self.assertIn("RESULT=FAIL_CLOSED", out.stdout, out.stderr)
        self.assertNotIn("LOADED", out.stdout,
                         "module load must not run after a failed purge")

    def test_cp2k_entry_aborts_before_launching_binary(self) -> None:
        """A failed purge must abort ai2kit_run_cp2k BEFORE the launcher/binary.

        Proves the engine ENTRY point (not just ai2kit_load_engine_modules) is
        fail-closed: a fake purge fails, a fake module-load succeeds, and a fake
        CP2K binary records any invocation — the entry must return non-zero and
        neither module load, the launcher, nor the CP2K binary may run."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            marker = Path(tmp) / "cp2k_launched"
            fake_cp2k = bin_dir / "cp2k-fake"
            fake_cp2k.write_text("#!/bin/bash\ntouch \"$CP2K_MARKER\"\n")
            fake_cp2k.chmod(0o755)
            script = (
                f'source "{RUNTIME}"\n'
                "module() {\n"
                "  case \"$1\" in\n"
                "    purge) echo 'purge_failed' >&2; return 1 ;;\n"
                "    load)  echo \"LOADED $2\" ;;\n"
                "    *)     return 0 ;;\n"
                "  esac\n"
                "}\n"
                f"export CP2K_MARKER='{marker}'\n"
                "AI2KIT_CP2K_MODULES='cp2k/2024.3'\n"
                f"AI2KIT_CP2K_BIN='{fake_cp2k}'\n"
                "AI2KIT_CP2K_LAUNCHER='direct'\n"
                f"if ai2kit_run_cp2k 1 '{tmp}/cp2k.inp' '{tmp}/cp2k.out'; then\n"
                "  echo 'RESULT=UNEXPECTED_SUCCESS'\n"
                "else\n"
                "  echo 'RESULT=FAIL_CLOSED'\n"
                "fi\n"
            )
            out = subprocess.run(["bash", "-c", script], text=True, capture_output=True)
            self.assertIn("RESULT=FAIL_CLOSED", out.stdout, out.stderr)
            self.assertNotIn("LOADED", out.stdout, "module load must not run")
            self.assertFalse(marker.exists(), "CP2K binary must not be launched")

    def test_shell_files_parse(self) -> None:
        for path in (HPC_ENV, RUNTIME):
            self.assertEqual(subprocess.run(["bash", "-n", str(path)]).returncode, 0)

    def test_generated_slurm_scripts_use_absolute_logs_and_four_rank_default(self) -> None:
        """The runtime contract must reach the GENERATED job scripts, not just
        the env file: relative log paths, the CP2K fallback of 8, and hardcoded
        resource lines are exactly what the 7/7 static tests above cannot see."""
        geopt = self.text(EXPERT / "01-geopt" / "run.sh")
        aimd = self.text(EXPERT / "02-aimd" / "run.sh")
        nvt = self.text(EXPERT / "04-validation" / "nvt" / "run.sh")
        for rel, text in (("geopt", geopt), ("aimd", aimd), ("nvt", nvt)):
            self.assertIn("SBATCH_OUTPUT:-", text, f"{rel} must resolve absolute logs")
            self.assertIn("SBATCH_ERROR:-", text, f"{rel} must resolve absolute logs")
        self.assertIn("AI2KIT_CP2K_NP:-4", geopt, "geopt four-rank default")
        self.assertIn("AI2KIT_CP2K_NP:-4", aimd, "aimd four-rank default")
        self.assertIn("AI2KIT_CP2K_OMP:-", geopt, "geopt OpenMP consistent")
        self.assertIn("AI2KIT_CP2K_OMP:-", aimd, "aimd OpenMP consistent")
        self.assertIn("AI2KIT_NVT_CPUS:-", nvt, "nvt resource configurable")

        # AL templates route log + resource lines through generation-time
        # placeholders so HPC mode bakes absolute %j logs into each job script.
        run = self.text(EXPERT / "03-active-learning" / "run.sh")
        for token in ("@SLURM_OUT@", "@SLURM_ERR@", "@CP2K_NP@", "@CP2K_OMP@",
                      "@LAMMPS_CPUS@", "@DEEPMD_CPUS@"):
            self.assertIn(token, run, token)
        cp2k_hdr = self.text(EXPERT / "03-active-learning" / "config" / "cp2k" / "slurm-header.sh")
        lmp_hdr = self.text(EXPERT / "03-active-learning" / "config" / "lammps" / "slurm-header.sh")
        dp_hdr = self.text(EXPERT / "03-active-learning" / "config" / "deepmd" / "slurm-header.sh")
        for hdr in (cp2k_hdr, lmp_hdr, dp_hdr):
            self.assertIn("@SLURM_OUT@", hdr)
            self.assertIn("@SLURM_ERR@", hdr)
        self.assertIn("@CP2K_NP@", cp2k_hdr)
        self.assertIn("@CP2K_OMP@", cp2k_hdr)
        self.assertIn("@LAMMPS_CPUS@", lmp_hdr)
        self.assertIn("@DEEPMD_CPUS@", dp_hdr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
