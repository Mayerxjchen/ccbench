from __future__ import annotations

import json
import os
from pathlib import Path

import bench
import pytest
from bench.cli import main as public_main
from bench.runtime_catalog import inspect_runtime, load_catalog
from bench.cli import main as legacy_main
from bench.contracts.case import CaseContractError, CaseSpec


def _case(root: Path, name: str, case_id: str) -> None:
    case = root / name
    case.mkdir()
    (case / "task.md").write_text("# task\n", encoding="utf-8")
    (case / "case.toml").write_text(
        'schema_version = "1.2"\n'
        '[execution]\nclass = "local_sandbox"\n'
        '[candidate]\ninstruction = "task.md"\nsubmission_root = "final"\n'
        'runner = "container_claude_code"\n'
        f'[task]\nname = "{case_id}"\n'
        '[agent]\nprofile = "claude-mvp"\n'
        '[verifier]\nprofile = "matclaw-cips-v1"\n'
        '[compute]\nclasses = ["cpu"]\n'
        '[coverage]\nscientific_domain = "paper"\n'
    , encoding="utf-8")


def test_public_package_exports_suite_api() -> None:
    assert callable(bench.inspect_suite)
    assert callable(bench.validate_suite)


def test_external_suite_discovery_ignores_cache_and_reports_readiness(tmp_path: Path) -> None:
    _case(tmp_path, "alpha", "paper/alpha")
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    _case(tmp_path, "001-paper", "paper/001-paper")
    (tmp_path / "002-missing-manifest").mkdir()
    (tmp_path / "DS_Store").mkdir()
    report = bench.inspect_suite(tmp_path)
    assert report["case_count"] == 3
    assert [row["directory"] for row in report["cases"]] == [
        "001-paper", "002-missing-manifest", "alpha"
    ]
    assert report["cases"][1]["readiness"] == "CASE_NOT_READY"
    assert report["ready"] is False


def test_directory_identity_mismatch_is_diagnostic_not_inferred(tmp_path: Path) -> None:
    _case(tmp_path, "001-paper", "paper/005-other")
    row = bench.inspect_suite(tmp_path)["cases"][0]
    assert row["case_id"] == "paper/005-other"
    assert any(d["code"] == "CASE_ID_DIRECTORY_MISMATCH" for d in row["diagnostics"])


def test_valid_unknown_coverage_slug_is_reported_as_extension(tmp_path: Path) -> None:
    _case(tmp_path, "001-paper", "paper/001-paper")
    path = tmp_path / "001-paper" / "case.toml"
    path.write_text(path.read_text(encoding="utf-8").replace(
        '[coverage]\nscientific_domain = "paper"\n',
        '[coverage]\nscientific_domain = "paper"\nmethod_family = "new_paper_method"\n',
    ), encoding="utf-8")
    row = bench.inspect_suite(tmp_path)["cases"][0]
    assert row["readiness"] == "READY"
    assert any(d["code"] == "COVERAGE_EXTENSION" for d in row["diagnostics"])


def test_external_runtime_not_ready_is_exposed_and_blocks_readiness(tmp_path: Path) -> None:
    _case(tmp_path, "001-runtime", "paper/001-runtime")
    path = tmp_path / "001-runtime" / "case.toml"
    path.write_text(
        path.read_text(encoding="utf-8") + '\n[runtime]\nstatus = "UNBUILT"\n',
        encoding="utf-8",
    )
    row = bench.inspect_suite(tmp_path)["cases"][0]
    assert row["runtime_status"] == "UNBUILT"
    assert row["readiness"] == "CASE_NEEDS_REVIEW"
    assert any(item["code"] == "RUNTIME_NOT_READY" for item in row["diagnostics"])


def test_legacy_and_public_cli_have_normalized_json(tmp_path: Path, capsys) -> None:
    _case(tmp_path, "001-paper", "paper/001-paper")
    assert public_main(["suite", "inspect", "--root", str(tmp_path)]) == 0
    public = json.loads(capsys.readouterr().out)
    assert legacy_main(["suite", "inspect", "--root", str(tmp_path)]) == 0
    legacy = json.loads(capsys.readouterr().out)
    assert public == legacy


def test_validate_returns_nonzero_for_missing_manifest(tmp_path: Path, capsys) -> None:
    (tmp_path / "005-paper").mkdir()
    assert public_main(["suite", "validate", "--root", str(tmp_path)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["validation"] == "FAIL"
    assert report["cases"][0]["readiness"] == "CASE_NOT_READY"


def test_absolute_external_case_path_can_be_exported_without_private_dirs(tmp_path: Path) -> None:
    case = tmp_path / "paper-case"
    _case(tmp_path, "paper-case", "paper/paper-case")
    (case / "input").mkdir()
    (case / "input" / "public.txt").write_text("public", encoding="utf-8")
    (case / "solution").mkdir()
    (case / "solution" / "answer.txt").write_text("private", encoding="utf-8")
    out = tmp_path / "bundle"
    from bench.mvp import export_case

    export_case(case, out)
    assert (out / "instruction.md").is_file()
    assert (out / "public.txt").is_file()
    assert not (out / "solution").exists()


def test_runtime_catalog_lists_capabilities_without_promoting_unqualified_images() -> None:
    catalog = load_catalog()
    ids = {entry["runtime_id"] for entry in catalog["runtimes"]}
    assert {"candidate-claude-code", "compute-ikkem-cpu", "compute-compshare-gpu"} <= ids
    candidate = inspect_runtime("candidate-claude-code")["runtime"]
    assert candidate["local_image"].startswith("bench-agent-claude-code:")
    assert candidate["qualification_status"] == "QUALIFIED_LOCAL"
    assert candidate["digest"].startswith("sha256:")


def test_candidate_catalog_pins_accepted_release_identity() -> None:
    """Pin the identity accepted during the 2026-09-11 release review.

    This deterministic repository check does not validate a signature or
    inspect a Docker daemon.  A future release must update this assertion
    together with the catalog; qualification behavior is covered separately
    by the Candidate runtime qualification tests.
    """
    candidate = inspect_runtime("candidate-claude-code")["runtime"]
    assert candidate["local_image"] == "bench-agent-claude-code:2.1.266"
    assert candidate["digest"] == (
        "sha256:37f7e5f390f882b053cb781c4140e5b299eb7f2f0cfd7e746ad2da4522d4fa4a"
    )


def test_runtime_cli_and_legacy_alias_are_normalized(capsys) -> None:
    assert public_main(["runtime", "inspect", "candidate-claude-code"]) == 0
    public = json.loads(capsys.readouterr().out)
    assert legacy_main(["runtime", "inspect", "candidate-claude-code"]) == 0
    legacy = json.loads(capsys.readouterr().out)
    assert public == legacy


def test_suite_reports_identity_digest_route_and_rejects_case_symlink(tmp_path: Path) -> None:
    (tmp_path / "suite.toml").write_text(
        'schema_version = "bench-suite/v1"\n'
        'suite_id = "paper-suite"\n'
        'paper_id = "paper-42"\n'
        'cases_root = "cases"\n', encoding="utf-8"
    )
    cases = tmp_path / "cases"
    cases.mkdir()
    _case(cases, "001-good", "paper/001-good")
    os.symlink(cases / "001-good", cases / "002-escape")
    report = bench.inspect_suite(tmp_path)
    assert report["suite_id"] == "paper-suite"
    assert report["paper_id"] == "paper-42"
    assert report["manifest_digest"].startswith("sha256:")
    good = next(row for row in report["cases"] if row["directory"] == "001-good")
    escaped = next(row for row in report["cases"] if row["directory"] == "002-escape")
    assert good["compute_route"] == ["cpu"]
    assert escaped["readiness"] == "CASE_NOT_READY"
    assert any(d["code"] == "SYMLINK_CASE_DIRECTORY" for d in escaped["diagnostics"])


def test_suite_rejects_manifest_instruction_and_public_input_symlinks(tmp_path: Path) -> None:
    _case(tmp_path, "001-unsafe", "paper/001-unsafe")
    case = tmp_path / "001-unsafe"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (case / "case.toml").unlink()
    os.symlink(outside, case / "case.toml")
    row = bench.inspect_suite(tmp_path)["cases"][0]
    assert any(item["code"] == "CASE_MANIFEST_SYMLINK" for item in row["diagnostics"])

    _case(tmp_path, "002-public", "paper/002-public")
    public_case = tmp_path / "002-public"
    (public_case / "input").mkdir()
    os.symlink(outside, public_case / "input" / "escape.txt")
    row = next(
        row for row in bench.inspect_suite(tmp_path)["cases"]
        if row["directory"] == "002-public"
    )
    assert any(item["code"] == "PUBLIC_SYMLINK" for item in row["diagnostics"])


def test_suite_manifest_hardlink_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "suite-source.toml"
    source.write_text('suite_id = "hardlinked"\n', encoding="utf-8")
    os.link(source, tmp_path / "suite.toml")
    with pytest.raises(ValueError, match="single-link regular"):
        bench.inspect_suite(tmp_path)


def test_case_manifest_hardlink_is_not_ready(tmp_path: Path) -> None:
    _case(tmp_path, "001-hardlinked", "paper/001-hardlinked")
    manifest = tmp_path / "001-hardlinked" / "case.toml"
    source = tmp_path / "case-source.toml"
    source.write_bytes(manifest.read_bytes())
    manifest.unlink()
    os.link(source, manifest)
    row = bench.inspect_suite(tmp_path)["cases"][0]
    assert row["readiness"] == "CASE_NOT_READY"
    assert row["diagnostics"][0]["code"] == "CASE_MANIFEST_UNSAFE"


def test_external_verifier_bundle_never_merges_repo_prefix_tests(tmp_path: Path) -> None:
    from bench.mvp import resolve_verifier_bundle

    case = tmp_path / "001-external"
    verifier = case / "verifier"
    verifier.mkdir(parents=True)
    (verifier / "test.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (verifier / "private.py").write_text("# case-local\n", encoding="utf-8")
    (case / "input").mkdir()
    (case / "input" / "public.json").write_text("{}\n", encoding="utf-8")
    staged = resolve_verifier_bundle(case, tmp_path / "run", strict_external=True)
    assert (staged / "verifier" / "private.py").is_file()
    assert (staged / "input" / "public.json").is_file()
    assert not (staged / "test_outputs.py").exists()


@pytest.mark.parametrize("missing", ["runner", "agent", "verifier", "compute"])
def test_external_strict_contract_rejects_missing_active_profile_fields(
    tmp_path: Path, missing: str,
) -> None:
    _case(tmp_path, "001-strict", "paper/001-strict")
    manifest = tmp_path / "001-strict" / "case.toml"
    text = manifest.read_text(encoding="utf-8")
    if missing == "runner":
        text = text.replace('runner = "container_claude_code"\n', "")
    elif missing == "agent":
        text = text.replace('[agent]\nprofile = "claude-mvp"\n', "")
    elif missing == "verifier":
        text = text.replace('[verifier]\nprofile = "matclaw-cips-v1"\n', "")
    else:
        text = text.replace('[compute]\nclasses = ["cpu"]\n', "")
    manifest.write_text(text, encoding="utf-8")
    with pytest.raises(CaseContractError):
        CaseSpec.load(tmp_path / "001-strict", strict_external=True)
    row = bench.inspect_suite(tmp_path)["cases"][0]
    assert row["readiness"] == "CASE_NOT_READY"
    assert row["diagnostics"][0]["code"] == "INVALID_CASE_TOML"
