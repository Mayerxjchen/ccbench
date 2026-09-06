#!/usr/bin/env python3
"""Capture Gate A2 CompShare bootstrap evidence.

Two operational modes:
* default (plan-only):
    Prints the allowlisted probe argv arrays and expected artifacts.
    Makes zero subprocess calls, reads no configuration or credentials.
* --execute-read-only:
    Executes exactly the allowlisted read-only probes via the pinned CLI (0.4.1),
    records raw JSON/text artifacts, summarizes them without leaking secrets,
    and seals an immutable, hashed Gate A2 bootstrap manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# Ensure project root is on sys.path
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ccbench.hpc.drivers.compshare.bootstrap_evidence import (
    EXPECTED_CLI_VERSION,
    BootstrapEvidenceError,
    build_allowlisted_probes,
    build_bootstrap_manifest,
    build_evidence_file,
    build_probe_record,
    canonical_manifest_digest,
    require_pinned_cli_version,
    validate_bootstrap_manifest,
)


def _resolve_cli_bin(explicit_bin: str | None = None) -> str:
    if explicit_bin:
        resolved = shutil.which(explicit_bin) or explicit_bin
        return os.path.abspath(resolved) if os.path.exists(resolved) else resolved

    venv_bin = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "compshare"
    if venv_bin.is_file():
        return str(venv_bin)

    system_bin = shutil.which("compshare")
    if system_bin:
        return system_bin

    return "compshare"


def _hash_file(path: str | Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _get_git_commit() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception:
        return "0" * 40


def _gather_environment() -> dict[str, str]:
    loc = "C"
    try:
        current_loc = locale.getlocale()
        if current_loc and current_loc[0]:
            loc = f"{current_loc[0]}.{current_loc[1] or 'UTF-8'}"
    except Exception:
        pass

    tz = "UTC"
    try:
        if time.tzname and time.tzname[0]:
            tz = time.tzname[0]
    except Exception:
        pass

    return {
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "python": platform.python_version(),
        "locale": loc,
        "timezone": tz,
    }


def plan_bootstrap_capture(
    *,
    cli_bin: str,
    region: str = "cn-sh2",
    zone: str = "cn-sh2-02",
    alternate_region: str = "cn-wlcb",
    alternate_zone: str = "cn-wlcb-01",
) -> list[dict[str, Any]]:
    """Return allowlisted probe plans without executing anything."""
    probes = build_allowlisted_probes(
        cli_bin=cli_bin,
        region=region,
        zone=zone,
        alternate_region=alternate_region,
        alternate_zone=alternate_zone,
    )
    return [p.to_dict() for p in probes]


def execute_bootstrap_capture(
    *,
    output_dir: Path,
    cli_bin: str,
    credential_profile_id: str = "compshare-cli",
    region: str = "cn-sh2",
    zone: str = "cn-sh2-02",
    alternate_region: str = "cn-wlcb",
    alternate_zone: str = "cn-wlcb-01",
    runner: Callable[[Sequence[str]], tuple[int, str, str]] | None = None,
    source_commit: str | None = None,
    capture_tool_path: str = "scripts/qualification/capture_compshare_bootstrap.py",
) -> dict[str, Any]:
    """Execute allowlisted read-only probes and write sealed evidence."""
    if output_dir.exists():
        if any(output_dir.iterdir()):
            raise BootstrapEvidenceError(
                f"Output directory already exists and is not empty: {output_dir}"
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)

    # Validate cli executable
    cli_path = Path(cli_bin)
    if not cli_path.is_file():
        resolved = shutil.which(cli_bin)
        if not resolved:
            raise BootstrapEvidenceError(f"CLI executable not found: {cli_bin}")
        cli_path = Path(resolved)

    executable_sha256 = _hash_file(cli_path)

    # Tool script hash
    this_script = Path(__file__).resolve()
    capture_tool_sha256 = _hash_file(this_script) if this_script.is_file() else "0" * 64

    # Allowlisted probes
    probe_specs = build_allowlisted_probes(
        cli_bin=str(cli_path),
        region=region,
        zone=zone,
        alternate_region=alternate_region,
        alternate_zone=alternate_zone,
    )

    def default_runner(argv: Sequence[str]) -> tuple[int, str, str]:
        res = subprocess.run(list(argv), capture_output=True, text=True)
        return res.returncode, res.stdout, res.stderr

    run_cmd = runner or default_runner

    captured_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    commit_id = source_commit or _get_git_commit()
    environment = _gather_environment()

    probes_records: list[dict[str, Any]] = []
    evidence_files: list[dict[str, Any]] = []
    cli_version_observed: str | None = None

    for spec in probe_specs:
        started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ret_code, stdout, stderr = run_cmd(spec.argv)
        finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Determine file name for this probe
        if spec.name == "version":
            file_name = "version.txt"
        else:
            file_name = f"{spec.name}.json"

        artifact_path = output_dir / file_name
        artifact_path.write_text(stdout, encoding="utf-8")
        os.chmod(artifact_path, 0o600)

        stdout_bytes = stdout.encode("utf-8")
        stderr_bytes = stderr.encode("utf-8")
        stdout_hash = hashlib.sha256(stdout_bytes).hexdigest()
        stderr_hash = hashlib.sha256(stderr_bytes).hexdigest() if stderr_bytes else None

        evidence_entry = build_evidence_file(
            artifact_path,
            root=output_dir,
            relative_path=file_name,
            role="raw_provider_response",
        )
        evidence_files.append(evidence_entry)

        # Parse summary
        summary: dict[str, Any] = {}
        if spec.name == "version":
            ver = require_pinned_cli_version(stdout)
            cli_version_observed = ver
            summary = {"version": ver}
        else:
            try:
                parsed = json.loads(stdout) if stdout.strip() else {}
                if isinstance(parsed, dict):
                    # Prefer unwrapped data or checks
                    if "data" in parsed and isinstance(parsed["data"], dict):
                        summary = parsed["data"]
                    else:
                        summary = parsed
                else:
                    summary = {"raw": type(parsed).__name__}
            except Exception:
                summary = {"unparsed": True}

        record = build_probe_record(
            name=spec.name,
            argv=spec.argv,
            captured_at=finished_at,
            started_at=started_at,
            finished_at=finished_at,
            exit_code=ret_code,
            summary=summary,
            evidence_path=file_name,
            stdout_sha256=stdout_hash,
            stderr_sha256=stderr_hash,
            cli_bin=str(cli_path),
            region=region,
            zone=zone,
            alternate_region=alternate_region,
            alternate_zone=alternate_zone,
        )
        probes_records.append(record)

    if cli_version_observed != EXPECTED_CLI_VERSION:
        raise BootstrapEvidenceError(
            f"Observed CLI version {cli_version_observed!r} does not match required {EXPECTED_CLI_VERSION!r}"
        )

    manifest = build_bootstrap_manifest(
        credential_profile_id=credential_profile_id,
        captured_at=captured_at,
        source_commit=commit_id,
        cli_version=EXPECTED_CLI_VERSION,
        executable_sha256=executable_sha256,
        probes=probes_records,
        evidence_files=evidence_files,
        environment=environment,
        capture_tool_sha256=capture_tool_sha256,
        executable_path=str(cli_path),
        capture_tool_path=capture_tool_path,
    )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o600)

    # Validate against schema and directory integrity
    validate_bootstrap_manifest(manifest, evidence_root=output_dir)

    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture Gate A2 CompShare bootstrap evidence."
    )
    parser.add_argument(
        "--execute-read-only",
        action="store_true",
        help="Execute the allowlisted read-only probes and persist evidence.",
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=None,
        help="Parent root directory for evidence runs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Explicit exact directory to write this evidence batch.",
    )
    parser.add_argument(
        "--cli-bin",
        type=str,
        default=None,
        help="Path to compshare CLI executable.",
    )
    parser.add_argument(
        "--profile-id",
        type=str,
        default="compshare-cli",
        help="Logical credential profile identifier (for audit only, never passed to CLI).",
    )
    parser.add_argument(
        "--region",
        type=str,
        default="cn-sh2",
        help="Primary region for probes.",
    )
    parser.add_argument(
        "--zone",
        type=str,
        default="cn-sh2-02",
        help="Primary zone for probes.",
    )
    parser.add_argument(
        "--alternate-region",
        type=str,
        default="cn-wlcb",
        help="Alternate region for probes.",
    )
    parser.add_argument(
        "--alternate-zone",
        type=str,
        default="cn-wlcb-01",
        help="Alternate zone for probes.",
    )

    args = parser.parse_args(argv)

    cli_bin = _resolve_cli_bin(args.cli_bin)

    if not args.execute_read_only:
        print("[PLAN-ONLY MODE] CompShare Gate A2 Bootstrap Probes:")
        print(f"CLI binary candidate: {cli_bin}")
        probes = plan_bootstrap_capture(
            cli_bin=cli_bin,
            region=args.region,
            zone=args.zone,
            alternate_region=args.alternate_region,
            alternate_zone=args.alternate_zone,
        )
        for p in probes:
            print(f"  - {p['name']}: {' '.join(p['argv'])}")
        print("\nNo subprocess executed. Pass --execute-read-only to run.")
        return 0

    # Determine destination directory
    if args.output_dir:
        out_dir = args.output_dir
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        root = args.evidence_root or (Path.home() / ".config" / "mlffbench" / "evidence" / "gate_a2")
        out_dir = root / ts

    try:
        manifest = execute_bootstrap_capture(
            output_dir=out_dir,
            cli_bin=cli_bin,
            credential_profile_id=args.profile_id,
            region=args.region,
            zone=args.zone,
            alternate_region=args.alternate_region,
            alternate_zone=args.alternate_zone,
        )
        print(f"Gate A2 evidence sealed successfully: {out_dir}")
        print(f"Manifest digest: {manifest['manifest_digest']}")
        return 0
    except Exception as exc:
        print(f"ERROR: Bootstrap capture failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
