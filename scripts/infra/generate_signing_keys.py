#!/usr/bin/env python3
"""Generate one Ed25519 keypair for a Bench trust role.

Private material is written atomically with mode 0600 and is never printed.
The public file is also kept mode 0600 by default so operators can choose
where to publish/copy the trust anchor explicitly.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # link(2) publishes the complete inode atomically and refuses to
        # replace a path created by a concurrent key generator.
        os.link(temporary, path)
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite existing key file: {path}") from exc
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--role", default="formal", help="trust role name, e.g. formal or image-qualification")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.role):
        parser.error("role must be a short filename-safe identifier")
    key = ed25519.Ed25519PrivateKey.generate()
    private_path = args.output_dir.expanduser().resolve() / f"{args.role}.private.hex"
    public_path = args.output_dir.expanduser().resolve() / f"{args.role}.public.hex"
    if private_path.exists() or private_path.is_symlink() or public_path.exists() or public_path.is_symlink():
        parser.error("refusing to overwrite an existing keypair; choose a new role or directory")
    private_created = False
    try:
        _atomic_write(private_path, key.private_bytes_raw().hex() + "\n")
        private_created = True
        _atomic_write(public_path, key.public_key().public_bytes_raw().hex() + "\n")
    except Exception as exc:
        # Do not leave a private key without its corresponding public anchor.
        if private_created:
            try:
                private_path.unlink()
            except FileNotFoundError:
                pass
        parser.error(str(exc))
    print(f"generated role={args.role}")
    print(f"private_file={private_path}")
    print(f"public_file={public_path}")
    print("private key content was not printed; load it explicitly into the role-specific signing command")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
