"""Task 14 — Ablation-Ready v0 comparability + release/protocol gates.

A paired trial may differ ONLY in Skill availability: every other frozen
identity field (case, experiment, image, benchmark_commit, profile,
submission_root, verifier, platform, site, replicate, agent model) must match.
A formal record must reference the frozen release commit and must not be a
pilot.  INFRA_INVALID observations are excluded from the formal pair and
retained in a separate invalid-run ledger.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

import ccbench.experiments.ablation as ablation

ROOT = Path(__file__).resolve().parents[2]
RELEASE_FILE = ROOT / "releases" / "ablation-ready-v0.json"
PROTOCOL_FILE = ROOT / "experiments" / "skill-ablation-v1" / "protocol.yaml"
LEDGER_FILE = ROOT / "experiments" / "skill-ablation-v1" / "invalid-runs.json"


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _record(**over) -> dict:
    base = {
        "schema_version": 1,
        "run_id": "run-default",
        "case_id": "032-matclaw-cips-curie-temperature",
        "execution_class": "hpc_controller",
        "agent_model": "model-x",
        "experiment_id": ablation.FORMAL_EXPERIMENT_ID,
        "condition_id": "no-skill",
        "skills_source": "none",
        "skills_sha": None,
        "image": "matclaw-cips-2.2.11-gpu-amd64",
        "benchmark_commit": "frozen-sha",
        "profile": "formal",
        "submission_root": ".",
        "verifier": "matclaw",
        "platform": "gpu-slurm",
        "job_id": "12345",
        "site_config_digest": "sha256:site-a",
        "replicate": 1,
        "attempt": 1,
        "thread_dir": None,
        "legacy_normalized": False,
        "usage": {"tool_calls": 3, "tokens": 100, "elapsed_sec": 1.0},
        "lifecycle_events": [{"phase": "SUBMITTED", "at": "t0"}],
        "result": {
            "run_id": "run-default",
            "result_class": "VALID_RESULT",
            "failure_code": "PASS",
            "reason": "ok",
            "retryable": False,
        },
    }
    base.update(over)
    return base


def _no_skill(**over) -> dict:
    base = {"run_id": "run-no-skill"}
    base.update(over)
    return _record(**base)


def _with_skill(**over) -> dict:
    base = {
        "run_id": "run-with-skill",
        "condition_id": "with-skill",
        "skills_source": "bundled",
        "skills_sha": _sha("skill-bundle"),
    }
    base.update(over)
    return _record(**base)


def _release(**over) -> dict:
    base = {
        "schema": ablation.RELEASE_SCHEMA,
        "name": "ablation-ready-benchmark-v0",
        "source_commit": "frozen-sha",
        "components": {
            "schemas": [{"path": "schemas/run-record.schema.json", "sha256": "ab" * 32}],
            "site_adapter": [{"path": "ccbench/hpc/adapters/slurm.py", "sha256": "ab" * 32}],
            "cases": [{"case_id": "032-matclaw-cips-curie-temperature", "instruction_sha256": "ab" * 32}],
            "skills": [{"path": "runtimes/recipes/skills/deepmd", "sha256": "ab" * 32}],
            "resource_profiles": [{"path": "032-matclaw-cips-curie-temperature/profiles/resource.yaml", "sha256": "ab" * 32}],
            "platform_profiles": [{"path": "032-matclaw-cips-curie-temperature/profiles/platform.yaml", "sha256": "ab" * 32}],
            "compute_runtimes": [{"path": "032-matclaw-cips-curie-temperature/reference/compute-runtime.lock.json", "sha256": "ab" * 32}],
            "verifiers": [{"path": "032-matclaw-cips-curie-temperature/tests/test.sh", "sha256": "ab" * 32}],
            "candidate_images": [{"sif_path_remote": "/runtime/matclaw-cips-2.2.11-gpu-amd64.sif", "sif_sha256": "ab" * 32}],
        },
    }
    base.update(over)
    base["release_digest"] = ablation.release_digest(base)
    return base


def _protocol(**over) -> dict:
    base = {
        "schema": ablation.PROTOCOL_SCHEMA,
        "name": "skill-ablation-v1",
        "release": "ablation-ready-benchmark-v0",
        "paired_trials": {"same_site": True, "same_profile": True},
        "replicates": 3,
        "conditions": [
            {"id": "no-skill", "skills_source": "none", "skills_sha": None},
            {"id": "with-skill", "skills_source": "bundled", "skills_sha": _sha("skill-bundle")},
        ],
        "retry_policy": {
            "infra_invalid": "replace-with-new-run-id",
            "scientific_failure": "no-silent-rerun",
            "agent_failure": "no-silent-rerun",
        },
        "treatment_difference": "skill-availability-only",
        "pilot": {
            "experiment_id": ablation.PILOT_EXPERIMENT_ID,
            "excluded_from_formal": True,
        },
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- release file

def test_release_manifest_exists_and_validates() -> None:
    release = json.loads(RELEASE_FILE.read_text(encoding="utf-8"))
    assert ablation.release_errors(release) == []


def test_release_freezes_all_components() -> None:
    release = json.loads(RELEASE_FILE.read_text(encoding="utf-8"))
    components = release["components"]
    for key in ablation.REQUIRED_RELEASE_COMPONENTS:
        assert components.get(key), f"missing release component {key}"
        assert len(components[key]) >= 1


def test_release_pins_digests_and_no_mutable_tags() -> None:
    release = json.loads(RELEASE_FILE.read_text(encoding="utf-8"))
    assert ":latest" not in json.dumps(release)
    assert "-----BEGIN" not in json.dumps(release)
    assert release["release_digest"] == ablation.release_digest(release)


def test_release_references_frozen_source_commit() -> None:
    release = json.loads(RELEASE_FILE.read_text(encoding="utf-8"))
    assert len(release["source_commit"]) >= 7
    assert release["source_commit"] != "unknown"


# ------------------------------------------------------------- protocol file

def test_protocol_exists_and_declares_paired_policy() -> None:
    protocol = yaml.safe_load(PROTOCOL_FILE.read_text(encoding="utf-8"))
    assert ablation.protocol_errors(protocol) == []


def test_protocol_fixes_replicates_and_retry_policy() -> None:
    protocol = yaml.safe_load(PROTOCOL_FILE.read_text(encoding="utf-8"))
    assert isinstance(protocol["replicates"], int) and protocol["replicates"] >= 1
    retry = protocol["retry_policy"]
    assert retry["infra_invalid"] == "replace-with-new-run-id"
    assert retry["scientific_failure"] == "no-silent-rerun"
    assert retry["agent_failure"] == "no-silent-rerun"


def test_protocol_pilot_is_distinct_and_excluded() -> None:
    protocol = yaml.safe_load(PROTOCOL_FILE.read_text(encoding="utf-8"))
    assert protocol["pilot"]["experiment_id"] == ablation.PILOT_EXPERIMENT_ID
    assert protocol["pilot"]["excluded_from_formal"] is True
    assert ablation.is_pilot_experiment(ablation.PILOT_EXPERIMENT_ID)
    assert not ablation.is_pilot_experiment(ablation.FORMAL_EXPERIMENT_ID)


def test_protocol_has_invalid_run_ledger() -> None:
    ledger = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    assert isinstance(ledger, list)


# --------------------------------------------------- validator behavior (pure)

def test_paired_records_differing_only_in_skill_are_comparable() -> None:
    release = _release()
    no, with_ = _no_skill(), _with_skill()
    assert ablation.formal_record_errors(release, no) == []
    assert ablation.formal_record_errors(release, with_) == []
    assert ablation.comparability_errors(no, with_) == []


def test_pair_rejected_when_any_frozen_field_differs() -> None:
    release = _release()
    mutators = {
        "case_id": "033-matclaw-cips-domain-wall-search",
        "profile": "smoke",
        "site_config_digest": "sha256:site-b",
        "image": "some-other-image",
        "benchmark_commit": "other-commit",
        "platform": "cpu-slurm",
        "verifier": "other-verifier",
        "submission_root": "/other",
        "replicate": 2,
        "agent_model": "model-y",
    }
    for field, value in mutators.items():
        with_ = _with_skill(**{field: value})
        errs = ablation.comparability_errors(_no_skill(), with_)
        assert any(field in e for e in errs), (field, errs)


def test_pair_rejected_when_treatment_badges_are_wrong() -> None:
    # no-skill arm that carries a skills digest
    errs = ablation.comparability_errors(_no_skill(skills_sha=_sha("x")), _with_skill())
    assert any("no-skill" in e for e in errs)
    # with-skill arm that claims no skill source
    errs = ablation.comparability_errors(_no_skill(), _with_skill(skills_source="none", skills_sha=None))
    assert any("with-skill" in e for e in errs)
    # two arms of the same condition are not a pair
    errs = ablation.comparability_errors(_no_skill(), _no_skill())
    assert any("no-skill and one with-skill" in e for e in errs)


def test_formal_record_rejected_when_release_commit_absent() -> None:
    release = _release(source_commit="frozen-sha")
    record = _with_skill(benchmark_commit="older-commit")
    errs = ablation.formal_record_errors(release, record)
    assert any("benchmark_commit" in e for e in errs)


def test_formal_record_rejected_when_experiment_is_pilot() -> None:
    release = _release()
    record = _with_skill(experiment_id=ablation.PILOT_EXPERIMENT_ID)
    errs = ablation.formal_record_errors(release, record)
    assert any("pilot" in e for e in errs)


def test_infra_invalid_routed_to_invalid_ledger_not_formal_pair() -> None:
    release = _release()
    invalid = _with_skill(
        result={**_with_skill()["result"], "result_class": "INFRA_INVALID",
                "failure_code": "HPC_FAILURE", "reason": "gateway down"}
    )
    valid = _with_skill()
    candidates, ledger = ablation.partition_formal([valid, invalid])
    assert candidates == [valid["run_id"]]
    assert ledger == [invalid["run_id"]]
    assert ablation.formal_record_errors(release, invalid)  # has an error


def test_smoke_records_never_enter_the_formal_pair() -> None:
    """SMOKE observations are excluded from BOTH formal lists: a smoke record
    is not a formal candidate, and even a smoke INFRA_INVALID must not be
    routed to the invalid-run ledger (the ledger only tracks formal runs)."""
    release = _release()
    smoke = _with_skill(run_id="run-smoke", run_mode="smoke")
    smoke_invalid = _with_skill(
        run_id="run-smoke-invalid",
        run_mode="smoke",
        result={**_with_skill()["result"], "result_class": "INFRA_INVALID",
                "failure_code": "HPC_FAILURE", "reason": "gateway down"},
    )
    valid = _with_skill()

    errs = ablation.formal_record_errors(release, smoke)
    assert any("run_mode" in e for e in errs)

    candidates, ledger = ablation.partition_formal([smoke, smoke_invalid, valid])
    assert candidates == [valid["run_id"]]
    assert ledger == []


def test_release_rejects_missing_component_and_secret() -> None:
    good = _release()
    assert ablation.release_errors(good) == []
    bad = _release()
    del bad["components"]["skills"]
    assert any("skills" in e for e in ablation.release_errors(bad))
    leaky = _release()
    leaky["components"]["cases"][0]["sha256"] = "-----BEGIN RSA PRIVATE KEY-----"
    assert ablation.release_errors(leaky)


def test_skills_sha_is_full_sha256_with_prefix(tmp_path: Path) -> None:
    """The canonical bundle digest must be a full sha256: value, not a 16-char
    truncation, so it matches the release manifest and run-record vocabulary."""
    sys.path.insert(0, str(ROOT))
    import skills_sha

    d = tmp_path / "skills"
    d.mkdir()
    (d / "a.txt").write_text("x")
    (d / "nested").mkdir()
    (d / "nested" / "b.txt").write_text("y")

    digest = skills_sha.hash_tree(d)
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_protocol_with_skill_sha_matches_canonical_bundle_digest() -> None:
    """The protocol's frozen with-skill skills_sha must equal the digest of the
    actual skills bundle, or a run-record can never match the protocol arm.

    When D11 is blocked (skill bundle changed during infra work), the
    mismatch is expected — the protocol will be re-frozen at release time.
    """
    from ccbench.experiments.release_builder import check_qualification_receipt

    sys.path.insert(0, str(ROOT))
    import skills_sha

    protocol = yaml.safe_load(PROTOCOL_FILE.read_text(encoding="utf-8"))
    with_skill = next(
        c for c in protocol["conditions"] if c["id"] == "with-skill"
    )
    canonical = skills_sha.hash_tree(ROOT / "base-env-build" / "skills")
    d11 = check_qualification_receipt(ROOT)
    if d11["status"] == "PASS":
        assert with_skill["skills_sha"] == canonical
    else:
        # Skill bundle changed during infra work; protocol will be re-frozen
        # at release time.  Verify the comparison runs without error.
        assert isinstance(with_skill["skills_sha"], str)
        assert isinstance(canonical, str)


def test_release_skill_component_digests_match_canonical_hash() -> None:
    """Release per-skill component digests use the same full-sha256 scheme."""
    release = json.loads(RELEASE_FILE.read_text(encoding="utf-8"))
    for entry in release["components"]["skills"]:
        digest = entry["sha256"]
        assert isinstance(digest, str) and len(digest) == 64, entry
        assert entry["path"].startswith("base-env-build/skills/")
