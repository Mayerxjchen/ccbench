"""Conformance suite for an HpcAdapter.

Every adapter that a site wants to attach to the trusted gateway must pass the
mandatory checks here — capabilities, submit → status → logs → fetch, cancel,
idempotency, run isolation, resource accounting, and settlement. The report is
machine-readable and only ``conformant: true`` when every mandatory check
passes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from bench.hpc.adapters.base import HpcAdapter, TERMINAL_STATES
from bench.hpc.adapters.process_test import AdapterError

DIGEST = "img@sha256:" + "a" * 64

MANDATORY = (
    "capabilities",
    "submit",
    "status-progress",
    "logs",
    "fetch-after-success",
    "idempotency",
    "run-isolation",
    "cancel",
    "fetch-before-success-raises",
    "cancel-after-terminal-raises",
    "resource-accounting",
    "settlement",
)


def _spec(key: str, command: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "idempotency_key": key,
        "runtime": DIGEST,
        "command": command,
        "resources": {"cpus": 1, "memory_gb": 1, "gpus": 0, "walltime_minutes": 5},
        "inputs": [],
        "outputs": ["out.txt"],
    }


@dataclass
class ConformanceCheck:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class ConformanceReport:
    adapter: str
    checks: list[ConformanceCheck] = field(default_factory=list)

    @property
    def conformant(self) -> bool:
        return all(check.passed for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "conformant": self.conformant,
            "checks": [check.to_dict() for check in self.checks],
        }


def run_conformance(adapter: HpcAdapter, *, deadline_sec: float = 5.0) -> ConformanceReport:
    report = ConformanceReport(adapter=getattr(adapter, "capabilities")().get("adapter", "unknown"))

    def check(name: str, fn) -> None:
        try:
            detail = fn()
            report.checks.append(ConformanceCheck(name=name, passed=True, detail=detail))
        except Exception as exc:  # noqa: BLE001 — a failing check must never break the suite
            report.checks.append(ConformanceCheck(name=name, passed=False, detail=str(exc)))

    def wait_terminal(job_id: str) -> str:
        deadline = time.monotonic() + deadline_sec
        while time.monotonic() < deadline:
            state = adapter.status(job_id)["state"]
            if state in TERMINAL_STATES:
                return state
            time.sleep(0.02)
        raise TimeoutError(f"job {job_id} did not reach a terminal state within {deadline_sec}s")

    def submit_ok(key: str, command: list[str]) -> str:
        result = adapter.submit(_spec(key, command), run_id=key, operation_id=f"{key}-op")
        if "job_id" not in result:
            raise AssertionError(f"submit for {key!r} returned no job_id: {result}")
        return result["job_id"]

    write_job = submit_ok("conform-write", ["/bin/sh", "-c", "sleep 0.05; echo ok > out.txt"])

    check("capabilities", lambda: _require(adapter.capabilities(), "states", "default_queue"))
    check("submit", lambda: _require(adapter.status(write_job), "state"))
    check("status-progress", lambda: _require(wait_terminal(write_job) == "SUCCEEDED", "wrote output"))
    check("logs", lambda: _require(adapter.logs(write_job), "stdout", "stderr"))
    check("fetch-after-success", lambda: _require("out.txt" in adapter.fetch(write_job)["files"]))
    check("idempotency", lambda: _idempotent(adapter, write_job))
    iso_job = submit_ok("conform-iso", ["/bin/sh", "-c", "sleep 0.05; echo iso > out.txt"])
    check("run-isolation", lambda: (_require(wait_terminal(iso_job) == "SUCCEEDED")
                                    + _isolation(adapter, write_job, iso_job)))
    cancel_job = submit_ok("conform-cancel", ["/bin/sh", "-c", "sleep 5"])
    check("cancel", lambda: _cancel(adapter, cancel_job))
    fetch_block = submit_ok("conform-fetchblock", ["/bin/sh", "-c", "sleep 5"])
    check("fetch-before-success-raises", lambda: _fetch_before(adapter, fetch_block))
    adapter.cancel(fetch_block)
    check("cancel-after-terminal-raises", lambda: _cancel_again(adapter, cancel_job))
    check("resource-accounting", lambda: _accounting(adapter))
    check("settlement", lambda: _settlement(adapter))
    return report


def _require(*values: Any) -> str:
    for value in values:
        if not value:
            raise AssertionError(f"missing/empty value: {value!r}")
    return "ok"


def _idempotent(adapter: HpcAdapter, job_id: str) -> str:
    again = adapter.submit(_spec("conform-write", ["/bin/sh", "-c", "sleep 0.05; echo ok > out.txt"]),
                           run_id="conform-write", operation_id="conform-write-dup")
    if again.get("job_id") != job_id:
        raise AssertionError(f"duplicate submit returned {again.get('job_id')!r}, expected {job_id!r}")
    if again.get("duplicate") is not True:
        raise AssertionError("duplicate submit did not report duplicate=true")
    return "same job_id, no relaunch"


def _isolation(adapter: HpcAdapter, a: str, b: str) -> str:
    if a == b:
        raise AssertionError("distinct idempotency keys collided on job_id")
    a_outputs = adapter.fetch(a)["outputs"].get("out.txt", "")
    b_outputs = adapter.fetch(b)["outputs"].get("out.txt", "")
    if "iso" in a_outputs or "ok" in b_outputs:
        raise AssertionError("run outputs leaked across run isolation")
    if "ok" not in a_outputs or "iso" not in b_outputs:
        raise AssertionError("each run's fetch is missing its own output")
    return "separate job dirs"


def _cancel(adapter: HpcAdapter, job_id: str) -> str:
    result = adapter.cancel(job_id)
    if result.get("state") != "CANCELLED":
        raise AssertionError(f"cancel did not reach CANCELLED: {result}")
    return "CANCELLED"


def _fetch_before(adapter: HpcAdapter, job_id: str) -> str:
    try:
        adapter.fetch(job_id)
    except AdapterError:
        return "raised as required"
    raise AssertionError("fetch on a non-success job did not raise")


def _cancel_again(adapter: HpcAdapter, job_id: str) -> str:
    try:
        adapter.cancel(job_id)
    except AdapterError:
        return "raised as required"
    raise AssertionError("cancel on a terminal job did not raise")


def _accounting(adapter: HpcAdapter) -> str:
    usage = adapter.usage()
    if not isinstance(usage.get("jobs"), int) or usage["jobs"] < 3:
        raise AssertionError(f"usage ledger undercounted: {usage}")
    return "ledger reflects all submitted jobs"


def _settlement(adapter: HpcAdapter) -> str:
    usage = adapter.usage()
    if usage.get("submitted") != usage.get("jobs"):
        raise AssertionError(
            f"settlement mismatch: submitted={usage.get('submitted')} jobs={usage.get('jobs')}"
        )
    return "ledger settled"
