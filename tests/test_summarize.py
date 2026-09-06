import json
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("dftworld_summarize", ROOT / "summarize.py")
summarize = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(summarize)

aggregate = summarize.aggregate
build_comparison = summarize.build_comparison
build_summary_data = summarize.build_summary_data
format_skill_state = summarize.format_skill_state
load_matclaw_status = summarize.load_matclaw_status
load_runs = summarize.load_runs
render_overview = summarize.render_overview
select_completed_cases = summarize.select_completed_cases
write_summary_views = summarize.write_summary_views


def execution(
    task: str,
    run_ts: str,
    ok: bool,
    *,
    condition_id: str = "no-skill",
    skills_invoked: list[str] | None = None,
    experiment_id: str = "default",
) -> dict:
    return {
        "run_ts": run_ts,
        "experiment_id": experiment_id,
        "condition_id": condition_id,
        "replicate": 1,
        "attempt": 1,
        "task": task,
        "ok": ok,
        "reward": 1.0 if ok else 0.0,
        "tool_calls": 3,
        "tokens": 1200,
        "elapsed_sec": 12.4,
        "skills_invoked": skills_invoked or [],
        "benchmark_commit": "abc123",
        "error": "" if ok else "failed",
    }


def write_case_state(root, case: str, *, smoke: bool, paper: bool) -> None:
    case_dir = root / case
    case_dir.mkdir()
    validation = {
        "benchmark_id": case,
        "state": "smoke_validated_paper_pending" if smoke else "constructed",
        "smoke_profile": {"completed": smoke},
        "paper_profile": {"completed": paper},
        "benchmark_valid": smoke and paper,
    }
    (case_dir / "VALIDATION.json").write_text(json.dumps(validation))
    (case_dir / "benchmark_valid.json").write_text(
        json.dumps(
            {
                "benchmark_id": case,
                "state": validation["state"],
                "benchmark_valid": smoke and paper,
                "reason": "fixture",
            }
        )
    )


def test_completed_case_uses_latest_pass_not_later_failure():
    rows = select_completed_cases(
        [
            execution("010-deepmd-train", "2026-01-01__00-00-00", True),
            execution("010-deepmd-train", "2026-01-02__00-00-00", False),
        ]
    )

    assert rows[0]["run_ts"] == "2026-01-01__00-00-00"
    assert rows[0]["latest_run_failed"] is True


def test_completed_cases_are_unique_and_sorted_by_case_number():
    rows = select_completed_cases(
        [
            execution("020-hartree-to-ev", "2026-01-01__00-00-00", True),
            execution("008-packmol-build", "2026-01-01__00-00-01", True),
            execution("008-packmol-build", "2026-01-02__00-00-00", True),
        ]
    )

    assert [row["task"] for row in rows] == [
        "008-packmol-build",
        "020-hartree-to-ev",
    ]
    assert rows[0]["run_ts"] == "2026-01-02__00-00-00"


def test_completed_cases_are_selected_independently_per_condition():
    rows = [
        execution(
            "010-deepmd-train",
            "2026-01-01__00-00-00",
            True,
            condition_id="no-skill",
        ),
        execution(
            "010-deepmd-train",
            "2026-01-02__00-00-00",
            True,
            condition_id="with-skill",
            skills_invoked=["deepmd"],
        ),
    ]

    assert select_completed_cases(rows, "with-skill")[0]["run_ts"].startswith(
        "2026-01-02"
    )
    assert select_completed_cases(rows, "no-skill")[0]["run_ts"].startswith(
        "2026-01-01"
    )


def test_comparison_has_null_missing_sides_and_signed_deltas():
    no_skill = execution(
        "010-deepmd-train",
        "2026-01-01__00-00-00",
        True,
        condition_id="no-skill",
    )
    with_skill = execution(
        "010-deepmd-train",
        "2026-01-02__00-00-00",
        True,
        condition_id="with-skill",
        skills_invoked=["deepmd"],
    )
    with_skill.update(tool_calls=5, tokens=1500, elapsed_sec=10.0)
    with_skill["benchmark_commit"] = "different-commit"
    only_no_skill = execution(
        "020-hartree-to-ev",
        "2026-01-03__00-00-00",
        True,
        condition_id="no-skill",
    )

    rows = build_comparison([only_no_skill, with_skill, no_skill])

    assert [row["task"] for row in rows] == [
        "010-deepmd-train",
        "020-hartree-to-ev",
    ]
    assert rows[0]["delta_with_minus_no"] == {
        "tool_calls": 2,
        "tokens": 300,
        "elapsed_sec": -2.4,
    }
    assert rows[0]["strictly_comparable"] is False
    assert rows[1]["with_skill"] is None
    assert rows[1]["delta_with_minus_no"] is None
    assert rows[1]["strictly_comparable"] is None


def test_summary_data_separates_conditions():
    rows = [
        execution(
            "008-packmol-build",
            "2026-01-01__00-00-00",
            True,
            condition_id="with-skill",
            skills_invoked=["packmol"],
        ),
        execution(
            "009-cp2k-run",
            "2026-01-02__00-00-00",
            True,
            condition_id="no-skill",
        ),
    ]

    data = build_summary_data(rows, root=None)

    assert data["with_skill"]["completed_cases"][0]["task"] == "008-packmol-build"
    assert data["no_skill"]["completed_cases"][0]["task"] == "009-cp2k-run"
    assert data["history"]["with_skill"][0]["skill_state"] == "packmol"
    assert data["history"]["no_skill"][0]["skill_state"] == "无 Skill 环境"


def test_skill_states_remain_distinct():
    assert (
        format_skill_state(
            {"skills_invoked": ["deepmd", "deepmd"], "condition_id": "with-skill"}
        )
        == "deepmd"
    )
    assert (
        format_skill_state({"skills_invoked": [], "condition_id": "with-skill"})
        == "未调用"
    )
    assert (
        format_skill_state({"skills_invoked": [], "condition_id": "no-skill"})
        == "无 Skill 环境"
    )


def test_matclaw_status_does_not_infer_agent_success(tmp_path):
    write_case_state(
        tmp_path,
        "032-matclaw-cips-curie-temperature",
        smoke=True,
        paper=False,
    )

    row = load_matclaw_status(tmp_path)[0]

    assert row["agent_passed"] == "未记录"
    assert row["oracle_calibrated"] is False
    assert row["smoke_validated"] is True


def test_matclaw_status_prefers_explicit_new_state_fields(tmp_path):
    write_case_state(
        tmp_path,
        "033-matclaw-cips-domain-wall-search",
        smoke=True,
        paper=False,
    )
    validation_path = (
        tmp_path / "033-matclaw-cips-domain-wall-search" / "VALIDATION.json"
    )
    validation = json.loads(validation_path.read_text())
    validation.update(
        {
            "construction_valid": True,
            "oracle_calibrated": True,
            "agent_passed": False,
        }
    )
    validation_path.write_text(json.dumps(validation))

    row = load_matclaw_status(tmp_path)[0]

    assert row["construction_valid"] is True
    assert row["oracle_calibrated"] is True
    assert row["agent_passed"] is False


def test_overview_is_case_oriented():
    rows = [
        execution(
            "010-deepmd-train",
            "2026-01-01__00-00-00",
            True,
            condition_id="with-skill",
            skills_invoked=["deepmd"],
        ),
        execution(
            "010-deepmd-train",
            "2026-01-02__00-00-00",
            True,
            condition_id="no-skill",
        ),
    ]
    report = render_overview(rows, root=None)

    assert "## 概览" in report
    headings = [
        "## With Skill 已完成案例",
        "## No Skill 已完成案例",
        "## With Skill / No Skill 直接对比",
        "## MatClaw 031–033 构造状态",
        "## With Skill 运行历史",
        "## No Skill 运行历史",
    ]
    positions = [report.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "Skill 调用" in report
    assert "## default" not in report


def test_write_summary_views_keeps_json_and_markdown_membership_aligned(tmp_path):
    rows = [
        execution(
            "008-packmol-build",
            "2026-01-01__00-00-00",
            True,
            condition_id="with-skill",
            skills_invoked=["packmol"],
        ),
        execution(
            "009-cp2k-run",
            "2026-01-02__00-00-00",
            True,
            condition_id="no-skill",
        ),
    ]

    markdown_path, json_path = write_summary_views(rows, tmp_path, root=None)
    data = json.loads(json_path.read_text())
    markdown = markdown_path.read_text()

    assert data["with_skill"]["completed_cases"][0]["task"] == "008-packmol-build"
    assert data["no_skill"]["completed_cases"][0]["task"] == "009-cp2k-run"
    assert "`008-packmol-build`" in markdown
    assert "`009-cp2k-run`" in markdown
    assert json_path.name == "summary.json"
    assert markdown_path.name == "SUMMARY.md"


def test_load_runs_prefers_run_record_over_legacy_summary(tmp_path):
    from ccbench.contracts.events import next_event
    from ccbench.contracts.result import BenchmarkResult
    from ccbench.contracts.run_record import RunRecordV2
    from ccbench.core.run_store import RunStore

    run_dir = tmp_path / "2026-08-18__01-00-00"
    run_dir.mkdir()
    # legacy summary.json present but stale/tampered — must be ignored
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "experiment_id": "legacy",
                "condition_id": "no-skill",
                "skill": "",
                "results": [{"task": "001-hello", "ok": False}],
            }
        )
    )
    # RunStore writes only v2 records; the canonical record wins over the
    # legacy summary view.
    event = next_event(
        None,
        "HARNESS",
        "candidate_frozen",
        {"run_id": "2026-08-18__01-00-00"},
        "2026-08-18T01:00:00Z",
    )
    record = RunRecordV2(
        run_id="2026-08-18__01-00-00",
        case_id="001-hello",
        execution_class="local_sandbox",
        agent_model="deepseek/deepseek-chat",
        condition_id="no-skill",
        skills_source="none",
        skills_sha=None,
        image="dftworld-base:sha256:abc",
        benchmark_commit="8f1921f",
        profile="paper",
        submission_root=".",
        verifier="dftworld-base:sha256:def",
        platform="local_docker",
        job_id=None,
        site_config_digest="sha256:" + "8" * 64,
        usage={"tool_calls": 3, "tokens": 1200, "elapsed_sec": 12.4},
        lifecycle_events=[
            {"phase": "PACKAGED", "at": "2026-08-18T01:00:00Z"},
            {"phase": "CANDIDATE_STARTING", "at": "2026-08-18T01:00:01Z"},
            {"phase": "CANDIDATE_RUNNING", "at": "2026-08-18T01:00:02Z"},
            {"phase": "CANDIDATE_STOPPING", "at": "2026-08-18T01:00:30Z"},
            {"phase": "CANDIDATE_FROZEN", "at": "2026-08-18T01:00:31Z"},
            {"phase": "SUBMISSION_COLLECTED", "at": "2026-08-18T01:00:32Z"},
            {"phase": "CANDIDATE_DESTROYED", "at": "2026-08-18T01:00:33Z"},
            {"phase": "STRUCTURALLY_VALIDATED", "at": "2026-08-18T01:00:34Z"},
            {"phase": "QUARANTINED", "at": "2026-08-18T01:00:35Z"},
            {"phase": "SEALED", "at": "2026-08-18T01:00:36Z"},
            {"phase": "VERIFYING", "at": "2026-08-18T01:00:37Z"},
            {"phase": "COMPLETED", "at": "2026-08-18T01:01:00Z"},
        ],
        result=BenchmarkResult.valid(
            "2026-08-18__01-00-00", passed=True, reason="ok"
        ),
        run_mode="formal",
        lock_digest="sha256:" + "1" * 64,
        event_root_digest=event.event_digest,
        event_chain=[event.to_dict()],
        attempts=[],
        budgets={
            "model_turns": 1,
            "logical_requests": 1,
            "api_attempts": 1,
            "tokens": 5,
            "usd_microcost": 0,
            "agent_active_walltime_ms": 1000,
            "run_total_walltime_ms": 2000,
            "local_tool_walltime_ms": 0,
            "api_retry_walltime_ms": 0,
            "scheduler_wait_ms": 0,
            "jobs": 0,
            "cpu_hours": 0,
            "gpu_hours": 0,
            "storage_byte_hours": 0,
        },
        runtime_identities={
            role: {
                "role": role,
                "profile": f"local-{role}",
                "image": (
                    "dftworld-base:sha256:def"
                    if role == "verifier"
                    else "dftworld-base:sha256:abc"
                ),
                "digest": "sha256:" + char * 64,
            }
            for role, char in zip(
                ("candidate", "control", "compute", "verifier"),
                "cdef",
                strict=True,
            )
        }
        | {"control": None},  # local_sandbox has no control plane
        remote_jobs=[],
        seal={
            "manifest_digest": "sha256:" + "9" * 64,
            "file_count": 0,
            "total_bytes": 0,
            "sealed_at": "2026-08-18T01:00:36Z",
            "legacy_layout": False,
            "exclusions": [],
        },
    )
    RunStore(tmp_path).create(record)

    runs = load_runs(tmp_path)

    assert len(runs) == 1
    assert runs[0]["experiment_id"] == "default"
    assert runs[0]["results"][0]["ok"] is True


def test_load_runs_falls_back_to_legacy_summary_when_no_record(tmp_path):
    run_dir = tmp_path / "2026-01-01__00-00-00"
    run_dir.mkdir()
    (run_dir / "summary.json").write_text(
        json.dumps(
            {
                "experiment_id": "legacy",
                "condition_id": "no-skill",
                "results": [{"task": "001-hello", "ok": True, "reward": 1.0}],
            }
        )
    )

    runs = load_runs(tmp_path)

    assert len(runs) == 1
    assert runs[0]["experiment_id"] == "legacy"
    assert runs[0]["results"][0]["ok"] is True


def test_summary_data_carries_release_reference():
    rows = [
        execution("008-packmol-build", "2026-01-01__00-00-00", True),
    ]
    release_file = ROOT / "releases" / "ablation-ready-v0.json"
    frozen = json.loads(release_file.read_text(encoding="utf-8"))

    data = build_summary_data(rows, root=ROOT)
    release = data["release"]
    assert release is not None
    assert release["name"] == frozen["name"]
    assert release["source_commit"] == frozen["source_commit"]
    assert release["release_digest"] == frozen["release_digest"]

    # unit fixtures without the manifest degrade gracefully
    assert build_summary_data(rows, root=None)["release"] is None


def test_summary_excludes_pilot_runs_from_formal_view():
    formal = execution("032-matclaw-cips-curie-temperature", "2026-02-01__00-00-00", True)
    pilot = execution(
        "032-matclaw-cips-curie-temperature",
        "2026-02-02__00-00-00",
        True,
        condition_id="with-skill",
        skills_invoked=["deepmd"],
        experiment_id="skill-ablation-v1-pilot",
    )

    data = build_summary_data([formal, pilot], root=None)

    # pilot never enters completed cases or the comparison
    assert [c["task"] for c in data["with_skill"]["completed_cases"]] == []
    assert [c["task"] for c in data["no_skill"]["completed_cases"]] == ["032-matclaw-cips-curie-temperature"]
    assert all("pilot" not in json.dumps(c) for c in data["comparison"])
    # raw history keeps every observation for traceability
    assert data["history"]["with_skill"][0]["experiment_id"] == "skill-ablation-v1-pilot"


def test_aggregate_excludes_infra_invalid_from_success_denominator():
    no_skill = execution("010-deepmd-train", "2026-01-01__00-00-00", False)
    no_skill["result_class"] = "INFRA_INVALID"
    passed = execution("010-deepmd-train", "2026-01-02__00-00-00", True)

    cell = aggregate([no_skill, passed])[
        ("default", "no-skill", "010-deepmd-train")
    ]

    assert cell["runs"] == 2
    assert cell["infra_invalid"] == 1
    assert cell["passed"] == 1
    assert cell["rate"] == 1.0


def test_aggregate_all_infra_is_zero_rate_not_zero_division():
    row = execution("010-deepmd-train", "2026-01-01__00-00-00", False)
    row["result_class"] = "INFRA_INVALID"

    cell = aggregate([row])[("default", "no-skill", "010-deepmd-train")]

    assert cell["infra_invalid"] == 1
    assert cell["rate"] == 0.0
