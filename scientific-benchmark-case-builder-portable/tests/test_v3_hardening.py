"""v3 hardening regressions: the three fixes ordered after Luna v2.

1. C-V8 integrity — declared artifacts must exist, every hash is checked
   without short-circuiting an earlier finding, and the manifest is
   type-validated before any int()/sorted() conversion, so a malformed
   contract is INVALID_SUBMISSION (agent-side), never retryable INFRA_INVALID.
2. derive_verifier_plan — every applicable layer is emitted explicitly with
   status selected|deferred; a mandatory deferral needs a reason; the
   machine-mirrored applicability table must not drift from verifier-policy.md;
   V4 (mandatory for every kind) can never silently disappear.
3. Test isolation — leave-one-out source context (invariant H): allow roots
   versus the sources lock, hash_sources.py --exclude recording audited
   omissions, and answer files such as acceptance.json never entering the
   builder context; the unconsumed scaffold dead stubs stayed deleted.

Every behavioral claim below is proven by execution (derive runs, the
verifier runs, the checker runs) — never by reading source text.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PACKAGE = Path(__file__).resolve().parents[1]
SKILL = PACKAGE / "skills" / "build-scientific-benchmark-case"
SCRIPTS_COMMON = SKILL / "scripts" / "common"
SCRIPTS_MLP = SKILL / "scripts" / "categories" / "mlp"
TEMPLATE = SKILL / "assets" / "case-template" / "common"
VERIFIER_POLICY = SKILL / "references" / "categories" / "mlp" / "verifier-policy.md"
FIXTURE_CASE = PACKAGE / "tests" / "fixtures" / "luna-regressions" / "mvp-passing"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


derive = _load("v3_derive", SCRIPTS_MLP / "derive_verifier_plan.py")
consistency = _load("v3_consistency", SCRIPTS_MLP / "check_draft_consistency.py")
runnable = _load("v3_runnable", SCRIPTS_COMMON / "check_discovery_runnable.py")
verifier = _load("v3_template_verifier", TEMPLATE / "tests" / "verifier.py")


# ------------------------------------------------------ (2) plan table mirror


def _parse_policy_table() -> dict[str, tuple[set[str], set[str]]]:
    text = VERIFIER_POLICY.read_text(encoding="utf-8")
    section = text.split("## Applicability by case kind", 1)[1].split("## Rules", 1)[0]
    parsed: dict[str, tuple[set[str], set[str]]] = {}
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 3 or cells[0] == "Case kind" or set("".join(cells)) <= set("-: "):
            continue
        kind = cells[0].strip("`")
        parsed[kind] = (
            {f"MLP-{v.strip()}" for v in cells[1].split(",") if v.strip()},
            {f"MLP-{v.strip()}" for v in cells[2].split(",") if v.strip()},
        )
    assert parsed, "no applicability table found in verifier-policy.md"
    return parsed


def test_kind_layers_mirror_the_verifier_policy_table() -> None:
    table = _parse_policy_table()
    mirrored = {
        kind: (set(spec["mandatory"]), set(spec["conditional"]))
        for kind, spec in derive.KIND_LAYERS.items()
    }
    assert mirrored == table, (
        "derive_verifier_plan.KIND_LAYERS drifted from the "
        "verifier-policy.md applicability table"
    )


# ------------------------------------------------------ (2) derive discipline


def _plan(design: dict) -> dict:
    return derive.derive(design)


def _fail_message(design: dict) -> str:
    with pytest.raises(SystemExit) as excinfo:
        derive.derive(design)
    return str(excinfo.value)


@pytest.mark.parametrize("kind", sorted(derive.KIND_LAYERS))
def test_every_mandatory_layer_is_emitted_without_capabilities(kind: str) -> None:
    # The V4-silent-disappearance regression: mandatory-for-kind layers must
    # appear (status selected) even when no capability declares them.
    plan = _plan({"case_kind": kind, "workflow_capabilities": []})
    assert plan["schema_version"] == 2
    statuses = {layer["id"]: layer for layer in plan["layers"]}
    for lid in derive.KIND_LAYERS[kind]["mandatory"]:
        assert lid in statuses, f"{lid} vanished for {kind}"
        assert statuses[lid]["status"] == "selected"
        assert statuses[lid]["mandatory"] is True
    for layer in plan["layers"]:
        assert layer["status"] in ("selected", "deferred")
        if layer["status"] == "deferred":
            assert str(layer.get("reason", "")).strip()


def test_mandatory_deferral_requires_and_carries_a_reason() -> None:
    design = {
        "case_kind": "final_model_retraining",
        "workflow_capabilities": [],
        "verifier_deferrals": [
            {"layer": "MLP-V4", "reason": "hidden set not yet frozen"}
        ],
    }
    plan = _plan(design)
    by_id = {layer["id"]: layer for layer in plan["layers"]}
    assert by_id["MLP-V4"]["status"] == "deferred"
    assert by_id["MLP-V4"]["reason"] == "hidden set not yet frozen"
    assert plan["deferred_layers"] == ["MLP-V4"]
    # fixture closure follows the SELECTED hard-outcome layers, not the policy
    assert plan["fixtures"]["negative"] == 0
    no_reason = dict(design, verifier_deferrals=[{"layer": "MLP-V4", "reason": "  "}])
    assert "a deferral without a reason" in _fail_message(no_reason)


def test_structural_layers_can_never_be_deferred() -> None:
    design = {
        "case_kind": "final_model_retraining",
        "workflow_capabilities": [],
        "verifier_deferrals": [{"layer": "MLP-V0", "reason": "whatever"}],
    }
    assert "structural layer" in _fail_message(design)


def test_deferral_of_an_inapplicable_layer_is_an_error() -> None:
    # V5 is neither mandatory nor capability-declared here: deferring it is
    # scope creep on a layer that was never in the plan.
    design = {
        "case_kind": "final_model_retraining",
        "workflow_capabilities": [],
        "verifier_deferrals": [{"layer": "MLP-V5", "reason": "not in scope"}],
    }
    assert "target inapplicable layers" in _fail_message(design)


def test_unknown_kind_and_condition_layers_stay_strict() -> None:
    assert "case_kind" in _fail_message({"case_kind": "vibes", "workflow_capabilities": []})
    plan = _plan({
        "case_kind": "final_model_retraining",
        "workflow_capabilities": ["iterative_improvement", "hidden_dynamic_stability"],
    })
    ids = [layer["id"] for layer in plan["layers"]]
    assert "MLP-V3" in ids and "MLP-V5" in ids  # condition met -> applicable
    assert "MLP-V6" not in ids  # condition not met, not mandatory -> out of scope


# ------------------------------------------------------ (1) C-V8 behavior


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_v8_reports_every_declared_artifact_problem(tmp_path: Path) -> None:
    root = tmp_path / "sealed"
    _write(root / "manifest.json", "{}")
    _write(root / "artifacts/model/student.pb", "good-model")
    _write(root / "artifacts/dataset/train.raw", "actual-bytes")
    sub = verifier.Submission(root)
    sub.manifest = {
        "artifacts": [
            {"path": "artifacts/model/student.pb", "sha256": "0" * 64},
            {"path": "artifacts/dataset/absent.raw", "sha256": "1" * 64},
            {"path": "artifacts/dataset/train.raw", "sha256": verifier._sha256(
                root / "artifacts/dataset/train.raw")},
        ]
    }
    ok, findings = verifier.check_v8_integrity(sub)
    assert not ok
    assert any("integrity mismatch for artifacts/model/student.pb" in f for f in findings)
    assert any("declared artifact missing from sealed root: artifacts/dataset/absent.raw"
               in f for f in findings)
    # the good artifact produced no finding, and neither failure suppressed
    # the other (no `and not errors` short-circuit)
    assert len([f for f in findings if "C-V8" in f]) == 2


def test_manifest_type_validation_precedes_any_conversion(tmp_path: Path) -> None:
    assert verifier.manifest_type_errors({
        "schema_version": 1,
        "case_id": "x",
        "artifacts": [{"path": "a", "sha256": "0" * 64, "frames": 10}],
        "lineage": [],
        "runtime_receipts": [],
        "provenance_sources": [],
    }) == []
    errors = verifier.manifest_type_errors({
        "schema_version": 1,
        "artifacts": [{"path": "a", "sha256": 123}],
        "lineage": [{"round": "one", "dataset": "d", "model": "m", "action": "train"}],
    })
    assert any("sha256" in e for e in errors)
    assert any("round" in e for e in errors)
    assert any("must be a list" in e for e in errors)  # missing lists are type errors


def test_type_garbage_submissions_classify_agent_side(tmp_path: Path) -> None:
    cases = {
        "int-sha": {"schema_version": 1, "artifacts": [{"path": "a", "sha256": 123}]},
        "string-round": {"schema_version": 1, "lineage": [
            {"round": "one", "dataset": "d", "model": "m", "action": "train"}]},
        "artifacts-not-list": {"schema_version": 1, "artifacts": {"path": "a"}},
    }
    for name, manifest in cases.items():
        root = tmp_path / name
        _write(root / "manifest.json", json.dumps(manifest))
        _write(root / "a", "payload")
        result = verifier.verify(root, f"type-{name}")
        assert result["result_class"] == "AGENT_FAILURE", name
        assert result["failure_code"] == "INVALID_SUBMISSION", name
        assert "manifest type validation failed" in result["reason"], name
        assert result["retryable"] is False, name


def test_v3_probe_fixtures_exist_in_template_and_ship_in_scaffold() -> None:
    negative = TEMPLATE / "tests" / "fixtures" / "negative"
    shipped = {p.name for p in negative.iterdir() if p.is_dir()}
    assert set(runnable.REQUIRED_NEGATIVE_FIXTURES) <= shipped
    for probe_name, _, _, _, _ in runnable.V3_INTEGRITY_PROBES:
        assert (TEMPLATE / "tests" / "fixtures" / probe_name).is_dir(), probe_name
    # the mvp-passing fixture mirrors the template probe set
    fixture_negative = FIXTURE_CASE / "tests" / "fixtures" / "negative"
    assert shipped == {p.name for p in fixture_negative.iterdir() if p.is_dir()}
    # template verifier and fixture verifier are the same hardened file
    assert (FIXTURE_CASE / "tests" / "verifier.py").read_bytes() == \
        (TEMPLATE / "tests" / "verifier.py").read_bytes()


def test_template_verifier_contract_selftests_pass() -> None:
    # Executes all thirteen construction-time self-tests (including the four
    # v3 probes with their exact attributions) through the real test.sh mount.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(TEMPLATE / "tests" / "test_verifier_contract.py"),
         "-q", "--no-header", "-p", "no:cacheprovider"],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-1000:]


# ------------------------------------------------------ (3) leave-one-out (H)


def _sources_lock(root: Path, sources: dict) -> Path:
    case = root / "case"
    lock_dir = case / "source"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "sources.lock.json").write_text(
        json.dumps({"schema_version": 1, "sources": sources}), encoding="utf-8")
    return case


def _design(allow: list[str], exclude: list[str] | None = None) -> dict:
    return {"source_context": {"allow": allow, "exclude": exclude or []}}


def test_hash_sources_exclude_records_and_omits_answer_files(tmp_path: Path) -> None:
    src = tmp_path / "inputs"
    _write(src / "paper-notes.md", "public notes")
    _write(src / "acceptance.json", '{"threshold": 0.1}')
    out = tmp_path / "lock.json"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_MLP / "hash_sources.py"),
         "--dir", f"material={src}", "--exclude", "acceptance.json", "--output", str(out)],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    entry = json.loads(out.read_text(encoding="utf-8"))["sources"]["material"]
    assert entry["excluded"] == ["acceptance.json"]
    # without the exclusion the digest changes: the answer was consumed whole
    out2 = tmp_path / "lock2.json"
    subprocess.run(
        [sys.executable, str(SCRIPTS_MLP / "hash_sources.py"),
         "--dir", f"material={src}", "--output", str(out2)], check=True)
    assert json.loads(out2.read_text(encoding="utf-8"))["sources"]["material"][
        "tree_sha256"] != entry["tree_sha256"]


def test_invariant_h_blocks_acceptance_json_from_builder_context(tmp_path: Path) -> None:
    src = tmp_path / "inputs"
    _write(src / "paper-notes.md", "public notes")
    _write(src / "acceptance.json", '{"threshold": 0.1}')

    def hash_lock(*exclude: str) -> dict:
        out = tmp_path / f"lock-{'-'.join(exclude) or 'none'}.json"
        cmd = [sys.executable, str(SCRIPTS_MLP / "hash_sources.py"),
               "--dir", f"material={src}", "--output", str(out)]
        for pattern in exclude:
            cmd += ["--exclude", pattern]
        subprocess.run(cmd, check=True)
        return json.loads(out.read_text(encoding="utf-8"))["sources"]

    # un-recorded answer file inside a locked directory: blocked
    case = _sources_lock(tmp_path, hash_lock())
    errors = consistency._check_source_context(case, _design([str(src)]))
    assert any("acceptance.json" in e and "excluded file inside locked source" in e
               for e in errors), errors

    # audited omission via hash_sources --exclude: allowed
    case = _sources_lock(tmp_path, hash_lock("acceptance.json"))
    assert consistency._check_source_context(case, _design([str(src)])) == []

    # a single file locked whole is consumed whole: never allowed
    case = _sources_lock(tmp_path, {
        "answer": {"kind": "file", "path": str(src / "acceptance.json"), "sha256": "0" * 64},
    })
    errors = consistency._check_source_context(case, _design([str(src)]))
    assert any("do not lock held-out answers as sources" in e for e in errors), errors

    # outside the allow roots: blocked regardless of name
    sneaky = tmp_path / "sneaky"
    _write(sneaky / "train-notes.md", "x")
    case = _sources_lock(tmp_path, {"other": {"kind": "directory", "path": str(sneaky)}})
    errors = consistency._check_source_context(case, _design([str(src)]))
    assert any("outside every" in e and "source_context.allow" in e for e in errors), errors


def test_invariant_h_is_conditional_and_fails_loudly_on_missing_lock(tmp_path: Path) -> None:
    # designs that predate source_context are untouched
    case = tmp_path / "old-case"
    case.mkdir()
    assert consistency._check_source_context(case, {}) == []
    # declaring the contract without the lock is a specific error, not silence
    case = tmp_path / "new-case"
    (case / "source").mkdir(parents=True)
    errors = consistency._check_source_context(case, _design([str(tmp_path)]))
    assert any("sources.lock.json missing" in e for e in errors), errors


# ------------------------------------------------------ (3) dead stubs gone


@pytest.mark.parametrize("tree", [TEMPLATE, FIXTURE_CASE], ids=["template", "mvp-passing"])
def test_dead_stubs_are_gone_and_survivors_match_producers(tree: Path) -> None:
    assert not (tree / "tests" / "fixture-matrix.yaml").exists()
    assert not (tree / "reference" / "inputs.lock.json").exists()
    assert not (tree / "source" / "source.lock.json").exists()
    assert (tree / "source" / "sources.lock.json").is_file()
    manifest = json.loads((tree / "evaluator-manifest.json").read_text(encoding="utf-8"))
    # matches the freeze_evaluator_manifest.py producer / check_release.py consumer
    assert set(manifest) == {"schema_version", "frozen", "objects"}


def test_scaffold_ships_probe_fixtures_and_no_dead_stubs(tmp_path: Path) -> None:
    design = PACKAGE / "tests" / "fixtures" / "simple-mlp-local" / "case-design.yaml"
    out = tmp_path / "case"
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_COMMON / "init_case.py"),
         "--category", "mlp", "--kind", "final_model_retraining",
         "--design", str(design), "--output", str(out)],
        text=True, capture_output=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    negative = out / "tests" / "fixtures" / "negative"
    assert {p.name for p in negative.iterdir() if p.is_dir()} == \
        set(runnable.REQUIRED_NEGATIVE_FIXTURES)
    assert (out / "source" / "sources.lock.json").is_file()
    assert not (out / "tests" / "fixture-matrix.yaml").exists()
    assert not (out / "reference" / "inputs.lock.json").exists()
    # the mlp plan overlay ships at schema 2 with explicit deferral accounting
    plan = yaml.safe_load((out / "verifier-plan.yaml").read_text(encoding="utf-8"))
    assert plan["schema_version"] == 2
    assert "deferred_layers" in plan and "case_kind" in plan


def test_bundle_agreement_skips_candidate_generated_entries(tmp_path: Path) -> None:
    """Test D1 fix: public/input-manifest.json entries marked candidate_generated: true

    must not fail bundle_agreement even though the candidate packaging rule never stages them.
    Covers both list format and path-map format.
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    public_dir = case_dir / "public"
    public_dir.mkdir()

    # List format: 1 staged input, 1 candidate_generated output, 1 missing input without flag
    manifest_list = {
        "schema_version": 1,
        "files": [
            {"candidate_path": "public/input.txt", "candidate_generated": False},
            {"candidate_path": "public/output-submission.json", "candidate_generated": True},
        ],
    }
    (public_dir / "input-manifest.json").write_text(json.dumps(manifest_list), encoding="utf-8")

    bundle = {
        "paths": ["public/input.txt", "submission-schema.json"],
    }
    design = {"contract": {"candidate_visible": False}}

    verdict = runnable.Verdict()
    runnable.check_bundle_agreement(case_dir, bundle, design, verdict)
    assert verdict.checks["bundle_agreement"]["status"] == "pass", verdict.checks["bundle_agreement"]["errors"]

    # Negative control: remove candidate_generated flag -> must fail
    manifest_list_fail = {
        "schema_version": 1,
        "files": [
            {"candidate_path": "public/input.txt"},
            {"candidate_path": "public/output-submission.json"},
        ],
    }
    (public_dir / "input-manifest.json").write_text(json.dumps(manifest_list_fail), encoding="utf-8")
    verdict_fail = runnable.Verdict()
    runnable.check_bundle_agreement(case_dir, bundle, design, verdict_fail)
    assert verdict_fail.checks["bundle_agreement"]["status"] == "fail"
    errs = verdict_fail.checks["bundle_agreement"]["errors"]
    assert any("public/output-submission.json" in e for e in errs)

    # Path-map format: 1 staged, 1 candidate_generated
    manifest_map = {
        "schema_version": 1,
        "files": {
            "public/input.txt": {"role": "input"},
            "public/output-submission.json": {"candidate_generated": True},
        },
    }
    (public_dir / "input-manifest.json").write_text(json.dumps(manifest_map), encoding="utf-8")
    verdict_map = runnable.Verdict()
    runnable.check_bundle_agreement(case_dir, bundle, design, verdict_map)
    assert verdict_map.checks["bundle_agreement"]["status"] == "pass", verdict_map.checks["bundle_agreement"]["errors"]

