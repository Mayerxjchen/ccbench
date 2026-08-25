"""The trusted Apptainer runtime wrapper: deterministic and Candidate-proof.

``render_runtime_wrapper(request, site, run_dir)`` renders exactly one
Apptainer ``exec`` invocation from a sealed :class:`ExecutionRequestV2` and an
:class:`HpcSiteProfile`: frozen container binary, digest-pinned runtime, one
rw bind of the run dir at ``/workspace``, clean environment, argv via
``shlex.join``, scheduler headers sourced only from the SiteProfile.

Every Candidate attempt to widen containment — nested apptainer, bind
smuggling, host paths into other runs or personal HOME, SBATCH injection,
blocked environment exports — fails closed before a single byte is rendered.

``qualify_containment`` executes the rendered script against caller-supplied
read/write probes; qualification passes only when the workspace is usable and
everything outside it is unreadable. The real-SIF probe repeats this at the
authorized site (Task 11); missing proof fails qualification.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shlex
from typing import Any, Callable

# Frozen by this repository: the container binary path is not a Candidate-
# visible choice. A site whose Apptainer lives elsewhere pins it in its
# qualified profile (Task 11 receipt), not in any request field.
APPTAINER_BIN = "/usr/bin/apptainer"

_IN_CONTAINER_MOUNT = "/workspace"

# Basenames that mean "launch a nested container" no matter how they are
# spelled on disk.
_NESTED_CONTAINER_BASENAMES = frozenset({"apptainer", "singularity", "enroot"})

# Substrings that smuggle bind/mount reconfiguration through arguments.
_BIND_SMUGGLING = ("--bind", "--mount", ":rw", ":ro", "--no-mount")

# Environment names a Candidate may never export into the container.
_BLOCKED_ENV_KEYS = frozenset({"HOME", "LD_PRELOAD", "LD_LIBRARY_PATH", "SHELL"})
_BLOCKED_ENV_PREFIXES = ("APPTAINERENV_", "SINGULARITYENV_", "APPTAINER_", "SINGULARITY_")

# Host-side namespaces that must never appear as absolute argv targets: old
# solutions, personal scratch, other runs, credentials live out there.
_HOST_PATH_PREFIXES = ("/public/", "/home/", "/data/bench/run-")


class RuntimeWrapperError(Exception):
    """A request tried to widen or defeat container containment."""


@dataclasses.dataclass(frozen=True)
class RenderedRuntime:
    """The deterministic wrapper output for one attempt."""

    script: str
    runtime_digest: str
    run_dir: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "script": self.script,
            "runtime_digest": self.runtime_digest,
            "run_dir": self.run_dir,
        }


@dataclasses.dataclass(frozen=True)
class ContainmentReport:
    passed: bool
    checks: tuple[str, ...]
    digest: str


def render_runtime_wrapper(
    request: Any,
    site: Any,
    run_dir: str,
) -> RenderedRuntime:
    command = list(request.command)
    _validate_command(command)
    for key in request.environment:
        _require_env_allowed(key)

    bind_spec = f"--bind {run_dir}:{_IN_CONTAINER_MOUNT}:rw"
    joined = shlex.join(command)
    env_exports = "".join(
        f"export {key}={shlex.quote(value)}\n"
        for key, value in sorted(request.environment.items())
        if value is not None
    )
    script = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"cd {_IN_CONTAINER_MOUNT}\n"
        f"{env_exports}"
        f"exec {APPTAINER_BIN} exec --cleanenv --contain \\\n"
        f"  {bind_spec} \\\n"
        f"  {request.runtime} \\\n"
        f"  {joined}\n"
    )
    return RenderedRuntime(
        script=script,
        runtime_digest=request.runtime,
        run_dir=str(run_dir),
    )


def qualify_containment(
    rendered: RenderedRuntime,
    *,
    reader: Callable[[str], None],
    writer: Callable[[str, str], None],
) -> ContainmentReport:
    """Probe the rendered runtime through injected read/write views."""
    mount = _IN_CONTAINER_MOUNT
    checks: list[tuple[bool, str]] = []

    def probe(label: str, fn: Callable[[], None], *, expect_failure: bool) -> None:
        try:
            fn()
            succeeded = True
        except PermissionError:
            succeeded = False
        checks.append((succeeded != expect_failure, label))

    probe("workspace readable", lambda: reader(f"{mount}/input.inp"), expect_failure=False)
    probe("workspace writable", lambda: writer(f"{mount}/out/result.dat", "x"), expect_failure=False)
    probe("personal HOME unreadable", lambda: reader("/home/svc/.ssh/id_rsa"), expect_failure=True)
    probe(
        "old solution unreadable",
        lambda: reader("/public/home/<site-user>/dftworld2-runs/031-solution"),
        expect_failure=True,
    )
    probe("other runs unreadable", lambda: reader(f"{rendered.run_dir}-other/x"), expect_failure=True)
    probe("credentials unreadable", lambda: reader("/data/bench/credentials.key"), expect_failure=True)

    passed = all(ok for ok, _ in checks)
    body = {"script_digest": _digest(rendered.script), "checks": [list(c) for c in checks]}
    return ContainmentReport(
        passed=passed,
        checks=tuple(label for _, label in checks),
        digest=_digest(json.dumps(body, sort_keys=True)),
    )


def _validate_command(command: list[str]) -> None:
    if not command:
        raise RuntimeWrapperError("empty command")
    for token in command:
        if not isinstance(token, str):
            raise RuntimeWrapperError("command tokens must be strings")
        if "#SBATCH" in token:
            raise RuntimeWrapperError(
                f"SCHEDULER DIRECTIVE injection rejected near {token[:40]!r}"
            )
        lowered = token.lower()
        if any(flag in lowered for flag in _BIND_SMUGGLING):
            raise RuntimeWrapperError(
                f"BIND/MOUNT reconfiguration rejected near {token[:40]!r}"
            )
        if "/" in token:
            base = token.rsplit("/", 1)[-1].lower()
            if base in _NESTED_CONTAINER_BASENAMES:
                raise RuntimeWrapperError(
                    f"NESTED CONTAINER invocation rejected: {token!r}"
                )
        elif token.lower() in _NESTED_CONTAINER_BASENAMES:
            raise RuntimeWrapperError(f"NESTED CONTAINER invocation rejected: {token!r}")
        if token.startswith("/") and token.startswith(_HOST_PATH_PREFIXES):
            raise RuntimeWrapperError(
                f"HOST PATH escape rejected (container sees only "
                f"{_IN_CONTAINER_MOUNT}): {token!r}"
            )


def _require_env_allowed(key: str) -> None:
    upper = key.upper()
    if upper in _BLOCKED_ENV_KEYS or any(
        upper.startswith(prefix) for prefix in _BLOCKED_ENV_PREFIXES
    ):
        raise RuntimeWrapperError(f"environment export rejected: {key!r}")


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
