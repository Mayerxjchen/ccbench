#!/usr/bin/env python3
"""Check Packmol availability and workspace writability without installing tools."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from packmol_common import emit_result, fail, parse_packmol_version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--version-timeout", type=float, default=10.0)
    args = parser.parse_args()
    workdir = Path(args.workdir)
    version: str | None = None
    probe_status: str | None = None
    probe_returncode: int | None = None
    try:
        if args.version_timeout <= 0:
            raise ValueError("version timeout must be positive")
        if not workdir.is_dir():
            raise ValueError(f"workdir does not exist: {workdir}")
        if not os.access(workdir, os.W_OK):
            raise ValueError(f"workdir is not writable: {workdir}")
        packmol_path = shutil.which("packmol")
        if packmol_path is None:
            raise ValueError("Packmol is not available in PATH")
        try:
            probe = subprocess.run(
                [packmol_path, "--version"],
                text=True,
                capture_output=True,
                check=False,
                timeout=args.version_timeout,
            )
        except subprocess.TimeoutExpired:
            probe_status = "timed_out"
        else:
            probe_returncode = probe.returncode
            if probe.returncode == 0:
                probe_status = "supported"
                version = parse_packmol_version(
                    "\n".join(part for part in (probe.stdout, probe.stderr) if part)
                )
            else:
                probe_status = "unsupported"
    except Exception as exc:
        return fail(
            check="validation",
            name="packmol_preflight",
            error=exc,
            source_paths=[str(workdir)],
            python_version=sys.version.split()[0],
        )
    emit_result(
        check="validation",
        name="packmol_preflight",
        status="PASS",
        preparation_stage="preflighted",
        source_paths=[str(workdir), packmol_path],
        python_version=sys.version.split()[0],
        packmol_path=packmol_path,
        packmol_version=version,
        version_probe_status=probe_status,
        version_probe_returncode=probe_returncode,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
