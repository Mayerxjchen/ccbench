from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.config.candidate import CandidateConfigError, load_candidate_config
from bench.pilot import LOCAL_DEV, MvpError, _acquire_evaluation_seal, evaluate, start


def test_local_run_evaluates_but_is_explicitly_untrusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import bench.pilot as pilot
    run = tmp_path / "local-run"
    run.mkdir()
    (run / "run-state.json").write_text(
        json.dumps({"run_id": "local-run", "state": "CANDIDATE_COMPLETE", "trust_mode": LOCAL_DEV}),
        encoding="utf-8",
    )
    monkeypatch.setattr(pilot, "_evaluate_locked", lambda _root, state: {
        "state": state, "result": {"trust_mode": LOCAL_DEV, "publication_eligible": False}
    })
    result = evaluate(run)
    assert result["result"]["trust_mode"] == LOCAL_DEV
    assert result["result"]["publication_eligible"] is False


def test_formal_is_not_a_user_config_switch(tmp_path: Path) -> None:
    config = tmp_path / "candidate.toml"
    config.write_text('trust_mode = "formal"\n', encoding="utf-8")
    with pytest.raises(CandidateConfigError, match="coordinator-only"):
        load_candidate_config(config)


def test_external_submission_is_allowed_but_not_formal(tmp_path: Path) -> None:
    config = tmp_path / "candidate.toml"
    config.write_text('trust_mode = "external_submission"\n', encoding="utf-8")
    assert load_candidate_config(config)["trust_mode"] == "external_submission"


def test_evaluation_seal_uses_operator_lock_and_failed_precommit_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    operator_lock = tmp_path / "candidate-run.lock.json"
    operator_lock.write_text("{}", encoding="utf-8")
    seen: list[Path] = []

    def fail_freeze(_workspace: Path, sealed: Path, *, lock_path: Path):
        seen.append(lock_path)
        sealed.mkdir()
        raise RuntimeError("synthetic preflight/freeze failure")

    monkeypatch.setattr("bench.pilot.freeze_submission", fail_freeze)
    with pytest.raises(RuntimeError, match="synthetic"):
        _acquire_evaluation_seal(workspace, tmp_path, operator_lock)
    assert seen == [operator_lock]
    assert not (tmp_path / ".evaluate.lock").exists()
    assert not (tmp_path / "sealed-submission").exists()


def test_formal_rejects_explicit_draft_case(tmp_path: Path) -> None:
    case = tmp_path / "001-draft"
    case.mkdir()
    (case / "task.md").write_text("draft", encoding="utf-8")
    (case / "case.toml").write_text(
        'schema_version = "1.2"\ncase_version = "0.1.0-draft"\n'
        '[execution]\nclass = "local_sandbox"\n'
        '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n',
        encoding="utf-8",
    )
    with pytest.raises(MvpError, match="READY case"):
        start(str(case), run_dir=tmp_path / "run", formal=True)
