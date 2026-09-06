"""Runtime registry: requirement + execution class -> exactly one RuntimeSet.

A run states what it needs (capability name, family, version constraint); the
registry resolves that to a set of immutable runtime identities across the
four roles (candidate, control, compute, verifier).  Resolution is
case-agnostic — no case id, no task name — and fails closed on zero, ambiguous,
or conflicting matches.
"""

from __future__ import annotations

import pytest

from ccbench.runtime.registry import (
    RuntimeProfile,
    RuntimeRegistry,
    RuntimeRegistryError,
    RuntimeRequirement,
    satisfies,
)


@pytest.fixture()
def registry():
    return RuntimeRegistry()


def test_dpmp_requirements_resolve_without_case_id(registry):
    resolved = registry.resolve(
        [RuntimeRequirement("dpmp", "deepmd-jax", ">=0.2")],
        "hpc_controller",
    )
    assert resolved.compute.profile == "dpmp-jax-v1"
    assert resolved.control.profile == "bench-hpc-control-v1"


def test_compute_image_and_roles_surface(registry):
    resolved = registry.resolve(
        [RuntimeRequirement("dpmp", "deepmd-jax", ">=0.2")], "local_sandbox"
    )
    assert resolved.compute.image == "dftworld-base-deepmd-jax:0.1.0-cpu"
    assert resolved.control.role == "control"
    assert resolved.candidate.role == "candidate"
    assert resolved.verifier.role == "verifier"


def test_zero_matches_fail_closed(registry):
    with pytest.raises(RuntimeRegistryError, match="no compute runtime"):
        registry.resolve(
            [RuntimeRequirement("dpmp", "deepmd-jax", ">=9.9")], "hpc_controller"
        )


def test_no_requirements_fail_closed(registry):
    with pytest.raises(RuntimeRegistryError, match="no runtime requirements"):
        registry.resolve([], "hpc_controller")


def test_ambiguous_matches_fail_closed():
    store = RuntimeRegistry((
        RuntimeProfile("a-v1", "compute", "deepmd-jax", ("dpmp",), "0.2",
                       "linux/amd64", frozenset({"hpc_controller"}), "img-a"),
        RuntimeProfile("b-v1", "compute", "deepmd-jax", ("dpmp",), "0.3",
                       "linux/amd64", frozenset({"hpc_controller"}), "img-b"),
        RuntimeProfile("ctl-v1", "control", "bench-hpc", ("control",), "1.0",
                       "linux/amd64", frozenset({"hpc_controller"}), "ctl-img"),
        RuntimeProfile("cand-v1", "candidate", "candidate", ("candidate",), "1.0",
                       "linux/amd64", frozenset({"hpc_controller"}), "cand-img"),
        RuntimeProfile("ver-v1", "verifier", "verifier", ("verifier",), "1.0",
                       "linux/amd64", frozenset({"hpc_controller"}), "ver-img"),
    ))
    with pytest.raises(RuntimeRegistryError, match="ambiguous"):
        store.resolve(
            [RuntimeRequirement("dpmp", "deepmd-jax", ">=0.2")], "hpc_controller"
        )


def test_version_constraints():
    assert satisfies("0.2.1", ">=0.2")
    assert satisfies("2.2.11", "==2.2.11")
    assert not satisfies("2.2.10", ">=2.2.11")
    assert satisfies("1.0", "*")
    assert not satisfies("0.1.9", ">=0.2")
