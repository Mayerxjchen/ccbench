# Probe: garbage manifest types classify INVALID_SUBMISSION (v3)

`sha256` is a bare integer and `lineage[0].round` is a string. Expected
classification: `AGENT_FAILURE / INVALID_SUBMISSION`, reason starting with
`manifest type validation failed`, retryable false. Before v3 the string
round reached `int()` and the verifier crashed into a retryable
`INFRA_INVALID`, charging the Agent's malformed manifest to the harness.
