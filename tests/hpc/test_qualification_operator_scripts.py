from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts/qualification/run_hpc_dispatcher.sh"
SUPERVISOR = ROOT / "scripts/qualification/supervise_hpc_dispatcher.sh"


def test_operator_scripts_are_relocatable_and_do_not_cancel_user_jobs():
    for script in (RUNNER, SUPERVISOR):
        text = script.read_text()
        assert "/Users/chenxuanjie/" not in text
        assert "dftworld2-qualification" not in text
        assert "scancel" not in text
        assert "xargs" not in text


def test_runtime_lock_paths_are_canonical():
    text = RUNNER.read_text()
    assert "reference/runtime/cp2k-runtime.lock.json" in text
