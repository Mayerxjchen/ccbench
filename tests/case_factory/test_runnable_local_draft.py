"""Task 9 + P0.1 — Runnable Local Draft smoke through the production checker.

The Draft is driven through the full Trusted Harness lifecycle by
``case_factory smoke``: package → candidate_start → agent_start → agent_stop →
candidate_freeze → submission_collect → candidate_destroy → quarantine →
verifier_start → record_write.  The Candidate is an isolated child process
(private HOME, workspace cwd); the Verifier goes through the real
``run_verifier``/``build_verifier_command`` path (audit runner asserts the
isolation argv and simulates container output).  Only the production checker
derives ``runtime_valid`` + ``candidate_smoke_valid``; tests never write gates.
On success benchmark_valid stays false and check_release stays blocked.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dftworld_bench.case_factory.state import read_factory_state
from dftworld_bench.case_factory.smoke import (
    AuditVerifierRunner,
    DECLARED_CONTENT,
    VERIFIER_ISOLATION,
    DockerScriptedCandidate,
    IsolatedScriptedCandidate,
    _gate_updates,
    audit_verifier,
    run_smoke,
)
from dftworld_bench.contracts.result import FailureCode
from dftworld_bench.core.harness import HarnessSpec, Profile, Treatment, TrustedHarness
from dftworld_bench.core.run_store import RunStore

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mlp-local-final-retraining"
ROOT = Path(__file__).resolve().parents[2]
CLI = [sys.executable, "-m", "dftworld_bench.case_factory"]

SMOKE_RUN_ID = "042-local-final-retraining-case-construction-smoke-0001"


def _smoke_case(tmp_path: Path) -> Path:
    """A private copy of the committed fixture; the smoke never mutates it."""
    case = tmp_path / "case"
    shutil.copytree(FIXTURE, case)
    return case


def test_production_smoke_validates_declared_output(tmp_path):
    case_dir = _smoke_case(tmp_path)
    runner = AuditVerifierRunner()
    result = run_smoke(case_dir, run_id=SMOKE_RUN_ID, runner=runner)
    # The audit runner still drives the real run_verifier; capture its argv.
    assert runner.isolation_ok is True
    assert runner.last_cmd is not None
    assert result.is_counted_scientifically is True
    assert result.failure_code is FailureCode.PASS


def test_verifier_isolation_flags_in_built_command(tmp_path):
    """The built verifier argv carries non-root/no-network/read-only isolation."""
    case_dir = _smoke_case(tmp_path)
    runner = AuditVerifierRunner()
    run_smoke(case_dir, run_id=SMOKE_RUN_ID, runner=runner)
    cmd = runner.last_cmd
    assert cmd is not None
    for token in VERIFIER_ISOLATION:
        assert token in cmd, f"missing isolation token {token!r} in {cmd}"


def test_isolated_candidate_runs_in_child_process(tmp_path):
    case_dir = _smoke_case(tmp_path)
    threads = tmp_path / "threads"
    agent = IsolatedScriptedCandidate(case_dir, threads)
    assert agent.version == "isolated-scripted-candidate-v2"
    assert agent._child is None  # no process before start

    import asyncio

    asyncio.run(agent.prepare())
    asyncio.run(agent.start("write your final output"))
    assert agent._child is not None
    assert agent._child.poll() == 0  # child exited cleanly
    assert agent.collect_logs()["candidate_home_isolated"] is True
    # The declared bytes are the child's product, not the test's.
    sealed = threads / case_dir.name / "workspace" / "final" / "result.txt"
    assert sealed.read_text(encoding="utf-8") == DECLARED_CONTENT
    # Real teardown: close() after the child already exited must not error.
    asyncio.run(agent.close())
    assert agent._child is None


def test_harness_smoke_validates_declared_output(tmp_path):
    """Full lifecycle through the harness with the isolated candidate.

    The direct-harness path is a SMOKE run: it must carry the same identity
    gates as any run (resolved lock digest, complete budgets, runtime
    identities, durable session) — there is no lockless bypass in either mode.
    """
    case_dir = _smoke_case(tmp_path)
    store = RunStore(tmp_path / "runs")
    agent = IsolatedScriptedCandidate(case_dir, tmp_path / "threads")

    from dftworld_bench.case_factory.smoke import smoke_identity
    from dftworld_bench.core.event_store import EventStore
    from dftworld_bench.core.harness import RunMode

    import asyncio

    import eval as evalmod  # noqa: E402

    tspec = evalmod.load_task(case_dir)
    identity = smoke_identity(
        case_dir, {"image": tspec.image}, SMOKE_RUN_ID
    )
    session = EventStore(tmp_path / "session" / "events.jsonl")
    harness = TrustedHarness(
        agent, store=store, verifier=audit_verifier, session=session
    )
    spec = HarnessSpec(
        case_id=tspec.name,
        case_dir=case_dir,
        image=tspec.image,
        instruction=tspec.instruction,
        run_id=SMOKE_RUN_ID,
        agent_timeout_sec=tspec.agent_timeout_sec,
        mode=RunMode.SMOKE,
        threads_root=tmp_path / "threads",
        gpus=tspec.gpus,
        verifier_timeout_sec=tspec.verifier_timeout_sec,
        verifier_env=tspec.verifier_env,
        submission_root=tspec.submission_root,
        legacy_submission_layout=tspec.legacy_submission_layout,
        budgets=identity["budgets"],
        lock_digest=identity["lock_digest"],
        runtime_identities=identity["runtime_identities"],
    )
    treatment = Treatment(
        experiment_id="case-construction-smoke",
        condition_id="no-skill",
        skills_source="none",
        replicate=1,
        attempt=1,
        benchmark_commit="unknown",
        agent_model="isolated-scripted-candidate-v2",
    )
    profile = Profile(
        name="local",
        execution_class=tspec.execution_class,
        platform="local_docker",
        site_config_digest=identity["site_config_digest"],
        legacy_normalized=tspec.legacy_submission_layout,
    )

    result = asyncio.run(harness.run(spec, treatment, profile))

    assert result.is_counted_scientifically is True
    assert result.failure_code is FailureCode.PASS
    assert harness.events == TrustedHarness.LOCAL_ORDER

    record = store.load(SMOKE_RUN_ID)
    assert record.condition_id == "no-skill"
    assert record.experiment_id == "case-construction-smoke"
    # The direct-harness path is a SMOKE run: real identity, but the record
    # is never counted and never formally eligible.
    assert record.run_mode == "smoke"
    assert record.to_dict()["counted"] is False
    assert record.to_dict()["formal_eligible"] is False


def test_smoke_derives_contract_gates_only(tmp_path):
    """The production checker — not a test — records contract gates, never the
    full container gates (no runner exercises a container-isolated Candidate)."""
    case_dir = _smoke_case(tmp_path)
    result = run_smoke(case_dir, run_id=SMOKE_RUN_ID, runner="audit")
    assert result.failure_code is FailureCode.PASS

    gates = read_factory_state(case_dir)
    assert gates.runtime_contract_valid is True
    assert gates.verifier_command_valid is True
    # Full runtime gates stay false: subprocess Candidate, no container sandbox.
    assert gates.runtime_valid is False
    assert gates.candidate_smoke_valid is False
    assert gates.design_valid is True
    assert gates.target_adapter_valid is True
    # benchmark_valid stays false regardless of factory gates.
    bv = json.loads((case_dir / "benchmark_valid.json").read_text(encoding="utf-8"))
    assert bv["benchmark_valid"] is False


def test_smoke_record_excluded_from_formal_statistics(tmp_path):
    """The smoke run is tagged; formal statistics must filter it out."""
    case_dir = _smoke_case(tmp_path)
    run_smoke(case_dir, run_id=SMOKE_RUN_ID, runner="audit")
    from dftworld_bench.experiments.ablation import (
        FORMAL_EXPERIMENT_ID,
        PILOT_EXPERIMENT_ID,
    )

    # The smoke writes gates, so the run itself lives in a temp RunStore inside
    # run_smoke; assert the experiment id is the smoke tag, not formal/pilot.
    from dftworld_bench.case_factory.smoke import SMOKE_EXPERIMENT

    assert SMOKE_EXPERIMENT == "case-construction-smoke"
    assert SMOKE_EXPERIMENT not in (FORMAL_EXPERIMENT_ID, PILOT_EXPERIMENT_ID)


def test_check_release_still_blocked_after_smoke(tmp_path):
    """Even with runtime/smoke gates set, benchmark_valid stays false."""
    case_dir = _smoke_case(tmp_path)
    run_smoke(case_dir, run_id=SMOKE_RUN_ID, runner="audit")
    _support = Path(__file__).resolve().parent / "support"
    _portable = (
        _support
        if _support.is_dir()
        else Path(__file__).resolve().parents[2]
        / "scientific-benchmark-case-builder-portable"
        / "skills" / "build-scientific-benchmark-case" / "scripts" / "common"
    )
    sys.path.insert(0, str(_portable))
    from check_release import check_release  # noqa: E402

    bv = json.loads((case_dir / "benchmark_valid.json").read_text(encoding="utf-8"))
    assert bv["benchmark_valid"] is False
    report = check_release(case_dir)
    assert report["valid"] is False


def test_smoke_cli_writes_gates_and_reports(tmp_path):
    case_dir = _smoke_case(tmp_path)
    proc = subprocess.run(
        [*CLI, "smoke", str(case_dir), "--target", "dftworld", "--runner", "audit"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["valid"] is True
    assert payload["benchmark_valid"] is False
    assert payload["factory_gates"]["runtime_contract_valid"] is True
    assert payload["factory_gates"]["verifier_command_valid"] is True
    assert payload["factory_gates"]["runtime_valid"] is False
    assert payload["factory_gates"]["candidate_smoke_valid"] is False


def test_smoke_cli_persists_evidence_with_runs_dir(tmp_path):
    """--runs-dir retains the RunRecord, sealed submission and verifier logs."""
    case_dir = _smoke_case(tmp_path)
    runs = tmp_path / "evidence" / "run-0001"
    proc = subprocess.run(
        [*CLI, "smoke", str(case_dir), "--target", "dftworld",
         "--runner", "audit", "--runs-dir", str(runs)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    evidence = payload["evidence"]
    assert evidence["runs_dir"] == str(runs)
    record = Path(evidence["run_record_path"])
    assert record.is_file(), "run record not persisted"
    assert evidence["run_record_sha256"] == _sha256_file(record)
    sealed = runs / case_dir.name / "sealed-submission"
    assert sealed.is_dir(), "sealed submission not persisted"
    assert evidence["sealed_submission_sha256"] == _sha256_tree(sealed)
    assert evidence["verifier_log_path"] == str(
        runs / case_dir.name / "verifier-logs"
    )
    assert (runs / case_dir.name / "verifier-logs" / "result.json").is_file()


def _sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_tree(root: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    for rel in sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file()):
        digest.update(str(rel).encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / rel).read_bytes())
    return digest.hexdigest()


def test_smoke_cli_unknown_runner_fails(tmp_path):
    case_dir = _smoke_case(tmp_path)
    proc = subprocess.run(
        [*CLI, "smoke", str(case_dir), "--target", "dftworld", "--runner", "bogus"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode != 0


def test_smoke_cli_unknown_candidate_fails(tmp_path):
    case_dir = _smoke_case(tmp_path)
    proc = subprocess.run(
        [*CLI, "smoke", str(case_dir), "--target", "dftworld",
         "--candidate", "bogus"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode != 0


def test_docker_candidate_command_isolation(tmp_path):
    """The container Candidate argv carries the sandbox isolation contract.

    Built (not executed) so the suite needs no docker daemon.
    """
    case_dir = _smoke_case(tmp_path)
    agent = DockerScriptedCandidate(case_dir, tmp_path / "threads")
    cmd = agent.run_command()
    for token in (
        "docker", "run", "--rm", "--network", "none",
        "--user", "65532:65532", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
    ):
        assert token in cmd, f"missing isolation token {token!r} in {cmd}"
    # Fresh HOME inside the container, no host HOME mount, no host git/ssh.
    assert "--env" in cmd and "HOME=/tmp/home" in cmd
    volumes = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "--volume"]
    assert volumes == [f"{agent.workspace.resolve()}:/workspace"], volumes
    assert "--workdir" in cmd and "/workspace" in cmd
    assert agent.image_tag in cmd
    # The image has python3 (no python shim); the write target handed to the
    # container's python is the inside-container path, never the host path.
    assert "python3" in cmd
    assert cmd[-2] == "/workspace/final/result.txt"
    assert agent.version == "docker-scripted-candidate-v1"


def test_gate_updates_require_real_containers():
    """Full runtime gates promote ONLY when both sides ran as real containers."""
    from dftworld_bench.contracts.result import BenchmarkResult

    passed = BenchmarkResult.valid("r1", passed=True)
    sci_fail = BenchmarkResult.valid("r2", passed=False)

    # Contract gates by every runner; full gates need both docker.
    assert _gate_updates(passed, real_containers=False) == {
        "runtime_contract_valid": True,
        "verifier_command_valid": True,
    }
    assert _gate_updates(passed, real_containers=True) == {
        "runtime_contract_valid": True,
        "verifier_command_valid": True,
        "runtime_valid": True,
        "candidate_smoke_valid": True,
    }
    # A scientific failure never promotes anything.
    assert _gate_updates(sci_fail, real_containers=True) == {}
    assert _gate_updates(sci_fail, real_containers=False) == {}


def test_smoke_cli_scientific_fail_exits_nonzero(tmp_path, monkeypatch):
    """A SCIENTIFIC_FAIL is counted but is NOT a passing smoke.

    is_counted_scientifically is true for both PASS and SCIENTIFIC_FAIL, so the
    CLI must gate its own valid/exit on FailureCode.PASS — never report a
    scientifically-failed smoke as valid with exit 0.
    """
    import dftworld_bench.case_factory.cli as cli
    from dftworld_bench.contracts.result import BenchmarkResult

    case_dir = _smoke_case(tmp_path)
    calls = {"n": 0}

    def _scientific_fail(case_dir, *, run_id, runner, candidate="audit",
                         runs_dir=None):
        calls["n"] += 1
        return BenchmarkResult.valid(
            run_id, passed=False, reason="model gave a scientifically wrong result"
        )

    monkeypatch.setattr(cli, "run_smoke", _scientific_fail)
    with pytest.raises(SystemExit) as exc:
        cli.cmd_smoke(case_dir, "dftworld", runner="audit")
    assert exc.value.code == 1, "scientific-fail smoke must exit nonzero"
    assert calls["n"] == 1
    # Gates stayed unset: no contract gate may flip on a scientific failure.
    gates = read_factory_state(case_dir)
    assert gates.runtime_contract_valid is False
    assert gates.verifier_command_valid is False


# --------------------------------------------------------------------------- #
# Layer-A regressions (Task 13): non-root Verifier runtime + source-safety.
# --------------------------------------------------------------------------- #

def test_harness_injects_verifier_python(tmp_path):
    """The Harness pins the Verifier's own interpreter — outside /root and /app.
    Tests must never execute an interpreter shipped in the sealed submission."""
    case_dir = _smoke_case(tmp_path)
    runner = AuditVerifierRunner()
    run_smoke(case_dir, run_id=SMOKE_RUN_ID, runner=runner)
    assert runner.last_cmd is not None
    joined = " ".join(runner.last_cmd)
    assert "VERIFIER_PYTHON=/opt/dftworld/venv/bin/python" in joined
    assert "/app/.venv" not in joined


def test_solution_mounted_only_into_fresh_verifier(tmp_path):
    """Reference implementation material (/solution) is Verifier-only: the
    fresh Verifier mounts it read-only; the container Candidate never sees it."""
    case_dir = _smoke_case(tmp_path)
    (case_dir / "solution").mkdir()
    (case_dir / "solution" / "solver.py").write_text(
        "def solve():\n    return {}\n", encoding="utf-8"
    )
    runner = AuditVerifierRunner()
    run_smoke(case_dir, run_id=SMOKE_RUN_ID, runner=runner)
    assert runner.last_cmd is not None
    verifier_volumes = [
        runner.last_cmd[i + 1]
        for i, token in enumerate(runner.last_cmd)
        if token == "--volume"
    ]
    assert any(v.endswith(":/solution:ro") for v in verifier_volumes), verifier_volumes

    agent = DockerScriptedCandidate(case_dir, tmp_path / "threads")
    candidate_cmd = agent.run_command()
    # The /solution guest path never appears in the container Candidate argv —
    # only the fresh Verifier sees the hidden reference implementation.
    assert not any("/solution" in token for token in candidate_cmd), candidate_cmd


def test_031_production_entry_calls_verify_not_pytest():
    """031's production /tests/test.sh calls the hidden scientific verify()
    entry directly — the same gate the HPC finalize path runs — never the
    development pytest retraining suites (which rewrite fixtures and do not
    belong in the read-only Verifier container)."""
    cands = [
        ROOT / "cases" / "001-matclaw-cips-active-distillation" / "verifier" / "test.sh",
        ROOT / "001-matclaw-cips-active-distillation" / "verifier" / "test.sh",
        ROOT / "001-matclaw-cips-active-distillation" / "tests" / "test.sh",
    ]
    test_sh = next((c for c in cands if c.is_file()), cands[0])
    content = test_sh.read_text(encoding="utf-8")
    assert "from verifier import verify" in content
    assert "verify(" in content
    executable = "\n".join(
        line for line in content.splitlines() if not line.lstrip().startswith("#")
    )
    assert "pytest" not in executable
