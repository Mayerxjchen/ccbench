#!/usr/bin/env python3
"""Create signed, host-local verifier runtime locks from real Docker images.

This deliberately accepts image names as arguments rather than baking image
digests into the repository.  Run it after building the three case verifier
images; the resulting directory can be selected with
``BENCH_VERIFIER_QUALIFICATION_DIR`` for a local Formal run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519

ROOT = Path(__file__).resolve().parents[2]


def _digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def qualify(profile: str, image: str, output: Path, key_hex: str, key_id: str) -> None:
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0 or not result.stdout.strip().startswith("sha256:"):
        raise RuntimeError(f"docker image inspect failed for {image}: {result.stderr.strip()}")
    image_digest = result.stdout.strip().splitlines()[0]
    # Execute the same public launcher used by production verifiers, rather
    # than merely importing pytest.  Only the launcher source and a tiny
    # public smoke test are mounted; no private case or repository material is
    # exposed to the image during qualification.
    with tempfile.TemporaryDirectory(prefix="bench-verifier-qualification-") as raw_tmp:
        tmp = Path(raw_tmp)
        package = tmp / "bench" / "verifiers"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        shutil.copyfile(ROOT / "bench" / "verifiers" / "launcher.py", package / "launcher.py")
        tests = tmp / "tests"
        tests.mkdir()
        (tests / "test_qualification_smoke.py").write_text(
            "import os\n\n"
            "def test_runtime_is_nonroot():\n"
            "    assert os.geteuid() == 65532\n",
            encoding="utf-8",
        )
        logs = tmp / "logs"
        logs.mkdir(mode=0o755)
        probe = subprocess.run(
            ["docker", "run", "--rm", "--user", "65532:65532",
             "--entrypoint", "python3", "--network", "none", "--read-only",
             "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
             "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
             "--env", "PYTHONPATH=/opt/qualifier",
             "--volume", f"{tmp / 'bench'}:/opt/qualifier/bench:ro",
             "--volume", f"{tests}:/opt/qualifier/tests:ro",
             "--volume", f"{logs}:/logs/verifier",
             image, "-m", "bench.verifiers.launcher",
             "--run-id", f"qualification-{profile}",
             "--test-path", "/opt/qualifier/tests/test_qualification_smoke.py",
             "--work-dir", "/opt/qualifier/tests", "--log-dir", "/logs/verifier",
             "--pytest-args", '["-p", "no:cacheprovider"]'],
            capture_output=True, text=True, timeout=120, check=False,
        )
        if probe.returncode not in (0, 1):
            raise RuntimeError(f"verifier launcher probe failed for {image}: {probe.stderr.strip()}")
        result_path = logs / "result.json"
        if not result_path.is_file():
            raise RuntimeError(f"verifier launcher produced no result.json for {image}: {probe.stderr.strip()}")
        try:
            result_payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"verifier launcher result is invalid for {image}: {exc}") from exc
        if result_payload.get("result_class") != "VALID_RESULT" or result_payload.get("failure_code") != "PASS":
            raise RuntimeError(f"verifier launcher smoke failed for {image}: {result_payload}")
    key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key_hex))
    body = {
        "schema_version": "verifier-runtime-qualification/v1",
        "profile": profile, "image_name": image, "image_digest": image_digest,
        "scope": "local-host", "qualified_at": datetime.now(timezone.utc).isoformat(),
        "probe": "docker run --rm --user 65532:65532 --network none image python3 -m bench.verifiers.launcher (public smoke)",
        "key_id": key_id,
    }
    receipt = dict(body)
    receipt["signature"] = {"algorithm": "ed25519", "key_id": key_id,
                             "signature_hex": key.sign(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hex()}
    receipt["receipt_digest"] = _digest(receipt)
    receipt_path = output / f"{profile}.receipt.json"
    _write(receipt_path, receipt)
    _write(output / f"{profile}.lock.json", {
        "schema_version": "verifier-runtime-lock/v1", "profile": profile,
        "role": "verifier", "status": "QUALIFIED", "image_name": image,
        "image_digest": image_digest, "receipt_digest": receipt["receipt_digest"],
        "receipt_path": str(receipt_path), "scope": "local-host",
    })
    print(f"{profile}: {image_digest}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--signing-key-hex", default=os.environ.get("BENCH_IMAGE_QUALIFICATION_SIGNING_KEY", ""))
    parser.add_argument("--key-id", default=os.environ.get("BENCH_IMAGE_QUALIFICATION_KEY_ID", "image-qualification-v1"))
    parser.add_argument("--profile-image", action="append", required=True,
                        metavar="PROFILE=IMAGE", help="repeat once for each case verifier profile")
    args = parser.parse_args(argv)
    if not args.signing_key_hex:
        parser.error("provide --signing-key-hex or BENCH_IMAGE_QUALIFICATION_SIGNING_KEY")
    try:
        for value in args.profile_image:
            if "=" not in value or not all(value.split("=", 1)):
                parser.error("--profile-image must be PROFILE=IMAGE")
            profile, image = value.split("=", 1)
            qualify(profile, image, args.output_dir.expanduser().resolve(), args.signing_key_hex, args.key_id)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
