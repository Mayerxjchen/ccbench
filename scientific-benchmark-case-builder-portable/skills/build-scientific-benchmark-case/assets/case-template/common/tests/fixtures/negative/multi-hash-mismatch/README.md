# Probe: hash checks never short-circuit (v3)

Two present artifacts both carry wrong hashes. Expected classification:
`AGENT_FAILURE / SCIENTIFIC_FAIL` whose reason names BOTH
`artifacts/model/student.pb` and `artifacts/dataset/train.raw`. A verifier
that stops after the first mismatch hides the second problem.
