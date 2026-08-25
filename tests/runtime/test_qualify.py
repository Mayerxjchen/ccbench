"""Runtime qualification: prove an image is what the registry claims.

Qualification never starts a scientific workflow.  It inspects digest,
platform, the fixed non-root UID, the bench-hpc CLI (help + every legal
command) and the absence of ssh/private keys/site config — all through an
injected runner so the checks are testable without Docker.
"""

from __future__ import annotations

from dftworld_bench.runtime.qualify import qualify_runtime
from dftworld_bench.runtime.registry import RuntimeIdentity


class FakeRunner:
    """Scripted stand-in for the Docker-backed runner.

    Defaults script every bench-hpc CLI invocation as *recognized* (rc 2 with
    the self-describing env error when no gateway env is set), python imports
    as fine, and ``help`` as printing usage.

    Default ``files`` includes safe system paths so fail-closed ssh/keys scan
    passes; pass ``files=("...",)`` to test SSH detection.
    """

    # Default files list: safe system paths that pass the no_ssh_or_keys check.
    _DEFAULT_FILES = ("/usr/bin/python3", "/app/main.py")

    def __init__(self, *, digest=None, platform="linux/amd64", user="",
                 files=(), script: dict | None = None):
        self.digest = digest
        self.platform = platform
        self.user = user
        self.files = list(files) if files else list(self._DEFAULT_FILES)
        self._script = dict(script or {})
        self.runs: list[tuple[str, tuple]] = []

    def image_digest(self, image):
        return self.digest

    def inspect(self, image):
        return {"platform": self.platform, "user": self.user, "files": list(self.files)}

    def run(self, image, argv):
        key = tuple(argv)
        self.runs.append((image, key))
        if key in self._script:
            return self._script[key]
        joined = " ".join(key)
        if key[:2] == ("python", "-c"):
            return (0, "", "")
        if "help" in key:
            return (0, "usage: python -m dftworld_bench.hpc <capabilities|...>", "")
        if key[:3] == ("python", "-m", "dftworld_bench.hpc"):
            return (2, "", "set BENCH_HPC_GATEWAY_URL")
        raise AssertionError(f"unscripted run: {joined}")


def _control_identity():
    return RuntimeIdentity(
        role="control",
        profile="bench-hpc-control-v1",
        image="dftworld-base-matclaw-cips:2.2.11-controller",
        digest="sha256:" + "a" * 64,
    )


def test_qualify_clean_control_image_passes():
    runner = FakeRunner(digest="sha256:" + "a" * 64, user="65532:65532")
    report = qualify_runtime(_control_identity(), runner)
    assert report.passed()
    names = {c.name for c in report.checks}
    assert {"digest", "platform", "non_root_uid", "python_imports",
            "bench_hpc_help", "commands", "no_ssh_or_keys"} <= names


def test_qualify_flags_digest_mismatch():
    runner = FakeRunner(digest="sha256:" + "b" * 64, user="65532:65532")
    report = qualify_runtime(_control_identity(), runner)
    assert not report.passed()
    digest_check = next(c for c in report.checks if c.name == "digest")
    assert not digest_check.ok


def test_qualify_flags_ssh_material_in_control_image():
    runner = FakeRunner(
        digest="sha256:" + "a" * 64,
        user="65532:65532",
        files=["/root/.ssh/id_rsa", "/etc/site-config.yaml"],
    )
    report = qualify_runtime(_control_identity(), runner)
    assert not report.passed()
    ssh_check = next(c for c in report.checks if c.name == "no_ssh_or_keys")
    assert not ssh_check.ok


def test_qualify_flags_non_root_uid_violation():
    runner = FakeRunner(digest="sha256:" + "a" * 64, user="root")
    report = qualify_runtime(_control_identity(), runner)
    assert not report.passed()
    uid_check = next(c for c in report.checks if c.name == "non_root_uid")
    assert not uid_check.ok


def test_qualify_runs_help_and_every_legal_command():
    runner = FakeRunner(digest="sha256:" + "a" * 64, user="65532:65532")
    report = qualify_runtime(_control_identity(), runner)
    assert report.passed()
    help_check = next(c for c in report.checks if c.name == "bench_hpc_help")
    assert help_check.ok and "usage" in help_check.detail
    command_check = next(c for c in report.checks if c.name == "commands")
    assert command_check.ok
    # every legal command was exercised
    from dftworld_bench.hpc.__main__ import COMMANDS

    exercised = {argv[-1] for _, argv in runner.runs if argv[:3] == ("python", "-m", "dftworld_bench.hpc")}
    assert set(COMMANDS) <= exercised


def test_qualify_unknown_command_fails_commands_check():
    runner = FakeRunner(
        digest="sha256:" + "a" * 64,
        user="65532:65532",
        script={("python", "-m", "dftworld_bench.hpc", "status"):
                (2, "", "unknown command: 'status'")},
    )
    report = qualify_runtime(_control_identity(), runner)
    assert not report.passed()
    command_check = next(c for c in report.checks if c.name == "commands")
    assert not command_check.ok


def test_report_is_json_serializable():
    runner = FakeRunner(digest="sha256:" + "a" * 64, user="65532:65532")
    report = qualify_runtime(_control_identity(), runner)
    import json

    json.dumps(report.to_dict())
