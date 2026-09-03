# Probe: declared-but-missing artifact (v3 C-V8 existence)

The manifest declares `artifacts/dataset/train.raw` with a well-formed hash
but never ships the file; every other check is clean. Expected classification:
`AGENT_FAILURE / SCIENTIFIC_FAIL` naming
`C-V8: declared artifact missing from sealed root`. A verifier that only
hashes files that happen to exist silently passes this.
