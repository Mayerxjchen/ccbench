"""Runtime qualification: prove an image is what the registry claims.

Qualification never starts a scientific workflow.  It inspects digest,
platform, the fixed non-root UID, the bench-hpc CLI (help + every legal
command) and the absence of ssh/private keys/site config — all through an
injected runner (Docker-backed in production, scripted in tests).

The check battery is role-aware: the controller images must also carry the
bench-hpc CLI and its import dependencies, while compute/verifier images are
only held to identity, platform, user and hygiene checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from dftworld_bench.config.profiles import canonical_json, digest_bytes
from dftworld_bench.runtime.registry import RuntimeIdentity

EXPECTED_PLATFORM = "linux/amd64"
EXPECTED_NON_ROOT_UID = "65532:65532"

# Python modules the bench-hpc CLI interpreter must be able to import.
CLI_IMPORTS = ("yaml", "jsonschema", "referencing")

# Path markers that must never appear inside a controller image.
SSH_MARKERS = (".ssh", "site-config")
KEY_BASENAMES = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "known_hosts", "authorized_keys",
}


class RuntimeRunner(Protocol):
    """Injected stand-in for the Docker-backed runner."""

    def image_digest(self, image: str) -> str | None: ...
    def inspect(self, image: str) -> dict[str, Any]: ...
    def run(self, image: str, argv: list[str]) -> tuple[int, str, str]: ...


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class QualificationReport:
    runtime: RuntimeIdentity
    checks: list[CheckResult] = field(default_factory=list)

    def passed(self) -> bool:
        return all(c.ok for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "runtime": {
                "role": self.runtime.role,
                "profile": self.runtime.profile,
                "image": self.runtime.image,
                "digest": self.runtime.digest,
            },
            "passed": self.passed(),
            "checks": [c.to_dict() for c in self.checks],
        }
        # Content-addressed: digest makes the receipt tamper-evident
        payload["digest"] = digest_bytes(canonical_json(payload))
        return payload


Check = Callable[[RuntimeIdentity, RuntimeRunner], CheckResult]


def _ok(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, True, detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name, False, detail)


def _check_digest(runtime: RuntimeIdentity, runner: RuntimeRunner) -> CheckResult:
    if not runtime.digest:
        return _fail("digest", "identity has no locked digest; run qualification first")
    actual = runner.image_digest(runtime.image)
    if actual != runtime.digest:
        return _fail("digest", f"image digest {actual!r} != locked {runtime.digest!r}")
    return _ok("digest", runtime.digest)


def _check_platform(runtime: RuntimeIdentity, runner: RuntimeRunner) -> CheckResult:
    platform = runner.inspect(runtime.image).get("platform", "")
    if platform != EXPECTED_PLATFORM:
        return _fail("platform", f"expected {EXPECTED_PLATFORM!r}, found {platform!r}")
    return _ok("platform", platform)


def _check_non_root_uid(runtime: RuntimeIdentity, runner: RuntimeRunner) -> CheckResult:
    user = runner.inspect(runtime.image).get("user", "")
    if user != EXPECTED_NON_ROOT_UID:
        return _fail(
            "non_root_uid",
            f"expected image user {EXPECTED_NON_ROOT_UID!r}, found {user!r}",
        )
    return _ok("non_root_uid", user)


def _check_python_imports(runtime: RuntimeIdentity, runner: RuntimeRunner) -> CheckResult:
    argv = ["python", "-c", "import " + ", ".join(CLI_IMPORTS)]
    rc, out, err = runner.run(runtime.image, argv)
    if rc != 0:
        return _fail("python_imports", f"rc={rc}: {err.strip()}")
    return _ok("python_imports", ", ".join(CLI_IMPORTS) + " import ok")


def _check_bench_hpc_help(runtime: RuntimeIdentity, runner: RuntimeRunner) -> CheckResult:
    from dftworld_bench.hpc.__main__ import COMMANDS

    rc, out, err = runner.run(
        runtime.image, ["python", "-m", "dftworld_bench.hpc", "help"]
    )
    if rc != 0:
        return _fail("bench_hpc_help", f"help exited rc={rc}: {err.strip()}")
    if "usage" not in out:
        return _fail("bench_hpc_help", f"help did not print usage; got {out[:120]!r}")
    return _ok("bench_hpc_help", f"usage on stdout; commands: {' | '.join(sorted(COMMANDS))}")


def _check_commands(runtime: RuntimeIdentity, runner: RuntimeRunner) -> CheckResult:
    from dftworld_bench.hpc.__main__ import COMMANDS

    for command in sorted(COMMANDS):
        rc, out, err = runner.run(
            runtime.image, ["python", "-m", "dftworld_bench.hpc", command]
        )
        # A recognized command with no gateway env exits 2 with a self-describing
        # error; anything else that mentions an unknown command is a broken image.
        if rc not in (0, 2) or "unknown command" in err:
            return _fail(
                "commands",
                f"{command}: rc={rc}, stderr={err.strip()[:160]!r}",
            )
    return _ok("commands", f"{len(COMMANDS)} commands recognized")


def _check_no_ssh_or_keys(runtime: RuntimeIdentity, runner: RuntimeRunner) -> CheckResult:
    files = runner.inspect(runtime.image).get("files", [])
    if not files:
        # Fail-closed: an empty file listing means inspect() returned nothing,
        # which is suspicious for control images. Rather than silently passing,
        # reject the qualification.
        return _fail(
            "no_ssh_or_keys",
            "inspector returned no file listing; cannot verify absence of ssh/keys",
        )
    for path in files:
        lowered = path.lower()
        base = lowered.rsplit("/", 1)[-1]
        if any(m in lowered for m in SSH_MARKERS) or base in KEY_BASENAMES:
            return _fail("no_ssh_or_keys", f"found {path} in image")
    return _ok("no_ssh_or_keys", "no ssh keys or site config found")


def _check_no_mig(runtime: RuntimeIdentity, runner: RuntimeRunner) -> CheckResult:
    """Fail if the runtime image's GPU allocation exposes MIG slicing.

    A MIG-partitioned GPU cannot satisfy a full-GPU ReqTRES; admitting it
    through qualification would let a MIG allocation bypass the site-profile
    full-GPU gate at submit time.
    """
    info = runner.inspect(runtime.image)
    gpu_str = str(info.get("gpu", "") or info.get("nvidia_gpu", "")).lower()
    if "mig" in gpu_str or ":" in gpu_str and "g." in gpu_str:
        return _fail("no_mig", f"runtime image exposes MIG-sliced GPU: {gpu_str}")
    return _ok("no_mig", "no MIG slicing detected")


# Control/candidate images are the trusted controller: they must carry the
# bench-hpc CLI and its imports.  Compute/verifier images are scientific
# runtimes and are only held to identity, platform, user and hygiene.
_CONTROL_CHECKS: tuple[Check, ...] = (
    _check_digest,
    _check_platform,
    _check_non_root_uid,
    _check_python_imports,
    _check_bench_hpc_help,
    _check_commands,
    _check_no_ssh_or_keys,
    _check_no_mig,
)
_COMPUTE_CHECKS: tuple[Check, ...] = (
    _check_digest,
    _check_platform,
    _check_non_root_uid,
    _check_no_ssh_or_keys,
    _check_no_mig,
)


def checks_for_role(role: str) -> tuple[Check, ...]:
    if role in ("control", "candidate"):
        return _CONTROL_CHECKS
    return _COMPUTE_CHECKS


def qualify_runtime(runtime: RuntimeIdentity, runner: RuntimeRunner) -> QualificationReport:
    """Prove *runtime* is what the registry claims, via the injected *runner*."""
    checks = [check(runtime, runner) for check in checks_for_role(runtime.role)]
    return QualificationReport(runtime, checks)
