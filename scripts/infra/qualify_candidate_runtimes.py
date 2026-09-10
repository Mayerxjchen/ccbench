#!/usr/bin/env python3
"""Qualify the reusable Candidate and gateway images on one Docker host.

The command never invents an image digest.  It inspects the local image ID,
executes the recipe's non-root startup probes, and writes a signed receipt plus
one lock per image.  Local locks are valid only on the host that produced
them; publication still requires a registry ``RepoDigest`` and coordinator
trust policy.

Example::

  .venv/bin/python scripts/infra/qualify_candidate_runtimes.py \
    --candidate-image bench-agent-claude-code:2.1.266 \
    --sidecar-image bench-gateway-anthropic:1 \
    --output-dir ~/.config/bench/qualifications \
    --signing-key-hex "$BENCH_IMAGE_QUALIFICATION_SIGNING_KEY"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_SMOKE = ROOT / "runtimes/recipes/claude-code-candidate-base/smoke.sh"


def _digest(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _run(argv: list[str], *, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _inspect(image: str) -> tuple[str, list[str]]:
    result = _run(["docker", "image", "inspect", image,
                   "--format", "{{.Id}}\n{{range .RepoDigests}}{{.}}\n{{end}}"])
    if result.returncode != 0:
        raise RuntimeError(f"docker image inspect failed for {image}: {result.stderr.strip()}")
    values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not values or not values[0].startswith("sha256:"):
        raise RuntimeError(f"docker inspect returned no local image ID for {image}")
    return values[0], values[1:]


def _probe(role: str, image: str) -> None:
    if role == "candidate":
        if not CANDIDATE_SMOKE.is_file():
            raise RuntimeError(f"candidate smoke script is missing: {CANDIDATE_SMOKE}")
        result = _run([
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "10001:10001", "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
            "--volume", f"{CANDIDATE_SMOKE}:/opt/bench-candidate-smoke.sh:ro",
            "--entrypoint", "bash", image, "/opt/bench-candidate-smoke.sh",
        ], timeout=120)
        if result.returncode != 0:
            detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
            raise RuntimeError(f"{role} hardened smoke failed for {image}: {detail}")
    else:
        command = ["docker", "run", "--rm", "--entrypoint", "python3",
                   "--user", "10002:10002", image, "-c",
                   "import os, asyncio\nassert os.getuid() == 10002\n"
                   "async def f():\n"
                   "    s = await asyncio.start_server(lambda r,w: w.close(), '127.0.0.1', 0)\n"
                   "    s.close()\n    await s.wait_closed()\n"
                   "asyncio.run(f())"]
        result = _run(command, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"{role} startup probe failed for {image}: {result.stderr.strip()}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def qualify(role: str, image: str, output_dir: Path, signing_key_hex: str, key_id: str,
            registry: bool = False) -> Path:
    image_id, repo_digests = _inspect(image)
    _probe(role, image)
    if registry:
        if not repo_digests:
            raise RuntimeError(f"no registry RepoDigest is available for {image}; use local qualification")
        image_ref = sorted(repo_digests)[0]
        image_digest = image_ref.rsplit("@", 1)[-1]
        scope = "registry"
    else:
        image_ref = image_id
        image_digest = image_id
        scope = "local-host"
    try:
        key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signing_key_hex))
    except Exception as exc:
        raise RuntimeError("--signing-key-hex must contain a 32-byte Ed25519 private key") from exc
    now = datetime.now(timezone.utc).isoformat()
    body: dict[str, Any] = {
        "schema_version": "candidate-runtime-qualification/v1",
        "role": role,
        "image_name": image,
        "image_digest": image_digest,
        "image_ref": image_ref,
        "repo_digests": sorted(repo_digests),
        "scope": scope,
        "qualified_at": now,
        "probe": (
            "hardened candidate smoke: uid10001, network-none, read-only, "
            "cap-drop-all, no-new-privileges, ASE write, Packmol generation"
            if role == "candidate"
            else "sidecar startup contract: uid10002, loopback bind"
        ),
        "key_id": key_id,
    }
    receipt = dict(body)
    receipt["signature"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "signature_hex": key.sign(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hex(),
    }
    receipt["receipt_digest"] = _digest(receipt)
    receipt_path = output_dir / f"{role}-receipt.json"
    lock_path = output_dir / f"{role}.lock.json"
    _write_json(receipt_path, receipt)
    lock = {
        "schema_version": "candidate-runtime-lock/v1",
        "status": "QUALIFIED",
        "role": role,
        "image_name": image,
        "image_digest": image_digest,
        "image_ref": image_ref,
        "scope": scope,
        "receipt_path": str(receipt_path),
        "receipt_digest": receipt["receipt_digest"],
        "qualified_at": now,
    }
    _write_json(lock_path, lock)
    print(json.dumps({"role": role, "lock": str(lock_path), "image_digest": image_digest,
                      "image_ref": image_ref, "scope": scope}, sort_keys=True))
    return lock_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--sidecar-image", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("~/.config/bench/qualifications").expanduser())
    parser.add_argument("--signing-key-hex", default=os.environ.get("BENCH_IMAGE_QUALIFICATION_SIGNING_KEY", ""))
    parser.add_argument("--key-id", default=os.environ.get("BENCH_IMAGE_QUALIFICATION_KEY_ID", "image-qualification-v1"))
    parser.add_argument("--registry", action="store_true",
                        help="bind the first inspected repo@sha256 digest instead of the host Image ID")
    args = parser.parse_args(argv)
    if not args.signing_key_hex:
        parser.error("provide --signing-key-hex or BENCH_IMAGE_QUALIFICATION_SIGNING_KEY")
    try:
        qualify("candidate", args.candidate_image, args.output_dir.expanduser().resolve(), args.signing_key_hex, args.key_id, args.registry)
        qualify("sidecar", args.sidecar_image, args.output_dir.expanduser().resolve(), args.signing_key_hex, args.key_id, args.registry)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"qualification failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
