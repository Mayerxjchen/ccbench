"""Benchmark result taxonomy.

Top-level classes: ``VALID_RESULT``, ``AGENT_FAILURE``, ``INFRA_INVALID``.
Only ``VALID_RESULT`` counts scientifically; ``INFRA_INVALID`` is excluded from
scientific denominators and may be retried with a fresh run_id. Every
non-VALID outcome carries an explicit ``FailureCode`` and machine-readable
reason — exceptions are never collapsed into ``reward = 0``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "result.schema.json"


class ResultClass(Enum):
    VALID_RESULT = "VALID_RESULT"
    AGENT_FAILURE = "AGENT_FAILURE"
    INFRA_INVALID = "INFRA_INVALID"


class FailureCode(Enum):
    # VALID_RESULT verdicts
    PASS = "PASS"
    SCIENTIFIC_FAIL = "SCIENTIFIC_FAIL"
    # AGENT_FAILURE
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    AGENT_BUDGET_EXHAUSTED = "AGENT_BUDGET_EXHAUSTED"
    RESOURCE_EXCEEDED = "RESOURCE_EXCEEDED"
    NO_SUBMISSION = "NO_SUBMISSION"
    INVALID_SUBMISSION = "INVALID_SUBMISSION"
    BAD_INPUT = "BAD_INPUT"
    # INFRA_INVALID
    SANDBOX_FAILURE = "SANDBOX_FAILURE"
    HARNESS_FAILURE = "HARNESS_FAILURE"
    GATEWAY_FAILURE = "GATEWAY_FAILURE"
    ADAPTER_FAILURE = "ADAPTER_FAILURE"
    HPC_FAILURE = "HPC_FAILURE"
    VERIFIER_FAILURE = "VERIFIER_FAILURE"
    # API / provider transient failures (infrastructure, not the agent)
    API_TRANSIENT_EXHAUSTED = "API_TRANSIENT_EXHAUSTED"
    API_RATE_LIMIT_EXHAUSTED = "API_RATE_LIMIT_EXHAUSTED"
    API_AUTH_CONFIG = "API_AUTH_CONFIG"
    API_QUOTA = "API_QUOTA"
    API_CONFIGURATION = "API_CONFIGURATION"


@dataclass(frozen=True)
class BenchmarkResult:
    """Classified outcome of one attempt."""

    run_id: str
    result_class: ResultClass
    failure_code: FailureCode | None
    reason: str
    retryable: bool = False

    @property
    def is_counted_scientifically(self) -> bool:
        return self.result_class is ResultClass.VALID_RESULT

    @classmethod
    def valid(cls, run_id: str, passed: bool, reason: str = "") -> "BenchmarkResult":
        return cls(
            run_id=run_id,
            result_class=ResultClass.VALID_RESULT,
            failure_code=FailureCode.PASS if passed else FailureCode.SCIENTIFIC_FAIL,
            reason=reason,
            retryable=False,
        )

    @classmethod
    def agent_failure(cls, run_id: str, code: FailureCode, reason: str) -> "BenchmarkResult":
        if code not in {
            FailureCode.AGENT_TIMEOUT,
            FailureCode.AGENT_BUDGET_EXHAUSTED,
            FailureCode.RESOURCE_EXCEEDED,
            FailureCode.NO_SUBMISSION,
            FailureCode.INVALID_SUBMISSION,
            FailureCode.BAD_INPUT,
        }:
            raise ValueError(f"{code.value} is not an AGENT_FAILURE code")
        return cls(
            run_id=run_id,
            result_class=ResultClass.AGENT_FAILURE,
            failure_code=code,
            reason=reason,
            retryable=False,
        )

    @classmethod
    def infra_invalid(cls, run_id: str, code: FailureCode, reason: str) -> "BenchmarkResult":
        if code not in {
            FailureCode.SANDBOX_FAILURE,
            FailureCode.HARNESS_FAILURE,
            FailureCode.GATEWAY_FAILURE,
            FailureCode.ADAPTER_FAILURE,
            FailureCode.HPC_FAILURE,
            FailureCode.VERIFIER_FAILURE,
            FailureCode.API_TRANSIENT_EXHAUSTED,
            FailureCode.API_RATE_LIMIT_EXHAUSTED,
            FailureCode.API_AUTH_CONFIG,
            FailureCode.API_QUOTA,
            FailureCode.API_CONFIGURATION,
        }:
            raise ValueError(f"{code.value} is not an INFRA_INVALID code")
        return cls(
            run_id=run_id,
            result_class=ResultClass.INFRA_INVALID,
            failure_code=code,
            reason=reason,
            retryable=True,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "run_id": self.run_id,
            "result_class": self.result_class.value,
            "failure_code": None if self.failure_code is None else self.failure_code.value,
            "reason": self.reason,
            "retryable": self.retryable,
            "is_counted_scientifically": self.is_counted_scientifically,
        }
        self._validate(payload)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BenchmarkResult":
        cls._validate(payload)
        return cls(
            run_id=str(payload["run_id"]),
            result_class=ResultClass(payload["result_class"]),
            failure_code=None
            if payload["failure_code"] is None
            else FailureCode(payload["failure_code"]),
            reason=str(payload["reason"]),
            retryable=bool(payload["retryable"]),
        )

    @staticmethod
    def _validate(payload: dict[str, Any]) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(payload),
            key=lambda e: list(e.path),
        )
        if errors:
            raise ValueError(f"result violates result.schema.json: {errors[0].message}")
