"""validate_pilot_pair.py — the CLI gate for a real-HPC pilot pair.

The comparator (``ablation.comparability_errors``) is covered in
``test_ablation_protocol.py``.  These tests pin the CLI wiring that the
user runs after every pilot pair: schema load, arm-skill badges vs the
frozen protocol, independent verifier output, sealed submission, lifecycle
closure, and the pilot-never-formal rule.

These are synthetic fixtures (the two-layer validation pattern): they prove
the *machinery* accepts a valid pair and flags each pairing defect.  A real
pilot pair's run-records are the evidence; a green CLI here is never a case
observation.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import scripts.ablation.validate_pilot_pair as vp

CASE = "032-matclaw-cips-curie-temperature"
PILOT = "skill-ablation-v1-pilot"
FROZEN_SHA = "sha256:984bcc8d4b2775c7f8d0f4a475e46c7e9b2e03b348bd14e4a42c90c0c12668e9"
BENCH_COMMIT = "aae1becabc11041f454a7dc2d617e2ccadb95763"


def _lock_payload(cond: str) -> dict:
    """A fully-frozen resolved run lock; only treatment fields differ."""
    lock = {
        "case": {"case_id": CASE, "case_version": "1.0", "schema_version": "1.2"},
        "experiment": {"template_name": "formal-long-default", "condition_id": cond,
                       "replicate": 1},
        "agent": {
            "provider": "openai", "model_id": "gpt-4o", "deployment_id": "default",
            "provider_model_version": "2024-08-06", "identity_strength": "alias",
            "engine": "pagent", "prompt_digest": "sha256:abc",
            "sampling_digest": "sha256:def", "context_digest": "sha256:ghi",
            "skill_bundle_digest": "sha256:none" if cond == "no-skill" else FROZEN_SHA,
            "tool_surface_digest": "sha256:jkl",
        },
        "api": {"api_profile_digest": "sha256:mno", "endpoint_env": "DFTWORLD_API_ENDPOINT",
                "credential_env": "DFTWORLD_API_KEY"},
        "candidate_runtime": {"image": "matclaw-cips-2.2.11-gpu-amd64",
                              "runtime_digest": "sha256:pqr",
                              "qualification_status": "passed"},
        "hpc": {"site_profile_digest": "sha256:stu", "scheduler": "slurm",
                "capabilities": ["batch_jobs", "gpu"]},
        "verifier": {"runtime_digest": "sha256:vwx",
                     "isolation_config_digest": "sha256:yza"},
        "infra": {"version": "2.0.0", "commit": BENCH_COMMIT},
        "budgets": {"max_model_turns": 512, "max_total_tokens": 100000000,
                    "agent_active_walltime_sec": 86400,
                    "scheduler_wait_walltime_sec": 604800},
    }
    return lock


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    """Write a no-skill + with-skill run-record pair with real thread dirs."""
    paths: dict[str, Path] = {}
    for cond in ("no-skill", "with-skill"):
        run_dir = tmp_path / f"jobs/{cond}-run"
        tdir = run_dir / "thread"
        (tdir / "verifier-logs").mkdir(parents=True)
        (tdir / "sealed-submission").mkdir()
        (tdir / "resolved-run-lock.json").write_text(
            json.dumps({"payload": _lock_payload(cond)}),
            encoding="utf-8",
        )
        (tdir / "verifier-logs" / "result.json").write_text(
            json.dumps({
                "run_id": f"run-{cond}",
                "result_class": "VALID_RESULT",
                "failure_code": None,
                "reason": "ok",
                "retryable": False,
            }),
            encoding="utf-8",
        )
        (tdir / "sealed-submission" / "submission.tar.gz").write_text("sealed", encoding="utf-8")
        base: dict = {
            "schema_version": 1,
            "run_id": f"run-{cond}",
            "case_id": CASE,
            "execution_class": "hpc_controller",
            "agent_model": "deepseek/deepseek-chat",
            "experiment_id": PILOT,
            "condition_id": cond,
            "skills_source": "none" if cond == "no-skill" else "image",
            "skills_sha": None if cond == "no-skill" else FROZEN_SHA,
            "image": "matclaw-cips-2.2.11-gpu-amd64",
            "benchmark_commit": BENCH_COMMIT,
            "profile": "formal",
            "submission_root": ".",
            "verifier": "matclaw",
            "platform": "gpu-slurm",
            "job_id": f"job-{cond}",
            "site_config_digest": "sha256:site-a",
            "replicate": 1,
            "attempt": 1,
            "thread_dir": str(tdir),
            "legacy_normalized": False,
            "usage": {"tool_calls": 3, "tokens": 100, "elapsed_sec": 1.0},
            "lifecycle_events": [
                {"phase": "SUBMITTED", "at": "t0"},
                {"phase": "COMPLETED", "at": "t1"},
            ],
            "result": {
                "run_id": f"run-{cond}",
                "result_class": "VALID_RESULT",
                "failure_code": None,
                "reason": "ok",
                "retryable": False,
            },
        }
        record_path = run_dir / "run-record.json"
        record_path.write_text(json.dumps(base), encoding="utf-8")
        paths[cond] = record_path
    return paths["no-skill"], paths["with-skill"]


def _run(*, mutate: callable | None = None) -> int:
    """Write a fresh pair, optionally mutate one record, run the CLI, rc."""
    with tempfile.TemporaryDirectory() as td:
        a_path, b_path = _write_pair(Path(td))
        if mutate is not None:
            mutate(a_path, b_path)
        return vp.main(["--no-skill", str(a_path), "--with-skill", str(b_path)])


def _thread_dir(record_path: Path) -> Path:
    return Path(json.loads(record_path.read_text(encoding="utf-8"))["thread_dir"])


def test_valid_pair_passes(tmp_path: Path) -> None:
    a, b = _write_pair(tmp_path)
    assert vp.main(["--no-skill", str(a), "--with-skill", str(b)]) == 0


def test_pair_with_missing_verifier_output_fails() -> None:
    def defect(a: Path, b: Path) -> None:
        (_thread_dir(a) / "verifier-logs" / "result.json").unlink()

    assert _run(mutate=defect) != 0


def test_pair_with_empty_seal_fails() -> None:
    def defect(a: Path, b: Path) -> None:
        for f in (_thread_dir(a) / "sealed-submission").iterdir():
            f.unlink()

    assert _run(mutate=defect) != 0


def test_pair_with_non_terminal_lifecycle_fails() -> None:
    def defect(a: Path, b: Path) -> None:
        payload = json.loads(a.read_text(encoding="utf-8"))
        payload["lifecycle_events"] = [{"phase": "SUBMITTED", "at": "t0"}]
        a.write_text(json.dumps(payload), encoding="utf-8")

    assert _run(mutate=defect) != 0


def test_wrong_skill_badges_fail() -> None:
    def defect(a: Path, b: Path) -> None:
        payload = json.loads(b.read_text(encoding="utf-8"))
        payload["skills_source"] = "none"
        payload["skills_sha"] = None
        b.write_text(json.dumps(payload), encoding="utf-8")

    assert _run(mutate=defect) != 0


def test_non_pilot_experiment_id_fails() -> None:
    def defect(a: Path, b: Path) -> None:
        payload = json.loads(b.read_text(encoding="utf-8"))
        payload["experiment_id"] = "skill-ablation-v1"  # the formal experiment id
        b.write_text(json.dumps(payload), encoding="utf-8")

    assert _run(mutate=defect) != 0


def test_frozen_field_mismatch_fails() -> None:
    def defect(a: Path, b: Path) -> None:
        payload = json.loads(b.read_text(encoding="utf-8"))
        payload["image"] = "other-image"
        b.write_text(json.dumps(payload), encoding="utf-8")

    assert _run(mutate=defect) != 0


def test_lock_confound_fails() -> None:
    """A resolved-lock difference beyond the treatment must fail the pair."""

    def defect(a: Path, b: Path) -> None:
        lock_path = _thread_dir(b) / "resolved-run-lock.json"
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        payload["payload"]["agent"]["model_id"] = "claude-sonnet-4"
        lock_path.write_text(json.dumps(payload), encoding="utf-8")

    assert _run(mutate=defect) != 0


def test_missing_lock_fails() -> None:
    """A pair without a resolved-run-lock.json must fail closed."""

    def defect(a: Path, b: Path) -> None:
        (_thread_dir(b) / "resolved-run-lock.json").unlink()

    assert _run(mutate=defect) != 0
