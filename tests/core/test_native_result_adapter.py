from __future__ import annotations

import json
import sys
from pathlib import Path

from bench.verifiers.native_result_adapter import main


def _native_script(path: Path, payload: object, rc: int) -> Path:
    script = path / "native.py"
    script.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path('result.json').write_text(json.dumps({payload!r}))\n"
        f"raise SystemExit({rc})\n",
        encoding="utf-8",
    )
    return script


def _result(output: Path) -> dict:
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


def test_native_pass_is_normalized_and_native_diagnostic_retained(tmp_path: Path) -> None:
    output = tmp_path / "logs"
    native = _native_script(tmp_path, {"passed": True, "score": 1.0, "gates": {"V0": True}}, 0)
    assert main(["--output", str(output), "--", sys.executable, str(native)]) == 0
    assert _result(output)["failure_code"] == "PASS"
    assert _result(output)["result_class"] == "VALID_RESULT"
    assert json.loads((output / "native-result.json").read_text())[
        "gates"
    ]["V0"] is True


def test_native_scientific_failure_counts_but_crash_is_infra(tmp_path: Path) -> None:
    output = tmp_path / "logs-fail"
    native = _native_script(tmp_path, {"passed": False, "score": 0.0}, 1)
    assert main(["--output", str(output), "--", sys.executable, str(native)]) == 1
    result = _result(output)
    assert result["result_class"] == "VALID_RESULT"
    assert result["failure_code"] == "SCIENTIFIC_FAIL"
    crashed = tmp_path / "crashed.py"
    crashed.write_text("raise SystemExit(2)\n", encoding="utf-8")
    output2 = tmp_path / "logs-crash"
    assert main(["--output", str(output2), "--", sys.executable, str(crashed)]) == 1
    result2 = _result(output2)
    assert result2["result_class"] == "INFRA_INVALID"
    assert result2["failure_code"] == "VERIFIER_FAILURE"


def test_native_exit_and_verdict_mismatch_is_infra(tmp_path: Path) -> None:
    for label, payload, rc in (
        ("zero-fail", {"passed": False}, 0),
        ("one-pass", {"passed": True}, 1),
    ):
        output = tmp_path / label
        native = _native_script(tmp_path, payload, rc)
        assert main(["--output", str(output), "--", sys.executable, str(native)]) == 1
        result = _result(output)
        assert result["result_class"] == "INFRA_INVALID"
        assert result["failure_code"] == "VERIFIER_FAILURE"


def test_native_readiness_status_never_counts_as_scientific_failure(tmp_path: Path) -> None:
    for status in ("NOT_READY", "CASE_NOT_READY", "UNBUILT", "UNQUALIFIED"):
        output = tmp_path / status
        native = _native_script(tmp_path, {"status": status, "passed": False}, 1)
        assert main(["--output", str(output), "--", sys.executable, str(native)]) == 1
        result = _result(output)
        assert result["result_class"] == "INFRA_INVALID"
        assert result["failure_code"] == "VERIFIER_FAILURE"


def test_precheck_and_unknown_statuses_never_count_for_any_exit(tmp_path: Path) -> None:
    payloads = [
        {"status": "PRECHECK_PASS", "passed": True},
        {"status": "INPUT_PREFLIGHT_PASS", "passed": True},
        {"status": "REQUIRES_SCIENTIFIC_REVIEW", "passed": True},
        {"status": "DRAFT_NOT_READY", "passed": False},
        {"status": "UNKNOWN", "passed": True},
        {"status": "CASE_NOT_READY", "passed": False, "scientific_ready": False},
        {"status": "FAIL", "passed": False, "scientific_ready": False},
        {"status": "PASS", "passed": True, "scientific_replay_implemented": False},
    ]
    for i, payload in enumerate(payloads):
        for rc in (0, 1, 20):
            output = tmp_path / f"case-{i}-{rc}"
            native = _native_script(tmp_path, payload, rc)
            assert main(["--output", str(output), "--", sys.executable, str(native)]) == 1
            result = _result(output)
            assert result["result_class"] == "INFRA_INVALID"
            assert result["is_counted_scientifically"] is False


def test_only_explicit_scientific_statuses_count(tmp_path: Path) -> None:
    for i, (status, rc, expected, code) in enumerate(
        [("PASS", 0, "VALID_RESULT", "PASS"), ("FAIL", 1, "VALID_RESULT", "SCIENTIFIC_FAIL"),
         ("SCIENTIFIC_FAIL", 20, "VALID_RESULT", "SCIENTIFIC_FAIL"),
         ("FAIL_SUBMISSION", 1, "VALID_RESULT", "SCIENTIFIC_FAIL"),
         ("SUBMISSION_FAILURE", 20, "VALID_RESULT", "SCIENTIFIC_FAIL")]
    ):
        output = tmp_path / f"explicit-{i}"
        native = _native_script(tmp_path, {"status": status}, rc)
        assert main(["--output", str(output), "--", sys.executable, str(native)]) == (0 if status == "PASS" else 1)
        result = _result(output)
        assert result["result_class"] == expected and result["failure_code"] == code
