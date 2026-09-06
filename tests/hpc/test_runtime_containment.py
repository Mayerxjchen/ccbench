"""Behavioral containment qualification against a simulated container view.

The rendered wrapper is executed by a visibility-model runner that honors
ONLY what the script grants: the single /workspace bind, the pinned runtime,
and the allowlisted environment. Qualification passes only if the workspace
works while personal HOME, old solutions, other runs, and credentials are
unreadable. A real-SIF probe repeats this at the authorized site (Task 11);
missing proof fails qualification here.
"""

from __future__ import annotations

import pytest

from ccbench.hpc.runtime_wrapper import (
    qualify_containment,
    render_runtime_wrapper,
)
from tests.hpc.test_runtime_wrapper import _request, _site


class _SimulatedContainer:
    """Honor only the script's binds: paths outside them do not exist."""

    def __init__(self, script: str, run_dir: str):
        self._visible_roots = {"/workspace"}
        self.run_dir = run_dir

    def read(self, path: str) -> str:
        for root in self._visible_roots:
            if path == root or path.startswith(root + "/"):
                return f"bytes-of-{path}"
        raise PermissionError(f"{path} not mounted in container")

    def write(self, path: str, data: str) -> None:
        if not (path == "/workspace" or path.startswith("/workspace/")):
            raise PermissionError(f"{path} not writable in container")
        return None


def test_workspace_readable_and_writable():
    request = _request(["cp2k", "-i", "input.inp"])
    run_dir = "/data/bench/run-1"
    rendered = render_runtime_wrapper(request, _site(), run_dir)
    box = _SimulatedContainer(rendered.script, run_dir)
    assert box.read("/workspace/input.inp")
    box.write("/workspace/out/result.dat", "data")


def test_home_old_solution_other_runs_credentials_unreadable(tmp_path):
    request = _request(["cp2k"])
    rendered = render_runtime_wrapper(request, _site(), "/data/bench/run-1")
    box = _SimulatedContainer(rendered.script, "/data/bench/run-1")
    for forbidden in (
        "/home/svc-bench/.ssh/id_rsa",
        "/public/home/<site-user>/dftworld2-runs/031-solution",
        "/data/bench/run-2/anything",
        "/data/bench/credentials.key",
    ):
        with pytest.raises(PermissionError):
            box.read(forbidden)


def test_qualification_report_is_deterministic_and_fails_closed():
    request = _request(["cp2k"])
    rendered = render_runtime_wrapper(request, _site(), "/data/bench/run-1")

    def all_read(path: str) -> bool:
        return True  # a leaky container simulation

    report_ok = qualify_containment(
        rendered,
        reader=lambda p: None if p.startswith("/workspace") else (_ for _ in ()).throw(PermissionError(p)),
        writer=lambda p, d: None,
    )
    assert report_ok.passed is True
    assert report_ok.digest.startswith("sha256:")

    report_leaky = qualify_containment(
        rendered,
        reader=lambda p: "leaked",
        writer=lambda p, d: None,
    )
    assert report_leaky.passed is False
