#!/usr/bin/env python3
"""Qualify the runtime registry's production images against its claims.

For every distinct image in ``default_profiles()`` this discovers the image's
sha256 digest, inspects platform / non-root UID, and runs the role-appropriate
check battery (controller images additionally prove the bench-hpc CLI and its
imports; compute/verifier images prove identity, platform, user and hygiene).

The checks never start a scientific workflow — they inspect and run the CLI
help + every legal command only.  Exit code is nonzero when any production
runtime fails qualification.  Python 3.12+, standard library only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# Make the repo importable when run as a loose script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dftworld_bench.runtime.qualify import checks_for_role, qualify_runtime
from dftworld_bench.runtime.registry import (
    RuntimeIdentity,
    RuntimeProfile,
    default_profiles,
)


class DockerRunner:
    """Docker-backed runtime runner implementing the qualification protocol."""

    def image_digest(self, image: str) -> str | None:
        proc = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    def inspect(self, image: str) -> dict[str, Any]:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return {"platform": "", "user": "", "files": []}
        try:
            data = json.loads(proc.stdout)[0]
        except (IndexError, json.JSONDecodeError):
            return {"platform": "", "user": "", "files": []}
        os_name = data.get("Os", "")
        arch = data.get("Architecture", "")
        config = data.get("Config", {}) or {}
        return {
            "platform": f"{os_name}/{arch}" if os_name and arch else "",
            "user": config.get("User", ""),
            "files": self._find_sensitive_files(image),
        }

    def run(self, image: str, argv: list[str]) -> tuple[int, str, str]:
        entrypoint = argv[0]
        proc = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", entrypoint, image, *argv[1:]],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr

    @staticmethod
    def _find_sensitive_files(image: str) -> list[str]:
        """Best-effort scan for ssh keys / site config; [] when scan unavailable."""
        script = (
            "find / \\( -path '*/.ssh/*' -o -name 'id_rsa' -o -name 'id_dsa' "
            "-o -name 'id_ecdsa' -o -name 'id_ed25519' -o -name 'known_hosts' "
            "-o -name 'authorized_keys' -o -name '*site-config*' \\) "
            "-print 2>/dev/null | head -100"
        )
        proc = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return []
        return [line for line in proc.stdout.splitlines() if line.strip()]


def _profiles_by_image() -> dict[str, list[RuntimeProfile]]:
    images: dict[str, list[RuntimeProfile]] = {}
    for profile in default_profiles():
        images.setdefault(profile.image, []).append(profile)
    return images


def _controller_role(profiles: list[RuntimeProfile]) -> bool:
    return any(p.role in ("control", "candidate") for p in profiles)


def qualify_all(runner: DockerRunner) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for image, profiles in sorted(_profiles_by_image().items()):
        digest = runner.image_digest(image)
        # Candidate/control share the controller image; a single battery covers
        # every profile that maps onto it.
        role = "control" if _controller_role(profiles) else "compute"
        for profile in profiles:
            identity = RuntimeIdentity(
                role=profile.role,
                profile=profile.name,
                image=image,
                digest=digest,
            )
            report = qualify_runtime(identity, runner)
            reports.append(report.to_dict())
    return reports


def _emit(reports: list[dict[str, Any]], json_output: bool) -> int:
    if json_output:
        print(json.dumps(reports, indent=2, sort_keys=True))
        return 0 if all(r["passed"] for r in reports) else 1
    for report in reports:
        status = "qualified" if report["passed"] else "FAILED"
        runtime = report["runtime"]
        print(
            f"{status:9s} {runtime['role']:9s} {runtime['profile']:24s} "
            f"{runtime['image']}  {runtime['digest'] or '(not found)'}"
        )
        for check in report["checks"]:
            if not check["ok"]:
                print(f"          - {check['name']}: {check['detail']}")
    return 0 if all(r["passed"] for r in reports) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="qualify every production runtime")
    parser.add_argument("--json", action="store_true", help="emit one JSON document")
    args = parser.parse_args(argv)
    if not args.all:
        parser.error("nothing to qualify; pass --all")
    reports = qualify_all(DockerRunner())
    return _emit(reports, args.json)


if __name__ == "__main__":
    sys.exit(main())
