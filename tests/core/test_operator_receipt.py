from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from bench.core.operator_receipt import (
    OperatorReceiptError,
    make_operator_receipt,
    verify_operator_receipt,
)
from bench.core.digests import sha256_file


def _keys() -> tuple[str, str]:
    private = ed25519.Ed25519PrivateKey.generate()
    return private.private_bytes_raw().hex(), private.public_key().public_bytes_raw().hex()


def _receipt(tmp_path: Path, *, run_id: str = "run-1") -> tuple[Path, str, str]:
    key, public = _keys()
    data = b"operator output\n"
    receipt = make_operator_receipt(
        run_id=run_id, attempt=1, compute_backend="ikkem",
        request_digests={"compute-requests/cpu.json": "sha256:" + "a" * 64},
        outputs=[{"path": "compute-results/result.dat", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}],
        signing_key_hex=key,
    )
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return path, public, key


def test_operator_receipt_binds_run_and_output_set(tmp_path: Path) -> None:
    path, public, _ = _receipt(tmp_path)
    digest, payload = verify_operator_receipt(
        path, trusted_public_key_hex=public, trusted_key_id="operator-v1",
        expected={"run_id": "run-1", "attempt": 1, "compute_backend": "ikkem",
                  "request_digests": {"compute-requests/cpu.json": "sha256:" + "a" * 64}},
        expected_outputs=["compute-results/result.dat"],
    )
    assert digest.startswith("sha256:")
    assert payload["run_id"] == "run-1"


def test_operator_receipt_rejects_tamper_and_cross_run_replay(tmp_path: Path) -> None:
    path, public, _ = _receipt(tmp_path)
    payload = json.loads(path.read_text())
    payload["run_id"] = "run-2"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OperatorReceiptError, match="digest|signature|binding"):
        verify_operator_receipt(
            path, trusted_public_key_hex=public, trusted_key_id="operator-v1",
            expected={"run_id": "run-1", "attempt": 1, "compute_backend": "ikkem",
                      "request_digests": {"compute-requests/cpu.json": "sha256:" + "a" * 64}},
            expected_outputs=["compute-results/result.dat"],
        )


def test_operator_receipt_rejects_path_traversal(tmp_path: Path) -> None:
    key, _ = _keys()
    with pytest.raises(OperatorReceiptError, match="relative"):
        make_operator_receipt(
            run_id="r", attempt=1, compute_backend="compshare", request_digests={},
            outputs=[{"path": "../escape", "sha256": "0" * 64, "size": 1}],
            signing_key_hex=key,
        )


def test_pilot_external_import_requires_and_consumes_signed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bench.pilot import MvpError, import_results

    run = tmp_path / "run-1"
    workspace = run / "candidate"
    request = workspace / "compute-requests" / "cpu.json"
    request.parent.mkdir(parents=True)
    request.write_text(json.dumps({"outputs": ["compute-results/result.dat"]}), encoding="utf-8")
    source = tmp_path / "operator"
    (source / "compute-results").mkdir(parents=True)
    output = source / "compute-results" / "result.dat"
    output.write_bytes(b"operator output\n")
    state = {
        "run_id": "run-1", "state": "COMPUTE_REQUIRED", "workspace": str(workspace),
        "trust_mode": "EXTERNAL_SUBMISSION", "compute_backend": "ikkem",
        "compute_requests": [str(request)],
        "compute_request_digests": {"compute-requests/cpu.json": sha256_file(request)},
    }
    (run / "run-state.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(MvpError, match="--receipt"):
        import_results(run, source)

    key, public = _keys()
    receipt = make_operator_receipt(
        run_id="run-1", attempt=1, compute_backend="ikkem",
        request_digests=state["compute_request_digests"],
        outputs=[{"path": "compute-results/result.dat", "sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "size": output.stat().st_size}],
        signing_key_hex=key,
    )
    receipt_path = tmp_path / "operator-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setenv("BENCH_OPERATOR_RECEIPT_TRUSTED_PUBLIC_KEY", public)
    imported = import_results(run, source, receipt=receipt_path)
    assert imported["operator_receipt_digest"] == receipt["receipt_digest"]
    assert (workspace / "compute-results/result.dat").read_bytes() == output.read_bytes()
    tampered = workspace / "compute-results/result.dat"
    tampered.chmod(0o644)
    tampered.write_bytes(b"tampered\n")
    from bench.pilot import _check_operator_outputs
    with pytest.raises(MvpError, match="changed"):
        _check_operator_outputs(
            workspace, [str(request)],
            expected_manifest=imported["operator_receipt_outputs"],
        )


def test_pilot_import_failure_does_not_leave_partial_results_or_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bench.pilot import MvpError, import_results

    run = tmp_path / "run-transaction"
    workspace = run / "candidate"
    request = workspace / "compute-requests" / "cpu.json"
    request.parent.mkdir(parents=True)
    request.write_text(json.dumps({"outputs": ["compute-results/a.dat", "compute-results/b.dat"]}), encoding="utf-8")
    source = tmp_path / "operator-transaction"
    (source / "compute-results").mkdir(parents=True)
    first = source / "compute-results/a.dat"
    first.write_bytes(b"a\n")
    state = {
        "run_id": "run-transaction", "state": "COMPUTE_REQUIRED", "workspace": str(workspace),
        "trust_mode": "EXTERNAL_SUBMISSION", "compute_backend": "compshare",
        "compute_requests": [str(request)], "compute_request_digests": {"compute-requests/cpu.json": sha256_file(request)},
    }
    (run / "run-state.json").write_text(json.dumps(state), encoding="utf-8")
    key, public = _keys()
    receipt = make_operator_receipt(
        run_id="run-transaction", attempt=1, compute_backend="compshare",
        request_digests=state["compute_request_digests"],
        outputs=[
            {"path": "compute-results/a.dat", "sha256": hashlib.sha256(b"a\n").hexdigest(), "size": 2},
            {"path": "compute-results/b.dat", "sha256": "0" * 64, "size": 2},
        ], signing_key_hex=key,
    )
    receipt_path = tmp_path / "transaction-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setenv("BENCH_OPERATOR_RECEIPT_TRUSTED_PUBLIC_KEY", public)
    with pytest.raises(MvpError, match="missing|unsafe"):
        import_results(run, source, receipt=receipt_path)
    assert not (workspace / "compute-results/a.dat").exists()
    assert not (run / ".control/operator-receipt.json").exists()


def test_formal_marker_cannot_be_downgraded_for_import(tmp_path: Path) -> None:
    from bench.pilot import MvpError, import_results

    run = tmp_path / "formal-run"
    workspace = run / "candidate"
    request = workspace / "compute-requests" / "cpu.json"
    request.parent.mkdir(parents=True)
    request.write_text(json.dumps({"outputs": ["compute-results/result.dat"]}), encoding="utf-8")
    (run / "run-state.json").write_text(json.dumps({
        "run_id": "formal-run", "state": "COMPUTE_REQUIRED", "formal": True,
        "trust_mode": "LOCAL_DEV", "workspace": str(workspace),
        "compute_requests": [str(request)], "compute_request_digests": {"compute-requests/cpu.json": sha256_file(request)},
    }), encoding="utf-8")
    source = tmp_path / "empty-source"
    source.mkdir()
    with pytest.raises(MvpError, match="receipt"):
        import_results(run, source)
