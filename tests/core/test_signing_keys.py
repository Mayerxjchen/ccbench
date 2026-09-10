from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519


def _module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "infra" / "generate_signing_keys.py"
    spec = importlib.util.spec_from_file_location("bench_generate_signing_keys", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_keygen_writes_mode_0600_pair_without_printing_private(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    assert module.main(["--output-dir", str(tmp_path), "--role", "formal"]) == 0
    private = tmp_path / "formal.private.hex"
    public = tmp_path / "formal.public.hex"
    assert private.stat().st_mode & 0o777 == 0o600
    assert public.stat().st_mode & 0o777 == 0o600
    private_hex = private.read_text().strip()
    public_hex = public.read_text().strip()
    key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
    assert key.public_key().public_bytes_raw().hex() == public_hex
    assert private_hex not in capsys.readouterr().out


def test_keygen_fails_closed_on_existing_pair(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _module()
    module.main(["--output-dir", str(tmp_path), "--role", "formal"])
    with pytest.raises(SystemExit):
        module.main(["--output-dir", str(tmp_path), "--role", "formal"])
    assert "existing keypair" in capsys.readouterr().err
