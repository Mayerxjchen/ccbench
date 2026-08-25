#!/bin/bash
set -e
# Common verifier launcher: delegates to dftworld_bench.verifiers.launcher
# for structured result.json/ctrf output.
exec python -m dftworld_bench.verifiers.launcher \
    --run-id "005-cp2k-version" \
    --test-path /tests/test_outputs.py
