#!/usr/bin/env python3
"""Task 4: fail-closed Slurm state handling for the 034 transport.

Unit tests (no scheduler, no Docker) always run and cover:
  * state suffixes / cancellation reasons (``COMPLETED+``, ``CANCELLED by uid``)
  * array / step rows (parent job row wins over ``42.0`` / ``42_1``)
  * UNKNOWN queries (a query failure is never a terminal state)
  * terminal failures (FAILED/TIMEOUT/... -> FAILED)
  * waiting through the queue (PENDING -> RUNNING -> COMPLETED)
  * timeout cancellation (a controller deadline cancels, never orphaning)
  * relative-log rejection (SubmitOpts.output must be absolute or %j-resolvable)

Docker integration is OPT-IN: pass a container id on argv or set
AI2KIT_TRANSPORT_TEST_CONTAINER.  Without one, it prints SKIP and exits 0.

Usage:
  python3 test_transport.py                  # unit tests only (+ SKIP)
  python3 test_transport.py <container>      # unit tests + pseudo-backend e2e
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

sys.path.insert(0, str(Path(__file__).resolve().parent))

from slurm_transport import (  # noqa: E402
    JobState,
    SlurmTransport,
    SubmitOpts,
    TransportError,
    make_transport,
    normalize_state,
    parse_job_id,
    parse_sacct_state,
    parse_squeue_state,
    resolve_output_path,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeTransport(SlurmTransport):
    """Scripted status() for wait() semantics tests."""

    def __init__(self, states: List[JobState], fail_until: int = 0) -> None:
        self._states = list(states)
        self._fail_until = fail_until
        self._calls = 0
        self.cancelled: List[str] = []

    def submit(self, script, opts=None):  # type: ignore[no-untyped-def]
        return "42"

    def status(self, job_id: str) -> JobState:
        self._calls += 1
        if self._calls <= self._fail_until:
            raise TransportError("transient query failure")
        return self._states.pop(0) if self._states else JobState.UNKNOWN

    def log(self, job_id: str, tail=None):  # type: ignore[no-untyped-def]
        return ""

    def cancel(self, job_id: str) -> None:
        self.cancelled.append(job_id)

    def stage(self, local_paths, remote_dir):  # type: ignore[no-untyped-def]
        return [str(p) for p in local_paths]

    def fetch(self, remote_paths, local_dir):  # type: ignore[no-untyped-def]
        return [Path(p) for p in remote_paths]


# --------------------------------------------------------------------------- #
# Unit tests
# --------------------------------------------------------------------------- #


class TestNormalizeState(unittest.TestCase):
    def test_state_suffix_is_stripped(self) -> None:
        self.assertIs(normalize_state("COMPLETED+"), JobState.COMPLETED)
        self.assertIs(normalize_state("RUNNING+"), JobState.RUNNING)

    def test_cancellation_reason_is_stripped(self) -> None:
        self.assertIs(normalize_state("CANCELLED by 12345"), JobState.CANCELLED)
        self.assertIs(normalize_state("CANCELLED by root"), JobState.CANCELLED)

    def test_full_names(self) -> None:
        self.assertIs(normalize_state("PENDING"), JobState.PENDING)
        self.assertIs(normalize_state("RUNNING"), JobState.RUNNING)
        self.assertIs(normalize_state("FAILED"), JobState.FAILED)
        self.assertIs(normalize_state("TIMEOUT"), JobState.FAILED)
        self.assertIs(normalize_state("OUT_OF_MEMORY"), JobState.FAILED)
        self.assertIs(normalize_state("PREEMPTED"), JobState.FAILED)

    def test_squeue_letters(self) -> None:
        self.assertIs(normalize_state("PD"), JobState.PENDING)
        self.assertIs(normalize_state("R"), JobState.RUNNING)
        self.assertIs(normalize_state("CD"), JobState.COMPLETED)
        self.assertIs(normalize_state("CA"), JobState.CANCELLED)

    def test_unknown_and_empty_map_to_unknown(self) -> None:
        self.assertIs(normalize_state(""), JobState.UNKNOWN)
        self.assertIs(normalize_state(None), JobState.UNKNOWN)
        self.assertIs(normalize_state("MYSTERY"), JobState.UNKNOWN)


class TestParseSacctState(unittest.TestCase):
    def test_parent_job_row_wins_over_step_rows(self) -> None:
        output = "JobID|State\n42.batch|FAILED\n42.extern|COMPLETED\n42|COMPLETED+\n"
        self.assertIs(parse_sacct_state(output, "42"), JobState.COMPLETED)

    def test_parent_row_after_step_rows_still_wins(self) -> None:
        # Numeric coercion must not let "42.0" masquerade as job 42.
        output = "JobID|State\n42.0|FAILED\n42|COMPLETED\n"
        self.assertIs(parse_sacct_state(output, "42"), JobState.COMPLETED)

    def test_array_child_rows_ignored_when_parent_present(self) -> None:
        output = "JobID|State\n42_0|RUNNING\n42_1|RUNNING\n42|FAILED\n"
        self.assertIs(parse_sacct_state(output, "42"), JobState.FAILED)

    def test_only_step_rows_are_fail_closed_unknown(self) -> None:
        # No parent row -> never guess a terminal state from a partial record.
        output = "JobID|State\n42.batch|COMPLETED\n42.extern|COMPLETED\n"
        self.assertIs(parse_sacct_state(output, "42"), JobState.UNKNOWN)

    def test_header_only_or_empty_is_unknown(self) -> None:
        self.assertIs(parse_sacct_state("JobID|State\n", "42"), JobState.UNKNOWN)
        self.assertIs(parse_sacct_state("", "42"), JobState.UNKNOWN)

    def test_unknown_job_id_is_unknown(self) -> None:
        output = "JobID|State\n42|COMPLETED\n"
        self.assertIs(parse_sacct_state(output, "999999"), JobState.UNKNOWN)

    def test_cancel_reason_survives_parsing(self) -> None:
        output = "JobID|State\n42|CANCELLED by 1001\n"
        self.assertIs(parse_sacct_state(output, "42"), JobState.CANCELLED)


class TestParseSqueueState(unittest.TestCase):
    def test_letters(self) -> None:
        self.assertIs(parse_squeue_state("42 R\n", "42"), JobState.RUNNING)
        self.assertIs(parse_squeue_state("42 PD\n", "42"), JobState.PENDING)
        self.assertIs(parse_squeue_state("42 CD\n", "42"), JobState.COMPLETED)

    def test_missing_job_is_unknown(self) -> None:
        self.assertIs(parse_squeue_state("43 R\n", "42"), JobState.UNKNOWN)
        self.assertIs(parse_squeue_state("", "42"), JobState.UNKNOWN)


class TestParseJobId(unittest.TestCase):
    def test_submitted_batch_job(self) -> None:
        self.assertEqual(parse_job_id("Submitted batch job 42\n"), "42")

    def test_parsable_bare_id(self) -> None:
        self.assertEqual(parse_job_id("42\n"), "42")

    def test_no_id_raises(self) -> None:
        with self.assertRaises(TransportError):
            parse_job_id("nothing here")


class TestResolveOutputPath(unittest.TestCase):
    def test_absolute_passthrough(self) -> None:
        self.assertEqual(
            resolve_output_path("/app/work", "42", "/logs/job-%j.out"),
            "/logs/job-42.out",
        )

    def test_relative_anchored_at_submit_dir(self) -> None:
        self.assertEqual(
            resolve_output_path("/app/work/geopt", "42", "slurm-%j.out"),
            "/app/work/geopt/slurm-42.out",
        )

    def test_default_slurm_id_out(self) -> None:
        self.assertEqual(
            resolve_output_path("/app/work", "42"), "/app/work/slurm-42.out"
        )

    def test_big_j_tokens(self) -> None:
        self.assertEqual(
            resolve_output_path("/app", "42", "%J.out"), "/app/42.out"
        )


class TestSubmitLogPathValidation(unittest.TestCase):
    def test_absolute_output_is_accepted(self) -> None:
        opts = SubmitOpts(output="/logs/slurm-%j.out")
        opts.to_argv()  # must not raise

    def test_relative_with_job_token_is_accepted(self) -> None:
        opts = SubmitOpts(output="slurm-%j.out", error="slurm-%J.out")
        opts.to_argv()  # %j / %J make a relative path resolvable per job

    def test_bare_relative_output_is_rejected(self) -> None:
        opts = SubmitOpts(output="slurm.out")
        with self.assertRaises(TransportError):
            opts.to_argv()

    def test_bare_relative_error_is_rejected(self) -> None:
        opts = SubmitOpts(output="/logs/slurm-%j.out", error="slurm.out")
        with self.assertRaises(TransportError):
            opts.to_argv()

    def test_no_output_fields_is_accepted(self) -> None:
        SubmitOpts().to_argv()  # must not raise


class TestWaitSemantics(unittest.TestCase):
    def test_wait_through_queue(self) -> None:
        t = _FakeTransport(
            [JobState.PENDING, JobState.RUNNING, JobState.COMPLETED]
        )
        self.assertIs(t.wait("42", poll=0.0, timeout=10), JobState.COMPLETED)
        self.assertEqual(t.cancelled, [])

    def test_wait_cancels_on_controller_timeout(self) -> None:
        t = _FakeTransport([JobState.RUNNING, JobState.RUNNING])
        with self.assertRaises(TimeoutError):
            t.wait("42", poll=0.0, timeout=0)
        self.assertEqual(t.cancelled, ["42"])

    def test_transient_query_failure_is_never_terminal(self) -> None:
        # First two queries raise (UNKNOWN); the third returns terminal.
        t = _FakeTransport([JobState.COMPLETED], fail_until=2)
        self.assertIs(t.wait("42", poll=0.0, timeout=10), JobState.COMPLETED)
        self.assertEqual(t.cancelled, [])

    def test_missing_records_are_not_success(self) -> None:
        # A job that is never recorded must raise TimeoutError (and cancel),
        # never return COMPLETED or FAILED.
        t = _FakeTransport([])  # always UNKNOWN
        with self.assertRaises(TimeoutError):
            t.wait("42", poll=0.0, timeout=0)
        self.assertEqual(t.cancelled, ["42"])


# --------------------------------------------------------------------------- #
# Opt-in Docker integration
# --------------------------------------------------------------------------- #


def _sh(cmd: str) -> str:
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True, check=True
    ).stdout


def run_docker_integration(container: str) -> int:
    """Pseudo-backend e2e inside an isolated PSEUDO_SLURM_DIR.  Returns an exit
    code; requires a running case container (opt-in by design)."""
    test_dir = "/app/transport-test"
    state_dir = "/tmp/trans-test-state"
    _sh(
        f"docker exec {container} sh -c 'rm -rf {test_dir} {state_dir} && "
        f"mkdir -p {test_dir} {state_dir} && "
        f"printf \"#!/bin/bash\\necho hello-from-transport\\necho jobid=$SLURM_JOB_ID\\n\" "
        f"> {test_dir}/hello.slurm'"
    )
    print("sandbox prepared")

    t = make_transport(
        "pseudo", container=container, workspace="/app", state_dir=state_dir
    )

    jid = t.submit(f"{test_dir}/hello.slurm")
    print(f"submitted job {jid}; immediate status={t.status(jid).value}")
    st = t.wait(jid, poll=2, timeout=120)
    print(f"terminal state={st.value}")
    assert st is JobState.COMPLETED, f"expected COMPLETED, got {st}"

    log = t.log(jid)
    print(f"log={log!r}")
    assert "hello-from-transport" in log, f"log missing expected line: {log!r}"
    assert t.log(jid, tail=1).strip(), "tail log unexpectedly empty"

    jid2 = t.submit(f"{test_dir}/hello.slurm")
    t.cancel(jid2)
    st2 = t.wait(jid2, poll=2, timeout=120)
    print(f"cancel terminal state={st2.value}")
    assert st2 is JobState.CANCELLED, f"expected CANCELLED, got {st2}"

    assert t.status("999999") is JobState.UNKNOWN, "unknown id should be UNKNOWN"

    print("PseudoSlurmTransport e2e OK")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    # unittest finds classes *and* any test_* functions; only the *UnitTests
    # classes below are meant to run here.
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1
    container = (
        argv[0]
        if argv
        else os.environ.get("AI2KIT_TRANSPORT_TEST_CONTAINER")
    )
    if not container:
        print(
            "SKIP: Docker integration requires a container argument or "
            "AI2KIT_TRANSPORT_TEST_CONTAINER"
        )
        return 0
    try:
        return run_docker_integration(container)
    except Exception as exc:  # noqa: BLE001 — report and exit non-zero
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
