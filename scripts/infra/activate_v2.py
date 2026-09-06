#!/usr/bin/env python3
"""Derived activation status for the HpcDispatcher simplification.

``--mode simulated`` runs the deterministic gate set (D0-D10) by invoking the
scoped test suites and reports one PASS/FAIL per gate as JSON. Real-site gates
(D11 canary receipt, D12 five-lineage Pilot) are reported NOT_RUN until the
authorized qualification artifacts exist — a simulated run never flips Formal
on.

Exit code: 0 when every deterministic gate passes and the real-site gates are
NOT_RUN; 1 on any deterministic failure.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTEST = ROOT / ".venv" / "bin" / "pytest"

# gate -> (title, [pytest targets], kind)
GATES: dict[str, tuple[str, list[str], str]] = {
    "D0": ("baseline suite green (known expected failures only)",
           ["tests"], "suite"),
    "D1": ("dispatcher facade parity",
           ["tests/hpc/test_dispatcher_facade.py",
            "tests/hpc/test_dispatcher_parity.py"], "scoped"),
    "D2": ("execution request v2 + input staging",
           ["tests/hpc/test_execution_request.py",
            "tests/hpc/test_input_staging.py"], "scoped"),
    "D3": ("operation-attempt lifecycle",
           ["tests/hpc/test_operation_attempts.py",
            "tests/hpc/test_client.py"], "scoped"),
    "D4": ("driver conformance + Formal rejection",
           ["tests/hpc/test_driver_conformance.py"], "scoped"),
    "D5": ("site profile freeze",
           ["tests/hpc/test_site_profile.py"], "scoped"),
    "D6": ("runtime wrapper containment",
           ["tests/hpc/test_runtime_wrapper.py",
            "tests/hpc/test_runtime_containment.py"], "scoped"),
    "D7": ("production path through dispatcher only",
           ["tests/hpc/test_dispatcher_production_path.py"], "scoped"),
    "D8": ("skill contract (deterministic part)",
           ["tests/skills/test_hpc_submit_benchmark_contract.py"], "scoped"),
    "D9": ("settlement + evidence semantics",
           ["tests/hpc/test_dispatcher_settlement.py",
            "tests/contracts/test_run_record_v2.py"], "scoped"),
    "D10": ("fault-injection matrix",
            ["tests/hpc/test_dispatcher_faults.py"], "scoped"),
    "D11": ("real-site canary qualification receipt", [], "real"),
    "D12": ("five-lineage Pilot evidence", [], "real"),
}

# No baseline failures: pytest is green.  D11/D12 are checked separately
# via the qualification receipt and pilot evidence.


def _run_pytest(targets: list[str]) -> tuple[str, str]:
    proc = subprocess.run(
        [str(PYTEST), "-q", "-p", "no:cacheprovider", *targets],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=900,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _check_d11_receipt() -> dict:
    """Check D11 qualification receipt and return gate result."""
    from ccbench.experiments.release_builder import check_qualification_receipt

    result = check_qualification_receipt(ROOT)
    status = result["status"]
    if status == "PASS":
        return {
            "gate": "D11", "title": GATES["D11"][0],
            "status": "PASS",
            "detail": f"receipt valid: {result.get('receipt_path', '?')}",
        }
    return {
        "gate": "D11", "title": GATES["D11"][0],
        "status": "NOT_RUN",
        "detail": result["detail"],
    }


def _gate_result(gate: str, targets: list[str]) -> dict:
    title, kind = GATES[gate][0], GATES[gate][2]
    if gate == "D11":
        return _check_d11_receipt()
    if kind == "real":
        return {"gate": gate, "title": title, "status": "NOT_RUN",
                "detail": "requires authorized real-site execution"}
    code, output = _run_pytest(targets)
    if kind == "suite":
        failed_lines = [
            line.split(" ")[1]
            for line in output.splitlines()
            if line.startswith("FAILED ")
        ]
        unexpected = sorted(set(failed_lines))
        status = "PASS" if code == 0 or (unexpected == [] and failed_lines) else "FAIL"
        detail = (f"{len(failed_lines)} expected; unexpected={unexpected}"
                  if status == "PASS" else output[-800:])
    else:
        status = "PASS" if code == 0 else "FAIL"
        detail = output[-400:] if status == "FAIL" else "scoped suite green"
    return {"gate": gate, "title": title, "status": status, "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("simulated", "real"),
                        default="simulated")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = [_gate_result(gate, GATES[gate][1]) for gate in GATES]
    deterministic_ok = all(r["status"] == "PASS" for r in results
                           if r["gate"] not in ("D11", "D12"))
    formal_enabled = False  # never flipped by a simulated run

    report = {
        "mode": args.mode,
        "formal_enabled": formal_enabled,
        "deterministic_all_pass": deterministic_ok,
        "gates": results,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for r in results:
            print(f"[{r['status']:7}] {r['gate']}: {r['title']}")
        print(f"deterministic_all_pass={deterministic_ok} formal_enabled=False")
    return 0 if deterministic_ok else 1


if __name__ == "__main__":
    sys.exit(main())
