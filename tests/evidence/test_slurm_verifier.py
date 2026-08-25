"""ER8: SlurmVerifierRuntime — cluster-native finalize verifier via sbatch.

The finalize transaction runs on the cluster login node (python3.9 + numpy +
zstd, no ase); the frozen CPU verifier SIF can only exec on a compute node, so
each verification is one ``sbatch --wait`` job. The runtime renders a slurm
script mirroring the 031 formal template's verifier invocation, submits it, and
reads ``verifier_report.json`` from the job's out dir. No host<->cluster byte
transfer is involved — submission, tests, and SIF all live on the cluster.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.evidence.curate_and_verify import CurateError
from scripts.evidence.finalize_run import SlurmVerifierRuntime

GRID = [100, 150, 200, 250, 275, 300, 325, 350, 375, 400, 450, 500, 600]


def _render_check(runtime: SlurmVerifierRuntime, tmp_path: Path) -> str:
    submission = tmp_path / "submission"
    submission.mkdir(parents=True)
    (submission / "result.json").write_text("{}")
    script = runtime._render_script(submission, "paper", tmp_path / "out")
    return script


def test_render_binds_submission_tests_and_out_readonly(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "verifier.py").write_text("")
    runtime = SlurmVerifierRuntime(
        sif="/sif/matclaw-cpu.sif", tests_src=tests,
        apptainer="/apptainer", scratch_base=str(tmp_path / "scratch"))
    script = _render_check(runtime, tmp_path / "run")

    assert f"--bind '{tmp_path / 'run' / 'submission'}:/app:ro'" in script
    assert f"--bind '{tests}:/tests:ro'" in script
    assert f"--bind '{tmp_path / 'run' / 'out'}:/out:rw'" in script
    assert "'/apptainer'" in script
    assert "'/sif/matclaw-cpu.sif'" in script
    assert "/opt/matclaw/bin/python" in script
    # verifier reads /app (the submission) and dumps the report into /out
    assert "verify(__import__('pathlib').Path('/app'),'paper')" in script
    assert "/out/verifier_report.json" in script


def test_render_emits_slurm_directives(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    runtime = SlurmVerifierRuntime(
        sif="/s", tests_src=tests, apptainer="/a",
        scratch_base=str(tmp_path), partition="gpu", gres="gpu:1",
        account="acct-blocked", cpus=8, mem="64G", time="02:00:00")
    script = _render_check(runtime, tmp_path / "run")
    assert "#SBATCH --partition=gpu" in script
    assert "#SBATCH --gres=gpu:1" in script
    assert "#SBATCH --account=acct-blocked" in script
    assert "#SBATCH --cpus-per-task=8" in script
    assert "#SBATCH --mem=64G" in script
    assert "#SBATCH --time=02:00:00" in script


def _fake_sbatch(tmp_path: Path) -> Path:
    """A fake ``sbatch`` that renders the verifier report as if apptainer ran.

    Reads the rendered slurm script, locates the out dir from the ``--bind
    <dir>:/out:rw`` mount, and writes a valid report there. Emulates the frozen
    verifier's shape for case 032 (13-value grid, Tc_K within tolerance).
    """
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    payload = json.dumps({
        "valid": True,
        "errors": [],
        "file_sha256": "f" * 64,
        "recomputed_estimate": {"Tc_K": 259.44},
        "recomputed_curve": [{"temperature_K": t} for t in GRID],
    }, indent=2)
    script = fake_dir / "sbatch"
    script.write_text(
        "#!/bin/bash\n"
        "# Read the slurm script (last arg), find the out dir from the /out bind.\n"
        'script="${@: -1}"\n'
        r'out="$(sed -nE "s/.*--bind .([^ ]*):\/out:rw.*/\1/p" "$script" | tail -1)"' + "\n"
        'mkdir -p "$out"\n'
        "cat > \"$out/verifier_report.json\" <<'EOF'\n"
        f"{payload}\n"
        "EOF\n"
        'echo "job-12345"\n'
    )
    script.chmod(0o755)
    return fake_dir


def test_run_submits_job_and_reads_report(tmp_path: Path, monkeypatch) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "verifier.py").write_text("def verify(submission, profile): ...")
    submission = tmp_path / "submission"
    submission.mkdir()
    (submission / "result.json").write_text("{}")

    monkeypatch.setenv("PATH", f"{_fake_sbatch(tmp_path)}:{Path('/usr/bin')}:{Path('/bin')}")
    runtime = SlurmVerifierRuntime(
        sif="/sif/matclaw-cpu.sif", tests_src=tests,
        apptainer="/apptainer", scratch_base=str(tmp_path / "scratch"))

    report = runtime.run(submission, "paper")
    assert report["valid"] is True
    assert report["recomputed_estimate"]["Tc_K"] == 259.44
    assert [r["temperature_K"] for r in report["recomputed_curve"]] == GRID


def test_run_fails_closed_when_no_report(tmp_path: Path, monkeypatch) -> None:
    """A job that completes without a report is a CurateError, not silent pass."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "verifier.py").write_text("")
    submission = tmp_path / "submission"
    submission.mkdir()
    (submission / "result.json").write_text("{}")

    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    (fake_dir / "sbatch").write_text("#!/bin/bash\necho job-999\nexit 0\n")
    (fake_dir / "sbatch").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_dir}:{Path('/usr/bin')}:{Path('/bin')}")

    runtime = SlurmVerifierRuntime(
        sif="/s", tests_src=tests, apptainer="/a", scratch_base=str(tmp_path / "scratch"))
    try:
        runtime.run(submission, "paper")
    except CurateError as exc:
        assert "verifier_report.json" in str(exc)
    else:
        raise AssertionError("expected CurateError for missing report")
