"""Benchmark result taxonomy: classification + retry semantics."""

from __future__ import annotations

import pytest

from bench.contracts.result import (
    BenchmarkResult,
    FailureCode,
    ResultClass,
)


def test_infra_invalid_is_not_a_scientific_failure():
    result = BenchmarkResult.infra_invalid("r1", FailureCode.HPC_FAILURE, "node lost")
    assert result.result_class == ResultClass.INFRA_INVALID
    assert result.is_counted_scientifically is False
    assert result.retryable is True


def test_valid_result_is_counted_scientifically():
    passed = BenchmarkResult.valid("r2", passed=True, reason="energy converged")
    failed = BenchmarkResult.valid("r2", passed=False, reason="wrong lattice")
    assert passed.result_class is ResultClass.VALID_RESULT
    assert passed.is_counted_scientifically is True
    assert passed.failure_code is FailureCode.PASS
    assert failed.failure_code is FailureCode.SCIENTIFIC_FAIL
    assert failed.is_counted_scientifically is True


def test_agent_failure_is_counted_non_success_not_infra():
    result = BenchmarkResult.agent_failure("r3", FailureCode.AGENT_TIMEOUT, "deadline")
    assert result.result_class is ResultClass.AGENT_FAILURE
    assert result.is_counted_scientifically is False
    assert result.retryable is False


def test_result_class_set_matches_normative_docs():
    assert {c.value for c in ResultClass} == {
        "VALID_RESULT",
        "AGENT_FAILURE",
        "INFRA_INVALID",
    }


def test_failure_codes_match_normative_docs():
    assert {c.value for c in FailureCode} == {
        "PASS",
        "SCIENTIFIC_FAIL",
        # AGENT_FAILURE
        "AGENT_TIMEOUT",
        "AGENT_BUDGET_EXHAUSTED",
        "RESOURCE_EXCEEDED",
        "NO_SUBMISSION",
        "INVALID_SUBMISSION",
        "BAD_INPUT",
        # INFRA_INVALID
        "SANDBOX_FAILURE",
        "HARNESS_FAILURE",
        "GATEWAY_FAILURE",
        "ADAPTER_FAILURE",
        "HPC_FAILURE",
        "VERIFIER_FAILURE",
        "API_TRANSIENT_EXHAUSTED",
        "API_RATE_LIMIT_EXHAUSTED",
        "API_AUTH_CONFIG",
        "API_QUOTA",
        "API_CONFIGURATION",
    }


def test_serialized_result_conforms_to_schema():
    result = BenchmarkResult.infra_invalid("r1", FailureCode.HARNESS_FAILURE, "boom")
    payload = result.to_dict()
    assert payload["result_class"] == "INFRA_INVALID"
    assert payload["failure_code"] == "HARNESS_FAILURE"
    assert payload["is_counted_scientifically"] is False
    assert payload["retryable"] is True


def test_agent_budget_exhausted_is_agent_failure():
    result = BenchmarkResult.agent_failure(
        "r7", FailureCode.AGENT_BUDGET_EXHAUSTED, "turn budget exhausted"
    )
    assert result.result_class is ResultClass.AGENT_FAILURE
    assert result.is_counted_scientifically is False


def test_api_transient_exhausted_is_infra_invalid_retryable():
    result = BenchmarkResult.infra_invalid(
        "r8", FailureCode.API_TRANSIENT_EXHAUSTED, "provider 503s exhausted retries"
    )
    assert result.result_class is ResultClass.INFRA_INVALID
    assert result.retryable is True


def test_api_auth_config_is_infra_invalid():
    result = BenchmarkResult.infra_invalid(
        "r9", FailureCode.API_AUTH_CONFIG, "credential misconfigured"
    )
    assert result.result_class is ResultClass.INFRA_INVALID
