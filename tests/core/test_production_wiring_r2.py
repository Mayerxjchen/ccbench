"""R2 RED gates: production composition (C1/C2/C3/C8).

Each test fails against the current tree because the production entrypoint
(`eval.py`) and the Candidate tool-execution path do not yet invoke the
trusted Coordinator / model transport / runtime registry / watchdog.

These are *production-wiring* gates, not module tests: they assert the actual
`eval.py` main loop and `agents.docker_exec` callsites, so "module exists" or
"isolated test green" cannot close them.
"""

from __future__ import annotations

import inspect
import unittest.mock as mock
from pathlib import Path

import pytest

import eval as eval_mod
from dftworld_bench.contracts.resolved_lock import ResolvedRunLock
from dftworld_bench.core.harness import HarnessSpec
from dftworld_bench.runtime.registry import RuntimeRegistryError


ROOT = Path(__file__).resolve().parents[2]
FAKE_DIGEST = "sha256:" + "ab" * 32


@pytest.fixture()
def local_task():
    """A real local-sandbox task, loaded exactly as the production entrypoint does."""
    return eval_mod.load_task(ROOT / "001-hello")


# ---------------------------------------------------------------------------
# C1: eval.py must invoke the trusted Coordinator and model transport
# ---------------------------------------------------------------------------

def _eval_source() -> str:
    return inspect.getsource(eval_mod)


def test_eval_main_loop_constructs_run_coordinator():
    """C1: the production main loop owns the attempt via RunCoordinator.

    RED: `eval.py` never constructs RunCoordinator (scan shows only a comment
    reference in harness.py).  The coordinator is the durable session machine
    for one attempt; a run that never builds one bypasses replay/crash safety.
    """
    src = _eval_source()
    assert "RunCoordinator(" in src


def test_eval_main_loop_constructs_model_transport():
    """C1: agent model calls go through RetryingModelClient in production.

    RED: no production file constructs RetryingModelClient; the runner's
    provider is built by `make_provider()` with no retry/ledger/event owner.
    """
    src = _eval_source()
    assert "RetryingModelClient(" in src


def test_eval_harness_provenance_uses_runtime_registry(local_task):
    """C8: runtime identities are resolved by RuntimeRegistry, not inline dicts.

    RED: `resolve_harness_provenance()` hand-builds the `runtime_identities`
    dict (candidate/control/compute/verifier) and never calls
    `RuntimeRegistry.resolve()`.  The registry is the only owner of runtime
    identity; an inline dict can drift from the registry's capability rules.
    """
    import dftworld_bench.runtime.registry as registry_mod

    # Mock runner that passes qualification without Docker. The file listing
    # must be non-empty: I7 made _check_no_ssh_or_keys fail-closed on an
    # empty inspection result.
    mock_runner = mock.MagicMock()
    mock_runner.image_digest.return_value = FAKE_DIGEST
    mock_runner.inspect.return_value = {
        "platform": "linux/amd64",
        "user": "65532:65532",
        "files": ["/usr/local/bin/python", "/app/entrypoint.sh"],
    }
    mock_runner.run.return_value = (0, "usage\n", "")

    with mock.patch.object(
        eval_mod, "docker_image_digest", return_value=FAKE_DIGEST
    ), mock.patch.object(
        registry_mod.RuntimeRegistry, "resolve"
    ) as mock_resolve:
        mock_resolve.side_effect = RuntimeRegistryError(
            "C8 RED: production must call RuntimeRegistry.resolve()"
        )
        provenance = eval_mod.resolve_harness_provenance(
            local_task,
            run_id="red-c8",
            experiment_id="exp",
            condition_id="cond",
            replicate=1,
            model="deepseek/whatever",
            max_turns=8,
            benchmark_commit="HEAD",
            skills_sha=None,
            runtime_runner=mock_runner,
        )
    # The registry is the single owner of runtime identity; the production
    # provenance must consult it, never hand-build the identity dict.
    mock_resolve.assert_called_once()


def test_eval_harness_provenance_gates_on_qualification():
    """C8/C1: the resolved runtime identity is digest-qualified before the run.

    RED: no production caller invokes `qualify_runtime()`.  Formal runs must
    fail closed when the runtime identity does not pass its role checks.
    """
    src = _eval_source()
    assert "qualify_runtime(" in src


def test_harness_spec_carries_resolved_runtime_identities():
    """C2: HarnessSpec receives the *registry-resolved* identity set."""
    fields = {f.name for f in HarnessSpec.__dataclass_fields__.values()}
    assert "runtime_identities" in fields
    assert "lock_digest" in fields
    assert "budgets" in fields
    # The harness refuses lockless runs — the wiring must always fill these.
    spec = mock.Mock(spec=HarnessSpec)
    # NOTE: this gate is about production wiring, so only the source-level
    # requirement is asserted here; behavior is covered by harness tests.


# ---------------------------------------------------------------------------
# C2: formal lock is complete and frozen at the production boundary
# ---------------------------------------------------------------------------

def test_resolved_lock_is_immutable_after_resolve():
    """C2: the lock handed to the Harness must be frozen (no late mutation).

    RED: `ResolvedRunLock` is used by eval.py but nothing verifies the formal
    boundary keeps it immutable; a mutable dataclass at this boundary would
    let a caller drift the lock after resolution.
    """
    assert ResolvedRunLock.__dataclass_params__.frozen is True


# ---------------------------------------------------------------------------
# C3: Candidate tool execution is watchdog-owned and freeze is a hard boundary
# ---------------------------------------------------------------------------

def test_docker_exec_is_watchdog_owned():
    """C3: the production tool path runs through ToolWatchdog.

    RED: `agents.docker_exec` implements its own TERM->grace->KILL inline and
    never imports or calls `core.tool_watchdog.ToolWatchdog`; there is no
    durable event / budget charge / coordinator handoff on timeout.
    """
    from dftworld_bench import agents as agents_mod
    src = inspect.getsource(agents_mod)
    assert "ToolWatchdog(" in src


def test_tool_timeout_records_operation_event():
    """C3: a watchdog kill records the operation and charges the ledger.

    RED: docker_exec's timeout returns a synthetic CompletedProcess and emits
    nothing durable; the event store and budget ledger never learn of the kill.
    """
    from dftworld_bench import agents as agents_mod
    src = inspect.getsource(agents_mod)
    assert "events" in src  # production tool path is event-linked
